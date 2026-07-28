/** @vitest-environment jsdom */

/**
 * R1 Phase 1 — AnalyzeSubmitForm × 真实 MarkdownTextInput 集成红灯测试。
 *
 * AnalyzeSubmitForm.test.tsx 把编辑器 mock 成 textarea，无法覆盖真实
 * contenteditable 的状态一致性。本套件**不做该 mock**：渲染真实 Plate
 * 编辑器，通过 candidate 恢复流（localStorage → setValue）向真实编辑器
 * 注入结构化 Markdown，断言 placeholder / CTA ready+disabled / chars /
 * Markdown hint / clear / 提交 / 无障碍关系在单一状态源下保持一致。
 *
 * 用户级粘贴与 Ctrl/Cmd+Enter 的浏览器行为由 Phase 5 Playwright 验收
 * 覆盖（jsdom 不支持 beforeinput，无法驱动 Slate 真实 DOM 管线）。
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AnalyzeSubmitForm } from "./AnalyzeSubmitForm";
import { PENDING_CANDIDATE_STORAGE_KEY } from "./pending-candidate";
import { R1_TEST_MARKDOWN } from "./r1-test-fixtures";

const navigationMock = vi.hoisted(() => ({
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => navigationMock,
}));

function createMemoryStorage(): Storage {
  const store = new Map<string, string>();
  return {
    get length() {
      return store.size;
    },
    clear() {
      store.clear();
    },
    getItem(key: string) {
      return store.get(key) ?? null;
    },
    key(index: number) {
      return Array.from(store.keys())[index] ?? null;
    },
    removeItem(key: string) {
      store.delete(key);
    },
    setItem(key: string, value: string) {
      store.set(key, value);
    },
  };
}

function seedPendingCandidate(inputSnapshot: string) {
  window.localStorage.setItem(
    PENDING_CANDIDATE_STORAGE_KEY,
    JSON.stringify({
      readingRecordId: "rec_r1_reedit",
      candidateDocumentId: "cand_r1_reedit",
      originalInputId: "inp_r1_reedit",
      inputSnapshot,
      filename: null,
      canonicalTextPreview: null,
      origin: "submit",
      savedAt: new Date().toISOString(),
    }),
  );
}

function renderForm() {
  return render(
    <AnalyzeSubmitForm readingGoal="daily_reading" readingVariant="intermediate_reading" />,
  );
}

function getSubmitButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: "开始透读" }) as HTMLButtonElement;
}

/**
 * 通过真实组件路径把结构化 Markdown 注入真实编辑器：
 * localStorage candidate → L2 Content Check（mock GET confirmed-source）→
 * 「返回修改」→ setText(draft) + editor.setValue(draft)。
 */
