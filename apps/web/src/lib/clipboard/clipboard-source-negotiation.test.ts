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
import { markdownTextInputPlugins } from "@/app/(private)/app/read/MarkdownTextInput";
import { deserializeMarkdownToBlocksWithStatus } from "@/lib/reader-plate/markdown/deserialize";
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
// image/code language 不丢；plain 额外/冲突字段一律不补（plain 侧独立字段
// 补齐不在此边界）；plain URL 永不进入 HTML img；callout fusion 不变。
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

// ---------------------------------------------------------------------------
// G1P-B-B · bounded non-URL companion-field fusion。所有用例都经公开 seam：
// negotiateClipboardSource → deserializeHybridClipboardFragment，且两侧
// deserializer 均为实际 mounted input editor（markdownTextInputPlugins）。
// HTML 始终是结构 truth；只补真正 missing 的 alt/title/code language。
// ---------------------------------------------------------------------------

function createMountedInputEditor() {
  return createPlateEditor({ plugins: markdownTextInputPlugins });
}

function createMountedDependencies() {
  const editor = createMountedInputEditor();
  return {
    deserializeHtml: (body: HTMLElement) =>
      editor.api.html.deserialize({ element: body }) as never,
    deserializeMarkdown: (markdown: string) =>
      editor.getApi(MarkdownPlugin).markdown.deserialize(markdown) as never,
  };
}

