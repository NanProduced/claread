/** @vitest-environment jsdom */

/**
 * ContentCheckPanel 渲染测试 — 三级提示（adaptation_notice 可展开 /
 * content_check 风险卡片处置 / rejected 提示）、操作层级与出口。
 * 状态机细节由 use-content-check.test.tsx 覆盖；这里锁 DOM 合同。
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ContentCheckPanel } from "./ContentCheckPanel";
import type { ReaderContentCheckItemDto } from "@/types/api/reader-plate";

// 与 AnalyzeSubmitForm.test.tsx 相同的 Plate 编辑器桩：textarea + ref handle。
vi.mock("./MarkdownTextInput", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./MarkdownTextInput")>();
  const React = await import("react");
  const { forwardRef, useEffect, useImperativeHandle, useRef, useState } = React;

  type MockHandle = {
    getSubmitText: () => string;
    getMarkdown: () => string;
    focus: () => void;
    clear: () => void;
    setValue: (markdown: string) => void;
    reveal: (excerpt: string) => boolean;
    canRevealExact: (excerpt: string) => boolean;
    revealExact: (excerpt: string) => boolean;
    measureExact: (excerpt: string) => { top: number; documentHeight: number } | null;
    flush: () => string;
  };

  const MockMarkdownTextInput = forwardRef<
    MockHandle,
    {
      initialValue: string;
      onChange: (markdown: string) => void;
      onSubmit: () => void;
      className?: string;
      id?: string;
    }
  >(function MockMarkdownTextInput(props, ref) {
    const [value, setValue] = useState(props.initialValue ?? "");
    const valueRef = useRef(value);
    const serializeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    valueRef.current = value;
    useEffect(
      () => () => {
        if (serializeTimerRef.current) clearTimeout(serializeTimerRef.current);
      },
      [],
    );
    useImperativeHandle(ref, () => ({
      getSubmitText: () => valueRef.current,
      getMarkdown: () => valueRef.current,
      focus: () => {
        const el = document.getElementById(props.id ?? "");
        if (el instanceof HTMLTextAreaElement) el.focus();
      },
      clear: () => {
        setValue("");
        valueRef.current = "";
      },
      setValue: (markdown: string) => {
        setValue(markdown);
        valueRef.current = markdown;
      },
      reveal: () => true,
      canRevealExact: (excerpt: string) => !excerpt.includes("Ambiguous"),
      revealExact: (excerpt: string) => {
        if (excerpt.includes("Ambiguous")) return false;
        const el = document.getElementById(props.id ?? "");
        if (el) el.dataset.revealedExcerpt = excerpt;
        return true;
      },
      measureExact: (excerpt: string) => {
        if (excerpt.includes("Ambiguous")) return null;
        return {
          top: excerpt === "Second exact anchor." ? 880 : excerpt === "First exact anchor." ? 96 : 120,
          documentHeight: 1_100,
        };
      },
      flush: () => {
        if (serializeTimerRef.current) {
          clearTimeout(serializeTimerRef.current);
          serializeTimerRef.current = null;
        }
        props.onChange(valueRef.current);
        return valueRef.current;
      },
    }));
    return React.createElement("textarea", {
      id: props.id,
      className: props.className,
      value,
      onChange: (event: React.ChangeEvent<HTMLTextAreaElement>) => {
        setValue(event.target.value);
        valueRef.current = event.target.value;
        if (serializeTimerRef.current) clearTimeout(serializeTimerRef.current);
        serializeTimerRef.current = setTimeout(() => {
          serializeTimerRef.current = null;
          props.onChange(valueRef.current);
        }, 150);
      },
      onKeyDown: (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
          event.preventDefault();
          props.onSubmit();
        }
      },
    });
  });

  return { ...actual, MarkdownTextInput: MockMarkdownTextInput };
});

const DRAFT_MARKDOWN = "# Title\n\n```python\ndef f():\n    pass\n";

function makeContentCheckItem(
  issueId: string,
  overrides: Partial<ReaderContentCheckItemDto> = {},
): ReaderContentCheckItemDto {
  return {
    code: "source_type_review_default",
    message: "technical detail",
    classification: "content_check",
    issue_id: issueId,
    tier: "routine",
    target_scope: "document",
    source_anchor: null,
    anchor_hash: null,
    evidence: { excerpt_text: null, proposed_patch: null },
    source_media_coordinate: null,
    ...overrides,
  };
}

function makeReadResponse(overrides: Record<string, unknown> = {}) {
  return {
    ok: true as const,
    source_document_id: "cs_1",
    record_generation: 1,
    revision: 1,
    status: "draft" as const,
    markdown_text: DRAFT_MARKDOWN,
    content_sha256: "a".repeat(64),
    edit_source: "initial" as const,
    updated_at: "2026-07-28T00:00:00.000Z",
    candidate: {
      candidate_document_id: "cand_1",
      status: "ready" as const,
      canonical_text_preview: "Title",
    },
    quality: null,
    adaptation_notice: [
      {
        code: "raw_html_block",
        message: "已移除原始 HTML 块",
        classification: "adaptation_notice" as const,
      },
      {
        code: "unsafe_link_protocol",
        message: "已降级不安全链接",
        classification: "adaptation_notice" as const,
      },
    ],
    content_check: [
      makeContentCheckItem("1111111111111111", {
        code: "has_unclosed_fence",
        message: "Fenced code block is missing its closing fence.",
        tier: "attention",
        target_scope: "range",
        source_anchor: { start_utf16: 9, end_utf16: 18 },
        anchor_hash: "b839d1b2b703576919548db08bd100e7c9be17820b76bd5bbe386a36507ec127",
        evidence: {
          excerpt_text: "```python",
          proposed_patch: "```python\n```",
        },
      }),
      makeContentCheckItem("2222222222222222", {
        code: "footnote_reference",
        message: "Footnote reference encountered.",
      }),
    ],
    ...overrides,
  };
}

function installFetchMock(
  readOverrides: Record<string, unknown> = {},
  previewResponse: (
    input: RequestInfo | URL,
    init?: RequestInit,
  ) => Response | Promise<Response> = () =>
    new Response(new Blob(["pdf"], { type: "application/pdf" }), {
      status: 200,
      headers: { "content-type": "application/pdf" },
    }),
) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    if (url.includes("/source-preview") && method === "GET") {
      return previewResponse(input, init);
    }
    if (url.includes("/confirmed-source") && method === "GET") {
      return new Response(JSON.stringify(makeReadResponse(readOverrides)), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    if (url.includes("/confirmed-source") && method === "PUT") {
      return new Response(
        JSON.stringify({
          ok: true,
          revision: 2,
          content_sha256: "b".repeat(64),
          outcome: "candidate_document_required",
          candidate: {
            candidate_document_id: "cand_1",
            status: "ready",
            canonical_text_preview: "Title",
          },
          quality: null,
          adaptation_notice: [],
          content_check: [],
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }
    if (url.includes("/confirm")) {
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    throw new Error(`Unexpected fetch ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function installObjectUrlMock(objectUrls = ["blob:source-preview"]) {
  const NativeUrl = globalThis.URL;
  const remainingUrls = [...objectUrls];
  const createObjectURL = vi.fn(
    () => remainingUrls.shift() ?? "blob:source-preview",
  );
  const revokeObjectURL = vi.fn();
  class PreviewUrl extends NativeUrl {}
  PreviewUrl.createObjectURL = createObjectURL;
  PreviewUrl.revokeObjectURL = revokeObjectURL;
  vi.stubGlobal("URL", PreviewUrl);
  return { createObjectURL, revokeObjectURL };
}

function installMatchMedia(initialMatches: boolean) {
  let matches = initialMatches;
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  const mediaQueryList = {
    get matches() {
      return matches;
    },
    media: "(min-width: 1024px)",
    onchange: null,
    addEventListener: vi.fn(
      (_type: string, listener: (event: MediaQueryListEvent) => void) => {
        listeners.add(listener);
      },
    ),
    removeEventListener: vi.fn(
      (_type: string, listener: (event: MediaQueryListEvent) => void) => {
        listeners.delete(listener);
      },
    ),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  } as unknown as MediaQueryList;

  vi.stubGlobal("matchMedia", vi.fn(() => mediaQueryList));
  return {
    change(nextMatches: boolean) {
      matches = nextMatches;
      const event = {
        matches: nextMatches,
        media: mediaQueryList.media,
      } as MediaQueryListEvent;
      listeners.forEach((listener) => listener(event));
    },
  };
}

function renderPanel(overrides: Partial<Parameters<typeof ContentCheckPanel>[0]> = {}) {
  const props: Parameters<typeof ContentCheckPanel>[0] = {
    recordId: "rec_cc_1",
    filename: null,
    origin: "submit",
    onOpenReader: vi.fn(),
    onConfirmed: vi.fn(),
    onSourceMissing: vi.fn(),
    onBackToInput: vi.fn(),
    onDefer: vi.fn(),
    ...overrides,
  };
  const view = render(<ContentCheckPanel {...props} />);
  return { ...props, ...view };
}

async function waitForPanelReady() {
  await waitFor(() =>
    expect(screen.getByTestId("content-check-confirm-button")).toBeTruthy(),
  );
}

describe("ContentCheckPanel 三级提示渲染", () => {
  beforeEach(() => {
    cleanup();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("默认不预取；打开原件后只请求当前 record + generation 并安全渲染 PDF 页", async () => {
    const { createObjectURL } = installObjectUrlMock();
    const fetchMock = installFetchMock({
      adaptation_notice: [],
      content_check: [
        makeContentCheckItem("preview-pdf-page", {
          source_media_coordinate: { page_number: 3, bbox: null },
        }),
      ],
    });
    renderPanel();
    await waitForPanelReady();

    expect(
      fetchMock.mock.calls.filter(([input]) =>
        String(input).includes("/source-preview"),
      ),
    ).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "查看原件" }));

    const frame = await screen.findByTitle("原件 PDF 预览");
    const previewCalls = fetchMock.mock.calls.filter(([input]) =>
      String(input).includes("/source-preview"),
    );
    expect(previewCalls).toHaveLength(1);
    expect(String(previewCalls[0]?.[0])).toBe(
      "/api/web/reader/records/rec_cc_1/source-preview?expected_generation=1",
    );
    expect(previewCalls[0]?.[1]?.signal).toBeTruthy();
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(frame.getAttribute("src")).toBe("blob:source-preview#page=3");
  });

  it.each(["image/png", "image/jpeg", "image/webp"])(
    "%s 只通过 Blob URL 安全渲染完整图片",
    async (mime) => {
      const { createObjectURL } = installObjectUrlMock(["blob:safe-image"]);
      installFetchMock(
        {
          adaptation_notice: [],
          content_check: [makeContentCheckItem("preview-image")],
        },
        () =>
          new Response(new Blob(["image"], { type: mime }), {
            status: 200,
            headers: { "content-type": mime },
          }),
      );
      renderPanel();
      await waitForPanelReady();

      fireEvent.click(screen.getByRole("button", { name: "查看原件" }));

      const image = await screen.findByRole("img", {
        name: "当前材料的原件预览",
      });
      expect(image.getAttribute("src")).toBe("blob:safe-image");
      expect(image.className).toContain("object-contain");
      expect(createObjectURL).toHaveBeenCalledTimes(1);
      expect(screen.getByText("未能精确定位，以下为原件参考页")).toBeTruthy();
      expect(screen.queryByTitle("原件 PDF 预览")).toBeNull();
    },
  );

  it.each([
    "image/svg+xml",
    "image/gif",
    "image/tiff",
    "text/html",
    "application/octet-stream",
  ])(
    "%s 非白名单 MIME fail-closed，且失败不改变编辑与确认状态",
    async (unsupportedMime) => {
      const { createObjectURL } = installObjectUrlMock();
      installFetchMock(
        {
          adaptation_notice: [],
          content_check: [makeContentCheckItem("preview-unsupported")],
        },
        () =>
          new Response(new Blob(["unsafe"], { type: unsupportedMime }), {
            status: 200,
            headers: { "content-type": unsupportedMime },
          }),
      );
      renderPanel();
      await waitForPanelReady();
      const confirm = screen.getByTestId(
        "content-check-confirm-button",
      ) as HTMLButtonElement;
      expect(confirm.disabled).toBe(false);

      fireEvent.click(screen.getByRole("button", { name: "查看原件" }));

      expect(
        await screen.findByText(
          "该原件暂不支持安全预览，正文可继续编辑与确认。",
        ),
      ).toBeTruthy();
      expect(screen.getAllByTestId("source-preview-live-region")).toHaveLength(1);
      expect(createObjectURL).not.toHaveBeenCalled();
      expect(screen.queryByRole("img")).toBeNull();
      expect(screen.queryByTitle("原件 PDF 预览")).toBeNull();
      expect(confirm.disabled).toBe(false);

      const editor = document.getElementById(
        "content-check-editor",
      ) as HTMLTextAreaElement;
      fireEvent.change(editor, { target: { value: "# Still editable" } });
      expect(editor.value).toBe("# Still editable");
    },
  );

  it("临时预览失败只提供紧凑重试，并由同一个 live region 恢复", async () => {
    installObjectUrlMock(["blob:retry-preview"]);
    let attempt = 0;
    const previewSignals: AbortSignal[] = [];
    const fetchMock = installFetchMock(
      {
        adaptation_notice: [],
        content_check: [makeContentCheckItem("preview-retry")],
      },
      (_input, init) => {
        if (init?.signal) previewSignals.push(init.signal);
        attempt += 1;
        return attempt === 1
          ? new Response(null, { status: 503 })
          : new Response(new Blob(["pdf"], { type: "application/pdf" }), {
              status: 200,
              headers: { "content-type": "application/pdf" },
            });
      },
    );
    renderPanel();
    await waitForPanelReady();

    fireEvent.click(screen.getByRole("button", { name: "查看原件" }));
    expect(
      await screen.findByText("原件暂时无法预览，正文可继续编辑与确认。"),
    ).toBeTruthy();
    expect(previewSignals[0]?.aborted).toBe(true);
    expect(screen.getAllByTestId("source-preview-live-region")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByTitle("原件 PDF 预览")).toBeTruthy();
    expect(
      fetchMock.mock.calls.filter(([input]) =>
        String(input).includes("/source-preview"),
      ),
    ).toHaveLength(2);
  });

  it("预览失败不改变 Attention、Routine、defer、返回输入或最终确认", async () => {
    installObjectUrlMock();
    installFetchMock({}, () => new Response(null, { status: 503 }));
    const props = renderPanel();
    await waitForPanelReady();

    fireEvent.click(screen.getAllByTestId("source-preview-trigger")[0]!);
    await screen.findByText("原件暂时无法预览，正文可继续编辑与确认。");

    fireEvent.click(screen.getByRole("button", { name: "稍后处理" }));
    expect(props.onDefer).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "重新输入" }));
    expect(props.onBackToInput).toHaveBeenCalledWith(DRAFT_MARKDOWN);

    fireEvent.click(screen.getAllByRole("button", { name: "确认当前内容" })[0]!);
    await waitFor(() =>
      expect(screen.getAllByTestId("content-check-risk-item")).toHaveLength(1),
    );
    fireEvent.click(screen.getByRole("button", { name: "确认当前内容" }));
    await waitFor(() =>
      expect(
        (screen.getByTestId("content-check-confirm-button") as HTMLButtonElement)
          .disabled,
      ).toBe(false),
    );

    fireEvent.click(screen.getByTestId("content-check-confirm-button"));
    await waitFor(() => expect(props.onConfirmed).toHaveBeenCalledWith("rec_cc_1"));
  });

  it("关闭、重开与 unmount 逐个 revoke 已创建的 Blob URL，并返回触发器焦点", async () => {
    const { revokeObjectURL } = installObjectUrlMock([
      "blob:first-preview",
      "blob:second-preview",
    ]);
    const cleanupOrder: string[] = [];
    revokeObjectURL.mockImplementation((objectUrl) => {
      cleanupOrder.push(`revoke:${objectUrl}`);
    });
    const previewSignals: AbortSignal[] = [];
    installFetchMock(
      {
        adaptation_notice: [],
        content_check: [makeContentCheckItem("preview-lifecycle")],
      },
      (_input, init) => {
        if (init?.signal) {
          const index = previewSignals.push(init.signal);
          init.signal.addEventListener(
            "abort",
            () => cleanupOrder.push(`abort:${index}`),
            { once: true },
          );
        }
        return new Response(new Blob(["pdf"], { type: "application/pdf" }), {
          status: 200,
          headers: { "content-type": "application/pdf" },
        });
      },
    );
    const view = renderPanel();
    await waitForPanelReady();
    const trigger = screen.getByRole("button", { name: "查看原件" });

    fireEvent.click(trigger);
    expect((await screen.findByTitle("原件 PDF 预览")).getAttribute("src")).toBe(
      "blob:first-preview#page=1",
    );
    expect(screen.getByText("未能精确定位，以下为原件参考页")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "关闭原件预览" }));
    await waitFor(() => expect(screen.queryByTestId("source-preview-drawer")).toBeNull());
    expect(revokeObjectURL).toHaveBeenNthCalledWith(1, "blob:first-preview");
    expect(previewSignals[0]?.aborted).toBe(true);
    expect(cleanupOrder.slice(0, 2)).toEqual([
      "abort:1",
      "revoke:blob:first-preview",
    ]);
    await waitFor(() => expect(document.activeElement).toBe(trigger));

    fireEvent.click(trigger);
    expect((await screen.findByTitle("原件 PDF 预览")).getAttribute("src")).toBe(
      "blob:second-preview#page=1",
    );
    view.unmount();
    expect(revokeObjectURL).toHaveBeenNthCalledWith(2, "blob:second-preview");
    expect(revokeObjectURL).toHaveBeenCalledTimes(2);
    expect(previewSignals[1]?.aborted).toBe(true);
    expect(cleanupOrder).toEqual([
      "abort:1",
      "revoke:blob:first-preview",
      "abort:2",
      "revoke:blob:second-preview",
    ]);
  });

  it("关闭 pending 请求会 abort，且未创建 URL 时不 revoke", async () => {
    const { revokeObjectURL } = installObjectUrlMock();
    let previewSignal: AbortSignal | undefined;
    installFetchMock(
      {
        adaptation_notice: [],
        content_check: [makeContentCheckItem("preview-pending")],
      },
      (_input, init) =>
        new Promise<Response>((_resolve, reject) => {
          previewSignal = init?.signal ?? undefined;
          previewSignal?.addEventListener(
            "abort",
            () => reject(new DOMException("Aborted", "AbortError")),
            { once: true },
          );
        }),
    );
    renderPanel();
    await waitForPanelReady();

    fireEvent.click(screen.getByRole("button", { name: "查看原件" }));
    expect(await screen.findByText("正在载入原件预览…")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "关闭原件预览" }));

    expect(previewSignal?.aborted).toBe(true);
    expect(revokeObjectURL).not.toHaveBeenCalled();
  });

  it("recordId / recordGeneration 身份切换会 abort 并 revoke 旧预览", async () => {
    const { revokeObjectURL } = installObjectUrlMock(["blob:old-record"]);
    let generation = 1;
    let previewSignal: AbortSignal | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/source-preview")) {
        previewSignal = init?.signal ?? undefined;
        return new Response(new Blob(["pdf"], { type: "application/pdf" }), {
          status: 200,
          headers: { "content-type": "application/pdf" },
        });
      }
      if (url.includes("/confirmed-source")) {
        return new Response(
          JSON.stringify(
            makeReadResponse({
              source_document_id: `cs_${generation}`,
              record_generation: generation,
              adaptation_notice: [],
              content_check: [makeContentCheckItem(`preview-record-${generation}`)],
            }),
          ),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const view = renderPanel();
    await waitForPanelReady();

    fireEvent.click(screen.getByRole("button", { name: "查看原件" }));
    await screen.findByTitle("原件 PDF 预览");
    generation = 2;
    view.rerender(
      <ContentCheckPanel
        recordId="rec_cc_2"
        filename={null}
        origin="submit"
        onOpenReader={view.onOpenReader}
        onConfirmed={view.onConfirmed}
        onSourceMissing={view.onSourceMissing}
        onBackToInput={view.onBackToInput}
        onDefer={view.onDefer}
      />,
    );

    await waitFor(() => expect(screen.queryByTestId("source-preview-drawer")).toBeNull());
    expect(previewSignal?.aborted).toBe(true);
    expect(revokeObjectURL).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:old-record");
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).includes("/records/rec_cc_2/confirmed-source"),
        ),
      ).toBe(true),
    );
  });

  it("请求替换会 abort 旧请求，late completion 不得覆盖当前预览", async () => {
    const { createObjectURL } = installObjectUrlMock(["blob:current-preview"]);
    const pending: Array<{
      resolve: (response: Response) => void;
      signal: AbortSignal | undefined;
    }> = [];
    installFetchMock(
      {
        adaptation_notice: [],
        content_check: [
          makeContentCheckItem("preview-old", {
            source_media_coordinate: { page_number: 2, bbox: null },
          }),
          makeContentCheckItem("preview-current", {
            source_media_coordinate: { page_number: 4, bbox: null },
          }),
        ],
      },
      (_input, init) =>
        new Promise<Response>((resolve) => {
          pending.push({ resolve, signal: init?.signal ?? undefined });
        }),
    );
    renderPanel();
    await waitForPanelReady();
    const triggers = screen.getAllByTestId("source-preview-trigger");

    fireEvent.click(triggers[0]!);
    await waitFor(() => expect(pending).toHaveLength(1));
    fireEvent.click(triggers[1]!);
    await waitFor(() => expect(pending).toHaveLength(2));
    expect(pending[0]?.signal?.aborted).toBe(true);

    await act(async () => {
      pending[1]?.resolve(
        new Response(new Blob(["current"], { type: "application/pdf" }), {
          status: 200,
          headers: { "content-type": "application/pdf" },
        }),
      );
    });
    const frame = await screen.findByTitle("原件 PDF 预览");
    expect(frame.getAttribute("src")).toBe("blob:current-preview#page=4");

    await act(async () => {
      pending[0]?.resolve(
        new Response(new Blob(["late"], { type: "application/pdf" }), {
          status: 200,
          headers: { "content-type": "application/pdf" },
        }),
      );
    });
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1));
    expect(frame.getAttribute("src")).toBe("blob:current-preview#page=4");
  });

  it("Mobile review Sheet 切到 Desktop 后关闭 dialog、解除 inert 并恢复编辑", async () => {
    const media = installMatchMedia(false);
    installFetchMock();
    renderPanel();
    await waitForPanelReady();
    const documentSurface = screen.getByTestId("content-check-document");

    fireEvent.click(
      await screen.findByRole("button", { name: /展开审查批注面板/ }),
    );
    await screen.findByRole("dialog", { name: "审查批注" });
    expect(documentSurface.hasAttribute("inert")).toBe(true);

    act(() => media.change(true));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(documentSurface.hasAttribute("inert")).toBe(false);
    const editor = document.getElementById("content-check-editor") as HTMLTextAreaElement;
    editor.focus();
    fireEvent.change(editor, { target: { value: "# Desktop remains editable" } });
    expect(document.activeElement).toBe(editor);
    expect(editor.value).toBe("# Desktop remains editable");
  });

  it("Mobile source loading 切到 Desktop 后 abort，并移除 dialog、inert 与 live region", async () => {
    const media = installMatchMedia(false);
    installObjectUrlMock();
    let previewSignal: AbortSignal | undefined;
    installFetchMock(
      {
        adaptation_notice: [],
        content_check: [makeContentCheckItem("responsive-loading")],
      },
      (_input, init) =>
        new Promise<Response>((_resolve, reject) => {
          previewSignal = init?.signal ?? undefined;
          previewSignal?.addEventListener(
            "abort",
            () => reject(new DOMException("Aborted", "AbortError")),
            { once: true },
          );
        }),
    );
    renderPanel();
    await waitForPanelReady();
    const documentSurface = screen.getByTestId("content-check-document");

    fireEvent.click(
      await screen.findByRole("button", { name: /展开审查批注面板/ }),
    );
    fireEvent.click(screen.getByRole("button", { name: "查看原件" }));
    await screen.findByText("正在载入原件预览…");

    act(() => media.change(true));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(previewSignal?.aborted).toBe(true);
    expect(documentSurface.hasAttribute("inert")).toBe(false);
    expect(screen.queryByTestId("source-preview-live-region")).toBeNull();
  });

  it("Mobile source ready 切到 Desktop 后按 abort → revoke 释放 Blob", async () => {
    const media = installMatchMedia(false);
    const { revokeObjectURL } = installObjectUrlMock(["blob:mobile-breakpoint"]);
    const cleanupOrder: string[] = [];
    revokeObjectURL.mockImplementation((objectUrl) => {
      cleanupOrder.push(`revoke:${objectUrl}`);
    });
    let previewSignal: AbortSignal | undefined;
    installFetchMock(
      {
        adaptation_notice: [],
        content_check: [makeContentCheckItem("responsive-mobile-ready")],
      },
      (_input, init) => {
        previewSignal = init?.signal ?? undefined;
        previewSignal?.addEventListener(
          "abort",
          () => cleanupOrder.push("abort"),
          { once: true },
        );
        return new Response(new Blob(["pdf"], { type: "application/pdf" }), {
          status: 200,
          headers: { "content-type": "application/pdf" },
        });
      },
    );
    renderPanel();
    await waitForPanelReady();
    const documentSurface = screen.getByTestId("content-check-document");

    fireEvent.click(
      await screen.findByRole("button", { name: /展开审查批注面板/ }),
    );
    fireEvent.click(screen.getByRole("button", { name: "查看原件" }));
    await screen.findByTitle("原件 PDF 预览");

    act(() => media.change(true));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(previewSignal?.aborted).toBe(true);
    expect(revokeObjectURL).toHaveBeenCalledOnce();
    expect(cleanupOrder).toEqual(["abort", "revoke:blob:mobile-breakpoint"]);
    expect(documentSurface.hasAttribute("inert")).toBe(false);
    expect(screen.queryByTestId("source-preview-live-region")).toBeNull();
  });

  it("Desktop source drawer 切到 Mobile 后释放资源且不自动打开 Dialog", async () => {
    const media = installMatchMedia(true);
    const { revokeObjectURL } = installObjectUrlMock(["blob:desktop-breakpoint"]);
    const cleanupOrder: string[] = [];
    revokeObjectURL.mockImplementation((objectUrl) => {
      cleanupOrder.push(`revoke:${objectUrl}`);
    });
    let previewSignal: AbortSignal | undefined;
    installFetchMock(
      {
        adaptation_notice: [],
        content_check: [makeContentCheckItem("responsive-desktop-ready")],
      },
      (_input, init) => {
        previewSignal = init?.signal ?? undefined;
        previewSignal?.addEventListener(
          "abort",
          () => cleanupOrder.push("abort"),
          { once: true },
        );
        return new Response(new Blob(["pdf"], { type: "application/pdf" }), {
          status: 200,
          headers: { "content-type": "application/pdf" },
        });
      },
    );
    renderPanel();
    await waitForPanelReady();
    const documentSurface = screen.getByTestId("content-check-document");

    fireEvent.click(screen.getByRole("button", { name: "查看原件" }));
    await screen.findByTitle("原件 PDF 预览");

    act(() => media.change(false));

    await waitFor(() =>
      expect(screen.queryByTestId("source-preview-drawer")).toBeNull(),
    );
    expect(previewSignal?.aborted).toBe(true);
    expect(revokeObjectURL).toHaveBeenCalledOnce();
    expect(cleanupOrder).toEqual(["abort", "revoke:blob:desktop-breakpoint"]);
    expect(documentSurface.hasAttribute("inert")).toBe(false);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByTestId("source-preview-live-region")).toBeNull();
  });

  it("标题、来源与确认后果清楚；说明文案不集中堆砌；不展示 raw Markdown 标记", async () => {
    installFetchMock();
    renderPanel();
    await waitForPanelReady();

    const panel = screen.getByTestId("content-check-panel");
    // 确认后果（冻结）在 footer 主操作旁说明一次；header 不再堆砌长说明。
    expect(panel.textContent).toContain("正文冻结");
    expect(screen.getByText("确认识别出的正文")).toBeTruthy();
    expect(screen.getByText(/来源：/)).toBeTruthy();

    // 结构化预览：编辑器内是内容本身，不裸露整篇 Markdown 之外的
    // raw 标记面板（不存在 raw source 视图切换）。
    expect(screen.queryByTestId("content-check-raw-markdown")).toBeNull();
  });

  it("adaptation_notice 默认折叠、点击展开摘要", async () => {
    installFetchMock();
    renderPanel();
    await waitForPanelReady();

    const rail = screen.getByTestId("content-check-adaptation-notice");
    expect(rail.textContent).toContain("已自动处理 2 项");
    expect(rail.textContent).not.toContain("已移除原始 HTML 块");

    fireEvent.click(screen.getByRole("button", { name: /已自动处理 2 项/ }));
    expect(rail.textContent).toContain("网页标记已清理");
    expect(rail.textContent).toContain("不安全链接已去掉");
  });

  it("content_check 风险卡片展示上下文与建议，支持逐条处置", async () => {
    installFetchMock();
    renderPanel();
    await waitForPanelReady();

    const items = screen.getAllByTestId("content-check-risk-item");
    expect(items).toHaveLength(2);

    const fenceItem = items.find(
      (item) => item.getAttribute("data-code") === "has_unclosed_fence",
    )!;
    // 原文上下文来自后端 structured evidence，不再由客户端猜测。
    expect(fenceItem.textContent).toContain("```python");
    expect(fenceItem.textContent).toContain("建议补上");
    await waitFor(() => expect(fenceItem.textContent).toContain("采用建议"));
    expect(fenceItem.textContent).toContain("代码块未闭合");

    // 保留普通文字：处置单条风险。
    const footnoteItem = items.find(
      (item) => item.getAttribute("data-code") === "footnote_reference",
    )!;
    fireEvent.click(
      Array.from(footnoteItem.querySelectorAll("button")).find(
        (button) => button.textContent === "确认当前内容",
      )!,
    );
    await waitFor(() =>
      expect(screen.getAllByTestId("content-check-risk-item")).toHaveLength(1),
    );
    expect(screen.getByTestId("content-check-resolved-summary").textContent).toContain(
      "已处理 1 项",
    );
  });

  it("采用后端 proposed_patch 后仍标记内容已修改、待用户确认", async () => {
    installFetchMock();
    renderPanel();
    await waitForPanelReady();

    const fenceItem = screen
      .getAllByTestId("content-check-risk-item")
      .find((item) => item.getAttribute("data-code") === "has_unclosed_fence")!;
    let adoptButton: HTMLButtonElement | undefined;
    await waitFor(() => {
      adoptButton = Array.from(fenceItem.querySelectorAll("button")).find(
        (button) => button.textContent?.includes("采用建议"),
      );
      expect(adoptButton).toBeTruthy();
    });
    fireEvent.click(adoptButton!);

    const editor = document.getElementById(
      "content-check-editor",
    ) as HTMLTextAreaElement;
    await waitFor(() => expect(editor.value).toContain("```python\n```"));
    await waitFor(() => expect(fenceItem.textContent).toContain("内容已修改，待确认"));
    await waitFor(() =>
      expect(
        (screen.getByTestId("content-check-confirm-button") as HTMLButtonElement)
          .disabled,
      ).toBe(true),
    );
  });

  it("两个远距 range marker 对齐各自精确锚点；changed/document/ambiguous 不伪造 marker", async () => {
    const first = "First exact anchor.";
    const second = "Second exact anchor.";
    const ambiguous = "Ambiguous exact anchor.";
    const markdown = [
      first,
      ...Array.from({ length: 24 }, (_, index) => `Filler paragraph ${index + 1}.`),
      second,
      ambiguous,
      ambiguous,
    ].join("\n\n");
    const rangeItem = (
      issueId: string,
      excerpt: string,
      anchorHash: string,
      start = markdown.indexOf(excerpt),
    ) =>
      makeContentCheckItem(issueId, {
        code: "has_unclosed_fence",
        tier: "attention",
        target_scope: "range",
        source_anchor: { start_utf16: start, end_utf16: start + excerpt.length },
        anchor_hash: anchorHash,
        evidence: { excerpt_text: excerpt, proposed_patch: null },
      });

    installFetchMock({
      markdown_text: markdown,
      content_check: [
        rangeItem(
          "1111111111111111",
          first,
          "362a72f735558633213bdcda7899f1cb4520d0ee8b64358b6ceab03e502a9657",
        ),
        rangeItem(
          "2222222222222222",
          second,
          "b9ac0655df5ea0742c776455d217a0d3958236d580bf8ae8c5ff83c3ffcdaef0",
        ),
        makeContentCheckItem("3333333333333333"),
        rangeItem("4444444444444444", first, "0".repeat(64)),
        rangeItem(
          "5555555555555555",
          ambiguous,
          "d8504669cfd611bd15eeae3521ec87682d1a673ce082fd4fa2154ff27f3cac46",
        ),
      ],
    });
    renderPanel();
    await waitForPanelReady();

    let markers: HTMLElement[] = [];
    await waitFor(() => {
      markers = screen.getAllByTestId("content-check-gutter-marker");
      expect(markers).toHaveLength(2);
    });
    expect(markers.map((marker) => marker.getAttribute("data-issue-id"))).toEqual([
      "1111111111111111",
      "2222222222222222",
    ]);
    const firstTop = Number.parseFloat(markers[0]?.style.top ?? "NaN");
    const secondTop = Number.parseFloat(markers[1]?.style.top ?? "NaN");
    expect(firstTop).toBeLessThan(secondTop);
    expect(secondTop - firstTop).toBeGreaterThan(500);

    const editor = document.getElementById("content-check-editor")!;
    fireEvent.click(markers[1]!);
    expect(editor.dataset.revealedExcerpt).toBe(second);
    expect(
      document
        .getElementById("content-check-issue-2222222222222222")
        ?.getAttribute("aria-current"),
    ).toBe("true");

    const firstCard = document.getElementById(
      "content-check-issue-1111111111111111",
    )!;
    fireEvent.click(firstCard.querySelector("button")!);
    expect(editor.dataset.revealedExcerpt).toBe(first);
    expect(firstCard.getAttribute("aria-current")).toBe("true");
  });

  it("锚点 hash 失效后停止旧定位，并显示位置变化与待确认状态", async () => {
    installFetchMock();
    renderPanel();
    await waitForPanelReady();

    const editor = document.getElementById(
      "content-check-editor",
    ) as HTMLTextAreaElement;
    fireEvent.change(editor, {
      target: { value: DRAFT_MARKDOWN.replace("```python", "```typescript") },
    });

    await waitFor(() => expect(screen.getByText("位置已变化")).toBeTruthy());
    expect(screen.queryByTestId("content-check-gutter-marker")).toBeNull();
    expect(screen.getAllByText("内容已修改，待确认").length).toBeGreaterThan(0);
  });

  it("正文存在未保存修改时，即使 Attention 已确认也禁用最终 CTA", async () => {
    installFetchMock();
    renderPanel();
    await waitForPanelReady();

    const attentionItem = screen
      .getAllByTestId("content-check-risk-item")
      .find((item) => item.getAttribute("data-code") === "has_unclosed_fence")!;
    fireEvent.click(
      Array.from(attentionItem.querySelectorAll("button")).find(
        (button) => button.textContent === "确认当前内容",
      )!,
    );
    const editor = document.getElementById(
      "content-check-editor",
    ) as HTMLTextAreaElement;
    fireEvent.change(editor, { target: { value: `${DRAFT_MARKDOWN}\nExtra` } });

    await waitFor(() =>
      expect(
        (screen.getByTestId("content-check-confirm-button") as HTMLButtonElement)
          .disabled,
      ).toBe(true),
    );
  });

  it("批量确认只处理 routine，attention 仍需逐项决定", async () => {
    installFetchMock();
    renderPanel();
    await waitForPanelReady();

    fireEvent.click(screen.getByTestId("content-check-keep-all-plain"));
    await waitFor(() => {
      const items = screen.getAllByTestId("content-check-risk-item");
      expect(items).toHaveLength(1);
      expect(items[0]?.getAttribute("data-code")).toBe("has_unclosed_fence");
    });
    expect(screen.getByTestId("content-check-resolved-summary").textContent).toContain(
      "已处理 1 项",
    );
    expect(
      (screen.getByTestId("content-check-confirm-button") as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("routine 建议不阻塞确认，attention 风险才阻塞", async () => {
    installFetchMock({
      content_check: [
        makeContentCheckItem("3333333333333333", {
          code: "footnote_reference",
          message: "Footnote reference encountered.",
        }),
      ],
    });
    renderPanel();
    await waitForPanelReady();

    expect(
      (screen.getByTestId("content-check-confirm-button") as HTMLButtonElement).disabled,
    ).toBe(false);
  });

  it("未逐项处理风险前禁止确认；同 code 的风险必须逐项处理", async () => {
    installFetchMock({
      content_check: [
        makeContentCheckItem("4444444444444444", {
          code: "has_unclosed_fence",
          message: "第一处未闭合代码块",
          tier: "attention",
        }),
        makeContentCheckItem("5555555555555555", {
          code: "has_unclosed_fence",
          message: "第二处未闭合代码块",
          tier: "attention",
        }),
      ],
    });
    renderPanel();
    await waitForPanelReady();

    const confirm = screen.getByTestId(
      "content-check-confirm-button",
    ) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);

    const firstItem = screen.getAllByTestId("content-check-risk-item")[0];
    fireEvent.click(
      Array.from(firstItem.querySelectorAll("button")).find(
        (button) => button.textContent === "确认当前内容",
      )!,
    );
    await waitFor(() =>
      expect(screen.getAllByTestId("content-check-risk-item")).toHaveLength(1),
    );
    expect(confirm.disabled).toBe(true);

    const remainingItem = screen.getAllByTestId("content-check-risk-item")[0];
    fireEvent.click(
      Array.from(remainingItem.querySelectorAll("button")).find(
        (button) => button.textContent === "确认当前内容",
      )!,
    );
    await waitFor(() => expect(confirm.disabled).toBe(false));
  });

  it("Ctrl/Cmd+Enter 与按钮共用风险处置门禁", async () => {
    const fetchMock = installFetchMock();
    const props = renderPanel();
    await waitForPanelReady();

    const editor = document.getElementById("content-check-editor")!;
    fireEvent.keyDown(editor, { key: "Enter", ctrlKey: true });
    await new Promise((resolve) => setTimeout(resolve, 0));
    const confirmCallsBeforeResolution = fetchMock.mock.calls.filter(([input]) =>
      String(input).includes("/candidate-documents/"),
    );
    expect(confirmCallsBeforeResolution).toHaveLength(0);
    expect(props.onConfirmed).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("content-check-keep-all-plain"));
    await waitFor(() =>
      expect(screen.getAllByTestId("content-check-risk-item")).toHaveLength(1),
    );
    const attentionItem = screen.getAllByTestId("content-check-risk-item")[0];
    fireEvent.click(
      Array.from(attentionItem.querySelectorAll("button")).find(
        (button) => button.textContent === "确认当前内容",
      )!,
    );
    fireEvent.keyDown(editor, { key: "Enter", metaKey: true });
    await waitFor(() =>
      expect(props.onConfirmed).toHaveBeenCalledWith("rec_cc_1"),
    );
    const confirmCallsAfterResolution = fetchMock.mock.calls.filter(([input]) =>
      String(input).includes("/candidate-documents/"),
    );
    expect(confirmCallsAfterResolution).toHaveLength(1);
  });

  it.each(["button", "shortcut"] as const)(
    "pending debounce 后立即 %s：flush 最新正文，只 PUT；新 Attention 处理后第二次才 confirm",
    async (entry) => {
      const latestMarkdown = "# Draft\n\nLatest keystroke.";
      const fetchMock = installFetchMock({
        adaptation_notice: [],
        content_check: [],
      });
      fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = (init?.method ?? "GET").toUpperCase();
        if (url.includes("/confirmed-source") && method === "GET") {
          return new Response(
            JSON.stringify(
              makeReadResponse({ adaptation_notice: [], content_check: [] }),
            ),
            { status: 200, headers: { "content-type": "application/json" } },
          );
        }
        if (url.includes("/confirmed-source") && method === "PUT") {
          expect(JSON.parse(String(init?.body))).toMatchObject({
            markdown_text: latestMarkdown,
          });
          return new Response(
            JSON.stringify({
              ok: true,
              revision: 2,
              content_sha256: "b".repeat(64),
              outcome: "candidate_document_required",
              candidate: {
                candidate_document_id: "cand_2",
                status: "ready",
                canonical_text_preview: "Latest keystroke.",
              },
              quality: null,
              adaptation_notice: [],
              content_check: [
                makeContentCheckItem("9999999999999999", {
                  code: "has_unclosed_fence",
                  tier: "attention",
                }),
              ],
            }),
            { status: 200, headers: { "content-type": "application/json" } },
          );
        }
        if (url.includes("/confirm") && method === "POST") {
          return new Response(JSON.stringify({ ok: true }), {
            status: 200,
            headers: { "content-type": "application/json" },
          });
        }
        throw new Error(`Unexpected fetch ${method} ${url}`);
      });

      const props = renderPanel();
      await waitForPanelReady();
      const editor = document.getElementById(
        "content-check-editor",
      ) as HTMLTextAreaElement;
      fireEvent.change(editor, { target: { value: latestMarkdown } });

      if (entry === "button") {
        fireEvent.click(screen.getByTestId("content-check-confirm-button"));
      } else {
        fireEvent.keyDown(editor, { key: "Enter", ctrlKey: true });
      }

      await waitFor(() =>
        expect(
          document.getElementById("content-check-issue-9999999999999999"),
        ).toBeTruthy(),
      );
      const putCalls = fetchMock.mock.calls.filter(
        ([input, init]) =>
          String(input).includes("/confirmed-source") &&
          (init?.method ?? "GET").toUpperCase() === "PUT",
      );
      const confirmCalls = fetchMock.mock.calls.filter(([input]) =>
        String(input).includes("/candidate-documents/"),
      );
      expect(putCalls).toHaveLength(1);
      expect(confirmCalls).toHaveLength(0);
      expect(props.onConfirmed).not.toHaveBeenCalled();
      expect(
        (screen.getByTestId("content-check-confirm-button") as HTMLButtonElement)
          .disabled,
      ).toBe(true);

      const newAttention = document.getElementById(
        "content-check-issue-9999999999999999",
      )!;
      fireEvent.click(
        Array.from(newAttention.querySelectorAll("button")).find(
          (button) => button.textContent === "确认当前内容",
        )!,
      );
      await waitFor(() =>
        expect(
          (screen.getByTestId("content-check-confirm-button") as HTMLButtonElement)
            .disabled,
        ).toBe(false),
      );

      if (entry === "button") {
        fireEvent.click(screen.getByTestId("content-check-confirm-button"));
      } else {
        fireEvent.keyDown(editor, { key: "Enter", metaKey: true });
      }

      await waitFor(() => expect(props.onConfirmed).toHaveBeenCalledTimes(1));
      expect(
        fetchMock.mock.calls.filter(([input]) =>
          String(input).includes("/candidate-documents/"),
        ),
      ).toHaveLength(1);
    },
  );

  it("rejected outcome 显示原因（quality.suitability.reasons 通道）并禁用主 CTA", async () => {
    // 用 PUT 结果驱动 rejected：先编辑再保存。真实后端合同：无顶层
    // suitability，原因在 quality.suitability.reasons / content_check。
    const fetchMock = installFetchMock();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.includes("/confirmed-source") && method === "PUT") {
        return new Response(
          JSON.stringify({
            ok: true,
            revision: 2,
            content_sha256: "b".repeat(64),
            outcome: "input_rejected_or_action_required",
            candidate: null,
            quality: {
              parser_name: "markdown_structured_source",
              parser_version: "1.0.0",
              profile: "default",
              suitability: {
                outcome: "input_rejected_or_action_required",
                word_count: 3,
                english_word_ratio: 0.1,
                natural_language_score: 0.2,
                flags: ["too_short_for_learning"],
                reasons: ["内容过短"],
              },
            },
            adaptation_notice: [],
            content_check: [],
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      if (url.includes("/confirmed-source")) {
        return new Response(
          JSON.stringify(makeReadResponse({ candidate: null })),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderPanel();
    await waitForPanelReady();

    const editor = document.getElementById(
      "content-check-editor",
    ) as HTMLTextAreaElement;
    fireEvent.change(editor, { target: { value: "abc" } });

    const saveButton = await screen.findByTestId("content-check-confirm-button");
    expect((saveButton as HTMLButtonElement).disabled).toBe(true);

    // 最终 CTA 不负责 flush 未保存正文；自动保存 PUT 返回 rejected。
    await waitFor(
      () => expect(screen.getByTestId("content-check-rejected")).toBeTruthy(),
      { timeout: 2_000 },
    );
    expect(screen.getByTestId("content-check-rejected").textContent).toContain(
      "英文内容太短，补充成一段完整的英文文章再试。",
    );
    expect(screen.getByTestId("content-check-rejected").textContent).not.toContain(
      "内容过短",
    );
    expect((saveButton as HTMLButtonElement).disabled).toBe(true);
  });

  it("rejected 原因回退：quality 无 flags 时取 content_check 映射", async () => {
    const fetchMock = installFetchMock();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.includes("/confirmed-source") && method === "PUT") {
        return new Response(
          JSON.stringify({
            ok: true,
            revision: 2,
            content_sha256: "b".repeat(64),
            outcome: "input_rejected_or_action_required",
            candidate: null,
            quality: { suitability: { reasons: [] } },
            adaptation_notice: [],
            content_check: [
              makeContentCheckItem("6666666666666666", {
                code: "code_dominant",
                message: "代码占比过高，不适合透读",
                tier: "attention",
              }),
            ],
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      if (url.includes("/confirmed-source")) {
        return new Response(
          JSON.stringify(makeReadResponse({ candidate: null })),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderPanel();
    await waitForPanelReady();

    const editor = document.getElementById(
      "content-check-editor",
    ) as HTMLTextAreaElement;
    fireEvent.change(editor, { target: { value: "abc" } });
    await waitFor(
      () => expect(screen.getByTestId("content-check-rejected")).toBeTruthy(),
      { timeout: 2_000 },
    );
    expect(screen.getByTestId("content-check-rejected").textContent).toContain(
      "这份内容以代码为主，批注价值有限，建议确认是否继续。",
    );
  });

  it("操作层级：确认主 CTA / 重新输入低噪出口 / 稍后处理低噪出口", async () => {
    installFetchMock();
    const props = renderPanel();
    await waitForPanelReady();

    fireEvent.click(screen.getByRole("button", { name: "稍后处理" }));
    expect(props.onDefer).toHaveBeenCalledWith({
      recordId: "rec_cc_1",
      candidateDocumentId: "cand_1",
      canonicalTextPreview: "Title",
    });

    fireEvent.click(screen.getByRole("button", { name: "重新输入" }));
    expect(props.onBackToInput).toHaveBeenCalledWith(DRAFT_MARKDOWN);

    fireEvent.click(screen.getByTestId("content-check-keep-all-plain"));
    await waitFor(() =>
      expect(screen.getAllByTestId("content-check-risk-item")).toHaveLength(1),
    );
    const attentionItem = screen.getAllByTestId("content-check-risk-item")[0];
    fireEvent.click(
      Array.from(attentionItem.querySelectorAll("button")).find(
        (button) => button.textContent === "确认当前内容",
      )!,
    );
    await waitFor(() =>
      expect(
        (screen.getByTestId("content-check-confirm-button") as HTMLButtonElement)
          .disabled,
      ).toBe(false),
    );
    fireEvent.click(screen.getByTestId("content-check-confirm-button"));
    await waitFor(() =>
      expect(props.onConfirmed).toHaveBeenCalledWith("rec_cc_1"),
    );
  });

  it("编辑后立即稍后处理会先 flush 保存，再退出", async () => {
    const callOrder: string[] = [];
    const fetchMock = installFetchMock();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.includes("/confirmed-source") && method === "GET") {
        return new Response(JSON.stringify(makeReadResponse()), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (url.includes("/confirmed-source") && method === "PUT") {
        callOrder.push("save");
        expect(JSON.parse(String(init?.body))).toMatchObject({
          markdown_text: "# Edited before defer",
        });
        return new Response(
          JSON.stringify({
            ok: true,
            revision: 2,
            content_sha256: "b".repeat(64),
            outcome: "candidate_document_required",
            candidate: makeReadResponse().candidate,
            quality: null,
            adaptation_notice: [],
            content_check: [],
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      throw new Error(`Unexpected fetch ${method} ${url}`);
    });
    const onDefer = vi.fn(() => callOrder.push("defer"));
    renderPanel({ onDefer });
    await waitForPanelReady();

    fireEvent.change(document.getElementById("content-check-editor")!, {
      target: { value: "# Edited before defer" },
    });
    fireEvent.click(screen.getByRole("button", { name: "稍后处理" }));

    await waitFor(() => expect(onDefer).toHaveBeenCalledTimes(1));
    expect(callOrder).toEqual(["save", "defer"]);
    expect(onDefer).toHaveBeenCalledWith({
      recordId: "rec_cc_1",
      candidateDocumentId: null,
      canonicalTextPreview: null,
    });
  });

  it("稍后处理保存失败时留在当前面板并可重试", async () => {
    const fetchMock = installFetchMock();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.includes("/confirmed-source") && method === "GET") {
        return new Response(JSON.stringify(makeReadResponse()), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (url.includes("/confirmed-source") && method === "PUT") {
        return new Response(
          JSON.stringify({ ok: false, status: 503, message: "保存暂时不可用" }),
          { status: 503, headers: { "content-type": "application/json" } },
        );
      }
      throw new Error(`Unexpected fetch ${method} ${url}`);
    });
    const onDefer = vi.fn();
    renderPanel({ onDefer });
    await waitForPanelReady();

    fireEvent.change(document.getElementById("content-check-editor")!, {
      target: { value: "# Unsaved" },
    });
    fireEvent.click(screen.getByRole("button", { name: "稍后处理" }));

    await waitFor(() => expect(screen.getByText("保存暂时不可用")).toBeTruthy());
    expect(onDefer).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "重试保存" })).toBeTruthy();
  });

  it("resume 来源隐藏重新输入", async () => {
    installFetchMock();
    renderPanel({ origin: "resume" });
    await waitForPanelReady();
    expect(screen.queryByRole("button", { name: "重新输入" })).toBeNull();
    expect(screen.getByRole("button", { name: "稍后处理" })).toBeTruthy();
  });
});
