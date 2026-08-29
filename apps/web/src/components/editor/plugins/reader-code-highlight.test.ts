/**
 * reader-code-highlight 模块单元测试：
 * - 语言解析：大小写不敏感、空白容错、未知语言 fail-closed。
 * - readerCodeToTokens：未知语言 / null 返回 null（高亮失败回退纯文本）。
 * - 已知语言（python）返回可渲染 tokens，且逐字还原源文本。
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  __inputCodeHighlightSchedulerState,
  inputCodeBlockDecorate,
  readerCodeLanguageLabel,
  readerCodeToTokens,
  readerCodeTokensToPlainText,
  resolveReaderCodeLanguage,
} from "./reader-code-highlight";

const PYTHON_CODE = 'def greet(name):\n    return "hello " + name\n';

describe("reader-code-highlight", () => {
  it("resolves known languages case-insensitively and rejects unknown ones", () => {
    expect(resolveReaderCodeLanguage("Python")).toBe("python");
    expect(resolveReaderCodeLanguage(" PY ")).toBe("python");
    expect(resolveReaderCodeLanguage(null)).toBeNull();
    expect(resolveReaderCodeLanguage("")).toBeNull();
    expect(resolveReaderCodeLanguage("klingon-x")).toBeNull();
  });

  it("readerCodeToTokens fails closed (null) for unknown languages", async () => {
    await expect(readerCodeToTokens("x = 1", "klingon-x")).resolves.toBeNull();
    await expect(readerCodeToTokens("x = 1", null)).resolves.toBeNull();
  });

  it("readerCodeLanguageLabel maps canonical and aliased languages to human-readable names", () => {
    expect(readerCodeLanguageLabel("python")).toBe("Python");
    expect(readerCodeLanguageLabel("javascript")).toBe("JavaScript");
    expect(readerCodeLanguageLabel("typescript")).toBe("TypeScript");
    expect(readerCodeLanguageLabel("js")).toBe("JavaScript");
    expect(readerCodeLanguageLabel("c++")).toBe("C++");
    expect(readerCodeLanguageLabel("c#")).toBe("C#");
    expect(readerCodeLanguageLabel("sh")).toBe("Shell");
    expect(readerCodeLanguageLabel("bash")).toBe("Shell");
    expect(readerCodeLanguageLabel("json")).toBe("JSON");
  });

  it("readerCodeLanguageLabel keeps unknown languages verbatim and never invents a badge for no language", () => {
    expect(readerCodeLanguageLabel("klingon-x")).toBe("klingon-x");
    expect(readerCodeLanguageLabel(" PY ")).toBe("Python");
    expect(readerCodeLanguageLabel("")).toBeNull();
    expect(readerCodeLanguageLabel("   ")).toBeNull();
    expect(readerCodeLanguageLabel(null)).toBeNull();
    expect(readerCodeLanguageLabel(undefined)).toBeNull();
  });

  it("readerCodeToTokens returns tokens that reconstruct the source verbatim", async () => {
    const tokens = await readerCodeToTokens(PYTHON_CODE, "python");
    expect(tokens).not.toBeNull();
    expect(readerCodeTokensToPlainText(tokens ?? [])).toBe(PYTHON_CODE);
    const flat = (tokens ?? []).flat();
    expect(flat.length).toBeGreaterThan(1);
  });
});

// ---------------------------------------------------------------------------
// 输入端调度（latest-wins）：同一 code block 的 burst 编辑只让最新版本进入
// tokenize；过期任务不缓存、不触发刷新；不同 code block / 多 editor 相互独立。
// ---------------------------------------------------------------------------

type FakeEditor = {
  editor: {
    api: {
      node: (at: unknown) => [unknown, unknown[]] | undefined;
      redecorate: ReturnType<typeof vi.fn>;
    };
  };
  redecorate: ReturnType<typeof vi.fn>;
};

function makeFakeEditor(children: unknown[]): FakeEditor {
  const nodeAt = (at: unknown): [unknown, unknown[]] | undefined => {
    if (!Array.isArray(at) || at.length === 0) {
      return undefined;
    }
    let node: unknown = { children };
    for (const index of at) {
      const kids = (node as { children?: unknown[] })?.children;
      if (!Array.isArray(kids)) {
        return undefined;
      }
      node = kids[index as number];
    }
    return [node, at];
  };
  const redecorate = vi.fn();
  return { editor: { api: { node: nodeAt, redecorate } }, redecorate };
}

function makeCodeBlock(lang: string | null, lines: string[]) {
  return {
    type: "code_block",
    lang,
    children: lines.map((line) => ({
      type: "code_line",
      children: [{ text: line }],
    })),
  };
}

function decorateLine(
  fake: FakeEditor,
  blockIndex: number,
  lineIndex: number,
  text: string,
) {
  return inputCodeBlockDecorate({
    editor: fake.editor as never,
    entry: [{ text }, [blockIndex, lineIndex, 0]] as never,
  });
}

async function pollUntil(check: () => boolean, label: string): Promise<void> {
  const deadline = Date.now() + 5000;
  while (!check()) {
    if (Date.now() > deadline) {
      throw new Error(`poll timeout: ${label}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}

describe("input code highlight scheduling (latest-wins)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("burst edits A→B→C tokenize only the latest version and leave no stale cache", async () => {
    // 捕获 setTimeout 回调但不执行：三次调度都发生在任何宏任务之前（确定性）。
    const captured: Array<() => void> = [];
    vi.stubGlobal("setTimeout", (fn: () => void) => {
      captured.push(fn);
      return captured.length;
    });
    vi.stubGlobal("clearTimeout", () => {});

    const block = makeCodeBlock("python", ["a = 1"]);
    const { editor, redecorate } = makeFakeEditor([block]);

    for (const text of ["a = 1", "bb = 22", "ccc = 333"]) {
      block.children[0].children[0].text = text;
      decorateLine({ editor, redecorate }, 0, 0, text);
    }

    vi.unstubAllGlobals();
    captured.forEach((run) => run());
    await pollUntil(
      () =>
        __inputCodeHighlightSchedulerState()
          .cacheKeys.some((key) => key.includes("ccc = 333")),
      "latest version cached",
    );

    const { cacheKeys } = __inputCodeHighlightSchedulerState();
    expect(cacheKeys.some((key) => key.includes("a = 1"))).toBe(false);
    expect(cacheKeys.some((key) => key.includes("bb = 22"))).toBe(false);
    // 只有最新版本触发有效刷新
    expect(redecorate).toHaveBeenCalledTimes(1);
    // 最新内容命中缓存并产出 decoration
    const ranges = decorateLine({ editor, redecorate }, 0, 0, "ccc = 333");
    expect(ranges.length).toBeGreaterThan(0);
  });

  it("in-flight stale tokenize completes without caching or refreshing outdated content", async () => {
    const captured: Array<() => void> = [];
    vi.stubGlobal("setTimeout", (fn: () => void) => {
      captured.push(fn);
      return captured.length;
    });
    vi.stubGlobal("clearTimeout", () => {});

    const block = makeCodeBlock("python", ["stale_old = 1"]);
    const { editor, redecorate } = makeFakeEditor([block]);

    decorateLine({ editor, redecorate }, 0, 0, "stale_old = 1");
    const runnerA = captured[0];
    expect(runnerA).toBeDefined();

    vi.unstubAllGlobals();
    // 启动旧版本 tokenize：同步启动、异步完成（无 await → 此刻必然在途）。
    runnerA?.();
    // 旧任务在途期间，同一 block 立即改为新内容。
    block.children[0].children[0].text = "stale_new = 2";
    decorateLine({ editor, redecorate }, 0, 0, "stale_new = 2");

    await pollUntil(
      () =>
        __inputCodeHighlightSchedulerState()
          .cacheKeys.some((key) => key.includes("stale_new = 2")),
      "latest version cached",
    );
    // 让旧任务的在途 promise 落地
    await pollUntil(
      () => __inputCodeHighlightSchedulerState().pendingKeys.length === 0,
      "pending drained",
    );

    const { cacheKeys } = __inputCodeHighlightSchedulerState();
    // 过期在途任务完成：不缓存旧版本、不触发刷新
    expect(cacheKeys.some((key) => key.includes("stale_old = 1"))).toBe(false);
    expect(cacheKeys.some((key) => key.includes("stale_new = 2"))).toBe(true);
    expect(redecorate).toHaveBeenCalledTimes(1);
  });

  it("two code blocks in the same editor highlight independently", async () => {
    const blockA = makeCodeBlock("python", ["alpha = 1"]);
    const blockB = makeCodeBlock("python", ["beta = 2"]);
    const { editor, redecorate } = makeFakeEditor([blockA, blockB]);

    decorateLine({ editor, redecorate }, 0, 0, "alpha = 1");
    decorateLine({ editor, redecorate }, 1, 0, "beta = 2");

    await pollUntil(
      () => {
        const { cacheKeys } = __inputCodeHighlightSchedulerState();
        return (
          cacheKeys.some((key) => key.includes("alpha = 1")) &&
          cacheKeys.some((key) => key.includes("beta = 2"))
        );
      },
      "both blocks cached",
    );

    const rangesA = decorateLine({ editor, redecorate }, 0, 0, "alpha = 1");
    const rangesB = decorateLine({ editor, redecorate }, 1, 0, "beta = 2");
    expect(rangesA.length).toBeGreaterThan(0);
    expect(rangesB.length).toBeGreaterThan(0);
  });

  it("same-key multi-editor dedup still notifies every editor", async () => {
    const blockA = makeCodeBlock("python", ["shared = 1"]);
    const blockB = makeCodeBlock("python", ["shared = 1"]);
    const fakeA = makeFakeEditor([blockA]);
    const fakeB = makeFakeEditor([blockB]);

    decorateLine(fakeA, 0, 0, "shared = 1");
    decorateLine(fakeB, 0, 0, "shared = 1");

    await pollUntil(
      () =>
        __inputCodeHighlightSchedulerState()
          .cacheKeys.some((key) => key.includes("shared = 1")),
      "shared key cached",
    );

    // 去重：同一 key 只缓存一份
    expect(
      __inputCodeHighlightSchedulerState()
        .cacheKeys.filter((key) => key.includes("shared = 1")).length,
    ).toBe(1);
    // 全部刷新：两个 editor 都收到完成通知
    expect(fakeA.redecorate).toHaveBeenCalled();
    expect(fakeB.redecorate).toHaveBeenCalled();
  });
});