/** negotiate + fragment 应用全链路（公开 seam）。 */
function fuseClipboardFragment(html: string, plain: string) {
  const dependencies = createMountedDependencies();
  const result = negotiateClipboardSource({ html, plain }, dependencies);
  if (result.kind !== "hybrid" || !result.fusion) {
    return { result, fragment: null };
  }
  return {
    result,
    fragment: deserializeHybridClipboardFragment(
      result.html,
      result.fusion,
      dependencies,
    ),
  };
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

function allLeafText(nodes: Descendant[]): string {
  let text = "";
  const walk = (ns: Descendant[]) => {
    for (const n of ns) {
      const node = n as Record<string, unknown>;
      if (typeof node.text === "string") text += node.text;
      if (Array.isArray(node.children)) {
        walk(node.children as Descendant[]);
      }
    }
  };
  walk(nodes);
  return text;
}

function codeBlockBody(node: Record<string, unknown>): string {
  return (
    (node.children as Array<{ children?: Array<{ text?: string }> }> | undefined)
      ?.map((line) => line.children?.map((c) => c.text ?? "").join("") ?? "")
      .join("\n") ?? ""
  );
}

describe("G1P-B-B image missing-field fusion（Sliding A）", () => {
  it.each([
{
        label: "alt missing",
        html: `<p>before <img src="https://example.com/a.png"> after</p>`,
        plain: `before ![plain-alt](https://example.com/a.png) after`,
        plainAlt: "plain-alt",
        htmlAlt: "",
        expectedAlt: "plain-alt",
        expectedTitle: undefined as string | undefined,
      },
    {
      label: "title missing",
      html: `<p>before <img src="https://example.com/a.png" alt="html-alt"> after</p>`,
      plain: `before ![html-alt](https://example.com/a.png "plain-title") after`,
      plainAlt: "html-alt",
      htmlAlt: "html-alt",
      expectedAlt: undefined as string | undefined,
      expectedTitle: "plain-title",
    },
    {
      label: "alt and title both missing",
      html: `<p>before <img src="https://example.com/a.png"> after</p>`,
      plain: `before ![plain-alt](https://example.com/a.png "plain-title") after`,
      plainAlt: "plain-alt",
      htmlAlt: "",
      expectedAlt: "plain-alt",
      expectedTitle: "plain-title",
    },
  ])(
    "$label：只补 missing 字段，URL 保持 HTML 原值，数量/path/邻接文本不变",
    ({ html, plain, plainAlt, htmlAlt, expectedAlt, expectedTitle }) => {
      const { result, fragment } = fuseClipboardFragment(html, plain);

      // 正向存在性：HTML fragment 确实含 img；plain deserializer 确实产出
      // companion img 与候选字段。
      const htmlEditor = createMountedInputEditor();
      const htmlFragment = htmlEditor.api.html.deserialize({
        element: new DOMParser().parseFromString(html, "text/html").body,
      }) as Descendant[];
      expect(collectByType(htmlFragment, "img")).toHaveLength(1);
      const plainEditor = createMountedInputEditor();
      const plainFragment = plainEditor
        .getApi(MarkdownPlugin)
        .markdown.deserialize(plain) as Descendant[];
      const plainImgs = collectByType(plainFragment, "img");
      expect(plainImgs).toHaveLength(1);
      expect((plainImgs[0] as { caption?: Descendant[] }).caption).toEqual([
        { text: plainAlt },
      ]);

      // 融合后：kind hybrid、字段补齐、URL 与邻接结构守恒。
      expect(result.kind).toBe("hybrid");
      expect(result.reason).toBe("html_plain_fields_fused");
      expect(fragment).not.toBeNull();
      const imgs = collectByType(fragment as Descendant[], "img");
      expect(imgs).toHaveLength(1);
      const img = imgs[0] as {
        url?: string;
        caption?: Descendant[];
        title?: string;
      };
      expect(img.url).toBe("https://example.com/a.png");
      expect(img.caption).toEqual([{ text: expectedAlt ?? htmlAlt }]);
      if (expectedTitle !== undefined) {
        expect(img.title).toBe(expectedTitle);
      } else {
        expect(img).not.toHaveProperty("title");
      }
      // 邻接正文不变（HTML 反序列化在 inline void 旁折叠空白，与未融合
      // fragment 完全一致）；字段值不进正文 leaf、plain URL 未写入任何位置
      const leafText = allLeafText(fragment as Descendant[]);
      expect(leafText).toBe("before after");
      expect(leafText).not.toContain("plain-alt");
      expect(leafText).not.toContain("plain-title");
    },
  );

  it("markdown serialize 观察：补齐的 alt/title 进 fence 风格输出，正文零漂移", () => {
    const { fragment } = fuseClipboardFragment(
      `<p>before <img src="https://example.com/a.png"> after</p>`,
      `before ![plain-alt](https://example.com/a.png "plain-title") after`,
    );
    expect(fragment).not.toBeNull();
    const editor = createMountedInputEditor();
    const md = editor
      .getApi(MarkdownPlugin)
      .markdown.serialize({ value: fragment as Descendant[] });
    expect(md).toContain("![plain-alt](https://example.com/a.png \"plain-title\")");
    expect(md).toContain("before");
    expect(md).toContain("after");
  });
});

describe("G1P-B-B code language fusion（Slice B）", () => {
  it("lang 缺失时从唯一对应 fence 补齐；code body 含内部空行逐字守恒", () => {
    const html = `<pre><code>line1\n\nline3</code></pre>`;
    const plain = "```python\nline1\n\nline3\n```";

    // 正向存在性：HTML code_block 存在且无 lang；plain fence 有 lang。
    const htmlEditor = createMountedInputEditor();
    const htmlFragment = htmlEditor.api.html.deserialize({
      element: new DOMParser().parseFromString(html, "text/html").body,
    }) as Descendant[];
    const htmlBlocks = collectByType(htmlFragment, "code_block");
    expect(htmlBlocks).toHaveLength(1);
    expect(htmlBlocks[0]).not.toHaveProperty("lang");
    const plainEditor = createMountedInputEditor();
    const plainBlocks = collectByType(
      plainEditor.getApi(MarkdownPlugin).markdown.deserialize(plain) as Descendant[],
      "code_block",
    );
    expect(plainBlocks).toHaveLength(1);
    expect((plainBlocks[0] as { lang?: string }).lang).toBe("python");

    const { result, fragment } = fuseClipboardFragment(html, plain);
    expect(result.kind).toBe("hybrid");
    expect(result.reason).toBe("html_plain_fields_fused");
    expect(fragment).not.toBeNull();
    const blocks = collectByType(fragment as Descendant[], "code_block");
    expect(blocks).toHaveLength(1);
    expect(blocks[0]).toMatchObject({ type: "code_block", lang: "python" });
    expect(codeBlockBody(blocks[0])).toBe("line1\n\nline3");
    // 节点数量与位置不变：仍是三个 code_line
    expect((blocks[0].children as unknown[]).length).toBe(3);
    expect(fragment).toHaveLength(1);

    const editor = createMountedInputEditor();
    const md = editor
      .getApi(MarkdownPlugin)
      .markdown.serialize({ value: fragment as Descendant[] });
    expect(md).toContain("```python");
    expect(md).toContain("line1");
    expect(md).toContain("line3");
  });

  it("HTML lang 已存在、plain 冲突：HTML 胜出，不覆盖", () => {
    const { result, fragment } = fuseClipboardFragment(
      `<pre><code class="language-python">x = 1</code></pre>`,
      "```typescript\nx = 1\n```",
    );
    expect(result.kind).toBe("html");
    expect(fragment).toBeNull();
    expect(result.html).toContain("language-python");
    expect(result.html).not.toContain("typescript");
  });
});

describe("G1P-B-B trust-boundary guards（Slice C）", () => {
  it("HTML alt=\"\" 是显式空值：plain alt 不得覆盖", () => {
    const { result, fragment } = fuseClipboardFragment(
      `<p>before <img src="https://example.com/a.png" alt=""> after</p>`,
      `before ![plain-alt](https://example.com/a.png) after`,
    );
    expect(result.kind).toBe("html");
    expect(fragment).toBeNull();
    // sanitizer 后 DOM hasAttribute("alt") 仍为 true：显式空值
    const dom = new DOMParser().parseFromString(result.html, "text/html");
    const domImg = dom.querySelector("img");
    expect(domImg?.hasAttribute("alt")).toBe(true);
  });

  it("HTML title=\"\" 是显式空值：plain title 不得覆盖", () => {
    const { result, fragment } = fuseClipboardFragment(
      `<p>before <img src="https://example.com/a.png" alt="a" title=""> after</p>`,
      `before ![a](https://example.com/a.png "plain-title") after`,
    );
    expect(result.kind).toBe("html");
    expect(fragment).toBeNull();
    expect(result.html).toContain('title=""');
  });

  it("HTML alt/title 已存在且与 plain 冲突：不融合", () => {
    const { result, fragment } = fuseClipboardFragment(
      `<p>before <img src="https://example.com/a.png" alt="html-alt" title="html-title"> after</p>`,
      `before ![plain-alt](https://example.com/a.png "plain-title") after`,
    );
    expect(result.kind).toBe("html");
    expect(fragment).toBeNull();
  });

  it("HTML missing src + safe plain URL：不补 URL（fail closed，alt 也不补）", () => {
    const html = `<p>before <img alt="x"> after</p>`;
    const plain = `before ![plain-alt](https://example.com/a.png) after`;
    // 正向存在性：HTML fragment 确有 img；plain 有 companion img + URL。
    const htmlEditor = createMountedInputEditor();
    const htmlFragment = htmlEditor.api.html.deserialize({
      element: new DOMParser().parseFromString(html, "text/html").body,
    }) as Descendant[];
    const htmlImgs = collectByType(htmlFragment, "img");
    expect(htmlImgs).toHaveLength(1);
    expect(htmlImgs[0]).not.toHaveProperty("url");
    const plainEditor = createMountedInputEditor();
    const plainImgs = collectByType(
      plainEditor.getApi(MarkdownPlugin).markdown.deserialize(plain) as Descendant[],
      "img",
    );
    expect(plainImgs).toHaveLength(1);
    expect((plainImgs[0] as { url?: string }).url).toBe(
      "https://example.com/a.png",
    );

    const { result, fragment } = fuseClipboardFragment(html, plain);
    expect(result.kind).toBe("html");
    expect(fragment).toBeNull();
    expect(result.html).not.toContain("src=");
  });

  it("sanitizer 摘除 unsafe src + safe plain URL：不恢复原 URL、不用 plain URL", () => {
    const html = `<p>before <img src="data:image/png;base64,AAAA" alt="kept"> after</p>`;
    const plain = `before ![plain-alt](https://example.com/a.png) after`;
    const { result, fragment } = fuseClipboardFragment(html, plain);
    expect(result.kind).toBe("html");
    expect(fragment).toBeNull();
    expect(result.html).not.toContain("src=");
    expect(result.html).not.toContain("data:image");
  });

  it("safe HTML URL A + plain URL B：URL 冲突，不融合，保留 A", () => {
    const html = `<p>before <img src="https://example.com/A.png"> after</p>`;
    const plain = `before ![plain-alt](https://example.com/B.png) after`;
    const { result, fragment } = fuseClipboardFragment(html, plain);
    expect(result.kind).toBe("html");
    expect(fragment).toBeNull();
    expect(result.html).toContain("https://example.com/A.png");
    expect(result.html).not.toContain("B.png");
  });

  it("通过 loadability 的 plain URL 仍不得进入 img url", () => {
    const html = `<p>before <img alt="x"> after</p>`;
    const plain = `before ![plain-alt](https://loadable.example.com/i.png) after`;
    const { result, fragment } = fuseClipboardFragment(html, plain);
    expect(result.kind).toBe("html");
    expect(fragment).toBeNull();
    expect(result.html).not.toContain("loadable.example.com");
    const dom = new DOMParser().parseFromString(result.html, "text/html");
    expect(dom.querySelector("img[src]")).toBeNull();
  });

  it("重复相同图片无法唯一消歧：整个重复组不融合", () => {
    const html =
      `<p><img src="https://example.com/dup.png"></p>` +
      `<p><img src="https://example.com/dup.png"></p>`;
    const plain = `![dup](https://example.com/dup.png) ![dup](https://example.com/dup.png)`;
    // 正向存在性：两侧各有至少两个候选 img。
    const htmlEditor = createMountedInputEditor();
    const htmlFragment = htmlEditor.api.html.deserialize({
      element: new DOMParser().parseFromString(html, "text/html").body,
    }) as Descendant[];
    expect(collectByType(htmlFragment, "img")).toHaveLength(2);
    const plainEditor = createMountedInputEditor();
    const plainImgs = collectByType(
      plainEditor.getApi(MarkdownPlugin).markdown.deserialize(plain) as Descendant[],
      "img",
    );
    expect(plainImgs.length).toBeGreaterThanOrEqual(2);

    const { result, fragment } = fuseClipboardFragment(html, plain);
    expect(result.kind).toBe("html");
    expect(fragment).toBeNull();
  });

  it("重复相同 code body 无法唯一消歧：整个重复组不融合", () => {
    const html =
      `<pre><code>a\n\nb</code></pre>` +
      `<pre><code>a\n\nb</code></pre>`;
    const plain = "```go\na\n\nb\n```\n\ntext\n\n```go\na\n\nb\n```";
    // 正向存在性：两侧各有至少两个候选 code_block。
    const htmlEditor = createMountedInputEditor();
    const htmlFragment = htmlEditor.api.html.deserialize({
      element: new DOMParser().parseFromString(html, "text/html").body,
    }) as Descendant[];
    expect(collectByType(htmlFragment, "code_block")).toHaveLength(2);
    const plainEditor = createMountedInputEditor();
    const plainBlocks = collectByType(
      plainEditor.getApi(MarkdownPlugin).markdown.deserialize(plain) as Descendant[],
      "code_block",
    );
    expect(plainBlocks.length).toBeGreaterThanOrEqual(2);

    const { result, fragment } = fuseClipboardFragment(html, plain);
    expect(result.kind).toBe("html");
    expect(fragment).toBeNull();
  });

  it("某字段冲突时，不部分补同一节点的另一个 missing 字段", () => {
    const html = `<p>before <img src="https://example.com/A.png"> after</p>`;
    const plain = `before ![plain-alt](https://example.com/B.png "plain-title") after`;
    const { result, fragment } = fuseClipboardFragment(html, plain);
    expect(result.kind).toBe("html");
    expect(fragment).toBeNull();
    // alt/title 均未被补（URL 冲突 → 整节点 fail closed）
    expect(result.html).toContain("https://example.com/A.png");
    expect(result.html).not.toContain("plain-alt");
    expect(result.html).not.toContain("plain-title");
  });

  it("rich heading/list/table/marks/link 结构不被 plain 替换；字段融合只补缺失项", () => {
    const html = [
      `<h2>Title</h2>`,
      `<p>before <img src="https://example.com/a.png"> after</p>`,
      `<ul><li>first <strong>strong</strong></li></ul>`,
      `<table><tr><th>H</th></tr><tr><td>cell</td></tr></table>`,
      `<p>Read the <a href="https://example.com/guide">guide</a>.</p>`,
      `<pre><code>print(1)</code></pre>`,
    ].join("");
    const plain = [
      `## Title`,
      ``,
      `before ![plain-alt](https://example.com/a.png) after`,
      ``,
      `- first **strong**`,
      ``,
      `| H |`,
      `| --- |`,
      `| cell |`,
      ``,
      `Read the [guide](https://example.com/guide).`,
      ``,
      "```python\nprint(1)\n```",
    ].join("\n");
    const { result, fragment } = fuseClipboardFragment(html, plain);
    expect(result.kind).toBe("hybrid");
    expect(result.reason).toBe("html_plain_fields_fused");
    expect(fragment).not.toBeNull();
    const nodes = fragment as Descendant[];
    const imgs = collectByType(nodes, "img");
    expect(imgs).toHaveLength(1);
    expect(imgs[0]).toMatchObject({
      url: "https://example.com/a.png",
      caption: [{ text: "plain-alt" }],
    });
    const blocks = collectByType(nodes, "code_block");
    expect(blocks).toHaveLength(1);
    expect(blocks[0]).toMatchObject({ lang: "python" });
    // rich 结构原样保留
    const types = nodes.map((n) => (n as { type?: string }).type);
    expect(types).toContain("h2");
    expect(types).toContain("ul");
    expect(types).toContain("table");
    expect(allLeafText(nodes)).toContain("guide");
    expect(allLeafText(nodes)).toContain("strong");
  });

  it("plain 只有图/code、HTML 没有对应节点：不新增节点", () => {
    const html = `<p>just text</p>`;
    const plain = "![only-plain](https://example.com/p.png)\n\n```python\nx = 1\n```\n\njust text";
    const { result, fragment } = fuseClipboardFragment(html, plain);
    expect(result.kind).toBe("html");
    expect(fragment).toBeNull();
    expect(result.html).not.toContain("<img");
    expect(result.html).not.toContain("<pre");
  });

  it("HTML 只有图/code、plain 没有对应节点：HTML 原样", () => {
    const html = `<p>before <img src="https://example.com/a.png"> after</p><pre><code>print(1)</code></pre>`;
    const plain = `before and after only`;
    const { result, fragment } = fuseClipboardFragment(html, plain);
    expect(result.kind).toBe("html");
    expect(fragment).toBeNull();
    expect(result.html).toContain("https://example.com/a.png");
    expect(result.html).toContain("<pre>");
  });

  it("callout all-or-nothing 既有行为不变（无字段融合时仍 declined）", () => {
    const result = negotiateClipboardSource({
      html: "<p>&lt;aside&gt;</p><p><strong>Alignment</strong>: read the <a href=\"https://trusted.example/guide\">guide</a>.</p><p>&lt;/aside&gt;</p>",
      plain: "<aside>\n**Alignment**: read the [guide](https://other.example/guide).\n</aside>",
    });
    expect(result.kind).toBe("html");
    expect(result.reason).toBe("html_aside_fusion_declined");
  });

  it("callout 与唯一 media 字段融合共存：各自在原位置生效，不交换不重复", () => {
    // 图片段与 aside 区域之间放列表块：HTML deserializer 会把相邻纯 inline
    // 段落合并为一个 run，slot 机制要求 region 邻接块级元素才能保持 marker
    // 段落独立（既有合同）；列表分隔让 img 段与 callout 各自独立定位。
    const html = [
      `<p>before <img src="https://example.com/i.png"> after</p>`,
      `<ul><li>between</li></ul>`,
      `<p>&lt;aside&gt;</p><p>🎯</p>`,
      `<p><strong>Alignment</strong>: keep the body.</p>`,
      `<p>&lt;/aside&gt;</p>`,
      `<p>Trailing.</p>`,
    ].join("");
    const plain = [
      `before ![plain-alt](https://example.com/i.png) after`,
      ``,
      `- between`,
      ``,
      `<aside>`,
      `🎯`,
      ``,
      `**Alignment**: keep the body.`,
      `</aside>`,
      ``,
      `Trailing.`,
    ].join("\n");
    const { result, fragment } = fuseClipboardFragment(html, plain);
    expect(result.kind).toBe("hybrid");
    expect(result.reason).toBe("html_plain_aside_fused");
    expect(fragment).not.toBeNull();
    const nodes = fragment as Descendant[];
    const callouts = collectByType(nodes, "source_callout");
    expect(callouts).toHaveLength(1);
    const imgs = collectByType(nodes, "img");
    expect(imgs).toHaveLength(1);
    expect(imgs[0]).toMatchObject({
      url: "https://example.com/i.png",
      caption: [{ text: "plain-alt" }],
    });
    // 原位置：img 在 callout 之前，Trailing 在最后
    const types = nodes.map((n) => (n as { type?: string }).type);
    expect(types.indexOf("source_callout")).toBeGreaterThan(
      types.indexOf("img"),
    );
    expect(types[types.length - 1]).toBe("p");
    expect(allLeafText(nodes)).toContain("Trailing.");
  });
});

// ---------------------------------------------------------------------------
// production plain-deserializer wiring。真实 MarkdownTextInput
// 注入的 deserializeMarkdown 是默认 projection deserializer
// （deserializeMarkdownToBlocksWithStatus(md).blocks，不产生 typed img），
// 因此 image alt/title fusion 必须在内部分辨 input-aware plain blocks，
// 而不是依赖调用方注入形态。
// ---------------------------------------------------------------------------

describe("production plain-deserializer wiring", () => {
  it("注入默认 projection deserializer 时 image field fusion 仍建立 plan", () => {
    const html = `<p>before <img src="https://example.com/a.png"> after</p>`;
    const plain = `before ![plain-alt](https://example.com/a.png "plain-title") after`;

    // 模拟真实生产调用关系：HTML 走 mounted input editor；plain 走
    // 默认/projection-only deserializer（不产生 typed img）。
    const editor = createPlateEditor({ plugins: markdownTextInputPlugins });
    const dependencies = {
      deserializeHtml: (body: HTMLElement) =>
        editor.api.html.deserialize({ element: body }) as never,
      deserializeMarkdown: (markdown: string) =>
        deserializeMarkdownToBlocksWithStatus(markdown).blocks as never,
    };

    // 正向存在性：注入的 plain deserializer 确实不产出 img。
    const injectedPlain = dependencies.deserializeMarkdown(plain) as Descendant[];
    expect(collectByType(injectedPlain, "img")).toHaveLength(0);

    const result = negotiateClipboardSource({ html, plain }, dependencies);
    // 1. negotiation 产生 field-fusion plan
    expect(result.kind).toBe("hybrid");
    expect(result.reason).toBe("html_plain_fields_fused");
    expect(result.fusion?.imageFieldMatches).toHaveLength(1);
    const match = result.fusion?.imageFieldMatches?.[0] as
      | { htmlSrc?: string; url?: unknown }
      | undefined;
    // 4. plain URL 不作为 replacement 进入 plan；htmlSrc 是 HTML 自身值
    expect(match).toBeDefined();
    expect(match?.url).toBeUndefined();
    expect(match?.htmlSrc).toBe("https://example.com/a.png");

    const fragment = deserializeHybridClipboardFragment(
      result.html,
      result.fusion as NonNullable<typeof result.fusion>,
      dependencies,
    );
    expect(fragment).not.toBeNull();
    const imgs = collectByType(fragment as Descendant[], "img");
    // 5. 节点数量不变
    expect(imgs).toHaveLength(1);
    const img = imgs[0] as { url?: string; caption?: Descendant[]; title?: string };
    // 2. 补入 plain alt/title
    expect(img.caption).toEqual([{ text: "plain-alt" }]);
    expect(img.title).toBe("plain-title");
    // 3. URL 仍精确来自 HTML
    expect(img.url).toBe("https://example.com/a.png");
    // 5. 邻接正文与 path 不变
    expect(allLeafText(fragment as Descendant[])).toBe("before after");
    expect(collectByType(fragment as Descendant[], "code_block")).toHaveLength(0);
  });
});