async function enterMarkdownViaRecoveryFlow(
  markdown: string,
  onFetch?: (url: string, init?: RequestInit) => Response | null,
) {
  seedPendingCandidate(markdown);
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/confirmed-source")) {
      return new Response(
        JSON.stringify({
          ok: true,
          source_document_id: "cs_r1",
          record_generation: 1,
          revision: 1,
          status: "draft",
          markdown_text: markdown,
          content_sha256: "a".repeat(64),
          edit_source: "initial",
          updated_at: "2026-07-28T00:00:00.000Z",
          candidate: {
            candidate_document_id: "cand_r1_reedit",
            status: "ready",
            canonical_text_preview: "",
          },
          quality: null,
          adaptation_notice: [],
          content_check: [],
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }
    const delegated = onFetch?.(url, init) ?? null;
    if (delegated) return delegated;
    throw new Error(`Unexpected fetch ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  renderForm();
  await waitFor(() => {
    expect(screen.getByTestId("content-check-confirm-button")).toBeTruthy();
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "返回修改" }));
  });
  await waitFor(() => {
    expect(screen.queryByTestId("content-check-panel")).toBeNull();
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  navigationMock.push.mockReset();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: createMemoryStorage(),
  });
  const url = new URL(window.location.href);
  url.search = "";
  window.history.replaceState({}, "", url.toString());
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AnalyzeSubmitForm × real MarkdownTextInput integration", () => {
  it("keeps the CTA truly disabled while the input is empty", () => {
    renderForm();
    const cta = getSubmitButton();
    // data-ready=false 时 CTA 必须真正 disabled，而不只是视觉态。
    expect(cta.getAttribute("data-ready")).toBe("false");
    expect(cta.disabled).toBe(true);
  });

  it("structured Markdown in the real editor drives placeholder, CTA, chars, hint, clear and DOM semantics", async () => {
    await enterMarkdownViaRecoveryFlow(R1_TEST_MARKDOWN);

    // placeholder 消失（内容已渲染）。
    await waitFor(() => {
      expect(screen.queryByText("Paste an English article here")).toBeNull();
    });

    // CTA 进入 ready 且 enabled。
    const cta = getSubmitButton();
    expect(cta.getAttribute("data-ready")).toBe("true");
    expect(cta.disabled).toBe(false);

    // chars 计数出现。
    expect(screen.getByText(/chars$/)).toBeTruthy();

    // Markdown 标记 hint 出现。
    expect(screen.getByTestId("read-source-markdown-hint")).toBeTruthy();

    // 清空按钮出现。
    expect(screen.getByTitle("清空")).toBeTruthy();

    // 真实 DOM 结构在编辑器内（不是 textarea mock）。
    const editorEl = document.querySelector("#analysis-text") as HTMLElement;
    expect(editorEl?.getAttribute("contenteditable")).toBe("true");
    expect(editorEl.querySelectorAll("h2")).toHaveLength(1);
    expect(editorEl.querySelectorAll("h3")).toHaveLength(3);
  });

  it("clearing after content resets placeholder, CTA and badges", async () => {
    await enterMarkdownViaRecoveryFlow(R1_TEST_MARKDOWN);
    await waitFor(() => {
      expect(getSubmitButton().getAttribute("data-ready")).toBe("true");
    });

    await act(async () => {
      fireEvent.click(screen.getByTitle("清空"));
    });

    await waitFor(() => {
      expect(screen.getByText("Paste an English article here")).toBeTruthy();
    });
    const cta = getSubmitButton();
    expect(cta.getAttribute("data-ready")).toBe("false");
    expect(cta.disabled).toBe(true);
    expect(screen.queryByTitle("清空")).toBeNull();
    expect(screen.queryByText(/chars$/)).toBeNull();
    expect(screen.queryByTestId("read-source-markdown-hint")).toBeNull();
  });

  it("submits structured content from the real editor to the unified endpoint", async () => {
    const submitMock = vi.fn((url: string, init?: RequestInit) => {
      expect(url).toBe("/api/web/reader-plate/input");
      const sent = JSON.parse(String(init?.body)) as { text: string };
      expect(sent.text).toContain("## 6. Implementation Plan");
      return new Response(
        JSON.stringify({
          ok: true,
          outcome: "stable_document_ready",
          reading_record_id: "rec_r1_submit",
          suitability: { outcome: "stable_document_ready", reasons: [], normalized_preview: "" },
          snapshot: { record: { title: "R1 submit" } },
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    });

    await enterMarkdownViaRecoveryFlow(R1_TEST_MARKDOWN, submitMock);
    await waitFor(() => {
      expect(getSubmitButton().getAttribute("data-ready")).toBe("true");
    });

    await act(async () => {
      fireEvent.click(getSubmitButton());
    });

    await waitFor(() => {
      expect(navigationMock.push).toHaveBeenCalledWith("/app/reader-record/rec_r1_submit");
    });

    expect(submitMock).toHaveBeenCalledTimes(1);
    const body = JSON.parse(String(submitMock.mock.calls[0][1]?.body)) as { text: string };
    // 提交载荷保留标题结构（不压平为纯文本）。
    expect(body.text).toContain("## 6. Implementation Plan");
    expect(body.text).toContain("### Step 1: Streamline Server Deployment Architecture");
  });

  it("wires a reliable programmatic accessible name and description for the contenteditable", async () => {
    renderForm();

    const editorEl = document.querySelector("#analysis-text") as HTMLElement;
    const labelledBy = editorEl?.getAttribute("aria-labelledby");
    const describedBy = editorEl?.getAttribute("aria-describedby");
    expect(labelledBy).toBeTruthy();
    expect(describedBy).toBeTruthy();

    // 引用目标必须真实存在且含可用文本。
    const labelEl = labelledBy ? document.getElementById(labelledBy) : null;
    const hintEl = describedBy ? document.getElementById(describedBy) : null;
    expect(labelEl?.textContent?.trim()).toBeTruthy();
    expect(hintEl?.textContent?.trim()).toBeTruthy();
  });

  it("keeps the visual placeholder out of the accessibility tree once labelled", () => {
    renderForm();

    const surface = document.querySelector("[data-testid='read-source-input']") as HTMLElement;
    // 视觉 placeholder overlay 与程序化名称/描述不得形成重复无障碍节点。
    const overlay = Array.from(surface.querySelectorAll("div")).find((d) =>
      d.textContent?.includes("Paste an English article here"),
    );
    expect(overlay, "visual placeholder overlay must exist").toBeTruthy();
    expect(overlay?.getAttribute("aria-hidden")).toBe("true");
  });
});
