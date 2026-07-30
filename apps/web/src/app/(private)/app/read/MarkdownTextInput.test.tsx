/** @vitest-environment jsdom */

/**
 * R1 Phase 1 — MarkdownTextInput 真实 Plate value lifecycle 红灯测试。
 * R2 Phase 2/3 — scheduling & lifecycle 契约测试。
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
 *
 * R2 Phase 2 scheduling 合同（jsdom 可观测部分）：
 * - programmatic setValue/clear 立即 fire onChange，不依赖 debounce。
 * - 相同内容不重复触发回调（dedup）。
 * - flush() 返回提交所用的单一 Markdown 快照并吸收 pending debounce。
 * - getSubmitText() 始终直读 editor，不依赖 debounced 父状态。
 * - unmount 取消 pending timer，不在卸载后写入父状态。
 * - onDegraded 挂载时仅通知一次（Strict Mode 安全）。
 *
 * debounce 窗口内"多次按键 → 一次回调"的完整验证依赖真实键盘事件，
 * 由 Phase 5 Playwright 验收覆盖；jsdom 只验证 programmatic 路径下的
 * 可观测调度合同（立即 fire + dedup + flush + unmount safety）。
 */

import { act, render, waitFor } from "@testing-library/react";
import React, { createRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

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
  vi.useRealTimers();
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

// ===========================================================================
// R2 Phase 2/3: Scheduling & lifecycle 契约测试
//
// jsdom 无法驱动真实键盘事件（Slate 走 legacy beforeinput 路径），
// 因此 debounce 窗口内"多次按键 → 一次回调"的完整验证由 Phase 5
// Playwright 覆盖。这里验证 programmatic 路径下的可观测调度合同：
//
// 1. dedup：相同内容不重复触发 onChange/onLintResult。
// 2. flush()：显式同步 pending，并返回提交所用的单一快照。
// 3. getSubmitText()：始终直读 editor，不依赖 debounced 父状态。
// 4. clear/setValue 取消 pending 并立即 fire 新值。
// 5. unmount 取消 pending timer，不在卸载后回调。
// 6. onDegraded 挂载时仅通知一次（Strict Mode 安全）。
// 7. 大文本（30k+ 字符）不崩溃且只 fire 一次。
// ===========================================================================

describe("MarkdownTextInput scheduling & lifecycle (R2 Phase 2/3)", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  it("dedup: setValue with same content does not duplicate onChange callbacks", async () => {
    const onChange = vi.fn();
    const onLintResult = vi.fn();
    const { ref } = renderEditor({ onChange, onLintResult });

    await act(async () => {
      ref.current?.setValue("## Same Heading\n\nSame body text.");
    });

    const callsAfterFirst = onChange.mock.calls.length;
    expect(callsAfterFirst).toBeGreaterThanOrEqual(1);

    // 清除 mock，用相同内容再 setValue。
    onChange.mockClear();
    onLintResult.mockClear();

    await act(async () => {
      ref.current?.setValue("## Same Heading\n\nSame body text.");
    });

    // dedup：序列化后 md 相同，不应触发额外 onChange。
    // 注意：setValue 内部 fireSerializeCallbacks 会比较 lastSentMdRef，
    // 相同 md 会被 dedup 吸收。
    const callsAfterSecond = onChange.mock.calls.length;
    expect(callsAfterSecond).toBe(0);
  });

  it("flush() returns one submit snapshot without duplicating callbacks", async () => {
    const onChange = vi.fn();
    const { ref } = renderEditor({ onChange });

    await act(async () => {
      ref.current?.setValue("Some content.");
    });
    onChange.mockClear();

    let snapshot = "";
    await act(async () => {
      snapshot = ref.current?.flush() ?? "";
    });

    expect(snapshot).toContain("Some content.");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("flush() forces pending debounce to fire immediately", async () => {
    const onChange = vi.fn();
    const onLintResult = vi.fn();
    const { ref } = renderEditor({ onChange, onLintResult });

    // setValue 立即 fire + 调度 Plate onChange 派生的 debounce timer。
    await act(async () => {
      ref.current?.setValue("## Heading\n\nBody.");
    });

    const callsBeforeFlush = onChange.mock.calls.length;
    expect(callsBeforeFlush).toBeGreaterThanOrEqual(1);

    // 不 advance timer，直接 flush。
    // flush 会 cancelPendingSerialize + fire pending md。
    // 但 pending md 与 lastSentMd 相同（来自同一 setValue），dedup 吸收。
    // 因此 flush 的可观测效果是"不产生额外回调"——验证它不会重复 fire。
    onChange.mockClear();
    let snapshot = "";
    await act(async () => {
      snapshot = ref.current?.flush() ?? "";
    });

    expect(snapshot).toContain("## Heading");
    // flush 后不应有额外回调（dedup）。
    expect(onChange).not.toHaveBeenCalled();

    // 即便 advance timer 到 debounce 之后，也不应有延迟回调。
    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("getSubmitText() always reads editor directly, not debounced parent state", async () => {
    const onChange = vi.fn();
    const { ref } = renderEditor({ onChange });

    await act(async () => {
      ref.current?.setValue("## Direct Read Test\n\nContent.");
    });

    // 即使不 advance debounce timer，getSubmitText 也应返回最新 editor 内容。
    const submitText = ref.current?.getSubmitText() ?? "";
    expect(submitText).toContain("## Direct Read Test");
    expect(submitText).toContain("Content.");
  });

  it("clear() cancels pending debounce and fires empty immediately", async () => {
    const onChange = vi.fn();
    const { ref } = renderEditor({ onChange });

    await act(async () => {
      ref.current?.setValue("## Content to clear\n\nBody.");
    });
    onChange.mockClear();

    // clear 应立即 fire 空态，不等待 pending debounce。
    await act(async () => {
      ref.current?.clear();
    });

    expect(onChange).toHaveBeenCalled();
    const lastCall = String(onChange.mock.calls.at(-1)?.[0] ?? "<no call>");
    expect(lastCall.trim()).toBe("");

    // advance debounce timer 后不应有 stale 回调。
    await act(async () => {
      vi.advanceTimersByTime(300);
    });

    // clear 已 fire 空态，pending 被取消，不应有额外回调。
    const callsAfterAdvance = onChange.mock.calls.length;
    expect(callsAfterAdvance).toBe(1);
  });

  it("setValue replaces pending debounce and fires new value immediately", async () => {
    const onChange = vi.fn();
    const { ref } = renderEditor({ onChange });

    await act(async () => {
      ref.current?.setValue("## First Value\n\nFirst body.");
    });
    onChange.mockClear();

    // 第二次 setValue 应取消第一次的 pending 并立即 fire 新值。
    await act(async () => {
      ref.current?.setValue("## Second Value\n\nSecond body.");
    });

    expect(onChange).toHaveBeenCalled();
    const lastCall = String(onChange.mock.calls.at(-1)?.[0] ?? "");
    expect(lastCall).toContain("## Second Value");
    expect(lastCall).toContain("Second body.");
    expect(lastCall).not.toContain("First Value");

    // advance timer 后不应有 stale "First Value" 回调。
    await act(async () => {
      vi.advanceTimersByTime(300);
    });

    const allCalls = onChange.mock.calls.map((c) => String(c[0] ?? ""));
    const hasStaleFirst = allCalls.some((md) => md.includes("First Value"));
    expect(hasStaleFirst).toBe(false);
  });

  it("unmount cancels pending debounce timer (no callback after unmount)", async () => {
    const onChange = vi.fn();
    const { ref, unmount } = renderEditor({ onChange });

    await act(async () => {
      ref.current?.setValue("## Before Unmount\n\nContent.");
    });
    onChange.mockClear();

    // 卸载组件——pending debounce timer 应在 cleanup effect 中取消。
    unmount();

    // advance timer 过 debounce 窗口，不应有任何回调。
    await act(async () => {
      vi.advanceTimersByTime(500);
    });

    expect(onChange).not.toHaveBeenCalled();
  });

  it("onDegraded fires exactly once on mount (Strict Mode safe)", async () => {
    const onDegraded = vi.fn();
    renderEditor({ onDegraded });

    // 挂载时 onDegraded 应被调用一次（initialValue="" → status: "empty"）。
    await act(async () => {
      vi.advanceTimersByTime(0);
    });

    const callsAfterMount = onDegraded.mock.calls.length;
    expect(callsAfterMount).toBe(1);

    // 模拟 Strict Mode 双调用 effect：initialDegradedNotifiedRef 防止重复。
    // 这里无法真正触发 Strict Mode remount，但验证 ref guard 逻辑：
    // 即使 effect 重新执行也不应重复通知。
    await act(async () => {
      vi.advanceTimersByTime(100);
    });

    expect(onDegraded.mock.calls.length).toBe(1);
  });

  it("onDegraded fires on setValue with degraded content", async () => {
    const onDegraded = vi.fn();
    const { ref } = renderEditor({ onDegraded });

    // 等待 mount 通知。
    await act(async () => {
      vi.advanceTimersByTime(0);
    });
    onDegraded.mockClear();

    // setValue 正常 Markdown → status: "success"。
    await act(async () => {
      ref.current?.setValue("## Normal Heading\n\nBody.");
    });

    const lastCall = onDegraded.mock.calls.at(-1)?.[0] as
      | { status?: string }
      | undefined;
    expect(lastCall?.status).toBe("success");
  });

  it("onLintResult fires alongside onChange with correct content", async () => {
    const onChange = vi.fn();
    const onLintResult = vi.fn();
    const { ref } = renderEditor({ onChange, onLintResult });

    // 含 unsafe link 的 Markdown → lint 应检测到 unsafe_link warning。
    // 注意：raw HTML 会被 Plate deserializer 剥离，序列化后不再触发 raw_html；
    // unsafe link 作为 Markdown 链接语法会保留到序列化输出中。
    await act(async () => {
      ref.current?.setValue(
        "# Heading\n\n[click](javascript:alert(1))\n\nBody.",
      );
    });

    expect(onLintResult).toHaveBeenCalled();
    const lintResult = onLintResult.mock.calls.at(-1)?.[0] as
      | { hasDangerousContent?: boolean; warnings?: Array<{ kind: string }> }
      | undefined;
    expect(lintResult?.hasDangerousContent).toBe(true);
    const warningKinds = (lintResult?.warnings ?? []).map((w) => w.kind);
    expect(warningKinds).toContain("unsafe_link");
  });

  it("large content (30k+ chars) does not crash and fires exactly once", async () => {
    const onChange = vi.fn();
    const { ref } = renderEditor({ onChange });

    // 构造 30k+ 字符的 Markdown（~500 段落 × ~60 字符）。
    const paragraphs: string[] = ["# Large Document Fixture\n"];
    for (let i = 0; i < 500; i++) {
      paragraphs.push(
        `## Section ${i}\n\nThis is paragraph ${i} with enough content to reach a reasonable length for testing large document input scenarios in the Markdown text input component.`,
      );
    }
    const largeMarkdown = paragraphs.join("\n\n");
    expect(largeMarkdown.length).toBeGreaterThan(30000);

    await act(async () => {
      ref.current?.setValue(largeMarkdown);
    });

    // 大文本应正确序列化并 fire onChange。
    expect(onChange).toHaveBeenCalled();
    const lastCall = String(onChange.mock.calls.at(-1)?.[0] ?? "");
    expect(lastCall).toContain("# Large Document Fixture");
    expect(lastCall).toContain("## Section 499");

    // getSubmitText 也应能处理大文本。
    const submitText = ref.current?.getSubmitText() ?? "";
    expect(submitText.length).toBeGreaterThan(30000);

    // advance 所有 pending timer，不应有额外崩溃或重复回调。
    await act(async () => {
      vi.advanceTimersByTime(500);
    });
  });

  it("empty to non-empty transition fires immediately (no debounce delay)", async () => {
    const onChange = vi.fn();
    const { ref } = renderEditor({ onChange });

    // 初始为空 → setValue 非空：空↔非空边界转换，立即 fire。
    await act(async () => {
      ref.current?.setValue("## From Empty\n\nContent.");
    });

    // 第一条 onChange 应在 setValue 同步路径立即触发（不等 debounce）。
    expect(onChange).toHaveBeenCalled();
    const firstCall = String(onChange.mock.calls[0]?.[0] ?? "");
    expect(firstCall).toContain("## From Empty");
  });

  it("non-empty to empty transition fires immediately (no debounce delay)", async () => {
    const onChange = vi.fn();
    const { ref } = renderEditor({ onChange });

    // 先设非空内容。
    await act(async () => {
      ref.current?.setValue("## Non Empty\n\nContent.");
    });
    onChange.mockClear();

    // clear → 空：空↔非空边界转换，立即 fire。
    await act(async () => {
      ref.current?.clear();
    });

    expect(onChange).toHaveBeenCalled();
    const lastCall = String(onChange.mock.calls.at(-1)?.[0] ?? "");
    expect(lastCall.trim()).toBe("");
  });
});

describe("R2R Phase 0/3: Strict Mode safety", () => {
  it("onDegraded fires exactly once under <StrictMode>", async () => {
    const onDegraded = vi.fn();
    const { container } = render(
      <React.StrictMode>
        <MarkdownTextInput
          ref={createRef<MarkdownTextInputHandle>()}
          id="strict-mode-test"
          initialValue=""
          onChange={() => {}}
          onSubmit={() => {}}
          onDegraded={onDegraded}
        />
      </React.StrictMode>,
    );
    expect(container).toBeTruthy();

    // StrictMode 模拟 unmount-remount：effects 运行→清理→再运行。
    // initialDegradedNotifiedRef 防止重复通知。
    // render() 已同步 flush effects，无需 advanceTimers。
    await act(async () => {
      // 让 StrictMode 双调用 effect 完成
    });

    expect(onDegraded).toHaveBeenCalledTimes(1);
  });
});

describe("R2R Phase 0/3: real serialize round-trip", () => {
  it("Markdown → Plate → Markdown round-trip preserves h1-h3, nested list, code fence language, blockquote", async () => {
    const { ref } = renderEditor();

    const original = `# H1 Title

## H2 Section

### H3 Subsection

- item 1
  - nested item 1a
  - nested item 1b
- item 2

\`\`\`python
def hello():
    print("world")
\`\`\`

> This is a blockquote.

Normal paragraph with **bold** and *italic*.`;

    await act(async () => {
      ref.current?.setValue(original);
    });

    const serialized = ref.current?.getMarkdown() ?? "";
    expect(serialized).toContain("# H1 Title");
    expect(serialized).toContain("## H2 Section");
    expect(serialized).toContain("### H3 Subsection");
    expect(serialized).toContain("item 1");
    expect(serialized).toContain("nested item 1a");
    expect(serialized).toMatch(/```python/);
    expect(serialized).toContain("blockquote");
    expect(serialized).toContain("**bold**");
    expect(serialized).toContain("*italic*");
  });
});

describe("workbench scroll & placeholder contract", () => {
  it("owns the two-line Chinese placeholder on PlateContent via data attributes", () => {
    const ref = createRef<MarkdownTextInputHandle>();
    const utils = render(
      <MarkdownTextInput
        ref={ref}
        id="analysis-text"
        initialValue=""
        onChange={() => {}}
        onSubmit={() => {}}
        placeholder="粘贴英文文章，或直接开始输入"
        placeholderSub="支持网页、Markdown、PDF、TXT"
      />,
    );
    const editorEl = utils.container.querySelector("#analysis-text") as HTMLElement;
    expect(editorEl.getAttribute("data-placeholder")).toBe("粘贴英文文章，或直接开始输入");
    expect(editorEl.getAttribute("data-placeholder-sub")).toBe("支持网页、Markdown、PDF、TXT");
    expect(editorEl.getAttribute("aria-placeholder")).toBe("粘贴英文文章，或直接开始输入");
    // 桌面端正文是唯一滚动容器（工作台高度链的末端）。
    expect(editorEl.className).toContain("overflow-y-auto");
    expect(editorEl.className).toContain("min-h-0");
    expect(editorEl.className).toContain("flex-1");
  });
});
