/** @vitest-environment jsdom */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { CalloutMarkdownRenderer } from "./CalloutMarkdownRenderer";
import { deserializeMarkdownToBlocks } from "@/lib/reader-plate/markdown/deserialize";
import type { Descendant } from "platejs";

function renderMarkdown(markdown: string) {
  const nodes = deserializeMarkdownToBlocks(markdown);
  return render(<CalloutMarkdownRenderer nodes={nodes} />);
}

describe("CalloutMarkdownRenderer", () => {
  it("renders plain text paragraph", () => {
    const { container } = renderMarkdown("Hello world");
    expect(container.textContent).toContain("Hello world");
    expect(container.querySelector("p")).not.toBeNull();
  });

  it("renders bold markdown as <strong>", () => {
    const { container } = renderMarkdown("**bold text**");
    const strong = container.querySelector("strong");
    expect(strong).not.toBeNull();
    expect(strong?.textContent).toBe("bold text");
  });

  it("renders italic markdown as <em>", () => {
    const { container } = renderMarkdown("*italic text*");
    const em = container.querySelector("em");
    expect(em).not.toBeNull();
    expect(em?.textContent).toBe("italic text");
  });

  it("renders inline code as <code>", () => {
    const { container } = renderMarkdown("`code here`");
    const code = container.querySelector("code");
    expect(code).not.toBeNull();
    expect(code?.textContent).toBe("code here");
  });

  it("renders GFM strikethrough as line-through span", () => {
    const { container } = renderMarkdown("~~deleted~~");
    const span = container.querySelector("span.line-through");
    expect(span).not.toBeNull();
    expect(span?.textContent).toBe("deleted");
  });

  it("renders heading as corresponding h tag", () => {
    const { container } = renderMarkdown("## Heading Two");
    const h2 = container.querySelector("h2");
    expect(h2).not.toBeNull();
    expect(h2?.textContent).toBe("Heading Two");
  });

  it("renders unordered list with li children", () => {
    const { container } = renderMarkdown("- alpha\n- beta");
    const ul = container.querySelector("ul");
    expect(ul).not.toBeNull();
    const items = ul?.querySelectorAll("li");
    expect(items?.length).toBe(2);
    expect(items?.[0]?.textContent).toBe("alpha");
    expect(items?.[1]?.textContent).toBe("beta");
  });

  it("renders ordered list with li children", () => {
    const { container } = renderMarkdown("1. first\n2. second");
    const ol = container.querySelector("ol");
    expect(ol).not.toBeNull();
    const items = ol?.querySelectorAll("li");
    expect(items?.length).toBe(2);
    expect(items?.[0]?.textContent).toBe("first");
    expect(items?.[1]?.textContent).toBe("second");
  });

  it("renders blockquote", () => {
    const { container } = renderMarkdown("> quoted text");
    const bq = container.querySelector("blockquote");
    expect(bq).not.toBeNull();
    expect(bq?.textContent).toContain("quoted text");
  });

  it("renders empty nodes array without crashing", () => {
    const { container } = render(<CalloutMarkdownRenderer nodes={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders unknown node type as fallback div", () => {
    const unknownNode = {
      type: "custom_unknown_type",
      children: [{ text: "mystery" }],
    } as unknown as Descendant;
    const { container } = render(
      <CalloutMarkdownRenderer nodes={[unknownNode]} />,
    );
    expect(container.textContent).toContain("mystery");
  });

  it("renders mixed bold + italic nested marks", () => {
    const { container } = renderMarkdown("***both***");
    const strong = container.querySelector("strong");
    const em = strong?.querySelector("em");
    expect(strong).not.toBeNull();
    expect(em).not.toBeNull();
    expect(em?.textContent).toBe("both");
  });
});
