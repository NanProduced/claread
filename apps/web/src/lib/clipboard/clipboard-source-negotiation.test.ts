// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import { MarkdownPlugin } from "@platejs/markdown";
import {
  BaseBlockquotePlugin,
  BaseBoldPlugin,
  BaseCodePlugin,
  BaseH1Plugin,
  BaseH2Plugin,
  BaseH3Plugin,
  BaseH4Plugin,
  BaseH5Plugin,
  BaseH6Plugin,
  BaseHorizontalRulePlugin,
  BaseItalicPlugin,
  BaseStrikethroughPlugin,
} from "@platejs/basic-nodes";
import { BaseCodeBlockPlugin, BaseCodeLinePlugin } from "@platejs/code-block";
import { BaseLinkPlugin } from "@platejs/link";
import {
  BaseBulletedListPlugin,
  BaseListItemContentPlugin,
  BaseListItemPlugin,
  BaseListPlugin,
  BaseNumberedListPlugin,
} from "@platejs/list-classic";
import {
  BaseTableCellHeaderPlugin,
  BaseTableCellPlugin,
  BaseTablePlugin,
  BaseTableRowPlugin,
} from "@platejs/table";
import { createPlateEditor, createPlatePlugin } from "platejs/react";
import type { Descendant } from "platejs";

import { SourceCalloutPlugin } from "@/components/editor/plugins/source-callout-kit";
import { MARKDOWN_PLUGIN_OPTIONS } from "@/components/editor/plugins/markdown-kit";
import { InputMarkdownImagePlugin } from "@/components/editor/plugins/input-markdown-image-kit";
import { remarkPreserveUnsupported } from "@/lib/reader-plate/markdown/remark-preserve-unsupported";
import {
  NOTION_CALLOUT_DUAL_MIME_HTML,
  NOTION_CALLOUT_DUAL_MIME_PLAIN,
} from "../../../tests/e2e/fixtures/clipboard-fixtures";
import {
  hasHighConfidencePlainAside,
  negotiateClipboardSource,
} from "./clipboard-source-negotiation";
import { deserializeHybridClipboardFragment } from "./clipboard-source-fusion";

const NOTION_DUAL_MIME_HTML = `
<h2>Reader Goals</h2>
<p>Opening with <strong>strong</strong> and <em>em</em> structure.</p>
<ul><li>first point</li><li>second point</li></ul>
<p>Read the <a href="https://example.com/guide">safe link</a>.</p>
<h3>Reference list</h3>
<ol><li><a href="https://example.com/reference">Reference A</a></li><li>Reference B</li></ol>
<table><thead><tr><th>Source</th><th>Meaning</th></tr></thead><tbody><tr><td>article</td><td>rich structure</td></tr></tbody></table>
<p>&lt;aside&gt;</p>
<p>🎯</p>
<p><strong>Alignment</strong>: preserve the callout body.</p>
<p>&lt;/aside&gt;</p>
<p>Trailing paragraph remains independent.</p>
`.trim();

const NOTION_DUAL_MIME_PLAIN = `## Reader Goals

Opening with **strong** and *em* structure.

- first point
- second point

Read the [safe link](https://example.com/guide).

### Reference list

1. [Reference A](https://example.com/reference)
2. Reference B

<aside>
🎯

**Alignment**: preserve the callout body.
</aside>

Trailing paragraph remains independent.`;

function createActualFingerprintEditor() {
  return createPlateEditor({
    plugins: [
      MarkdownPlugin.configure({
        options: {
          ...MARKDOWN_PLUGIN_OPTIONS,
          remarkPlugins: [
            ...MARKDOWN_PLUGIN_OPTIONS.remarkPlugins,
            remarkPreserveUnsupported,
          ],
        },
      }),
      SourceCalloutPlugin,
      BaseH1Plugin,
      BaseH2Plugin,
      BaseH3Plugin,
      BaseH4Plugin,
      BaseH5Plugin,
      BaseH6Plugin,
      BaseBlockquotePlugin,
      BaseHorizontalRulePlugin,
      BaseBoldPlugin,
      BaseItalicPlugin,
      BaseCodePlugin,
      BaseStrikethroughPlugin,
      BaseLinkPlugin,
      BaseListPlugin,
      BaseBulletedListPlugin,
      BaseNumberedListPlugin,
      BaseListItemPlugin,
      BaseListItemContentPlugin,
      BaseCodeBlockPlugin,
      BaseCodeLinePlugin,
      BaseTablePlugin,
      BaseTableRowPlugin,
      BaseTableCellPlugin,
      BaseTableCellHeaderPlugin,
    ],
  });
}


