import { describe, expect, it } from "vitest";

import {
  sanitizeMarkdownForStreamdown,
  sanitizeMermaidSource,
} from "./streamdown";

describe("streamdown mermaid sanitization", () => {
  it("normalizes nested straight quotes inside mermaid node labels", () => {
    const source = [
      "graph TD",
      "C1[\"日本是全球最老龄化社会之一<br/>\"银发人口\"持续增长\"] --> C2[结果]",
    ].join("\n");

    expect(sanitizeMermaidSource(source)).toContain(
      "C1[日本是全球最老龄化社会之一<br/>“银发人口”持续增长] --> C2[结果]",
    );
  });

  it("only rewrites mermaid fenced blocks inside markdown", () => {
    const markdown = [
      "普通正文里的 \"quoted text\" 不应被改动。",
      "",
      "```mermaid",
      "graph TD",
      "A[\"政策支持\"和\"补贴\"] --> B[影响]",
      "```",
    ].join("\n");

    const sanitized = sanitizeMarkdownForStreamdown(markdown);

    expect(sanitized).toContain("普通正文里的 \"quoted text\" 不应被改动。");
    expect(sanitized).toContain("```mermaid");
    expect(sanitized).toContain("A[政策支持“和”补贴] --> B[影响]");
    expect(sanitized).not.toContain("A[\"政策支持\"和\"补贴\"] --> B[影响]");
  });
});
