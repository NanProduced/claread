/**
 * reader-code-highlight 模块单元测试：
 * - 语言解析：大小写不敏感、空白容错、未知语言 fail-closed。
 * - readerCodeToTokens：未知语言 / null 返回 null（高亮失败回退纯文本）。
 * - 已知语言（python）返回可渲染 tokens，且逐字还原源文本。
 */
import { describe, expect, it } from "vitest";

import {
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
