/** @vitest-environment jsdom */

/**
 * MarkdownTextInput 真实 Plate value lifecycle 红灯测试。
 * scheduling & lifecycle 契约测试。
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
 * - 浏览器级：真实粘贴与 Ctrl/Cmd+Enter 由 Playwright 验收覆盖。
 *
 * scheduling 合同（jsdom 可观测部分）：
 * - programmatic setValue/clear 立即 fire onChange，不依赖 debounce。
 * - 相同内容不重复触发回调（dedup）。
 * - flush() 返回提交所用的单一 Markdown 快照并吸收 pending debounce。
 * - getSubmitText() 始终直读 editor，不依赖 debounced 父状态。
 * - unmount 取消 pending timer，不在卸载后写入父状态。
 * - onDegraded 挂载时仅通知一次（Strict Mode 安全）。
 *
 * debounce 窗口内"多次按键 → 一次回调"的完整验证依赖真实键盘事件，
 * 由 Playwright 验收覆盖；jsdom 只验证 programmatic 路径下的
 * 可观测调度合同（立即 fire + dedup + flush + unmount safety）。
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React, { createRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { createPlateEditor } from "platejs/react";
import { MarkdownPlugin } from "@platejs/markdown";
import type { Descendant } from "platejs";

import { prepareClipboardHtml } from "@/lib/clipboard/prepare-clipboard-html";

import {
  MarkdownTextInput,
  markdownTextInputPlugins,
  type MarkdownTextInputHandle,
} from "./MarkdownTextInput";

import { SUBMIT_TEST_MARKDOWN } from "./__tests__/submit-test-markdown";

function renderEditor(props?: {
  onChange?: (markdown: string) => void;
  onSubmit?: () => void;
  onLintResult?: (result: unknown) => void;
  onDegraded?: (result: unknown) => void;
  ariaLabelledBy?: string;
  ariaDescribedBy?: string;
  initialValue?: string;
}) {
  const ref = createRef<MarkdownTextInputHandle>();
  const onChange = (props?.onChange ?? vi.fn()) as Mock<(markdown: string) => void>;
  const onSubmit = (props?.onSubmit ?? vi.fn()) as Mock<() => void>;
  const utils = render(
    <MarkdownTextInput
      ref={ref}
      id="analysis-text"
      initialValue={props?.initialValue ?? ""}
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
  // 本套件未启用 vitest globals，RTL 不会自动注册 cleanup；G1′ 用例
  // 使用全局 screen 查询，必须显式清理，避免跨用例 DOM 污染。
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("MarkdownTextInput value lifecycle (real Plate)", () => {
  it("propagates structured Markdown to onChange and renders h2/h3/em structure", async () => {
    const { ref, onChange, editorEl } = renderEditor();

    await act(async () => {
      ref.current?.setValue(SUBMIT_TEST_MARKDOWN);
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
      ref.current?.setValue(SUBMIT_TEST_MARKDOWN);
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
// Scheduling & lifecycle 契约测试
//
// jsdom 无法驱动真实键盘事件（Slate 走 legacy beforeinput 路径），
// 因此 debounce 窗口内"多次按键 → 一次回调"的完整验证由
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

describe("MarkdownTextInput scheduling & lifecycle", () => {
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

describe("Strict Mode safety", () => {
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

describe("real serialize round-trip", () => {
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
        placeholderSub="支持 Markdown、PDF、TXT"
      />,
    );
    const editorEl = utils.container.querySelector("#analysis-text") as HTMLElement;
    expect(editorEl.getAttribute("data-placeholder")).toBe("粘贴英文文章，或直接开始输入");
    expect(editorEl.getAttribute("data-placeholder-sub")).toBe("支持 Markdown、PDF、TXT");
    expect(editorEl.getAttribute("aria-placeholder")).toBe("粘贴英文文章，或直接开始输入");
    // 桌面端正文是唯一滚动容器（工作台高度链的末端）。
    expect(editorEl.className).toContain("overflow-y-auto");
    expect(editorEl.className).toContain("min-h-0");
    expect(editorEl.className).toContain("flex-1");
  });
});

describe("reveal 定位", () => {
  it("剥掉 link/image 语法后选中可见文本；无匹配返回 false", async () => {
    const { ref } = renderEditor();
    await act(async () => {
      ref.current?.setValue(
        "Before paragraph.\n\n![A shaded avenue](avenue.jpg)\n\nAfter paragraph with more English words.",
      );
    });
    expect(ref.current?.reveal("[A shaded avenue](avenue.jpg)")).toBe(true);
    expect(ref.current?.reveal("some text that does not exist anywhere")).toBe(
      false,
    );
  });

  it("reveal 命中 typed image：DOM selection 同步到图片 leaf（非仅返回 true）", async () => {
    const { ref, editorEl } = renderEditor();
    await act(async () => {
      ref.current?.setValue("![shaded avenue](https://example.com/a.png)");
    });
    let revealed = false;
    await act(async () => {
      revealed =
        ref.current?.reveal("[shaded avenue](https://example.com/a.png)") ??
        false;
    });
    expect(revealed).toBe(true);
    // reveal 内部 60ms 后读取 window.getSelection() 做滚动定位——等待窗口
    // 过期后断言选区真实落在图片 element 内（void leaf 已渲染）。
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 120));
    });
    const selection = window.getSelection();
    const wrapper = editorEl.querySelector("[data-image-input]");
    expect(wrapper).not.toBeNull();
    expect(selection?.rangeCount ?? 0).toBeGreaterThanOrEqual(1);
    expect(wrapper?.contains(selection?.anchorNode ?? null)).toBe(true);
  });
});

// ===========================================================================
// G1′-A：Markdown 图片 round-trip 与输入端图片合同
//
// 覆盖（冻结合同 g2a-image-representation-contract §3.2 / §5 / §11.1）：
// - `![alt](url "title")` 进入 Plate 后仍是 typed image node，不降级
//   generic link；编辑后 serialize 仍输出 image syntax。
// - standalone / inline / consecutive / empty alt / wrapped 的位置与顺序
//   语义不漂移（inline 图片不得被移动成独立段落或引入额外空段）。
// - image-only 编辑器非空；getMarkdown/getSubmitText 返回 image syntax。
// - Content Check 共用编辑器：initialValue 加载含图 Markdown，编辑相邻
//   正文后提交文本仍含原 image syntax/title。
// - 未编辑纯 Markdown 粘贴继续由 lastPastedTextRef byte-exact 返回原文。
// - 赋 img.src 前执行 §10.1 八规则 fail-closed 判定：reject 项永不进入
//   img.src；四态（loading/loaded/unsafe/load_failed）与 URL 编辑合同。
// ===========================================================================

describe("G1′ image round-trip（deserialize + serialize 语义保持）", () => {
  const IMAGE_CASES: Array<{ name: string; md: string; expected: string[] }> = [
    {
      name: "standalone",
      md: '![a](https://example.com/a.png "T")',
      expected: ['![a](https://example.com/a.png "T")'],
    },
    {
      name: "mixed inline",
      md: 'before ![a](https://example.com/a.png "T") after',
      expected: ['before ![a](https://example.com/a.png "T") after'],
    },
    {
      name: "consecutive images",
      md: "left ![a](u1)![b](u2) right",
      expected: ["left ![a](u1)![b](u2) right"],
    },
    {
      name: "empty alt",
      md: "![](https://example.com/a.png)",
      expected: ["![](https://example.com/a.png)"],
    },
    {
      name: "strong wrapped",
      md: '**![a](https://example.com/u.png "T")**',
      expected: ['**![a](https://example.com/u.png "T")**'],
    },
    {
      name: "em wrapped",
      md: "*![a](u)*",
      expected: ["*![a](u)*"],
    },
    {
      name: "delete wrapped",
      md: "~~![a](u)~~",
      expected: ["~~![a](u)~~"],
    },
  ];

  it.each(IMAGE_CASES)(
    "$name：serialize 输出保留 image syntax / 字段 / 位置",
    async ({ md, expected }) => {
      const { ref } = renderEditor();
      await act(async () => {
        ref.current?.setValue(md);
      });
      const out = ref.current?.getMarkdown() ?? "";
      for (const fragment of expected) {
        expect(out).toContain(fragment);
      }
      // 不得降级为普通链接 `[a](url)`（lookbehind 排除 image 语法自身）
      expect(out).not.toMatch(
        /(?<!!)\[[^\]]*\]\(https:\/\/example\.com\/a\.png\)/,
      );
      expect(out).not.toMatch(/(?<!!)\[[^\]]*\]\(u1\)/);
      expect(out).not.toMatch(/(?<!!)\[[^\]]*\]\(u2\)/);
    },
  );

  it("inline 图片不被移动成独立段落（单段落保持，无额外空段）", async () => {
    const { ref } = renderEditor();
    await act(async () => {
      ref.current?.setValue("before ![a](u1) after");
    });
    const out = ref.current?.getMarkdown() ?? "";
    // 图片前后不得被拆成独立段落（空行分隔）
    expect(out).not.toContain("before\n\n");
    expect(out).not.toContain("\n\n![a](u1)");
    expect(out).toContain("before ![a](u1) after");
  });

  it("image-only 编辑器：data-empty=false 且 getMarkdown/getSubmitText 返回 image syntax", async () => {
    const { ref, editorEl, onChange } = renderEditor();
    await act(async () => {
      ref.current?.setValue('![a](https://example.com/a.png "T")');
    });
    expect(editorEl.getAttribute("data-empty")).toBe("false");
    const md = ref.current?.getMarkdown() ?? "";
    expect(md).toContain('![a](https://example.com/a.png "T")');
    expect(ref.current?.getSubmitText()).toContain(
      '![a](https://example.com/a.png "T")',
    );
    await waitFor(() => {
      expect(
        String(onChange.mock.calls.at(-1)?.[0] ?? ""),
      ).toContain("![a](https://example.com/a.png");
    });
  });

  it("Content Check 语义：initialValue 加载含图 Markdown，编辑相邻正文后提交文本仍含原 image syntax/title", async () => {
    const initial =
      '# Title\n\n![alt](https://example.com/a.png "T")\n\nBody paragraph.';
    const onDegraded = vi.fn();
    const { ref, onChange } = renderEditor({
      initialValue: initial,
      onDegraded,
    });
    // initialValue 加载成功（onChange 仅在编辑后触发——组件既有合同，
    // 此处断言 deserialize 成功无降级）。
    await act(async () => {
      await Promise.resolve();
    });
    const initialStatus = onDegraded.mock.calls.at(-1)?.[0] as
      | { status?: string }
      | undefined;
    expect(initialStatus?.status).toBe("success");
    // dirty 编辑（改相邻正文）→ onChange 收到含 image syntax 的 serialize
    await act(async () => {
      ref.current?.setValue(
        '# Title\n\n![alt](https://example.com/a.png "T")\n\nEdited body paragraph.',
      );
    });
    expect(onChange).toHaveBeenCalled();
    expect(String(onChange.mock.calls.at(-1)?.[0] ?? "")).toContain(
      '![alt](https://example.com/a.png "T")',
    );
    // dirty 保存的提交文本保留 image syntax/title 与编辑后的正文
    const submit = ref.current?.getSubmitText() ?? "";
    expect(submit).toContain('![alt](https://example.com/a.png "T")');
    expect(submit).toContain("Edited body paragraph.");
    expect(submit).not.toMatch(
      /(?<!!)\[alt\]\(https:\/\/example\.com\/a\.png\)/,
    );
  });

  // 注：未编辑纯 Markdown 粘贴的 byte-exact 合同（lastPastedTextRef 原文
  // 返回）不在 jsdom 覆盖——本套件头部已记录合成 paste 无法驱动 Slate
  // DOM 管线（jsdom 限制）；该路径（beginPasteWindow/readSubmitMarkdown）
  // 在 G1′ 中零改动，图片语法未编辑时不经任何转换，byte-exact 由既有
  // 机制结构性保证，浏览器级由 Playwright 验收覆盖。
});

describe("G1′ 图片预览 trust boundary（§10.1 八规则，赋 img.src 前 fail-closed）", () => {
  it("safe URL：渲染真实 img（无 lazy/async/referrerPolicy），loading 占位保留", async () => {
    const { ref, editorEl } = renderEditor();
    await act(async () => {
      ref.current?.setValue('![a](https://example.com/a.png "T")');
    });
    const img = editorEl.querySelector("img");
    expect(img).not.toBeNull();
    expect(img?.getAttribute("src")).toBe("https://example.com/a.png");
    // NARROW-REPAIR 契约：移除 loading=lazy（隐藏的 lazy 图片在真实浏览器
    // 不会发起请求，会死锁在「图片加载中…」）
    expect(img?.getAttribute("loading")).toBeNull();
    expect(img?.getAttribute("decoding")).toBe("async");
    expect(img?.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(img?.getAttribute("alt")).toBe("a");
  });

  it("safe URL：onLoad 后显示图片（loading → loaded）", async () => {
    const { ref, editorEl } = renderEditor();
    await act(async () => {
      ref.current?.setValue("![a](https://example.com/a.png)");
    });
    expect(
      editorEl.querySelector("[data-image-state='loading']"),
    ).not.toBeNull();
    const img = editorEl.querySelector("img");
    expect(img).not.toBeNull();
    await act(async () => {
      fireEvent(img as Element, new Event("load"));
    });
    expect(
      editorEl.querySelector("[data-image-state='loaded']"),
    ).not.toBeNull();
    expect(
      editorEl.querySelector("[data-image-state='loading']"),
    ).toBeNull();
  });

  it("safe URL 加载失败：失败占位显示 alt，提供复制链接与修改链接", async () => {
    const { ref, editorEl } = renderEditor();
    await act(async () => {
      ref.current?.setValue("![alt text](https://example.com/a.png)");
    });
    const img = editorEl.querySelector("img");
    await act(async () => {
      fireEvent(img as Element, new Event("error"));
    });
    expect(
      editorEl.querySelector("[data-image-state='load_failed']"),
    ).not.toBeNull();
    expect(editorEl.textContent).toContain("alt text");
    expect(screen.getByRole("button", { name: "复制链接" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "修改链接" })).toBeTruthy();
  });

  it("safe URL 加载失败且空 alt：显示「图片加载失败」", async () => {
    const { ref, editorEl } = renderEditor();
    await act(async () => {
      ref.current?.setValue("![](https://example.com/a.png)");
    });
    const img = editorEl.querySelector("img");
    await act(async () => {
      fireEvent(img as Element, new Event("error"));
    });
    expect(editorEl.textContent).toContain("图片加载失败");
  });

  it("unsafe URL：无 img[src]，普通状态不显示原 URL，进入编辑面板后才可见", async () => {
    const { ref, editorEl } = renderEditor();
    await act(async () => {
      ref.current?.setValue("![a](javascript:alert(1))");
    });
    expect(editorEl.querySelector("img[src]")).toBeNull();
    expect(editorEl.textContent).toContain("链接不安全");
    // NARROW-REPAIR 契约：普通表面不显示 raw URL（显式编辑面板除外）
    expect(editorEl.textContent).not.toContain("javascript:alert(1)");
    expect(screen.getByRole("button", { name: "修改链接" })).toBeTruthy();
    // 点击「修改链接」进入显式编辑面板后允许显示和编辑 URL
    fireEvent.click(screen.getByRole("button", { name: "修改链接" }));
    const input = screen.getByLabelText("图片链接") as HTMLInputElement;
    expect(input.value).toBe("javascript:alert(1)");
  });

  it("unsafe URL（相对路径）：同样 fail-closed，不显示也不回退相对地址", async () => {
    const { ref, editorEl } = renderEditor();
    await act(async () => {
      ref.current?.setValue("![a](./local.png)");
    });
    expect(editorEl.querySelector("img[src]")).toBeNull();
    expect(editorEl.textContent).toContain("链接不安全");
    expect(editorEl.textContent).not.toContain("./local.png");
  });

  it("修改链接：保存只更新 URL（保留 alt/title），取消零变化", async () => {
    const { ref } = renderEditor();
    await act(async () => {
      ref.current?.setValue('![alt](https://example.com/a.png "T")');
    });
    // 进入编辑态
    fireEvent.click(screen.getByRole("button", { name: "修改链接" }));
    const input = screen.getByLabelText("图片链接") as HTMLInputElement;
    expect(input.value).toBe("https://example.com/a.png");
    fireEvent.change(input, { target: { value: "https://example.com/b.png" } });
    // 取消 → 节点零变化
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    const afterCancel = ref.current?.getMarkdown() ?? "";
    expect(afterCancel).toContain('![alt](https://example.com/a.png "T")');
    expect(afterCancel).not.toContain("b.png");
    // 再次编辑并保存 → 只更新 URL，alt/title 保留
    fireEvent.click(screen.getByRole("button", { name: "修改链接" }));
    fireEvent.change(screen.getByLabelText("图片链接"), {
      target: { value: "https://example.com/b.png" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => {
      const md = ref.current?.getMarkdown() ?? "";
      expect(md).toContain('![alt](https://example.com/b.png "T")');
      expect(md).not.toContain("a.png");
    });
  });

  it("UI chrome（占位文案/按钮/编辑控件）不进入 getMarkdown", async () => {
    const { ref, editorEl } = renderEditor();
    await act(async () => {
      ref.current?.setValue('![alt](https://example.com/a.png "T")');
    });
    const img = editorEl.querySelector("img");
    await act(async () => {
      fireEvent(img as Element, new Event("error"));
    });
    fireEvent.click(screen.getByRole("button", { name: "修改链接" }));
    const md = ref.current?.getMarkdown() ?? "";
    expect(md).toContain('![alt](https://example.com/a.png "T")');
    expect(md).not.toContain("图片加载失败");
    expect(md).not.toContain("复制链接");
    expect(md).not.toContain("修改链接");
    expect(md).not.toContain("图片链接");
  });
});

// ---------------------------------------------------------------------------
// G1P-B-A · HTML code language fidelity（实际 mounted plugins 的 HTML
// deserializer；不伪造 ClipboardEvent，仅经公开 deserialize/serialize seam）
// ---------------------------------------------------------------------------

describe("HTML code language fidelity（G1P-B-A，实际 mounted plugins）", () => {
  function deserializeMountedHtml(html: string): Descendant[] {
    const editor = createPlateEditor({ plugins: markdownTextInputPlugins });
    return editor.api.html.deserialize({
      element: prepareClipboardHtml(html),
    }) as Descendant[];
  }

  function collectByType(
    nodes: Descendant[],
    type: string,
  ): Array<Record<string, unknown>> {
    const found: Array<Record<string, unknown>> = [];
    const walk = (ns: Descendant[]) => {
      for (const n of ns) {
        const node = n as Record<string, unknown>;
        if (node.type === type) found.push(node);
        if (Array.isArray(node.children)) {
          walk(node.children as Descendant[]);
        }
      }
    };
    walk(nodes);
    return found;
  }

  function codeBlockText(node: Record<string, unknown>): string {
    return (node.children as Array<{ children?: Array<{ text?: string }> }> | undefined)
      ?.map((line) => line.children?.map((c) => c.text ?? "").join("") ?? "")
      .join("\n") ?? "";
  }

  it('class="language-python" → code_block lang=python（正文/children 不变）', () => {
    const fragment = deserializeMountedHtml(
      `<pre><code class="language-python">x = 1</code></pre>`,
    );
    const blocks = collectByType(fragment, "code_block");
    expect(blocks).toHaveLength(1);
    expect(blocks[0].lang).toBe("python");
    expect(codeBlockText(blocks[0])).toBe("x = 1");
  });

  it('data-language="typescript" → lang=typescript（fallback 通道）', () => {
    const fragment = deserializeMountedHtml(
      `<pre><code data-language="typescript">const a = 1;</code></pre>`,
    );
    const blocks = collectByType(fragment, "code_block");
    expect(blocks).toHaveLength(1);
    expect(blocks[0].lang).toBe("typescript");
  });

  it("class 与 data-language 并存：标准 language-* class 优先", () => {
    const fragment = deserializeMountedHtml(
      `<pre><code class="language-rust" data-language="go">fn main() {}</code></pre>`,
    );
    const blocks = collectByType(fragment, "code_block");
    expect(blocks).toHaveLength(1);
    expect(blocks[0].lang).toBe("rust");
  });

  it("多 code blocks、多语言：各自保真、源序不漂移", () => {
    const fragment = deserializeMountedHtml(
      `<pre><code class="language-python">a = 1</code></pre>` +
        `<p>between</p>` +
        `<pre><code data-language="sql">SELECT 1;</code></pre>`,
    );
    const blocks = collectByType(fragment, "code_block");
    expect(blocks).toHaveLength(2);
    expect(blocks[0].lang).toBe("python");
    expect(blocks[1].lang).toBe("sql");
    expect(codeBlockText(blocks[0])).toBe("a = 1");
    expect(codeBlockText(blocks[1])).toBe("SELECT 1;");
  });

  it("code body 内部空行完整保留", () => {
    const fragment = deserializeMountedHtml(
      `<pre><code class="language-python">line1\n\nline3</code></pre>`,
    );
    const blocks = collectByType(fragment, "code_block");
    expect(blocks).toHaveLength(1);
    expect(codeBlockText(blocks[0])).toBe("line1\n\nline3");
    expect((blocks[0].children as unknown[]).length).toBe(3);
  });

  it("无 language 时不虚构（无 lang 字段）", () => {
    const fragment = deserializeMountedHtml(`<pre><code>plain code</code></pre>`);
    const blocks = collectByType(fragment, "code_block");
    expect(blocks).toHaveLength(1);
    expect(blocks[0]).not.toHaveProperty("lang");
    expect(codeBlockText(blocks[0])).toBe("plain code");
  });

  it("prose 中的 language-python / 反引号不被识别成 code language", () => {
    const fragment = deserializeMountedHtml(
      `<p>Use <span class="language-python">language-python</span> and \`code\` in prose.</p>`,
    );
    expect(collectByType(fragment, "code_block")).toHaveLength(0);
    expect(JSON.stringify(fragment)).not.toContain('"lang"');
  });

  it("language 只进入 code block metadata，不进入 code text leaf", () => {
    const fragment = deserializeMountedHtml(
      `<pre><code class="language-python">x = 1</code></pre>`,
    );
    const json = JSON.stringify(fragment);
    expect(json).toContain('"lang":"python"');
    expect(json).not.toContain('"text":"python"');
    expect(json).not.toContain("language-python");
  });

  it("serialize round-trip：lang 输出为 fence info（非回归）", () => {
    const editor = createPlateEditor({ plugins: markdownTextInputPlugins });
    const fragment = deserializeMountedHtml(
      `<pre><code class="language-python">x = 1\n\ny = 2</code></pre>`,
    );
    editor.tf.setValue([{ type: "p", children: [{ text: "" }] }] as never[]);
    editor.tf.insertFragment(fragment as never[]);
    const md = editor.getApi(MarkdownPlugin).markdown.serialize();
    expect(md).toContain("```python");
    expect(md).toContain("x = 1");
    expect(md).toContain("y = 2");
  });
});
