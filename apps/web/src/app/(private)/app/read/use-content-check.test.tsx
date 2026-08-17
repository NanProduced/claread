/** @vitest-environment jsdom */

/**
 * Content Check 状态机（useContentCheck）测试 — mock BFF 合同。
 *
 * 覆盖：初始 GET 加载、编辑→PUT reparse、stale 409 自动重放、双重 stale
 * 进入 conflict、conflict 恢复（载入最新 / 重放）、保存失败保留草稿、
 * confirm 成功 / stale_candidate_revision 重试 / stable 直达、
 * source_frozen → open_reader、404 → onSourceMissing。
 */

import { renderHook, act, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { readRejectedReasons, useContentCheck } from "./use-content-check";

const RECORD_ID = "rec_cc_1";

function makeReadResponse(overrides: Record<string, unknown> = {}) {
  return {
    ok: true as const,
    source_document_id: "cs_1",
    record_generation: 1,
    revision: 1,
    status: "draft" as const,
    markdown_text: "# Draft\n\nOriginal body.",
    content_sha256: "a".repeat(64),
    edit_source: "initial" as const,
    updated_at: "2026-07-28T00:00:00.000Z",
    candidate: {
      candidate_document_id: "cand_1",
      status: "ready" as const,
      canonical_text_preview: "Original body.",
    },
    quality: null,
    adaptation_notice: [],
    content_check: [],
    ...overrides,
  };
}

function makeUpdateResponse(overrides: Record<string, unknown> = {}) {
  return {
    ok: true as const,
    revision: 2,
    content_sha256: "b".repeat(64),
    outcome: "candidate_document_required" as const,
    candidate: {
      candidate_document_id: "cand_2",
      status: "ready" as const,
      canonical_text_preview: "Edited body.",
    },
    quality: null,
    adaptation_notice: [],
    content_check: [],
    ...overrides,
  };
}

function stalePutError(currentRevision: number) {
  return {
    ok: false as const,
    status: 409,
    code: "stale_source_revision",
    message: "草稿已被其他更新抢先保存。",
    currentRevision,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

type RouteHandler = (init?: RequestInit) => Response | Promise<Response>;

interface MockRoutes {
  onGet?: RouteHandler;
  onPut?: RouteHandler;
  onConfirm?: RouteHandler;
}

function installFetchMock(routes: MockRoutes) {
  const calls = { get: 0, put: 0, confirm: 0 };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    if (url.includes("/confirmed-source") && method === "GET") {
      calls.get += 1;
      if (!routes.onGet) throw new Error(`Unexpected GET ${url}`);
      return routes.onGet(init);
    }
    if (url.includes("/confirmed-source") && method === "PUT") {
      calls.put += 1;
      if (!routes.onPut) throw new Error(`Unexpected PUT ${url}`);
      return routes.onPut(init);
    }
    if (url.includes("/confirm") && method === "POST") {
      calls.confirm += 1;
      if (!routes.onConfirm) throw new Error(`Unexpected POST ${url}`);
      return routes.onConfirm(init);
    }
    throw new Error(`Unexpected fetch ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, calls };
}

function createCallbacks() {
  return {
    onOpenReader: vi.fn(),
    onSourceMissing: vi.fn(),
    onConfirmed: vi.fn(),
  };
}

function renderContentCheck(callbacks = createCallbacks()) {
  const utils = renderHook(() =>
    useContentCheck({ recordId: RECORD_ID, ...callbacks }),
  );
  return { ...utils, callbacks };
}

describe("useContentCheck 初始加载", () => {
  beforeEach(() => {
    vi.useRealTimers();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("GET 200 → ready，草稿与 candidate 载入", async () => {
    installFetchMock({ onGet: () => json(makeReadResponse()) });
    const { result } = renderContentCheck();

    await waitFor(() => expect(result.current.state.phase).toBe("ready"));
    expect(result.current.state.draft?.revision).toBe(1);
    expect(result.current.workingMarkdown).toBe("# Draft\n\nOriginal body.");
    expect(result.current.state.draft?.candidate?.candidate_document_id).toBe("cand_1");
    expect(result.current.state.dirty).toBe(false);
  });

  it("GET 404 → onSourceMissing（存量记录终态）", async () => {
    installFetchMock({
      onGet: () =>
        json(
          { ok: false, status: 404, code: "confirmed_source_not_found", message: "x" },
          404,
        ),
    });
    const { result, callbacks } = renderContentCheck();

    await waitFor(() =>
      expect(callbacks.onSourceMissing).toHaveBeenCalledTimes(1),
    );
    expect(result.current.state.draft).toBeNull();
  });

  it("GET 409 record_state_advanced → onOpenReader", async () => {
    installFetchMock({
      onGet: () =>
        json(
          {
            ok: false,
            status: 409,
            code: "candidate_conflict_open_reader",
            message: "已进入阅读",
          },
          409,
        ),
    });
    const { callbacks } = renderContentCheck();

    await waitFor(() =>
      expect(callbacks.onOpenReader).toHaveBeenCalledWith(RECORD_ID),
    );
  });

  it("GET 网络失败 → error + retryLoad 恢复", async () => {
    let fail = true;
    installFetchMock({
      onGet: () => {
        if (fail) throw new Error("network down");
        return json(makeReadResponse());
      },
    });
    const { result } = renderContentCheck();

    await waitFor(() => expect(result.current.state.phase).toBe("error"));

    fail = false;
    act(() => result.current.retryLoad());
    await waitFor(() => expect(result.current.state.phase).toBe("ready"));
  });
});

describe("useContentCheck 编辑 → PUT reparse", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("handleEdit 标脏；saveNow PUT 200 后 revision/提示更新", async () => {
    const putBodies: unknown[] = [];
    installFetchMock({
      onGet: () => json(makeReadResponse()),
      onPut: (init) => {
        putBodies.push(JSON.parse(String(init?.body ?? "{}")));
        return json(
          makeUpdateResponse({
            adaptation_notice: [
              { code: "raw_html_block", message: "已清洗 HTML", classification: "adaptation_notice" },
            ],
            content_check: [
              { code: "unclosed_fence", message: "代码块未闭合", classification: "content_check" },
            ],
          }),
        );
      },
    });
    const { result } = renderContentCheck();
    await waitFor(() => expect(result.current.state.phase).toBe("ready"));

    act(() => result.current.handleEdit("# Draft\n\nEdited body."));
    expect(result.current.state.dirty).toBe(true);

    let saved = false;
    await act(async () => {
      saved = await result.current.saveNow();
    });
    expect(saved).toBe(true);
    expect(result.current.state.dirty).toBe(false);
    expect(result.current.state.draft?.revision).toBe(2);
    expect(result.current.state.draft?.candidate?.candidate_document_id).toBe("cand_2");
    expect(result.current.state.draft?.adaptationNotice).toHaveLength(1);
    expect(result.current.state.draft?.contentCheck).toHaveLength(1);

    expect(putBodies[0]).toMatchObject({
      expected_revision: 1,
      markdown_text: "# Draft\n\nEdited body.",
      edit_source: "content_check",
    });
  });

  it("PUT 网络失败保留用户修改并回到可重试状态", async () => {
    installFetchMock({
      onGet: () => json(makeReadResponse()),
      onPut: () => {
        throw new Error("network down");
      },
    });
    const { result } = renderContentCheck();
    await waitFor(() => expect(result.current.state.phase).toBe("ready"));

    act(() => result.current.handleEdit("# Draft\n\nUnsaved edit."));
    let saved = true;
    await act(async () => {
      saved = await result.current.saveNow();
    });

    expect(saved).toBe(false);
    expect(result.current.state.phase).toBe("ready");
    expect(result.current.state.dirty).toBe(true);
    expect(result.current.state.errorMessage).toContain("保存失败");
    expect(result.current.workingMarkdown).toBe("# Draft\n\nUnsaved edit.");
  });

  it("新 revision 的重新检查结果不会继承上一版的处置状态", async () => {
    installFetchMock({
      onGet: () =>
        json(
          makeReadResponse({
            content_check: [
              {
                code: "footnote_ref",
                message: "旧版本脚注",
                classification: "content_check",
              },
            ],
          }),
        ),
      onPut: () =>
        json(
          makeUpdateResponse({
            revision: 2,
            content_check: [
              {
                code: "footnote_ref",
                message: "新版本脚注",
                classification: "content_check",
              },
            ],
          }),
        ),
    });
    const { result } = renderContentCheck();
    await waitFor(() => expect(result.current.state.phase).toBe("ready"));

    act(() => result.current.resolveCheckCode("footnote_ref"));
    expect(result.current.resolvedCheckCodes.has("footnote_ref")).toBe(true);

    act(() => result.current.handleEdit("# Draft\n\nEdited."));
    await act(async () => {
      await result.current.saveNow();
    });

    expect(result.current.state.draft?.revision).toBe(2);
    expect(result.current.resolvedCheckCodes.size).toBe(0);
  });

  it("防抖自动保存触发一次 PUT", async () => {
    vi.useFakeTimers();
    const { calls } = installFetchMock({
      onGet: () => json(makeReadResponse()),
      onPut: () => json(makeUpdateResponse()),
    });
    const { result } = renderContentCheck();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.state.phase).toBe("ready");

    act(() => result.current.handleEdit("# Draft\n\nEdited body."));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1300);
    });
    expect(calls.put).toBe(1);
    vi.useRealTimers();
  });

  it("PUT stale 409 → 自动重取最新草稿并以用户文本重放一次", async () => {
    const putRevisions: number[] = [];
    let putCount = 0;
    const { calls } = installFetchMock({
      onGet: () =>
        json(makeReadResponse({ revision: 5, markdown_text: "# Draft\n\nSomeone else." })),
      onPut: (init) => {
        putCount += 1;
        const body = JSON.parse(String(init?.body ?? "{}"));
        putRevisions.push(body.expected_revision);
        if (putCount === 1) {
          return json(stalePutError(5), 409);
        }
        return json(makeUpdateResponse({ revision: 6 }));
      },
    });
    const { result } = renderContentCheck();
    // 初始 GET 返回 revision 5 的草稿（用户基于它编辑）。
    await waitFor(() => expect(result.current.state.phase).toBe("ready"));

    act(() => result.current.handleEdit("# Draft\n\nMy edit wins."));
    let saved = false;
    await act(async () => {
      saved = await result.current.saveNow();
    });

    expect(saved).toBe(true);
    expect(calls.put).toBe(2);
    expect(putRevisions).toEqual([5, 5]);
    expect(result.current.state.draft?.revision).toBe(6);
    expect(result.current.state.infoMessage).toContain("最新版本");
  });

  it("重放仍 stale → conflict；以我的版本重试成功后 ready", async () => {
    let putCount = 0;
    installFetchMock({
      onGet: () =>
        json(makeReadResponse({ revision: 5, markdown_text: "# Draft\n\nSomeone else." })),
      onPut: () => {
        putCount += 1;
        if (putCount <= 2) return json(stalePutError(5), 409);
        return json(makeUpdateResponse({ revision: 6 }));
      },
    });
    const { result } = renderContentCheck();
    await waitFor(() => expect(result.current.state.phase).toBe("ready"));

    act(() => result.current.handleEdit("# Draft\n\nMy edit."));
    await act(async () => {
      await result.current.saveNow();
    });
    expect(result.current.state.phase).toBe("conflict");
    expect(result.current.state.errorMessage).toContain("抢先保存");

    let retried = false;
    await act(async () => {
      retried = await result.current.retryWithLatestRevision();
    });
    expect(retried).toBe(true);
    expect(result.current.state.phase).toBe("ready");
    expect(result.current.state.draft?.revision).toBe(6);
  });

  it("conflict 后 reloadLatest 放弃本地修改并载入服务端草稿", async () => {
    let putCount = 0;
    installFetchMock({
      onGet: () =>
        json(makeReadResponse({ revision: 5, markdown_text: "# Draft\n\nServer latest." })),
      onPut: () => {
        putCount += 1;
        return json(stalePutError(5), 409);
      },
    });
    const { result } = renderContentCheck();
    await waitFor(() => expect(result.current.state.phase).toBe("ready"));

    act(() => result.current.handleEdit("# Draft\n\nMy edit."));
    await act(async () => {
      await result.current.saveNow();
    });
    expect(result.current.state.phase).toBe("conflict");

    let latest: string | null = null;
    await act(async () => {
      latest = await result.current.reloadLatest();
    });
    expect(latest).toBe("# Draft\n\nServer latest.");
    expect(result.current.state.phase).toBe("ready");
    expect(result.current.state.dirty).toBe(false);
    expect(result.current.workingMarkdown).toBe("# Draft\n\nServer latest.");
    expect(putCount).toBe(2);
  });

  it("PUT 5xx → 保留用户修改（dirty 不丢）并可重试", async () => {
    let fail = true;
    installFetchMock({
      onGet: () => json(makeReadResponse()),
      onPut: () => {
        if (fail) {
          return json({ ok: false, status: 503, code: "upstream_unavailable", message: "服务暂不可用" }, 503);
        }
        return json(makeUpdateResponse());
      },
    });
    const { result } = renderContentCheck();
    await waitFor(() => expect(result.current.state.phase).toBe("ready"));

    act(() => result.current.handleEdit("# Draft\n\nKeep my edit."));
    let saved = true;
    await act(async () => {
      saved = await result.current.saveNow();
    });
    expect(saved).toBe(false);
    expect(result.current.state.phase).toBe("ready");
    expect(result.current.state.dirty).toBe(true);
    expect(result.current.state.draft?.revision).toBe(1);
    expect(result.current.workingMarkdown).toBe("# Draft\n\nKeep my edit.");

    fail = false;
    await act(async () => {
      saved = await result.current.saveNow();
    });
    expect(saved).toBe(true);
    expect(result.current.state.dirty).toBe(false);
  });

  it("PUT idempotent_noop → 良性成功：revision/candidate 不变，清 dirty，不重取", async () => {
    const { calls } = installFetchMock({
      onGet: () => json(makeReadResponse()),
      onPut: () =>
        json(
          makeUpdateResponse({
            outcome: "idempotent_noop",
            revision: 1,
            content_sha256: "a".repeat(64),
            candidate: null,
          }),
        ),
    });
    const { result } = renderContentCheck();
    await waitFor(() => expect(result.current.state.phase).toBe("ready"));

    act(() => result.current.handleEdit("# Draft\n\nOriginal body."));
    // 与已保存文本同 hash（此处即同一文本）：不调度自动保存，但显式
    // saveNow 仍应走 PUT 并得到幂等 no-op。
    let saved = false;
    await act(async () => {
      saved = await result.current.saveNow("# Draft\n\nOriginal body.");
    });

    expect(saved).toBe(true);
    expect(calls.get).toBe(1); // 未因 no-op 重取
    expect(result.current.state.phase).toBe("ready");
    expect(result.current.state.dirty).toBe(false);
    expect(result.current.state.errorMessage).toBeNull();
    // revision / candidate / outcome 均未推进。
    expect(result.current.state.draft?.revision).toBe(1);
    expect(result.current.state.draft?.candidate?.candidate_document_id).toBe("cand_1");
    expect(result.current.state.draft?.outcome).toBe("candidate_document_required");
  });

  it("PUT source_frozen → onOpenReader", async () => {
    installFetchMock({
      onGet: () => json(makeReadResponse()),
      onPut: () =>
        json(
          { ok: false, status: 409, code: "candidate_conflict_open_reader", message: "已冻结" },
          409,
        ),
    });
    const { result, callbacks } = renderContentCheck();
    await waitFor(() => expect(result.current.state.phase).toBe("ready"));

    act(() => result.current.handleEdit("# Draft\n\nEdit."));
    await act(async () => {
      await result.current.saveNow();
    });
    expect(callbacks.onOpenReader).toHaveBeenCalledWith(RECORD_ID);
  });
});

describe("useContentCheck confirm 流程", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("干净状态直接 confirm 200 → onConfirmed", async () => {
    const { calls } = installFetchMock({
      onGet: () => json(makeReadResponse()),
      onConfirm: () => json({ ok: true }),
    });
    const { result, callbacks } = renderContentCheck();
    await waitFor(() => expect(result.current.state.phase).toBe("ready"));

    await act(async () => {
      await result.current.confirmAndStart();
    });
    expect(calls.put).toBe(0);
    expect(calls.confirm).toBe(1);
    expect(callbacks.onConfirmed).toHaveBeenCalledWith(RECORD_ID);
  });

  it("脏状态先 flush 保存再 confirm", async () => {
    const order: string[] = [];
    installFetchMock({
      onGet: () => json(makeReadResponse()),
      onPut: () => {
        order.push("put");
        return json(makeUpdateResponse());
      },
      onConfirm: () => {
        order.push("confirm");
        return json({ ok: true });
      },
    });
    const { result, callbacks } = renderContentCheck();
    await waitFor(() => expect(result.current.state.phase).toBe("ready"));

    act(() => result.current.handleEdit("# Draft\n\nEdited before confirm."));
    await act(async () => {
      await result.current.confirmAndStart();
    });
    expect(order).toEqual(["put", "confirm"]);
    expect(callbacks.onConfirmed).toHaveBeenCalledWith(RECORD_ID);
  });

  it("confirm 409 stale_candidate_revision → 重取新 candidate 重试一次", async () => {
    const confirmUrls: string[] = [];
    let confirmCount = 0;
    let getCount = 0;
    installFetchMock({
      onGet: () => {
        getCount += 1;
        return json(
          makeReadResponse(
            getCount === 1
              ? {}
              : {
                  candidate: {
                    candidate_document_id: "cand_fresh",
                    status: "ready",
                    canonical_text_preview: "Fresh.",
                  },
                },
          ),
        );
      },
      onConfirm: () => {
        confirmCount += 1;
        confirmUrls.push(String(confirmCount));
        if (confirmCount === 1) {
          return json(
            { ok: false, status: 409, code: "stale_candidate_revision", message: "候选已过期" },
            409,
          );
        }
        return json({ ok: true });
      },
    });
    const { result, callbacks } = renderContentCheck();
    await waitFor(() => expect(result.current.state.phase).toBe("ready"));

    // 记录 confirm URL：包装 fetchMock 不可行，改为检查 fetch 调用参数。
    await act(async () => {
      await result.current.confirmAndStart();
    });
    expect(confirmCount).toBe(2);
    expect(callbacks.onConfirmed).toHaveBeenCalledWith(RECORD_ID);

    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    const confirmCalls = fetchMock.mock.calls.filter((c) =>
      String(c[0]).includes("/candidate-documents/"),
    );
    expect(String(confirmCalls[0][0])).toContain("cand_1");
    expect(String(confirmCalls[1][0])).toContain("cand_fresh");
    expect(confirmUrls).toHaveLength(2);
  });

  it("outcome=stable_document_ready → 不发 confirm，直接 onConfirmed", async () => {
    installFetchMock({
      onGet: () => json(makeReadResponse()),
      onPut: () =>
        json(
          makeUpdateResponse({
            outcome: "stable_document_ready",
            candidate: null,
          }),
        ),
    });
    const { result, callbacks } = renderContentCheck();
    await waitFor(() => expect(result.current.state.phase).toBe("ready"));

    act(() => result.current.handleEdit("# Draft\n\nNow clean."));
    await act(async () => {
      await result.current.confirmAndStart();
    });
    expect(callbacks.onConfirmed).toHaveBeenCalledWith(RECORD_ID);
  });

  it("rejected outcome（无 candidate）→ 提示且不发 confirm", async () => {
    const { calls } = installFetchMock({
      onGet: () => json(makeReadResponse({ candidate: null })),
    });
    const { result, callbacks } = renderContentCheck();
    await waitFor(() => expect(result.current.state.phase).toBe("ready"));

    await act(async () => {
      await result.current.confirmAndStart();
    });
    expect(calls.confirm).toBe(0);
    expect(callbacks.onConfirmed).not.toHaveBeenCalled();
    expect(result.current.state.errorMessage).toContain("可确认");
  });
});

describe("useContentCheck content_check 处置", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("resolveCheckCode / resolveAllCheckCodes", async () => {
    installFetchMock({
      onGet: () =>
        json(
          makeReadResponse({
            content_check: [
              { code: "unclosed_fence", message: "m1", classification: "content_check" },
              { code: "footnote_ref", message: "m2", classification: "content_check" },
            ],
          }),
        ),
    });
    const { result } = renderContentCheck();
    await waitFor(() => expect(result.current.state.phase).toBe("ready"));

    act(() => result.current.resolveCheckCode("unclosed_fence"));
    expect(result.current.resolvedCheckCodes.has("unclosed_fence")).toBe(true);
    expect(result.current.resolvedCheckCodes.has("footnote_ref")).toBe(false);

    act(() => result.current.resolveAllCheckCodes());
    expect(result.current.resolvedCheckCodes.has("footnote_ref")).toBe(true);
  });
});

describe("readRejectedReasons", () => {
  it("maps content_check codes to user copy and ignores English diagnostic reasons", () => {
    const reasons = readRejectedReasons(
      {
        suitability: {
          reasons: [
            "pdf_text defaults to candidate review unless extraction confidence is explicitly high and the text is clearly simple.",
          ],
        },
      },
      [
        {
          code: "code_dominant",
          message: "Input appears to be code-dominant without Markdown prose structure.",
          classification: "content_check",
        },
      ],
    );
    expect(reasons).toEqual([
      "这份内容以代码为主，批注价值有限，建议确认是否继续。",
    ]);
    expect(reasons.join(" ")).not.toContain("pdf_text");
    expect(reasons.join(" ")).not.toContain("code-dominant");
  });

  it("uses generic fallback when no flags or content_check codes exist", () => {
    expect(readRejectedReasons({ suitability: { reasons: ["english debug"] } }, [])).toEqual([
      "这份内容暂时无法生成阅读版本，可以调整后重新提交。",
    ]);
  });

  it("prefers suitability.flags mapping over content_check and never renders English reasons", () => {
    const reasons = readRejectedReasons(
      {
        suitability: {
          flags: ["too_short_for_learning"],
          reasons: ["English content is too short for learning (37 words)."],
        },
      },
      [],
    );
    expect(reasons).toEqual(["英文内容太短，补充成一段完整的英文文章再试。"]);
    expect(reasons.join(" ")).not.toContain("English");
  });
});
