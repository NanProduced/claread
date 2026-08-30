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

import type { ReaderContentCheckItemDto } from "@/types/api/reader-plate";
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
      reveal: (excerpt: string) => boolean;
      canRevealExact: (excerpt: string) => boolean;
      revealExact: (excerpt: string) => boolean;
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
      reveal: () => true,
      canRevealExact: () => true,
      revealExact: () => true,
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

const DRAFT_MARKDOWN = "# Title\n\n```python\ndef f():\n    pass\n";

function makeContentCheckItem(
  issueId: string,
  overrides: Partial<ReaderContentCheckItemDto> = {},
): ReaderContentCheckItemDto {
  return {
    code: "has_unclosed_fence",
    message: "technical detail",
    classification: "content_check",
    issue_id: issueId,
    tier: "attention",
    target_scope: "range",
    source_anchor: { start_utf16: 9, end_utf16: 18 },
    anchor_hash: "b839d1b2b703576919548db08bd100e7c9be17820b76bd5bbe386a36507ec127",
    evidence: {
      excerpt_text: "```python",
      proposed_patch: "```python\n```",
    },
    source_media_coordinate: null,
    ...overrides,
  };
}

function makeReadResponse() {
  return {
    ok: true as const,
    source_document_id: "cs_a11y",
    record_generation: 1,
    revision: 1,
    status: "draft" as const,
    markdown_text: DRAFT_MARKDOWN,
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
    content_check: [makeContentCheckItem("aaaaaaaaaaaaaaaa")],
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

    fireEvent.click(screen.getByRole("button", { name: "确认当前内容" }));
    const confirm = screen.getByTestId("content-check-confirm-button");
    confirm.focus();
    expect(document.activeElement).toBe(confirm);

    const back = screen.getByRole("button", { name: "重新输入" });
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

  it("低噪说明自动保存，移动端由面板滚动且主要操作满足 44px 目标", async () => {
    installFetchMock();
    renderPanel();
    await waitForReady();

    expect(screen.getByText("正文可直接修改，修改会自动保存")).toBeTruthy();

    const panel = screen.getByTestId("content-check-panel");
    expect(panel.className).toContain("overflow-hidden");
    const scrollOwner = screen.getByTestId("content-check-document");
    expect(scrollOwner.className).toContain("overflow-y-auto");

    const editor = document.getElementById("content-check-editor");
    expect(editor?.className).toContain("min-h-[28rem]");
    const adopt = await screen.findByRole("button", { name: "采用建议" });
    expect(adopt.className).toContain("max-lg:min-h-11");
    expect(screen.getByRole("button", { name: "稍后处理" }).className).toContain(
      "max-lg:min-h-11",
    );
  });

  it("风险卡片操作按钮（采用建议/确认当前内容）与定位入口可按名称定位", async () => {
    installFetchMock();
    renderPanel();
    await waitForReady();

    expect(await screen.findByRole("button", { name: /采用建议/ })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "确认当前内容" }).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "去修改" })).toBeNull();
    expect(screen.queryByTestId("content-check-keep-all-plain")).toBeNull();
  });

  it("移动端 Sheet 具名、模态、锁定正文，Esc 关闭后焦点返回触发器", async () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockReturnValue({
        matches: false,
        media: "(min-width: 1024px)",
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }),
    );
    installFetchMock();
    renderPanel();

    const trigger = await screen.findByRole("button", {
      name: /展开审查批注面板/,
    });
    expect(screen.getByTestId("content-check-panel").className).toContain(
      "h-[calc(100dvh-12rem)]",
    );
    const documentSurface = screen.getByTestId("content-check-document");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(documentSurface.hasAttribute("inert")).toBe(false);

    trigger.focus();
    fireEvent.click(trigger);
    const dialog = await screen.findByRole("dialog", { name: "审查批注" });
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.className).toContain("sm:mt-0");
    expect(documentSurface.hasAttribute("inert")).toBe(true);
    expect(dialog.contains(document.activeElement)).toBe(true);

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(documentSurface.hasAttribute("inert")).toBe(false);
    expect(document.activeElement).toBe(trigger);
  });
});
