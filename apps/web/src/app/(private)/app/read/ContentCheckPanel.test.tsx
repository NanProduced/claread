/** @vitest-environment jsdom */

/**
 * ContentCheckPanel 渲染测试 — 三级提示（adaptation_notice 可展开 /
 * content_check 风险卡片处置 / rejected 提示）、操作层级与出口。
 * 状态机细节由 use-content-check.test.tsx 覆盖；这里锁 DOM 合同。
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ContentCheckPanel } from "./ContentCheckPanel";

// 与 AnalyzeSubmitForm.test.tsx 相同的 Plate 编辑器桩：textarea + ref handle。
vi.mock("./MarkdownTextInput", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./MarkdownTextInput")>();
  const React = await import("react");
  const { forwardRef, useImperativeHandle, useRef, useState } = React;

  type MockHandle = {
    getSubmitText: () => string;
    getMarkdown: () => string;
    focus: () => void;
    clear: () => void;
    setValue: (markdown: string) => void;
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
    valueRef.current = value;
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
      flush: () => valueRef.current,
    }));
    return React.createElement("textarea", {
      id: props.id,
      className: props.className,
      value,
      onChange: (event: React.ChangeEvent<HTMLTextAreaElement>) => {
        setValue(event.target.value);
        valueRef.current = event.target.value;
        props.onChange(event.target.value);
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
      {
        code: "has_unclosed_fence",
        message: "Fenced code block is missing its closing fence.",
        classification: "content_check" as const,
      },
      {
        code: "footnote_reference",
        message: "Footnote reference encountered.",
        classification: "content_check" as const,
      },
    ],
    ...overrides,
  };
}

function installFetchMock(readOverrides: Record<string, unknown> = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
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
  render(<ContentCheckPanel {...props} />);
  return props;
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

  it("说明为什么需要确认与确认后会发生什么；不展示 raw Markdown 标记", async () => {
    installFetchMock();
    renderPanel();
    await waitForPanelReady();

    const panel = screen.getByTestId("content-check-panel");
    expect(panel.textContent).toContain("先请你过目");
    expect(panel.textContent).toContain("正文冻结");
    expect(screen.getByText("确认识别出的正文")).toBeTruthy();

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
    // 原文上下文（excerpt 从草稿派生）+ 明确建议。不直接渲染后端 message。
    expect(fenceItem.textContent).toContain("```python");
    expect(fenceItem.textContent).toContain("建议补上");
    expect(fenceItem.textContent).toContain("采用建议");
    expect(fenceItem.textContent).toContain("代码块未闭合");

    // 保留普通文字：处置单条风险。
    const footnoteItem = items.find(
      (item) => item.getAttribute("data-code") === "footnote_reference",
    )!;
    fireEvent.click(
      Array.from(footnoteItem.querySelectorAll("button")).find(
        (button) => button.textContent === "保留原文",
      )!,
    );
    await waitFor(() =>
      expect(screen.getAllByTestId("content-check-risk-item")).toHaveLength(1),
    );
    expect(screen.getByTestId("content-check-resolved-summary").textContent).toContain(
      "已处理 1 项",
    );
  });

  it("采用建议对 has_unclosed_fence 机械补全围栏并写回编辑器", async () => {
    installFetchMock();
    renderPanel();
    await waitForPanelReady();

    const fenceItem = screen
      .getAllByTestId("content-check-risk-item")
      .find((item) => item.getAttribute("data-code") === "has_unclosed_fence")!;
    fireEvent.click(
      Array.from(fenceItem.querySelectorAll("button")).find(
        (button) => button.textContent?.includes("采用建议"),
      )!,
    );

    const editor = document.getElementById(
      "content-check-editor",
    ) as HTMLTextAreaElement;
    expect(editor.value).toBe(`${DRAFT_MARKDOWN}\`\`\`\n`);
    await waitFor(() =>
      expect(
        screen
          .getAllByTestId("content-check-risk-item")
          .some((item) => item.getAttribute("data-code") === "has_unclosed_fence"),
      ).toBe(false),
    );
  });

  it("全部保留原文清空风险列表", async () => {
    installFetchMock();
    renderPanel();
    await waitForPanelReady();

    fireEvent.click(screen.getByTestId("content-check-keep-all-plain"));
    await waitFor(() =>
      expect(screen.queryAllByTestId("content-check-risk-item")).toHaveLength(0),
    );
    expect(screen.getByTestId("content-check-resolved-summary").textContent).toContain(
      "已处理 2 项",
    );
  });

  it("未逐项处理风险前禁止确认；同 code 的风险必须逐项处理", async () => {
    installFetchMock({
      content_check: [
        {
          code: "footnote_reference",
          message: "第一处脚注引用",
          classification: "content_check",
        },
        {
          code: "footnote_reference",
          message: "第二处脚注引用",
          classification: "content_check",
        },
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
        (button) => button.textContent === "保留原文",
      )!,
    );
    expect(screen.getAllByTestId("content-check-risk-item")).toHaveLength(1);
    expect(confirm.disabled).toBe(true);

    fireEvent.click(screen.getByTestId("content-check-keep-all-plain"));
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
    fireEvent.keyDown(editor, { key: "Enter", metaKey: true });
    await waitFor(() =>
      expect(props.onConfirmed).toHaveBeenCalledWith("rec_cc_1"),
    );
    const confirmCallsAfterResolution = fetchMock.mock.calls.filter(([input]) =>
      String(input).includes("/candidate-documents/"),
    );
    expect(confirmCallsAfterResolution).toHaveLength(1);
  });

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

    fireEvent.click(screen.getByTestId("content-check-keep-all-plain"));
    await waitFor(() =>
      expect((saveButton as HTMLButtonElement).disabled).toBe(false),
    );

    // 风险已处置后 Ctrl+Enter → confirm 前 flush 保存 → PUT 返回 rejected。
    fireEvent.keyDown(editor, { key: "Enter", ctrlKey: true });

    await waitFor(() =>
      expect(screen.getByTestId("content-check-rejected")).toBeTruthy(),
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
              {
                code: "code_dominant",
                message: "代码占比过高，不适合透读",
                classification: "content_check",
              },
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
    fireEvent.click(screen.getByTestId("content-check-keep-all-plain"));
    await waitFor(() =>
      expect(
        (screen.getByTestId(
          "content-check-confirm-button",
        ) as HTMLButtonElement).disabled,
      ).toBe(false),
    );
    fireEvent.keyDown(editor, { key: "Enter", ctrlKey: true });

    await waitFor(() =>
      expect(screen.getByTestId("content-check-rejected")).toBeTruthy(),
    );
    expect(screen.getByTestId("content-check-rejected").textContent).toContain(
      "这份内容以代码为主，批注价值有限，建议确认是否继续。",
    );
  });

  it("操作层级：确认主 CTA / 返回修改次 CTA / 稍后处理低噪出口", async () => {
    installFetchMock();
    const props = renderPanel();
    await waitForPanelReady();

    fireEvent.click(screen.getByRole("button", { name: "稍后处理" }));
    expect(props.onDefer).toHaveBeenCalledWith({
      recordId: "rec_cc_1",
      candidateDocumentId: "cand_1",
      canonicalTextPreview: "Title",
    });

    fireEvent.click(screen.getByRole("button", { name: "返回修改" }));
    expect(props.onBackToInput).toHaveBeenCalledWith(DRAFT_MARKDOWN);

    fireEvent.click(screen.getByTestId("content-check-keep-all-plain"));
    fireEvent.click(screen.getByTestId("content-check-confirm-button"));
    await waitFor(() =>
      expect(props.onConfirmed).toHaveBeenCalledWith("rec_cc_1"),
    );
  });

  it("resume 来源隐藏返回修改", async () => {
    installFetchMock();
    renderPanel({ origin: "resume" });
    await waitForPanelReady();
    expect(screen.queryByRole("button", { name: "返回修改" })).toBeNull();
    expect(screen.getByRole("button", { name: "稍后处理" })).toBeTruthy();
  });
});
