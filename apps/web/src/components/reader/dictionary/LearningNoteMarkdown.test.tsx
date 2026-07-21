/** @vitest-environment jsdom */

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  LEARNING_NOTE_ALLOWED_ELEMENTS,
  LearningNoteMarkdown,
} from "./LearningNoteMarkdown";

describe("LearningNoteMarkdown", () => {
  it("renders the allowed Markdown subset", () => {
    const { container } = render(
      <LearningNoteMarkdown
        markdown={
          "常见搭配：`give up` + 名词。\n\n- **不要**写成 give uping\n- 可接动名词"
        }
      />,
    );

    const root = container.querySelector('[data-testid="learning-note-markdown"]');
    expect(root).toBeTruthy();

    // Inline code (Streamdown uses data-streamdown="inline-code" on <code>)
    const code = root?.querySelector('code, [data-streamdown="inline-code"]');
    expect(code?.textContent).toBe("give up");

    // Bold (Streamdown may render as <strong> or span[data-streamdown=strong])
    const strong = root?.querySelector(
      'strong, b, [data-streamdown="strong"]',
    );
    expect(strong).toBeTruthy();
    expect(strong?.textContent).toContain("不要");

    // Unordered list
    expect(root?.querySelector("ul")).toBeTruthy();
    expect(root?.querySelectorAll("li").length).toBeGreaterThanOrEqual(2);

    expect(root?.textContent).toContain("常见搭配");
    expect(root?.textContent).toContain("可接动名词");
  });

  it("does not emit DOM for headings, links, images, tables, blockquotes, ordered lists, or code blocks", () => {
    const { container } = render(
      <LearningNoteMarkdown
        markdown={[
          "# Heading should not be h1",
          "",
          "See [link](https://example.com/path) and ![alt](https://example.com/x.png).",
          "",
          "> quote line",
          "",
          "1. ordered one",
          "2. ordered two",
          "",
          "| a | b |",
          "| --- | --- |",
          "| 1 | 2 |",
          "",
          "```",
          "const x = 1;",
          "```",
          "",
          "<script>alert(1)</script>",
          "",
          "Safe **bold** and `code` remain.",
        ].join("\n")}
      />,
    );

    const root = container.querySelector('[data-testid="learning-note-markdown"]');
    expect(root).toBeTruthy();

    // Forbidden structure tags must not appear
    expect(root?.querySelector("h1, h2, h3, h4, h5, h6")).toBeNull();
    expect(root?.querySelector("a")).toBeNull();
    expect(root?.querySelector("img")).toBeNull();
    expect(root?.querySelector("table, thead, tbody, tr, th, td")).toBeNull();
    expect(root?.querySelector("blockquote")).toBeNull();
    expect(root?.querySelector("ol")).toBeNull();
    expect(root?.querySelector("pre")).toBeNull();
    expect(root?.querySelector("script")).toBeNull();

    // Allowed subset still works on the same payload
    expect(
      root?.querySelector('strong, b, [data-streamdown="strong"]')?.textContent,
    ).toContain("bold");
    const codes = Array.from(
      root?.querySelectorAll('code, [data-streamdown="inline-code"]') ?? [],
    );
    expect(codes.some((node) => node.textContent === "code")).toBe(true);
    // Fenced block body must not remain as a code element
    expect(codes.every((node) => !node.textContent?.includes("const x"))).toBe(
      true,
    );
  });

  it("returns null for empty input", () => {
    const { container } = render(<LearningNoteMarkdown markdown="   " />);
    expect(container.querySelector('[data-testid="learning-note-markdown"]')).toBeNull();
  });

  it("exports the contract allowlist", () => {
    expect(LEARNING_NOTE_ALLOWED_ELEMENTS).toEqual([
      "p",
      "strong",
      "b",
      "code",
      "ul",
      "li",
      "br",
    ]);
  });
});