describe("Clipboard Source Negotiation", () => {
  it("locally fuses a complete plain aside into rich HTML instead of choosing one whole MIME", () => {
    const result = negotiateClipboardSource({
      html: NOTION_DUAL_MIME_HTML,
      plain: NOTION_DUAL_MIME_PLAIN,
    });

    expect(result.kind).toBe("hybrid");
    expect(result.reason).toBe("html_plain_aside_fused");
    expect(result.html).toContain("<h2>Reader Goals</h2>");
    expect(result.html).toContain("<ul>");
    expect(result.html).toContain("https://example.com/guide");
    expect(result.html).toContain("Reference list");
    expect(result.html).toContain("&lt;aside&gt;");
    expect(result.fusion?.matches[0]?.plainAsideMarkdown).toContain("<aside>");
    expect(result.plain).toBe(NOTION_DUAL_MIME_PLAIN);
  });

  it("replaces only the matched DOM region in the Plate fragment", () => {
    const result = negotiateClipboardSource({
      html: NOTION_DUAL_MIME_HTML,
      plain: NOTION_DUAL_MIME_PLAIN,
    });
    if (result.kind !== "hybrid" || !result.fusion) {
      throw new Error("expected a hybrid clipboard plan");
    }

    const fragment = deserializeHybridClipboardFragment(
      result.html,
      result.fusion,
      {
        deserializeHtml: (body) =>
          Array.from(body.children).map((element) => ({
            type: element.tagName.toLowerCase(),
            children: [{ text: element.textContent ?? "" }],
          })),
        deserializeMarkdown: () => [
          {
            type: "source_callout",
            displayIcon: "🎯",
            children: [
              { type: "p", children: [{ text: "Alignment: preserve the callout body." }] },
            ],
          },
        ],
      },
    );

    expect(fragment).not.toBeNull();
    expect(fragment?.filter((node) => node.type === "source_callout")).toHaveLength(1);
    expect(fragment?.some((node) => node.type === "h2")).toBe(true);
    expect(fragment?.some((node) => node.type === "ul")).toBe(true);
    expect(fragment?.some((node) => node.type === "ol")).toBe(true);
    expect(fragment?.some((node) => node.type === "table")).toBe(true);
    expect(fragment?.some((node) => JSON.stringify(node).includes("<aside>"))).toBe(false);
  });

  it("uses the mounted Plate HTML and Markdown deserializers for fingerprint validation", () => {
    const editor = createActualFingerprintEditor();
    const result = negotiateClipboardSource(
      {
        html: NOTION_DUAL_MIME_HTML,
        plain: NOTION_DUAL_MIME_PLAIN,
      },
      {
        deserializeHtml: (body) =>
          editor.api.html.deserialize({ element: body }) as never,
        deserializeMarkdown: (markdown) =>
          editor.getApi(MarkdownPlugin).markdown.deserialize(markdown) as never,
      },
    );

    expect(result.kind).toBe("hybrid");
    expect(result.fusion?.matches).toHaveLength(1);
  });

  it("validates the complete e2e dual-MIME fixture with the mounted deserializers", () => {
    const editor = createActualFingerprintEditor();
    const result = negotiateClipboardSource(
      {
        html: NOTION_CALLOUT_DUAL_MIME_HTML,
        plain: NOTION_CALLOUT_DUAL_MIME_PLAIN,
      },
      {
        deserializeHtml: (body) =>
          editor.api.html.deserialize({ element: body }) as never,
        deserializeMarkdown: (markdown) =>
          editor.getApi(MarkdownPlugin).markdown.deserialize(markdown) as never,
      },
    );

    expect(result.kind).toBe("hybrid");
    expect(result.fusion?.matches).toHaveLength(2);
  });

  it("replaces the complete e2e fixture through the mounted Plate HTML seam", () => {
    const editor = createActualFingerprintEditor();
    const dependencies = {
      deserializeHtml: (body: HTMLElement) =>
        editor.api.html.deserialize({ element: body }) as never,
      deserializeMarkdown: (markdown: string) =>
        editor.getApi(MarkdownPlugin).markdown.deserialize(markdown) as never,
    };
    const result = negotiateClipboardSource(
      {
        html: NOTION_CALLOUT_DUAL_MIME_HTML,
        plain: NOTION_CALLOUT_DUAL_MIME_PLAIN,
      },
      dependencies,
    );
    if (result.kind !== "hybrid" || !result.fusion) {
      throw new Error("expected hybrid plan");
    }
    const fragment = deserializeHybridClipboardFragment(
      result.html,
      result.fusion,
      dependencies,
    );
    expect(fragment?.filter((node) => node.type === "source_callout")).toHaveLength(2);
  });

  it("keeps real HTML aside even when plain also contains an aside", () => {
    const result = negotiateClipboardSource({
      html: `<aside><p><strong>Rich title</strong></p><ul><li>item</li></ul></aside>`,
      plain: NOTION_DUAL_MIME_PLAIN,
    });

    expect(result.kind).toBe("html");
    expect(result.reason).toBe("html_structured");
    expect(result.html).toContain("<aside>");
  });

  it.each([
    ["escaped tag", "\\<aside>\nbody\n</aside>"],
    ["inline code", "Use `<aside>` for a callout."],
    ["fenced code", "```html\n<aside>\nbody\n</aside>\n```"],
    ["unclosed", "<aside>\nbody"],
    ["prose example", "Use <aside> to describe a side note."],
  ])("does not classify %s as a high-confidence aside", (_label, value) => {
    expect(hasHighConfidencePlainAside(value)).toBe(false);
  });

  it("does not let plain aside text replace rich title/list/table HTML", () => {
    const result = negotiateClipboardSource({
      html: `<h2>Title</h2><ul><li>&lt;aside&gt; is discussed</li></ul><table><tr><td>1</td></tr></table>`,
      plain: NOTION_DUAL_MIME_PLAIN,
    });

    expect(result.kind).toBe("html");
  });

  it("declines same-label callouts when safe URLs differ", () => {
    const result = negotiateClipboardSource({
      html: "<p>&lt;aside&gt;</p><p><strong>Alignment</strong>: read the <a href=\"https://trusted.example/guide\">guide</a>.</p><p>&lt;/aside&gt;</p>",
      plain: "<aside>\n**Alignment**: read the [guide](https://other.example/guide).\n</aside>",
    });

    expect(result.kind).toBe("html");
    expect(result.reason).toBe("html_aside_fusion_declined");
    expect(result.html).toContain("https://trusted.example/guide");
    expect(result.html).not.toContain("https://other.example/guide");
  });

  it("fuses an unordered list with direct text inside a callout", () => {
    const result = negotiateClipboardSource({
      html: [
        "<p>&lt;aside&gt;</p>",
        "<ul><li>Read the guide</li></ul>",
        "<p>&lt;/aside&gt;</p>",
      ].join(""),
      plain: ["<aside>", "- Read the guide", "</aside>"].join("\n"),
    });

    expect(result.kind).toBe("hybrid");
    expect(result.fusion?.matches[0]?.fingerprint.blocks).toMatchObject([
      {
        type: "list:unordered",
        children: [
          {
            type: "list_item",
            children: [{ type: "list_item_content", visibleText: "Read the guide" }],
          },
        ],
      },
    ]);
  });

  it("fuses an ordered list with Markdown ordered-list structure", () => {
    const result = negotiateClipboardSource({
      html: [
        "<p>&lt;aside&gt;</p>",
        "<ol><li>First</li><li>Second</li></ol>",
        "<p>&lt;/aside&gt;</p>",
      ].join(""),
      plain: ["<aside>", "1. First", "2. Second", "</aside>"].join("\n"),
    });

    expect(result.kind).toBe("hybrid");
    expect(result.fusion?.matches[0]?.fingerprint.blocks[0]?.type).toBe(
      "list:ordered",
    );
  });

  it("preserves strong, em, and link semantics inside list-item content", () => {
    const result = negotiateClipboardSource({
      html: [
        "<p>&lt;aside&gt;</p>",
        '<ul><li><strong>Read</strong> the <em>guide</em> at <a href="https://example.com/guide">here</a></li></ul>',
        "<p>&lt;/aside&gt;</p>",
      ].join(""),
      plain: [
        "<aside>",
        "- **Read** the *guide* at [here](https://example.com/guide)",
        "</aside>",
      ].join("\n"),
    });

    expect(result.kind).toBe("hybrid");
    expect(result.fusion?.matches[0]?.fingerprint.links).toEqual([
      { visibleText: "here", sanitizedHref: "https://example.com/guide" },
    ]);
    expect(result.fusion?.matches[0]?.fingerprint.blocks[0]?.children[0]).toMatchObject({
      type: "list_item",
      children: [
        {
          type: "list_item_content",
          marks: [],
          children: expect.arrayContaining([
            expect.objectContaining({ visibleText: "Read", marks: ["bold"] }),
            expect.objectContaining({ visibleText: "guide", marks: ["italic"] }),
            expect.objectContaining({ type: "link", linkHref: "https://example.com/guide" }),
          ]),
        },
      ],
    });
  });

  it("fuses nested lists while keeping the nested list below its list item", () => {
    const result = negotiateClipboardSource({
      html: [
        "<p>&lt;aside&gt;</p>",
        "<ul><li>Top<ul><li>Nested</li></ul></li></ul>",
        "<p>&lt;/aside&gt;</p>",
      ].join(""),
      plain: ["<aside>", "- Top", "  - Nested", "</aside>"].join("\n"),
    });

    expect(result.kind).toBe("hybrid");
    expect(result.fusion?.matches[0]?.fingerprint.blocks).toMatchObject([
      {
        type: "list:unordered",
        children: [
          {
            type: "list_item",
            children: [
              { type: "list_item_content", visibleText: "Top" },
              {
                type: "list:unordered",
                children: [
                  {
                    type: "list_item",
                    children: [{ type: "list_item_content", visibleText: "Nested" }],
                  },
                ],
              },
            ],
          },
        ],
      },
    ]);
  });

  it("declines when a list-item link URL differs even if the label matches", () => {
    const result = negotiateClipboardSource({
      html: [
        "<p>&lt;aside&gt;</p>",
        '<ul><li>Read the <a href="https://trusted.example/guide">guide</a></li></ul>',
        "<p>&lt;/aside&gt;</p>",
      ].join(""),
      plain: [
        "<aside>",
        "- Read the [guide](https://other.example/guide)",
        "</aside>",
      ].join("\n"),
    });

    expect(result.kind).toBe("html");
    expect(result.reason).toBe("html_aside_fusion_declined");
    expect(result.html).toContain("https://trusted.example/guide");
    expect(result.html).not.toContain("https://other.example/guide");
  });

  it("declines when list-item visible text differs", () => {
    const result = negotiateClipboardSource({
      html: [
        "<p>&lt;aside&gt;</p>",
        "<ul><li>Read the guide</li></ul>",
        "<p>&lt;/aside&gt;</p>",
      ].join(""),
      plain: ["<aside>", "- Read this guide", "</aside>"].join("\n"),
    });

    expect(result.kind).toBe("html");
    expect(result.reason).toBe("html_aside_fusion_declined");
  });

  it("declines when ordered HTML is paired with unordered Markdown", () => {
    const result = negotiateClipboardSource({
      html: [
        "<p>&lt;aside&gt;</p>",
        "<ol><li>First</li></ol>",
        "<p>&lt;/aside&gt;</p>",
      ].join(""),
      plain: ["<aside>", "- First", "</aside>"].join("\n"),
    });

    expect(result.kind).toBe("html");
    expect(result.reason).toBe("html_aside_fusion_declined");
  });

  it("fuses two callouts when the second one contains a nested list", () => {
    const result = negotiateClipboardSource({
      html: [
        "<p>&lt;aside&gt;</p>",
        "<ul><li>First item</li></ul>",
        "<p>&lt;/aside&gt;</p>",
        "<p>Between</p>",
        "<p>&lt;aside&gt;</p>",
        "<ol><li>Second item<ul><li>Nested item</li></ul></li></ol>",
        "<p>&lt;/aside&gt;</p>",
      ].join(""),
      plain: [
        "<aside>",
        "- First item",
        "</aside>",
        "",
        "Between",
        "",
        "<aside>",
        "1. Second item",
        "   - Nested item",
        "</aside>",
      ].join("\n"),
    });

    expect(result.kind).toBe("hybrid");
    expect(result.fusion?.matches).toHaveLength(2);
    expect(result.fusion?.matches.map((match) => match.documentOrder)).toEqual([0, 1]);
  });

  it("declines the entire plan when one of two list fingerprints differs", () => {
    const result = negotiateClipboardSource({
      html: [
        "<p>&lt;aside&gt;</p><ul><li>First</li></ul><p>&lt;/aside&gt;</p>",
        "<p>&lt;aside&gt;</p><ol><li>Second</li></ol><p>&lt;/aside&gt;</p>",
      ].join(""),
      plain: [
        "<aside>",
        "- First",
        "</aside>",
        "",
        "<aside>",
        "- Second",
        "</aside>",
      ].join("\n"),
    });

    expect(result.kind).toBe("html");
    expect(result.reason).toBe("html_aside_fusion_declined");
    expect(result.fusion).toBeUndefined();
    expect(result.html).toContain("<ol>");
    expect(result.html).toContain("Second");
  });

  it("declines when the link count differs even if visible text is equal", () => {
    const result = negotiateClipboardSource({
      html: "<p>&lt;aside&gt;</p><p>Read the <a href=\"https://example.com/guide\">guide</a>.</p><p>&lt;/aside&gt;</p>",
      plain: "<aside>\nRead the guide at https://example.com/guide.\n</aside>",
    });

    expect(result.kind).toBe("html");
    expect(result.reason).toBe("html_aside_fusion_declined");
  });

  it("declines unsafe URL candidates instead of treating them as equal text", () => {
    const result = negotiateClipboardSource({
      html: "<p>&lt;aside&gt;</p><p><a href=\"javascript:alert(1)\">guide</a></p><p>&lt;/aside&gt;</p>",
      plain: "<aside>\n[guide](https://example.com/guide)\n</aside>",
    });

    expect(result.kind).toBe("html");
    expect(result.reason).toBe("html_aside_fusion_declined");
    expect(result.html).not.toMatch(/javascript:/i);
  });

  it("does not mask literal underscore or code-marker differences", () => {
    const result = negotiateClipboardSource({
      html: "<p>&lt;aside&gt;</p><p>literal a_b and backtick `text`</p><p>&lt;/aside&gt;</p>",
      plain: "<aside>\nliteral ab and backtick text\n</aside>",
    });

    expect(result.kind).toBe("html");
    expect(result.reason).toBe("html_aside_fusion_declined");
  });

  it("fuses two complete callouts in document order while preserving rich neighbors", () => {
    const html = [
      "<h2>Two callouts</h2>",
      "<p>Before the callouts.</p>",
      "<p>&lt;aside&gt;</p><p>🎯</p>",
      "<p><strong>Alignment</strong>: keep the <a href=\"https://example.com/one\">first guide</a>.</p>",
      "<p>&lt;/aside&gt;</p>",
      "<ul><li>Between callouts</li></ul>",
      "<p>&lt;aside&gt;</p><p>⚠️</p>",
      "<p><em>Warning</em>: keep the <a href=\"https://example.com/two\">second guide</a>.</p>",
      "<p>&lt;/aside&gt;</p>",
      "<table><tr><th>After</th></tr><tr><td>table</td></tr></table>",
      "<h3>Reference list</h3>",
      "<ol><li><a href=\"https://example.com/reference\">Reference</a></li></ol>",
      "<p>Trailing paragraph remains independent.</p>",
    ].join("");
    const plain = [
      "## Two callouts",
      "",
      "Before the callouts.",
      "",
      "<aside>",
      "🎯",
      "",
      "**Alignment**: keep the [first guide](https://example.com/one).",
      "</aside>",
      "",
      "- Between callouts",
      "",
      "<aside>",
      "⚠️",
      "",
      "*Warning*: keep the [second guide](https://example.com/two).",
      "</aside>",
      "",
      "| After |",
      "| --- |",
      "| table |",
      "",
      "### Reference list",
      "",
      "1. [Reference](https://example.com/reference)",
      "",
      "Trailing paragraph remains independent.",
    ].join("\n");
    const result = negotiateClipboardSource({ html, plain });

    expect(result.kind).toBe("hybrid");
    expect(result.reason).toBe("html_plain_aside_fused");
    expect(result.fusion?.matches).toHaveLength(2);
    expect(result.fusion?.matches.map((match) => match.documentOrder)).toEqual([0, 1]);
    expect(result.fusion?.matches[0]?.plainAsideMarkdown).toContain("🎯");
    expect(result.fusion?.matches[1]?.plainAsideMarkdown).toContain("⚠️");
    expect(result.html).toContain("<h2>Two callouts</h2>");
    expect(result.html).toContain("<ul>");
    expect(result.html).toContain("<table>");
    expect(result.html).toContain("Reference list");
    expect(result.html).toContain("Trailing paragraph remains independent.");
  });

  it("does not partially fuse when one of two callouts has a mismatched URL", () => {
    const html = [
      "<p>&lt;aside&gt;</p><p>first <a href=\"https://example.com/one\">guide</a></p><p>&lt;/aside&gt;</p>",
      "<p>&lt;aside&gt;</p><p>second <a href=\"https://example.com/two\">guide</a></p><p>&lt;/aside&gt;</p>",
    ].join("");
    const plain = [
      "<aside>",
      "first [guide](https://example.com/one)",
      "</aside>",
      "",
      "<aside>",
      "second [guide](https://other.example/two)",
      "</aside>",
    ].join("\n");
    const result = negotiateClipboardSource({ html, plain });

    expect(result.kind).toBe("html");
    expect(result.reason).toBe("html_aside_fusion_declined");
    expect(result.fusion).toBeUndefined();
    expect(result.html).toContain("https://example.com/one");
    expect(result.html).toContain("https://example.com/two");
  });

  it("fuses duplicate callout bodies deterministically by order", () => {
    const html = [
      "<p>Before</p>",
      "<p>&lt;aside&gt;</p><p>🎯</p><p>Same <a href=\"https://example.com/same\">guide</a>.</p><p>&lt;/aside&gt;</p>",
      "<p>&lt;aside&gt;</p><p>🎯</p><p>Same <a href=\"https://example.com/same\">guide</a>.</p><p>&lt;/aside&gt;</p>",
      "<p>After</p>",
    ].join("");
    const plain = [
      "Before",
      "",
      "<aside>",
      "🎯",
      "",
      "Same [guide](https://example.com/same).",
      "</aside>",
      "",
      "<aside>",
      "🎯",
      "",
      "Same [guide](https://example.com/same).",
      "</aside>",
      "",
      "After",
    ].join("\n");
    const result = negotiateClipboardSource({ html, plain });

    expect(result.kind).toBe("hybrid");
    expect(result.fusion?.matches).toHaveLength(2);
    expect(result.fusion?.matches.map((match) => match.plainAsideMarkdown.match(/🎯/gu)?.length)).toEqual([1, 1]);
  });

  it("replaces all validated multi-callout slots without swapping icons or trailing text", () => {
    const html = [
      "<h2>Before</h2>",
      "<p>&lt;aside&gt;</p><p>🎯</p><p>First body</p><p>&lt;/aside&gt;</p>",
      "<ul><li>Between</li></ul>",
      "<p>&lt;aside&gt;</p><p>⚠️</p><p>Second body</p><p>&lt;/aside&gt;</p>",
      "<p>Trailing</p>",
    ].join("");
    const plain = [
      "## Before",
      "",
      "<aside>",
      "🎯",
      "",
      "First body",
      "</aside>",
      "",
      "- Between",
      "",
      "<aside>",
      "⚠️",
      "",
      "Second body",
      "</aside>",
      "",
      "Trailing",
    ].join("\n");
    const negotiated = negotiateClipboardSource({ html, plain });
    if (negotiated.kind !== "hybrid" || !negotiated.fusion) {
      throw new Error("expected a multi-callout hybrid plan");
    }

    const fragment = deserializeHybridClipboardFragment(
      negotiated.html,
      negotiated.fusion,
      {
        deserializeHtml: (body) =>
          Array.from(body.children).map((element) => ({
            type: element.tagName.toLowerCase(),
            children: [{ text: element.textContent ?? "" }],
          })),
        deserializeMarkdown: (markdown) => [
          {
            type: "source_callout",
            displayIcon: markdown.includes("⚠️") ? "⚠️" : "🎯",
            children: [
              {
                type: "p",
                children: [
                  {
                    text: markdown.includes("Second body")
                      ? "Second body"
                      : "First body",
                  },
                ],
              },
            ],
          },
        ],
      },
    );

    const callouts = fragment?.filter((node) => node.type === "source_callout") ?? [];
    expect(callouts).toHaveLength(2);
    expect((callouts[0] as { displayIcon?: string }).displayIcon).toBe("🎯");
    expect((callouts[1] as { displayIcon?: string }).displayIcon).toBe("⚠️");
    expect(fragment?.map((node) => node.type)).toEqual([
      "h2",
      "source_callout",
      "ul",
      "source_callout",
      "p",
    ]);
  });
});

