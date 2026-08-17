/** @vitest-environment jsdom */

/**
 * ContentCheckPanel a11y 合同（jsdom 层，不跑浏览器）。
 *
 * 覆盖：交互元素可访问名称、编辑器程序化标签、键盘焦点可达主 CTA、
 * Esc 无破坏（非模态语义）、reduced-motion 降级类存在。
 * 浏览器级 Tab 顺序 / :focus-visible 样式由用户页面验收覆盖。
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ContentCheckPanel } from "./ContentCheckPanel";

// 与 ContentCheckPanel.test.tsx 相同的编辑器桩。
vi.mock("./MarkdownTextInput", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./MarkdownTextInput")>();
  const React = await import("react");
  const { forwardRef, useImperativeHandle, useRef, useState } = React;

  const MockMarkdownTextInput = forwardRef<
    {
      getSubmitText: () => string;
      getMarkdown: () => string;
      focus: () => void;
      clear: () => void;
      setValue: (markdown: string) => void;
      flush: () => string;
    },
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
    });
  });

  return { ...actual, MarkdownTextInput: MockMarkdownTextInput };
});

function makeReadResponse() {
  return {
    ok: true as const,
    source_document_id: "cs_a11y",
    record_generation: 1,
    revision: 1,
    status: "draft" as const,
    markdown_text: "# Title\n\n```python\ndef f():\n    pass\n",
    content_sha256: "a".repeat(64),
    edit_source: "initial" as const,
    updated_at: "2026-07-28T00:00:00.000Z",
    candidate: {
      candidate_document_id: "cand_a11y",
      status: "ready" as const,
      canonical_text_preview: "Title",
    },
    quality: null,
    adaptation_notice: [
      { code: "raw_html_block", message: "已移除原始 HTML 块", classification: "adaptation_notice" as const },
    ],
    content_check: [
      { code: "has_unclosed_fence", message: "代码块缺少结束围栏", classification: "content_check" as const },
    ],
  };
}

function installFetchMock() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/confirmed-source")) {
      return new Response(JSON.stringify(makeReadResponse()), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    throw new Error(`Unexpected fetch ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
}

function renderPanel() {
  render(
    <ContentCheckPanel
      recordId="rec_a11y"
      filename={null}
      origin="submit"
      onOpenReader={vi.fn()}
      onConfirmed={vi.fn()}
      onSourceMissing={vi.fn()}
      onBackToInput={vi.fn()}
      onDefer={vi.fn()}
    />,
  );
}

async function waitForReady() {
  await waitFor(() =>
    expect(screen.getByTestId("content-check-confirm-button")).toBeTruthy(),
  );
}

describe("ContentCheckPanel a11y 合同", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("所有交互按钮都有可访问名称（文本 / aria-label / title）", async () => {
    installFetchMock();
    renderPanel();
    await waitForReady();

    const panel = screen.getByTestId("content-check-panel");
    const buttons = Array.from(panel.querySelectorAll("button"));
    expect(buttons.length).toBeGreaterThan(0);
    for (const button of buttons) {
      const name =
        button.textContent?.trim() ||
        button.getAttribute("aria-label") ||
        button.getAttribute("title") ||
        "";
      expect(name, `button missing accessible name: ${button.outerHTML.slice(0, 80)}`).not.toBe("");
    }
  });

  it("编辑器有程序化标签（label htmlFor 关联 content-check-editor）", async () => {
    installFetchMock();
    renderPanel();
    await waitForReady();

    const editor = document.getElementById("content-check-editor");
    expect(editor).toBeTruthy();
    const label = panel_label();
    expect(label?.textContent?.trim()).toBe("待确认正文预览与编辑");

    function panel_label() {
      return document.querySelector('label[for="content-check-editor"]');
    }
  });

  it("键盘焦点可落到主 CTA 与次操作", async () => {
    installFetchMock();
    renderPanel();
    await waitForReady();

    fireEvent.click(screen.getByTestId("content-check-keep-all-plain"));
    const confirm = screen.getByTestId("content-check-confirm-button");
    confirm.focus();
    expect(document.activeElement).toBe(confirm);

    const back = screen.getByRole("button", { name: "返回修改" });
    back.focus();
    expect(document.activeElement).toBe(back);

    const defer = screen.getByRole("button", { name: "稍后处理" });
    defer.focus();
    expect(document.activeElement).toBe(defer);
  });

  it("Esc 不破坏面板（非模态，无 Esc  dismiss 陷阱）", async () => {
    installFetchMock();
    renderPanel();
    await waitForReady();

    fireEvent.keyDown(screen.getByTestId("content-check-panel"), {
      key: "Escape",
    });
    expect(screen.getByTestId("content-check-panel")).toBeTruthy();
    expect(screen.getByTestId("content-check-confirm-button")).toBeTruthy();
  });

  it("reduced-motion 降级：面板根带 motion-reduce:animate-none", async () => {
    installFetchMock();
    renderPanel();
    await waitForReady();

    const panel = screen.getByTestId("content-check-panel");
    expect(panel.className).toContain("motion-reduce:animate-none");
    expect(panel.className).toContain("motion-safe:duration-200");
  });

  it("风险卡片操作按钮（采用建议/保留原文/去修改）可按名称定位", async () => {
    installFetchMock();
    renderPanel();
    await waitForReady();

    expect(screen.getByRole("button", { name: /采用建议/ })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "保留原文" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: "去修改" }).length).toBeGreaterThan(0);
    expect(screen.getByTestId("content-check-keep-all-plain").textContent).toContain(
      "全部保留原文",
    );
  });
});
