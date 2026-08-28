/**
 * @vitest-environment jsdom
 *
 * Reader 正文代码块体验：
 * - 语法高亮：渲染层 token spans（shiki codeToTokens），文本逐字不变，
 *   禁止 dangerouslySetInnerHTML / innerHTML，未知语言 / 高亮失败 fail-closed。
 * - 复制交互：hover / focus-visible 工具区（语言 badge + 复制按钮），
 *   clipboard 写代码原文，成功 / 失败轻反馈，chrome 均 copy-exclude。
 * - 容器排版：padding / radius / 暖色底（surface-raised 系）落在
 *   globals.css 的 reader-record-plate-markdown-code-block 区段。
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ReaderAnchorSegmentNodeDto,
  ReaderPlateSnapshotDto,
  ReaderUnitNodeDto,
} from "@/types/api/reader-plate";

vi.mock("@/components/providers/appearance-provider", () => ({
  useAppearance: () => ({
    themePreference: "system" as const,
    resolvedTheme: "light" as const,
    setThemePreference: vi.fn(),
  }),
}));

import { ReaderRecordPlateSurface } from "@/components/reader/plate/ReaderRecordPlateSurface";
import { primitiveFocusRing } from "@/components/primitives/shared";
import {
  makeSnapshot,
  makeUnit,
} from "@/components/reader/plate/reader-record-plate-surface-fixtures";

const PYTHON_CODE = 'def greet(name):\n    return "hello " + name';
const READER_CODE_TOKEN_SELECTOR = ".reader-record-plate-code-token";

const clipboardWriteTextMock = vi.fn<(text: string) => Promise<void>>();

function makeCodeBlockSnapshot(
  code: string,
  codeLanguage: string | null,
): ReaderPlateSnapshotDto {
  const unit = makeUnit({ vocabularyMarks: [], grammarMarks: [] });
  const sourceBlock = unit.children[0];
  if (sourceBlock?.type !== "reader_source_block") {
    throw new Error("Expected reader_source_block");
  }
  sourceBlock.stableBlockType = "code_block";
  sourceBlock.stableBlockId = "b_code";
  sourceBlock.codeLanguage = codeLanguage;
  const segment = sourceBlock.children[0] as
    | ReaderAnchorSegmentNodeDto
    | undefined;
  if (!segment || segment.type !== "reader_anchor_segment") {
    throw new Error("Expected reader_anchor_segment");
  }
  const leaf = segment.children[0] as { text?: unknown } | undefined;
  if (!leaf || typeof leaf.text !== "string") {
    throw new Error("Expected source text leaf");
  }
  leaf.text = code;
  unit.children = [sourceBlock];
  return {
    ...makeSnapshot(),
    value: [unit as ReaderUnitNodeDto],
  };
}

function renderCodeBlock(code: string, codeLanguage: string | null) {
  return render(
    <ReaderRecordPlateSurface
      snapshot={makeCodeBlockSnapshot(code, codeLanguage)}
    />,
  );
}

function getPre(container: HTMLElement): HTMLElement {
  const pre = container.querySelector<HTMLElement>(
    "pre.reader-record-plate-markdown-code-block",
  );
  if (!pre) {
    throw new Error("Expected stable code_block pre");
  }
  return pre;
}

function getCode(container: HTMLElement): HTMLElement {
  const code = getPre(container).querySelector<HTMLElement>("code");
  if (!code) {
    throw new Error("Expected stable code_block code");
  }
  return code;
}

function readWebSource(relativePath: string): string {
  return readFileSync(resolve(process.cwd(), relativePath), "utf8");
}

beforeEach(() => {
  // jsdom does not implement Range.getBoundingClientRect
  if (!Range.prototype.getBoundingClientRect) {
    Range.prototype.getBoundingClientRect = vi.fn(() => ({
      x: 0,
      y: 0,
      top: 0,
      left: 0,
      bottom: 20,
      right: 100,
      width: 100,
      height: 20,
      toJSON() {
        return { x: 0, y: 0, top: 0, left: 0, bottom: 20, right: 100, width: 100, height: 20 };
      },
    })) as unknown as Range["getBoundingClientRect"];
  }
  if (!HTMLElement.prototype.scrollIntoView) {
    HTMLElement.prototype.scrollIntoView = vi.fn();
  }
  if (!HTMLElement.prototype.scrollTo) {
    HTMLElement.prototype.scrollTo = vi.fn();
  }
  vi.stubGlobal(
    "ResizeObserver",
    class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.includes("/api/web/reader/records/") && url.pathname.endsWith("/favorite")) {
        return Promise.resolve(
          new Response(JSON.stringify({ ok: true, favorited: false }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      return Promise.resolve(new Response("Not Found", { status: 404 }));
    }),
  );
  Object.defineProperty(navigator, "clipboard", {
    value: {
      writeText: (text: string) => clipboardWriteTextMock(text),
    },
    configurable: true,
    writable: true,
  });
  clipboardWriteTextMock.mockResolvedValue(undefined);
  window.getSelection()?.removeAllRanges();
});

afterEach(() => {
  window.getSelection()?.removeAllRanges();
  try {
    window.localStorage?.removeItem?.("claread.reader.settings.v4");
  } catch {
    // Ignore jsdom localStorage variants that do not expose the full Storage API.
  }
  clipboardWriteTextMock.mockReset();
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ReaderStableCodeBlockComponent code UX", () => {
  describe("syntax highlight (rendering layer)", () => {
    it("renders plain text on first paint, then highlighted token spans with verbatim text", async () => {
      const { container } = renderCodeBlock(PYTHON_CODE, "python");
      const code = getCode(container);

      // 首屏不阻塞：加载前原样显示纯文本。
      expect(code.textContent).toBe(PYTHON_CODE);

      await waitFor(
        () => {
          expect(code.querySelectorAll(READER_CODE_TOKEN_SELECTOR).length).toBeGreaterThan(
            1,
          );
        },
        { timeout: 5000 },
      );

      // 高亮后文本逐字不变。
      expect(code.textContent).toBe(PYTHON_CODE);
    });

    it("assigns distinct highlight roles to keyword and string tokens", async () => {
      const { container } = renderCodeBlock(PYTHON_CODE, "python");
      const code = getCode(container);

      await waitFor(
        () => {
          expect(code.querySelector(`${READER_CODE_TOKEN_SELECTOR}--keyword`)).not.toBeNull();
        },
        { timeout: 5000 },
      );
      expect(code.querySelector(`${READER_CODE_TOKEN_SELECTOR}--string`)).not.toBeNull();
      expect(code.querySelector(`${READER_CODE_TOKEN_SELECTOR}--function`)).not.toBeNull();
    });

    it("keeps navigable data attrs and native selection over highlighted text", async () => {
      const { container } = renderCodeBlock(PYTHON_CODE, "python");
      const pre = getPre(container);
      const code = getCode(container);

      await waitFor(
        () => {
          expect(code.querySelectorAll(READER_CODE_TOKEN_SELECTOR).length).toBeGreaterThan(
            1,
          );
        },
        { timeout: 5000 },
      );

      expect(pre.getAttribute("data-reader-record-stable-block-type")).toBe("code_block");
      expect(pre.getAttribute("data-reader-record-node")).toBe("code_block");
      expect(pre.getAttribute("data-unit-id")).toBe("unit_1");
      expect(pre.getAttribute("data-language")).toBe("python");

      // 原生选区跨 token spans 仍返回逐字文本（与纯文本渲染一致）。
      const range = document.createRange();
      range.selectNodeContents(code);
      const selection = window.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
      expect(window.getSelection()?.toString()).toBe(PYTHON_CODE);
    });

    it("falls back to plain text for unknown languages", async () => {
      const { container } = renderCodeBlock(
        "x = 1",
        "definitely-unknown-lang",
      );
      const code = getCode(container);

      expect(code.textContent).toBe("x = 1");

      // 给潜在的高亮异步任务留出窗口，确认不会出现 token spans。
      await new Promise((resolvePromise) => setTimeout(resolvePromise, 100));
      expect(code.querySelector(READER_CODE_TOKEN_SELECTOR)).toBeNull();
      expect(code.textContent).toBe("x = 1");
    });

    it("keeps textContent identical across the plain-to-highlighted swap for trailing-newline code", async () => {
      // slate 纯文本渲染对以 \n 结尾的代码块会多渲染一个换行；
      // 高亮渲染必须复刻该行为，保证加载前后 selection/copy 文本不变。
      const code = 'x = 1\n';
      const { container } = renderCodeBlock(code, "python");
      const codeEl = getCode(container);

      expect(codeEl.textContent).toBe("x = 1\n\n");

      await waitFor(
        () => {
          expect(codeEl.querySelectorAll(READER_CODE_TOKEN_SELECTOR).length).toBeGreaterThan(
            0,
          );
        },
        { timeout: 5000 },
      );
      expect(codeEl.textContent).toBe("x = 1\n\n");
    });

    it("never uses dangerouslySetInnerHTML or innerHTML in the highlight module", () => {
      const source = readWebSource(
        "src/components/editor/plugins/reader-code-highlight.ts",
      );
      expect(source).not.toMatch(/dangerouslySetInnerHTML/);
      expect(source).not.toMatch(/innerHTML/);
    });
  });

  describe("copy toolbar", () => {
    it("renders a hover/focus-visible toolbar with language badge and copy button, copy-excluded", async () => {
      const { container } = renderCodeBlock(PYTHON_CODE, "python");
      const pre = getPre(container);

      const toolbar = pre.querySelector<HTMLElement>('[data-testid="code-toolbar"]');
      expect(toolbar).not.toBeNull();
      if (!toolbar) {
        throw new Error("Expected code toolbar");
      }
      expect(toolbar.getAttribute("data-reader-record-copy-exclude")).toBe("true");
      expect(toolbar.getAttribute("contenteditable")).toBe("false");
      // hover / focus-visible 才显示（Notion 式 chrome 收敛）。
      expect(toolbar.className).toMatch(/\bopacity-0\b/);
      expect(toolbar.className).toMatch(/group-hover:opacity-100/);
      expect(toolbar.className).toMatch(/group-focus-within:opacity-100/);

      const badge = toolbar.querySelector<HTMLElement>('[data-testid="code-language-badge"]');
      expect(badge).not.toBeNull();
      expect(badge?.textContent).toBe("python");
      expect(badge?.getAttribute("contenteditable")).toBe("false");
      expect(badge?.getAttribute("draggable")).toBe("false");
      expect(badge?.getAttribute("data-reader-record-copy-exclude")).toBe("true");

      const copyButton = toolbar.querySelector<HTMLButtonElement>(
        '[data-testid="code-copy-button"]',
      );
      expect(copyButton).not.toBeNull();
      expect(copyButton?.getAttribute("data-reader-record-copy-exclude")).toBe("true");
    });

    it("reveals on the toolbar's own focus-within, not only via the group ancestor", async () => {
      // 键盘无障碍：Tab 聚焦「复制代码」后工具区必须可见。显露不能只依赖
      // 祖先 `.group` 的 group-focus-within 链路；工具栏自身必须携带
      // focus-within:opacity-100（后代获得焦点时由工具栏自己显现）。
      const { container } = renderCodeBlock(PYTHON_CODE, "python");
      const toolbar = container.querySelector<HTMLElement>(
        '[data-testid="code-toolbar"]',
      );
      expect(toolbar).not.toBeNull();
      expect(toolbar?.className).toMatch(/(^|\s)focus-within:opacity-100(\s|$)/);
    });

    it("copy button reuses the primitive focus ring for a visible keyboard focus state", async () => {
      // 与图片工具栏按钮同款：复用 primitiveFocusRing，保证 focus-visible
      // 有设计系统 ring（浏览器默认 outline 在 0.7rem chrome 上几乎不可见）。
      const { container } = renderCodeBlock(PYTHON_CODE, "python");
      const copyButton = container.querySelector<HTMLButtonElement>(
        '[data-testid="code-copy-button"]',
      );
      expect(copyButton).not.toBeNull();
      for (const token of primitiveFocusRing.split(/\s+/)) {
        expect(copyButton?.className).toContain(token);
      }
    });

    it("renders an always-on copy toolbar without badge for no-language code blocks", async () => {
      const { container } = renderCodeBlock("plain code", null);
      const pre = getPre(container);
      const code = getCode(container);

      // 无语言 fence：无 badge、无 pt-6，但 pre 恒带 relative group，
      // 工具区（复制按钮）恒渲染且可复制代码原文。
      expect(code.textContent).toBe("plain code");
      expect(code.className ?? "").not.toMatch(/\bpt-\d+\b/);
      expect(pre.className).toMatch(/\brelative\b/);
      expect(pre.className).toMatch(/\bgroup\b/);

      const toolbar = pre.querySelector<HTMLElement>('[data-testid="code-toolbar"]');
      expect(toolbar).not.toBeNull();
      expect(toolbar?.getAttribute("data-reader-record-copy-exclude")).toBe("true");
      expect(pre.querySelector('[data-testid="code-language-badge"]')).toBeNull();

      fireEvent.click(screen.getByTestId("code-copy-button"));
      await waitFor(() => {
        expect(clipboardWriteTextMock).toHaveBeenCalledWith("plain code");
      });
      await waitFor(() => {
        expect(screen.getByTestId("code-copy-status").textContent).toBe("已复制");
      });
    });

    it("copies the exact code text to the clipboard and shows success feedback", async () => {
      const { container } = renderCodeBlock(PYTHON_CODE, "python");
      expect(container.querySelector('[data-testid="code-copy-button"]')).not.toBeNull();

      fireEvent.click(screen.getByTestId("code-copy-button"));

      await waitFor(() => {
        expect(clipboardWriteTextMock).toHaveBeenCalledWith(PYTHON_CODE);
      });
      await waitFor(() => {
        expect(screen.getByTestId("code-copy-status").textContent).toBe("已复制");
      });
    });

    it("shows failure feedback when the clipboard write rejects", async () => {
      clipboardWriteTextMock.mockRejectedValue(new Error("denied"));
      const { container } = renderCodeBlock(PYTHON_CODE, "python");
      expect(container.querySelector('[data-testid="code-copy-button"]')).not.toBeNull();

      fireEvent.click(screen.getByTestId("code-copy-button"));

      await waitFor(() => {
        expect(clipboardWriteTextMock).toHaveBeenCalledWith(PYTHON_CODE);
      });
      await waitFor(() => {
        expect(screen.getByTestId("code-copy-status").textContent).toBe("复制失败");
      });
    });
  });

  describe("container typography (globals.css)", () => {
    it("styles the code block container per the warm paper baseline", () => {
      const css = readWebSource("src/app/globals.css");
      const ruleMatch = css.match(
        /\.reader-record-plate-markdown-code-block\s*\{([^}]*)\}/,
      );
      expect(ruleMatch).not.toBeNull();
      const rule = ruleMatch?.[1] ?? "";

      expect(rule).toContain("padding: 0.75rem 0.875rem");
      expect(rule).toContain("border-radius: 10px");
      expect(rule).toMatch(
        /background-color:\s*color-mix\(in srgb, var\(--surface-raised\) 96%, var\(--vocab-amber\) 4%\)/,
      );
      expect(rule).toContain(
        "font-size: calc(var(--reader-record-note-body-size) * 0.9)",
      );
      expect(rule).toContain("line-height: 1.55");
    });

    it("maps token roles to warm, theme-aware CSS variables", () => {
      const css = readWebSource("src/app/globals.css");
      expect(
        css.match(
          /\.reader-record-plate-markdown-code-block\s+\.[a-z-]+--keyword\s*\{[^}]*--grammar-violet/,
        ),
      ).not.toBeNull();
      expect(
        css.match(
          /\.reader-record-plate-markdown-code-block\s+\.[a-z-]+--string\s*\{[^}]*--vocab-amber/,
        ),
      ).not.toBeNull();
    });

    it("removes the cool gray bg-muted/40 chrome from both code block containers", () => {
      const source = readWebSource(
        "src/components/editor/plugins/reader-blocks-kit.tsx",
      );
      // 两条渲染路径（stable 与 markdown）共用同一 CSS 类；
      // 底色与圆角由 globals.css 的容器规则持有（表格头的 bg-muted/40 不在范围）。
      const codeBlockClassUsages =
        source.match(/reader-record-plate-markdown-code-block[^`]*/g) ?? [];
      expect(codeBlockClassUsages.length).toBeGreaterThanOrEqual(2);
      for (const usage of codeBlockClassUsages) {
        expect(usage).not.toContain("bg-muted");
        expect(usage).not.toMatch(/\brounded\b/);
      }
    });

    it("pins shiki as an exact direct dependency of apps/web", () => {
      const pkg = JSON.parse(
        readWebSource("package.json"),
      ) as { dependencies?: Record<string, string> };
      expect(pkg.dependencies?.shiki).toBe("3.23.0");
    });
  });
});