// ---------------------------------------------------------------------------
// G1P-B-A dual-MIME Layer A 边界：HTML 仍是结构 truth，只让 HTML 自身已有的
// image/code language 不丢；plain 额外/冲突字段一律不补（O-B1 属 G1P-B-B，
// 本轮不实现）；plain URL 永不进入 HTML img；callout fusion 不变。
// ---------------------------------------------------------------------------

describe("G1P-B-A dual-MIME Layer A boundary", () => {
  it("dual-MIME image：HTML 自身 src/alt/title 保真，plain 冲突字段不覆盖不补齐", () => {
    const result = negotiateClipboardSource({
      html: `<p>body</p><img src="https://example.com/html.png" alt="html-alt" title="html-title">`,
      plain: `body\n\n![plain-alt](https://example.com/plain.png "plain-title")`,
    });

    // HTML 仍是结构 truth，不整篇退回 plain、不触发 fusion
    expect(result.kind).toBe("html");
    expect(result.fusion).toBeUndefined();

    const editor = createPlateEditor({
      plugins: [
        InputMarkdownImagePlugin,
        createPlatePlugin({ key: "p", node: { isElement: true } }),
      ],
    });
    const fragment = editor.api.html.deserialize({
      element: result.html,
    }) as Descendant[];
    const json = JSON.stringify(fragment);
    // HTML 自身字段保真（typed img）
    expect(json).toContain('"url":"https://example.com/html.png"');
    expect(json).toContain('"text":"html-alt"');
    expect(json).toContain('"title":"html-title"');
    // plain 冲突字段未覆盖/漏入 HTML fragment（plain URL 永不进 HTML img）
    expect(json).not.toContain("plain.png");
    expect(json).not.toContain("plain-alt");
    expect(json).not.toContain("plain-title");
  });

  it("dual-MIME code language：HTML 保留自身 language 信号，plain 冲突语言不覆盖", () => {
    const result = negotiateClipboardSource({
      html: `<p>intro</p><pre><code class="language-python">x = 1</code></pre>`,
      plain: "intro\n\n```typescript\nconst x = 1;\n```",
    });

    expect(result.kind).toBe("html");
    expect(result.fusion).toBeUndefined();
    // result.html 保留 HTML 自身 language-* class（G1P-B-A parser 的输入）；
    // plain 的 typescript fence 语言不进入 HTML
    expect(result.html).toContain("language-python");
    expect(result.html).not.toContain("typescript");
  });
});
