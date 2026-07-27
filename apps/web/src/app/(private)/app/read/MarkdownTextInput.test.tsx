/** @vitest-environment jsdom */

/**
 * R1 Phase 1 — MarkdownTextInput 真实 Plate value lifecycle 红灯测试。
 *
 * 与 AnalyzeSubmitForm.test.tsx 不同，本套件**不 mock** MarkdownTextInput：
 * 渲染真实 Plate 编辑器，通过组件公开的 setValue / clear 驱动内容变化，封住
 * "handleChange 挂在 PlateContent DOM onChange 上 → 用户输入/粘贴从不
 * 触发父状态同步" 这一失效点。
 *
 * jsdom 限制说明：jsdom 不支持 beforeinput，Slate 走 legacy 路径，
 * 合成 paste/keydown 无法完整驱动 Slate 的 DOM 管线（已实证：paste 不
 * 插入内容、Ctrl+Enter 在真实浏览器有效但 jsdom 被 legacy 路径吞掉）。
 * 因此：
 * - 组件级：用真实 Plate editor 的公开组件入口（setValue / clear）覆盖
 *   value lifecycle 与序列化保真。
 * - 浏览器级：真实粘贴与 Ctrl/Cmd+Enter 由 Phase 5 Playwright 验收覆盖。
 */

import { act, render, waitFor } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, describe, expect, it, vi, type Mock } from "vitest";

import {
  MarkdownTextInput,
  type MarkdownTextInputHandle,
} from "./MarkdownTextInput";
import { R1_TEST_MARKDOWN } from "./r1-test-fixtures";

function renderEditor(props?: {
  onChange?: (markdown: string) => void;
  onSubmit?: () => void;
  onLintResult?: (result: unknown) => void;
  onDegraded?: (result: unknown) => void;
  ariaLabelledBy?: string;
  ariaDescribedBy?: string;
}) {
  const ref = createRef<MarkdownTextInputHandle>();
  const onChange = (props?.onChange ?? vi.fn()) as Mock<(markdown: string) => void>;
  const onSubmit = (props?.onSubmit ?? vi.fn()) as Mock<() => void>;
  const utils = render(
    <MarkdownTextInput
      ref={ref}
      id="analysis-text"
      initialValue=""
      onChange={onChange}
      onSubmit={onSubmit}
      onLintResult={props?.onLintResult}
      onDegraded={props?.onDegraded}
      ariaLabelledBy={props?.ariaLabelledBy}
      ariaDescribedBy={props?.ariaDescribedBy}
    />,
  );
  const editorEl = utils.container.querySelector("#analysis-text") as HTMLElement;
  return { ref, onChange, onSubmit, editorEl, ...utils };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("MarkdownTextInput value lifecycle (real Plate)", () => {
  it("propagates structured Markdown to onChange and renders h2/h3/em structure", async () => {
    const { ref, onChange, editorEl } = renderEditor();

    await act(async () => {
      ref.current?.setValue(R1_TEST_MARKDOWN);
    });

    await waitFor(() => {
      expect(onChange).toHaveBeenCalled();
    });

    // 最后一次 onChange 载荷是序列化后的 Markdown，保留标题结构。
    const lastCall = String(onChange.mock.calls.at(-1)?.[0] ?? "");
    expect(lastCall).toContain("## 6. Implementation Plan");
    expect(lastCall).toContain("### Step 1: Streamline Server Deployment Architecture");
    expect(lastCall).toContain("### Step 2: Data Storage Migration & Feature Adaptation");
    expect(lastCall).toContain("### Step 3: Canary Deployment & Validation");

    // DOM 语义结构：1×h2、3×h3、至少 1×em。
    expect(editorEl.querySelectorAll("h2")).toHaveLength(1);
    expect(editorEl.querySelectorAll("h3")).toHaveLength(3);
    expect(editorEl.querySelectorAll("em").length).toBeGreaterThanOrEqual(1);

    expect(ref.current?.getSubmitText()).toContain("## 6. Implementation Plan");
  });

  it("preserves a legitimate zero-width space in non-empty content", async () => {
    const { ref, onChange } = renderEditor();
    const markdownWithZeroWidthSpace = "## Heading\n\nalpha\u200Bbeta";

    await act(async () => {
      ref.current?.setValue(markdownWithZeroWidthSpace);
    });

    await waitFor(() => {
      expect(onChange).toHaveBeenCalled();
    });
    expect(String(onChange.mock.calls.at(-1)?.[0] ?? "")).toContain(
      "alpha\u200Bbeta",
    );
    expect(ref.current?.getSubmitText()).toContain("alpha\u200Bbeta");
  });

  it("keeps plain text content as a normal paragraph without heading nodes", async () => {
    const { ref, onChange, editorEl } = renderEditor();

    await act(async () => {
      ref.current?.setValue("Just a plain English paragraph without markers.");
    });

    await waitFor(() => {
      expect(onChange).toHaveBeenCalled();
    });

    expect(editorEl.querySelectorAll("h2,h3,h4")).toHaveLength(0);
    expect(editorEl.querySelector("p")?.textContent).toContain(
      "Just a plain English paragraph without markers.",
    );
  });

  it("notifies onChange when cleared so parent state can reset", async () => {
    const { ref, onChange } = renderEditor();

    await act(async () => {
      ref.current?.setValue(R1_TEST_MARKDOWN);
    });
    await waitFor(() => {
      expect(onChange).toHaveBeenCalled();
    });
    onChange.mockClear();

    await act(async () => {
      ref.current?.clear();
    });

    await waitFor(() => {
      expect(onChange).toHaveBeenCalled();
    });
    const cleared = String(onChange.mock.calls.at(-1)?.[0] ?? "<no call>");
    expect(cleared.trim()).toBe("");
    expect(ref.current?.getSubmitText()).toBe("");
  });

  it("notifies onChange when setValue replaces content programmatically", async () => {
    const { ref, onChange } = renderEditor();

    await act(async () => {
      ref.current?.setValue("## Replaced Heading\n\nReplaced body text.");
    });

    await waitFor(() => {
      expect(onChange).toHaveBeenCalled();
    });
    const last = String(onChange.mock.calls.at(-1)?.[0] ?? "");
    expect(last).toContain("## Replaced Heading");
    expect(last).toContain("Replaced body text.");
  });

  it("forwards aria-labelledby / aria-describedby onto the contenteditable", () => {
    const { editorEl } = renderEditor({
      ariaLabelledBy: "analysis-text-label",
      ariaDescribedBy: "analysis-text-hint",
    });

    // contenteditable 不是 labelable 元素：<label for> 不能可靠命名它。
    // 必须显式 aria-labelledby / aria-describedby。
    expect(editorEl.getAttribute("aria-labelledby")).toBe("analysis-text-label");
    expect(editorEl.getAttribute("aria-describedby")).toBe("analysis-text-hint");
  });
});
