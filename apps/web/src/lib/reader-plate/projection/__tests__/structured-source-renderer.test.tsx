/**
 * @vitest-environment jsdom
 *
 * Fixture-driven component tests for StructuredSourceRenderer.
 *
 * Each test case mirrors the G0 frozen fixture JSON from
 * `services/api/tests/fixtures/markdown_structured_source/`. The fixture
 * data is inlined as typed constants to avoid cross-project JSON import
 * issues and to keep the test self-contained. The assertions verify the
 * rendered DOM structure (block tags, data attributes, diagnostic panels)
 * against the G0 Structured Source Contract
 * (`services/api/tests/fixtures/markdown_structured_source/CONTRACT.md`).
 *
 apps/web/docs/reader-ia.md
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type {
  ReaderStructuredSourceBlock,
  ReaderStructuredSourceDiagnostic,
} from "@/types/api/reader-plate";

import { StructuredSourceRenderer } from "../structured-source-renderer";

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// Fixture data (mirrors G0 frozen fixtures — do NOT mutate in tests)
// ---------------------------------------------------------------------------

const SIMPLE_PARAGRAPH_BLOCKS: ReaderStructuredSourceBlock[] = [
  {
    block_id: "b1",
    block_type: "paragraph",
    text_content: "This is a simple paragraph for baseline testing.",
    payload_json: {},
    parent_block_id: null,
    order_index: 0,
    source_range: { line_start: 1, line_end: 1 },
  },
];

const SIMPLE_PARAGRAPH_DIAGNOSTIC: ReaderStructuredSourceDiagnostic = {
  fixture_name: "simple_paragraph",
  warnings: [],
  unsupported: [],
  outcome: "stable_document_ready",
};

const COMPLEX_DOCUMENT_BLOCKS: ReaderStructuredSourceBlock[] = [
  {
    block_id: "b1",
    block_type: "heading",
    text_content: "Article Title",
    payload_json: { level: 1 },
    parent_block_id: null,
    order_index: 0,
    source_range: { line_start: 1, line_end: 1 },
  },
  {
    block_id: "b2",
    block_type: "heading",
    text_content: "Introduction",
    payload_json: { level: 2 },
    parent_block_id: null,
    order_index: 1,
    source_range: { line_start: 3, line_end: 3 },
  },
  {
    block_id: "b3",
    block_type: "paragraph",
    text_content: "The article covers multiple topics including inline code and a link.",
    payload_json: {},
    parent_block_id: null,
    order_index: 2,
    source_range: { line_start: 5, line_end: 5 },
  },
  {
    block_id: "b4",
    block_type: "heading",
    text_content: "Subsection",
    payload_json: { level: 3 },
    parent_block_id: null,
    order_index: 3,
    source_range: { line_start: 7, line_end: 7 },
  },
  {
    block_id: "b5",
    block_type: "list",
    text_content: null,
    payload_json: { ordered: false, depth: 0 },
    parent_block_id: null,
    order_index: 4,
    source_range: { line_start: 9, line_end: 12 },
  },
  {
    block_id: "b6",
    block_type: "list_item",
    text_content: "First item",
    payload_json: { ordered: false, marker: "-", ordinal: null, depth: 0 },
    parent_block_id: "b5",
    order_index: 5,
    source_range: { line_start: 9, line_end: 9 },
  },
  {
    block_id: "b7",
    block_type: "list_item",
    text_content: "Second item with emphasis",
    payload_json: { ordered: false, marker: "-", ordinal: null, depth: 0 },
    parent_block_id: "b5",
    order_index: 6,
    source_range: { line_start: 10, line_end: 12 },
  },
  {
    block_id: "b8",
    block_type: "list",
    text_content: null,
    payload_json: { ordered: true, depth: 1 },
    parent_block_id: "b7",
    order_index: 7,
    source_range: { line_start: 11, line_end: 12 },
  },
  {
    block_id: "b9",
    block_type: "list_item",
    text_content: "Nested ordered",
    payload_json: { ordered: true, marker: "1.", ordinal: 1, depth: 1 },
    parent_block_id: "b8",
    order_index: 8,
    source_range: { line_start: 11, line_end: 11 },
  },
  {
    block_id: "b10",
    block_type: "list_item",
    text_content: "Another nested item",
    payload_json: { ordered: true, marker: "2.", ordinal: 2, depth: 1 },
    parent_block_id: "b8",
    order_index: 9,
    source_range: { line_start: 12, line_end: 12 },
  },
  {
    block_id: "b11",
    block_type: "table",
    text_content: null,
    payload_json: { alignments: ["default", "center"] },
    parent_block_id: null,
    order_index: 10,
    source_range: { line_start: 14, line_end: 16 },
  },
  {
    block_id: "b12",
    block_type: "table_row",
    text_content: null,
    payload_json: { is_header: true, row_index: 0 },
    parent_block_id: "b11",
    order_index: 11,
    source_range: { line_start: 14, line_end: 14 },
  },
  {
    block_id: "b13",
    block_type: "table_cell",
    text_content: "Col A",
    payload_json: { column_index: 0, alignment: "default", is_header: true },
    parent_block_id: "b12",
    order_index: 12,
    source_range: { line_start: 14, line_end: 14 },
  },
  {
    block_id: "b14",
    block_type: "table_cell",
    text_content: "Col B",
    payload_json: { column_index: 1, alignment: "center", is_header: true },
    parent_block_id: "b12",
    order_index: 13,
    source_range: { line_start: 14, line_end: 14 },
  },
  {
    block_id: "b15",
    block_type: "table_row",
    text_content: null,
    payload_json: { is_header: false, row_index: 1 },
    parent_block_id: "b11",
    order_index: 14,
    source_range: { line_start: 16, line_end: 16 },
  },
  {
    block_id: "b16",
    block_type: "table_cell",
    text_content: "1",
    payload_json: { column_index: 0, alignment: "default", is_header: false },
    parent_block_id: "b15",
    order_index: 15,
    source_range: { line_start: 16, line_end: 16 },
  },
  {
    block_id: "b17",
    block_type: "table_cell",
    text_content: "2",
    payload_json: { column_index: 1, alignment: "center", is_header: false },
    parent_block_id: "b15",
    order_index: 16,
    source_range: { line_start: 16, line_end: 16 },
  },
  {
    block_id: "b18",
    block_type: "code_block",
    text_content: 'def hello():\n    print("hi")',
    payload_json: { language: "python" },
    parent_block_id: null,
    order_index: 17,
    source_range: { line_start: 18, line_end: 21 },
  },
  {
    block_id: "b19",
    block_type: "blockquote",
    text_content: "A blockquote with strikethrough.",
    payload_json: {},
    parent_block_id: null,
    order_index: 18,
    source_range: { line_start: 23, line_end: 23 },
  },
  {
    block_id: "b20",
    block_type: "thematic_break",
    text_content: null,
    payload_json: {},
    parent_block_id: null,
    order_index: 19,
    source_range: { line_start: 25, line_end: 25 },
  },
  {
    block_id: "b21",
    block_type: "paragraph",
    text_content: "Final paragraph with code and bold.",
    payload_json: {},
    parent_block_id: null,
    order_index: 20,
    source_range: { line_start: 27, line_end: 27 },
  },
];

const COMPLEX_DOCUMENT_DIAGNOSTIC: ReaderStructuredSourceDiagnostic = {
  fixture_name: "r14_complex",
  warnings: [
    {
      code: "strikethrough_extension",
      message: "Strikethrough syntax used; requires GFM strikethrough plugin.",
      blocks_freeze: false,
    },
  ],
  unsupported: [],
  outcome: "stable_document_ready",
};

const NESTED_LIST_BLOCKS: ReaderStructuredSourceBlock[] = [
  {
    block_id: "b1",
    block_type: "list",
    text_content: null,
    payload_json: { ordered: false, depth: 0 },
    parent_block_id: null,
    order_index: 0,
    source_range: { line_start: 1, line_end: 6 },
  },
  {
    block_id: "b2",
    block_type: "list_item",
    text_content: "Level 1 unordered item A",
    payload_json: { ordered: false, marker: "-", ordinal: null, depth: 0 },
    parent_block_id: "b1",
    order_index: 1,
    source_range: { line_start: 1, line_end: 5 },
  },
  {
    block_id: "b3",
    block_type: "list",
    text_content: null,
    payload_json: { ordered: false, depth: 1 },
    parent_block_id: "b2",
    order_index: 2,
    source_range: { line_start: 2, line_end: 5 },
  },
  {
    block_id: "b4",
    block_type: "list_item",
    text_content: "Level 2 unordered item B",
    payload_json: { ordered: false, marker: "-", ordinal: null, depth: 1 },
    parent_block_id: "b3",
    order_index: 3,
    source_range: { line_start: 2, line_end: 3 },
  },
  {
    block_id: "b5",
    block_type: "list",
    text_content: null,
    payload_json: { ordered: false, depth: 2 },
    parent_block_id: "b4",
    order_index: 4,
    source_range: { line_start: 3, line_end: 3 },
  },
  {
    block_id: "b6",
    block_type: "list_item",
    text_content: "Level 3 unordered item C",
    payload_json: { ordered: false, marker: "-", ordinal: null, depth: 2 },
    parent_block_id: "b5",
    order_index: 5,
    source_range: { line_start: 3, line_end: 3 },
  },
  {
    block_id: "b7",
    block_type: "list",
    text_content: null,
    payload_json: { ordered: true, depth: 1 },
    parent_block_id: "b2",
    order_index: 6,
    source_range: { line_start: 4, line_end: 5 },
  },
  {
    block_id: "b8",
    block_type: "list_item",
    text_content: "Level 2 ordered item D",
    payload_json: { ordered: true, marker: "1.", ordinal: 1, depth: 1 },
    parent_block_id: "b7",
    order_index: 7,
    source_range: { line_start: 4, line_end: 5 },
  },
  {
    block_id: "b9",
    block_type: "list",
    text_content: null,
    payload_json: { ordered: true, depth: 2 },
    parent_block_id: "b8",
    order_index: 8,
    source_range: { line_start: 5, line_end: 5 },
  },
  {
    block_id: "b10",
    block_type: "list_item",
    text_content: "Level 3 ordered item E",
    payload_json: { ordered: true, marker: "1.", ordinal: 1, depth: 2 },
    parent_block_id: "b9",
    order_index: 9,
    source_range: { line_start: 5, line_end: 5 },
  },
  {
    block_id: "b11",
    block_type: "list_item",
    text_content: "Another top level item F",
    payload_json: { ordered: false, marker: "-", ordinal: null, depth: 0 },
    parent_block_id: "b1",
    order_index: 10,
    source_range: { line_start: 6, line_end: 6 },
  },
];

const GFM_TABLE_BLOCKS: ReaderStructuredSourceBlock[] = [
  {
    block_id: "b1",
    block_type: "table",
    text_content: null,
    payload_json: { alignments: ["left", "center", "right"], column_count: 3 },
    parent_block_id: null,
    order_index: 0,
    source_range: { line_start: 1, line_end: 4 },
  },
  {
    block_id: "b2",
    block_type: "table_row",
    text_content: null,
    payload_json: { is_header: true, row_index: 0 },
    parent_block_id: "b1",
    order_index: 1,
    source_range: { line_start: 1, line_end: 1 },
  },
  {
    block_id: "b3",
    block_type: "table_cell",
    text_content: "Name",
    payload_json: { column_index: 0, alignment: "left", is_header: true },
    parent_block_id: "b2",
    order_index: 2,
    source_range: { line_start: 1, line_end: 1 },
  },
  {
    block_id: "b4",
    block_type: "table_cell",
    text_content: "Age",
    payload_json: { column_index: 1, alignment: "center", is_header: true },
    parent_block_id: "b2",
    order_index: 3,
    source_range: { line_start: 1, line_end: 1 },
  },
  {
    block_id: "b5",
    block_type: "table_cell",
    text_content: "City",
    payload_json: { column_index: 2, alignment: "right", is_header: true },
    parent_block_id: "b2",
    order_index: 4,
    source_range: { line_start: 1, line_end: 1 },
  },
  {
    block_id: "b6",
    block_type: "table_row",
    text_content: null,
    payload_json: { is_header: false, row_index: 1 },
    parent_block_id: "b1",
    order_index: 5,
    source_range: { line_start: 3, line_end: 3 },
  },
  {
    block_id: "b7",
    block_type: "table_cell",
    text_content: "Bob",
    payload_json: { column_index: 0, alignment: "left", is_header: false },
    parent_block_id: "b6",
    order_index: 6,
    source_range: { line_start: 3, line_end: 3 },
  },
  {
    block_id: "b8",
    block_type: "table_cell",
    text_content: "30",
    payload_json: { column_index: 1, alignment: "center", is_header: false },
    parent_block_id: "b6",
    order_index: 7,
    source_range: { line_start: 3, line_end: 3 },
  },
  {
    block_id: "b9",
    block_type: "table_cell",
    text_content: "NYC",
    payload_json: { column_index: 2, alignment: "right", is_header: false },
    parent_block_id: "b6",
    order_index: 8,
    source_range: { line_start: 3, line_end: 3 },
  },
];

const CODE_MERMAID_BLOCKS: ReaderStructuredSourceBlock[] = [
  {
    block_id: "b1",
    block_type: "heading",
    text_content: "Diagram",
    payload_json: { level: 1 },
    parent_block_id: null,
    order_index: 0,
    source_range: { line_start: 1, line_end: 1 },
  },
  {
    block_id: "b2",
    block_type: "paragraph",
    text_content: "Some intro text.",
    payload_json: {},
    parent_block_id: null,
    order_index: 1,
    source_range: { line_start: 3, line_end: 3 },
  },
  {
    block_id: "b3",
    block_type: "code_block",
    text_content: "graph TD\n    A --> B\n    B --> C",
    payload_json: { language: "mermaid" },
    parent_block_id: null,
    order_index: 2,
    source_range: { line_start: 5, line_end: 9 },
  },
  {
    block_id: "b4",
    block_type: "code_block",
    text_content: "x = 1",
    payload_json: { language: "python" },
    parent_block_id: null,
    order_index: 3,
    source_range: { line_start: 11, line_end: 13 },
  },
  {
    block_id: "b5",
    block_type: "paragraph",
    text_content: "Final paragraph.",
    payload_json: {},
    parent_block_id: null,
    order_index: 4,
    source_range: { line_start: 15, line_end: 15 },
  },
];

const CODE_MERMAID_DIAGNOSTIC: ReaderStructuredSourceDiagnostic = {
  fixture_name: "code_mermaid",
  warnings: [
    {
      code: "mermaid_static_only",
      message:
        "Mermaid code block is stored as static text; diagram is not rendered or executed.",
      blocks_freeze: false,
    },
  ],
  unsupported: [],
  outcome: "stable_document_ready",
};

const RAW_HTML_BLOCKS: ReaderStructuredSourceBlock[] = [
  {
    block_id: "b1",
    block_type: "paragraph",
    text_content: "This is inside HTML.",
    payload_json: { extracted_from: "html_block" },
    parent_block_id: null,
    order_index: 0,
    source_range: { line_start: 1, line_end: 5 },
  },
  {
    block_id: "b2",
    block_type: "paragraph",
    text_content: "A paragraph with inline HTML.",
    payload_json: {},
    parent_block_id: null,
    order_index: 1,
    source_range: { line_start: 7, line_end: 7 },
  },
  {
    block_id: "b3",
    block_type: "paragraph",
    text_content: "Bold via HTML",
    payload_json: { extracted_from: "html_inline" },
    parent_block_id: null,
    order_index: 2,
    source_range: { line_start: 9, line_end: 9 },
  },
];

const RAW_HTML_DIAGNOSTIC: ReaderStructuredSourceDiagnostic = {
  fixture_name: "raw_html",
  warnings: [
    {
      code: "raw_html_block",
      message: "Raw HTML block detected; stored as text but requires candidate review.",
      blocks_freeze: false,
    },
    {
      code: "inline_html",
      message: "Inline HTML tag stripped from paragraph text.",
      blocks_freeze: false,
    },
  ],
  unsupported: [
    {
      code: "raw_html",
      message:
        "Raw HTML is not a first-class block type in the first phase; text is extracted but structure is not preserved.",
    },
  ],
  outcome: "candidate_document_required",
};

const FOOTNOTE_BLOCKS: ReaderStructuredSourceBlock[] = [
  {
    block_id: "b1",
    block_type: "heading",
    text_content: "Document",
    payload_json: { level: 1 },
    parent_block_id: null,
    order_index: 0,
    source_range: { line_start: 1, line_end: 1 },
  },
  {
    block_id: "b2",
    block_type: "paragraph",
    text_content: "This has a footnote reference.",
    payload_json: {},
    parent_block_id: null,
    order_index: 1,
    source_range: { line_start: 3, line_end: 3 },
  },
  {
    block_id: "b3",
    block_type: "footnote",
    text_content: "This is the footnote definition.",
    payload_json: { footnote_id: "1" },
    parent_block_id: null,
    order_index: 2,
    source_range: { line_start: 5, line_end: 5 },
  },
];

const FOOTNOTE_DIAGNOSTIC: ReaderStructuredSourceDiagnostic = {
  fixture_name: "footnote",
  warnings: [
    {
      code: "footnote_reference",
      message: "Footnote reference encountered; footnote plugin not enabled in first phase.",
      blocks_freeze: false,
    },
  ],
  unsupported: [
    {
      code: "footnote_full_semantics",
      message:
        "Footnote definition is captured as a block but full footnote semantics (multi-ref, backref) are not supported in first phase.",
    },
  ],
  outcome: "candidate_document_required",
};

const UNSAFE_LINK_BLOCKS: ReaderStructuredSourceBlock[] = [
  {
    block_id: "b1",
    block_type: "heading",
    text_content: "Unsafe Links",
    payload_json: { level: 1 },
    parent_block_id: null,
    order_index: 0,
    source_range: { line_start: 1, line_end: 1 },
  },
  {
    block_id: "b2",
    block_type: "paragraph",
    text_content: "A javascript link and a data link and a vbscript link.",
    payload_json: {
      links: [],
      stripped_links: [
        { text: "javascript link", href: "javascript:alert(1)", reason: "unsafe_protocol" },
        {
          text: "data link",
          href: "data:text/html,<script>alert(1)</script>",
          reason: "unsafe_protocol",
        },
        { text: "vbscript link", href: "vbscript:msgbox(1)", reason: "unsafe_protocol" },
      ],
    },
    parent_block_id: null,
    order_index: 1,
    source_range: { line_start: 3, line_end: 3 },
  },
  {
    block_id: "b3",
    block_type: "paragraph",
    text_content: "A safe https link and mailto.",
    payload_json: {
      links: [
        { text: "https link", href: "https://example.com" },
        { text: "mailto", href: "mailto:test@example.com" },
      ],
    },
    parent_block_id: null,
    order_index: 2,
    source_range: { line_start: 5, line_end: 5 },
  },
];

const UNSAFE_LINK_DIAGNOSTIC: ReaderStructuredSourceDiagnostic = {
  fixture_name: "unsafe_link",
  warnings: [
    {
      code: "unsafe_link_protocol",
      message:
        "Links with unsafe protocols (javascript/data/vbscript) were stripped from paragraph text; link text preserved.",
      blocks_freeze: false,
    },
  ],
  unsupported: [
    {
      code: "unsafe_link_sanitization",
      message:
        "Unsafe-protocol link sanitization is a first-phase safety measure; full link audit requires candidate review.",
    },
  ],
  outcome: "candidate_document_required",
};

const UNCLOSED_FENCE_BLOCKS: ReaderStructuredSourceBlock[] = [
  {
    block_id: "b1",
    block_type: "heading",
    text_content: "Unclosed Fence",
    payload_json: { level: 1 },
    parent_block_id: null,
    order_index: 0,
    source_range: { line_start: 1, line_end: 1 },
  },
  {
    block_id: "b2",
    block_type: "paragraph",
    text_content: "Some text.",
    payload_json: {},
    parent_block_id: null,
    order_index: 1,
    source_range: { line_start: 3, line_end: 3 },
  },
  {
    block_id: "b3",
    block_type: "code_block",
    text_content: 'def unclosed():\n    return "no closing fence"',
    payload_json: { language: "python", fenced: true, closed: false },
    parent_block_id: null,
    order_index: 2,
    source_range: { line_start: 5, line_end: 7 },
  },
];

const UNCLOSED_FENCE_DIAGNOSTIC: ReaderStructuredSourceDiagnostic = {
  fixture_name: "unclosed_fence",
  warnings: [
    {
      code: "has_unclosed_fence",
      message:
        "Fenced code block is missing its closing fence; captured as code_block but requires candidate review for boundary correctness.",
      blocks_freeze: false,
    },
  ],
  unsupported: [],
  outcome: "candidate_document_required",
};

const REJECT_EMPTY_BLOCKS: ReaderStructuredSourceBlock[] = [
  {
    block_id: "b1",
    block_type: "code_block",
    text_content:
      'def foo():\n    pass\n\nclass Bar:\n    def baz(self):\n        return 42\n\nif __name__ == "__main__":\n    foo()',
    payload_json: { language: "python", fenced: true, closed: true },
    parent_block_id: null,
    order_index: 0,
    source_range: { line_start: 1, line_end: 11 },
  },
];

const REJECT_EMPTY_DIAGNOSTIC: ReaderStructuredSourceDiagnostic = {
  fixture_name: "reject_empty",
  warnings: [
    {
      code: "code_dominant",
      message:
        "Input is code-dominant with no narrative blocks; rejected from stable document freeze, action required.",
      blocks_freeze: false,
    },
  ],
  unsupported: [],
  outcome: "input_rejected_or_action_required",
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("StructuredSourceRenderer", () => {
  describe("simple_paragraph fixture", () => {
    it("renders a single paragraph block", () => {
      const { container } = render(
        <StructuredSourceRenderer
          blocks={SIMPLE_PARAGRAPH_BLOCKS}
          diagnostic={SIMPLE_PARAGRAPH_DIAGNOSTIC}
        />,
      );

      expect(screen.getByTestId("structured-source-renderer")).toBeTruthy();
      const paragraph = container.querySelector(
        '[data-block-id="b1"][data-block-type="paragraph"]',
      );
      expect(paragraph).toBeTruthy();
      expect(paragraph?.tagName).toBe("P");
      expect(paragraph?.textContent).toBe(
        "This is a simple paragraph for baseline testing.",
      );
    });

    it("renders stable_document_ready outcome with no warnings", () => {
      render(
        <StructuredSourceRenderer
          blocks={SIMPLE_PARAGRAPH_BLOCKS}
          diagnostic={SIMPLE_PARAGRAPH_DIAGNOSTIC}
        />,
      );

      const outcome = screen.getByTestId("structured-source-outcome");
      expect(outcome.getAttribute("data-outcome")).toBe("stable_document_ready");
      expect(outcome.textContent).toContain("结构化文档就绪");
      expect(screen.queryByTestId("structured-source-warnings")).toBeNull();
      expect(screen.queryByTestId("structured-source-unsupported")).toBeNull();
    });
  });

  describe("r14_complex fixture", () => {
    it("renders all block types in the tree", () => {
      const { container } = render(
        <StructuredSourceRenderer
          blocks={COMPLEX_DOCUMENT_BLOCKS}
          diagnostic={COMPLEX_DOCUMENT_DIAGNOSTIC}
        />,
      );

      const blockTypes = new Set(
        Array.from(
          container.querySelectorAll("[data-block-type]"),
        ).map((el) => el.getAttribute("data-block-type")),
      );
      expect(blockTypes.has("heading")).toBe(true);
      expect(blockTypes.has("paragraph")).toBe(true);
      expect(blockTypes.has("list")).toBe(true);
      expect(blockTypes.has("list_item")).toBe(true);
      expect(blockTypes.has("table")).toBe(true);
      expect(blockTypes.has("table_row")).toBe(true);
      expect(blockTypes.has("table_cell")).toBe(true);
      expect(blockTypes.has("code_block")).toBe(true);
      expect(blockTypes.has("blockquote")).toBe(true);
      expect(blockTypes.has("thematic_break")).toBe(true);
    });

    it("renders heading hierarchy h1 → h2 → h3", () => {
      const { container } = render(
        <StructuredSourceRenderer blocks={COMPLEX_DOCUMENT_BLOCKS} />,
      );

      const h1 = container.querySelector('[data-block-id="b1"]');
      const h2 = container.querySelector('[data-block-id="b2"]');
      const h3 = container.querySelector('[data-block-id="b4"]');
      expect(h1?.tagName).toBe("H1");
      expect(h2?.tagName).toBe("H2");
      expect(h3?.tagName).toBe("H3");
    });

    it("renders nested ordered list inside unordered list_item", () => {
      const { container } = render(
        <StructuredSourceRenderer blocks={COMPLEX_DOCUMENT_BLOCKS} />,
      );

      const outerList = container.querySelector('[data-block-id="b5"]');
      expect(outerList?.tagName).toBe("UL");
      expect(outerList?.getAttribute("data-list-ordered")).toBe("false");

      const nestedOrderedList = container.querySelector('[data-block-id="b8"]');
      expect(nestedOrderedList?.tagName).toBe("OL");
      expect(nestedOrderedList?.getAttribute("data-list-ordered")).toBe("true");

      // The ordered list should be a descendant of the outer ordered-list list_item
      const listItemB7 = container.querySelector('[data-block-id="b7"]');
      expect(listItemB7?.contains(nestedOrderedList ?? null)).toBe(true);
    });

    it("renders table with thead and tbody", () => {
      const { container } = render(
        <StructuredSourceRenderer blocks={COMPLEX_DOCUMENT_BLOCKS} />,
      );

      const thead = container.querySelector(
        '[data-testid="structured-source-table-head"]',
      );
      const tbody = container.querySelector(
        '[data-testid="structured-source-table-body"]',
      );
      expect(thead).toBeTruthy();
      expect(tbody).toBeTruthy();

      // The header row should be in thead; the body row in tbody
      const headerRow = thead?.querySelector('[data-block-id="b12"]');
      const bodyRow = tbody?.querySelector('[data-block-id="b15"]');
      expect(headerRow).toBeTruthy();
      expect(bodyRow).toBeTruthy();
    });

    it("renders code_block with python language", () => {
      const { container } = render(
        <StructuredSourceRenderer blocks={COMPLEX_DOCUMENT_BLOCKS} />,
      );

      const codeBlock = container.querySelector('[data-block-id="b18"]');
      expect(codeBlock?.tagName).toBe("PRE");
      expect(codeBlock?.getAttribute("data-language")).toBe("python");
      const code = codeBlock?.querySelector("code");
      expect(code?.textContent).toContain('def hello():');
    });

    it("renders visible language badge for code_block with language", () => {
      // code blocks with non-empty language render a visible
      // badge in the top-right corner so users can identify the language
      // without reading the code body.
      const { container } = render(
        <StructuredSourceRenderer blocks={COMPLEX_DOCUMENT_BLOCKS} />,
      );

      const codeBlock = container.querySelector('[data-block-id="b18"]');
      const badge = codeBlock?.querySelector('[data-testid="code-language-badge"]');
      expect(badge).toBeTruthy();
      expect(badge?.textContent).toBe("python");
    });

    it("does not render language badge for code_block without language", () => {
      // code blocks without a language identifier must not
      // render an empty badge.
      const blocksWithoutLang: ReaderStructuredSourceBlock[] = [
        {
          block_id: "b1",
          block_type: "code_block",
          text_content: "plain code",
          payload_json: {},
          parent_block_id: null,
          order_index: 0,
          source_range: { line_start: 1, line_end: 1 },
        },
      ];

      const { container } = render(
        <StructuredSourceRenderer blocks={blocksWithoutLang} />,
      );

      const codeBlock = container.querySelector('[data-block-id="b1"]');
      const badge = codeBlock?.querySelector('[data-testid="code-language-badge"]');
      expect(badge).toBeNull();
    });

    it("renders blockquote and thematic_break", () => {
      const { container } = render(
        <StructuredSourceRenderer blocks={COMPLEX_DOCUMENT_BLOCKS} />,
      );

      const blockquote = container.querySelector('[data-block-id="b19"]');
      expect(blockquote?.tagName).toBe("BLOCKQUOTE");
      expect(blockquote?.textContent).toContain("A blockquote with strikethrough.");

      const hr = container.querySelector('[data-block-id="b20"]');
      expect(hr?.tagName).toBe("HR");
    });

    it("surfaces strikethrough_extension warning and stable outcome", () => {
      render(
        <StructuredSourceRenderer
          blocks={COMPLEX_DOCUMENT_BLOCKS}
          diagnostic={COMPLEX_DOCUMENT_DIAGNOSTIC}
        />,
      );

      const warning = screen.getByTestId(
        "structured-source-warning-strikethrough_extension",
      );
      expect(warning.getAttribute("data-warning-code")).toBe(
        "strikethrough_extension",
      );

      const outcome = screen.getByTestId("structured-source-outcome");
      expect(outcome.getAttribute("data-outcome")).toBe("stable_document_ready");
    });
  });

  describe("nested_list fixture", () => {
    it("renders 3-level nesting with mixed ordered/unordered lists", () => {
      const { container } = render(
        <StructuredSourceRenderer blocks={NESTED_LIST_BLOCKS} />,
      );

      // Level 0: outer list → its list item
      const rootList = container.querySelector('[data-block-id="b1"]');
      expect(rootList?.tagName).toBe("UL");

      // Level 1: nested lists inside the list item
      const ul1 = container.querySelector('[data-block-id="b3"]');
      const ol1 = container.querySelector('[data-block-id="b7"]');
      expect(ul1?.tagName).toBe("UL");
      expect(ol1?.tagName).toBe("OL");

      // Level 2: deeper nested lists inside their parents
      const ul2 = container.querySelector('[data-block-id="b5"]');
      const ol2 = container.querySelector('[data-block-id="b9"]');
      expect(ul2?.tagName).toBe("UL");
      expect(ol2?.tagName).toBe("OL");
    });
  });

  describe("gfm_table fixture", () => {
    it("renders table with 3-column alignments and header + body rows", () => {
      const { container } = render(
        <StructuredSourceRenderer blocks={GFM_TABLE_BLOCKS} />,
      );

      const table = container.querySelector('[data-block-id="b1"]');
      expect(table?.tagName).toBe("TABLE");

      // Header cells
      const headerCellName = container.querySelector('[data-block-id="b3"]');
      expect(headerCellName?.tagName).toBe("TH");
      expect(headerCellName?.getAttribute("data-cell-alignment")).toBe("left");
      expect(headerCellName?.textContent).toBe("Name");

      const headerCellAge = container.querySelector('[data-block-id="b4"]');
      expect(headerCellAge?.tagName).toBe("TH");
      expect(headerCellAge?.getAttribute("data-cell-alignment")).toBe("center");

      const headerCellCity = container.querySelector('[data-block-id="b5"]');
      expect(headerCellCity?.tagName).toBe("TH");
      expect(headerCellCity?.getAttribute("data-cell-alignment")).toBe("right");

      // Body cells
      const bodyCellBob = container.querySelector('[data-block-id="b7"]');
      expect(bodyCellBob?.tagName).toBe("TD");
      expect(bodyCellBob?.textContent).toBe("Bob");
    });

    it("applies textAlign style for aligned cells", () => {
      const { container } = render(
        <StructuredSourceRenderer blocks={GFM_TABLE_BLOCKS} />,
      );

      const centerCell = container.querySelector('[data-block-id="b4"]') as HTMLElement;
      expect(centerCell?.style.textAlign).toBe("center");

      const rightCell = container.querySelector('[data-block-id="b5"]') as HTMLElement;
      expect(rightCell?.style.textAlign).toBe("right");
    });
  });

  describe("code_mermaid fixture", () => {
    it("tags mermaid code block with data-mermaid and does not execute", () => {
      const { container } = render(
        <StructuredSourceRenderer
          blocks={CODE_MERMAID_BLOCKS}
          diagnostic={CODE_MERMAID_DIAGNOSTIC}
        />,
      );

      const mermaidBlock = container.querySelector('[data-block-id="b3"]');
      expect(mermaidBlock?.tagName).toBe("PRE");
      expect(mermaidBlock?.getAttribute("data-language")).toBe("mermaid");

      const mermaidCode = mermaidBlock?.querySelector("code");
      expect(mermaidCode?.getAttribute("data-mermaid")).toBe("true");
      expect(mermaidCode?.getAttribute("data-language")).toBe("mermaid");
      // Mermaid content is stored as static text, not executed
      expect(mermaidCode?.textContent).toContain("graph TD");
    });

    it("does not render language badge for mermaid code_block", () => {
      // mermaid blocks have a separate static-render path and
      // already carry data-mermaid; a "MERMAID" badge would be noise.
      const { container } = render(
        <StructuredSourceRenderer
          blocks={CODE_MERMAID_BLOCKS}
          diagnostic={CODE_MERMAID_DIAGNOSTIC}
        />,
      );

      const mermaidBlock = container.querySelector('[data-block-id="b3"]');
      const badge = mermaidBlock?.querySelector('[data-testid="code-language-badge"]');
      expect(badge).toBeNull();

      // Sanity: non-mermaid code block in same fixture still gets a badge.
      const pythonBlock = container.querySelector('[data-block-id="b4"]');
      const pythonBadge = pythonBlock?.querySelector('[data-testid="code-language-badge"]');
      expect(pythonBadge).toBeTruthy();
      expect(pythonBadge?.textContent).toBe("python");
    });

    it("surfaces mermaid_static_only warning", () => {
      render(
        <StructuredSourceRenderer
          blocks={CODE_MERMAID_BLOCKS}
          diagnostic={CODE_MERMAID_DIAGNOSTIC}
        />,
      );

      const warning = screen.getByTestId(
        "structured-source-warning-mermaid_static_only",
      );
      expect(warning.getAttribute("data-warning-code")).toBe("mermaid_static_only");
    });
  });

  describe("raw_html fixture", () => {
    it("renders extracted_from attribute on paragraphs", () => {
      const { container } = render(
        <StructuredSourceRenderer blocks={RAW_HTML_BLOCKS} />,
      );

      const htmlBlockParagraph = container.querySelector(
        '[data-block-id="b1"]',
      );
      expect(htmlBlockParagraph?.getAttribute("data-extracted-from")).toBe(
        "html_block",
      );

      const htmlInlineParagraph = container.querySelector(
        '[data-block-id="b3"]',
      );
      expect(htmlInlineParagraph?.getAttribute("data-extracted-from")).toBe(
        "html_inline",
      );
    });

    it("surfaces raw_html_block + inline_html warnings and raw_html unsupported", () => {
      render(
        <StructuredSourceRenderer
          blocks={RAW_HTML_BLOCKS}
          diagnostic={RAW_HTML_DIAGNOSTIC}
        />,
      );

      expect(
        screen.getByTestId("structured-source-warning-raw_html_block"),
      ).toBeTruthy();
      expect(
        screen.getByTestId("structured-source-warning-inline_html"),
      ).toBeTruthy();

      const unsupported = screen.getByTestId("structured-source-unsupported");
      expect(unsupported.querySelector('[data-unsupported-code="raw_html"]')).toBeTruthy();

      const outcome = screen.getByTestId("structured-source-outcome");
      expect(outcome.getAttribute("data-outcome")).toBe(
        "candidate_document_required",
      );
    });
  });

  describe("footnote fixture", () => {
    it("renders footnote as aside with role=note", () => {
      const { container } = render(
        <StructuredSourceRenderer blocks={FOOTNOTE_BLOCKS} />,
      );

      const footnote = container.querySelector('[data-block-id="b3"]');
      expect(footnote?.tagName).toBe("ASIDE");
      expect(footnote?.getAttribute("role")).toBe("note");
      expect(footnote?.getAttribute("data-footnote-id")).toBe("1");
      expect(footnote?.textContent).toContain("This is the footnote definition.");
    });

    it("surfaces footnote_reference warning and footnote_full_semantics unsupported", () => {
      render(
        <StructuredSourceRenderer
          blocks={FOOTNOTE_BLOCKS}
          diagnostic={FOOTNOTE_DIAGNOSTIC}
        />,
      );

      expect(
        screen.getByTestId("structured-source-warning-footnote_reference"),
      ).toBeTruthy();

      const unsupported = screen.getByTestId("structured-source-unsupported");
      expect(
        unsupported.querySelector(
          '[data-unsupported-code="footnote_full_semantics"]',
        ),
      ).toBeTruthy();
    });
  });

  describe("unsafe_link fixture", () => {
    it("renders safe https and mailto links", () => {
      const { container } = render(
        <StructuredSourceRenderer blocks={UNSAFE_LINK_BLOCKS} />,
      );

      const safeLinks = container.querySelectorAll(
        '[data-testid="structured-source-safe-link"]',
      );
      expect(safeLinks.length).toBe(2);
      expect(safeLinks[0]?.getAttribute("href")).toBe("https://example.com");
      expect(safeLinks[1]?.getAttribute("href")).toBe("mailto:test@example.com");
    });

    it("renders stripped_links notice for unsafe protocols", () => {
      const { container } = render(
        <StructuredSourceRenderer blocks={UNSAFE_LINK_BLOCKS} />,
      );

      const strippedNotice = container.querySelector(
        '[data-testid="structured-source-stripped-links"]',
      );
      expect(strippedNotice).toBeTruthy();
      expect(strippedNotice?.textContent).toContain("3");
      expect(strippedNotice?.textContent).toContain("不安全链接已移除");
    });

    it("surfaces unsafe_link_protocol warning and candidate outcome", () => {
      render(
        <StructuredSourceRenderer
          blocks={UNSAFE_LINK_BLOCKS}
          diagnostic={UNSAFE_LINK_DIAGNOSTIC}
        />,
      );

      expect(
        screen.getByTestId("structured-source-warning-unsafe_link_protocol"),
      ).toBeTruthy();

      const outcome = screen.getByTestId("structured-source-outcome");
      expect(outcome.getAttribute("data-outcome")).toBe(
        "candidate_document_required",
      );
    });
  });

  describe("unclosed_fence fixture", () => {
    it("renders code_block with data-closed=false", () => {
      const { container } = render(
        <StructuredSourceRenderer blocks={UNCLOSED_FENCE_BLOCKS} />,
      );

      const codeBlock = container.querySelector('[data-block-id="b3"]');
      expect(codeBlock?.tagName).toBe("PRE");
      expect(codeBlock?.getAttribute("data-fenced")).toBe("true");
      expect(codeBlock?.getAttribute("data-closed")).toBe("false");
    });

    it("surfaces has_unclosed_fence warning and candidate outcome", () => {
      render(
        <StructuredSourceRenderer
          blocks={UNCLOSED_FENCE_BLOCKS}
          diagnostic={UNCLOSED_FENCE_DIAGNOSTIC}
        />,
      );

      expect(
        screen.getByTestId("structured-source-warning-has_unclosed_fence"),
      ).toBeTruthy();

      const outcome = screen.getByTestId("structured-source-outcome");
      expect(outcome.getAttribute("data-outcome")).toBe(
        "candidate_document_required",
      );
    });
  });

  describe("reject_empty fixture", () => {
    it("renders single code_block and input_rejected outcome", () => {
      const { container } = render(
        <StructuredSourceRenderer
          blocks={REJECT_EMPTY_BLOCKS}
          diagnostic={REJECT_EMPTY_DIAGNOSTIC}
        />,
      );

      const codeBlock = container.querySelector('[data-block-id="b1"]');
      expect(codeBlock?.tagName).toBe("PRE");
      expect(codeBlock?.getAttribute("data-language")).toBe("python");

      const outcome = screen.getByTestId("structured-source-outcome");
      expect(outcome.getAttribute("data-outcome")).toBe(
        "input_rejected_or_action_required",
      );

      const warning = screen.getByTestId(
        "structured-source-warning-code_dominant",
      );
      expect(warning.getAttribute("data-warning-code")).toBe("code_dominant");
    });
  });

  describe("edge cases", () => {
    it("renders nothing in diagnostics panel when diagnostic is omitted", () => {
      render(<StructuredSourceRenderer blocks={SIMPLE_PARAGRAPH_BLOCKS} />);

      expect(screen.queryByTestId("structured-source-diagnostics")).toBeNull();
      expect(screen.queryByTestId("structured-source-outcome")).toBeNull();
    });

    it("renders empty blocks container for empty input", () => {
      render(<StructuredSourceRenderer blocks={[]} />);

      const blocksContainer = screen.getByTestId("structured-source-blocks");
      expect(blocksContainer.children.length).toBe(0);
    });

    it("renders unknown block type as defensive fallback paragraph", () => {
      const unknownBlock: ReaderStructuredSourceBlock = {
        block_id: "b1",
        block_type: "footnote" as ReaderStructuredSourceBlock["block_type"],
        text_content: "fallback text",
        payload_json: {},
        parent_block_id: null,
        order_index: 0,
        source_range: { line_start: 1, line_end: 1 },
      };

      // Replace block_type with an unknown value via a cast to test fallback
      const blockWithUnknownType = {
        ...unknownBlock,
        block_type: "unknown_future_block",
      } as unknown as ReaderStructuredSourceBlock;

      const { container } = render(
        <StructuredSourceRenderer blocks={[blockWithUnknownType]} />,
      );

      const fallback = container.querySelector('[data-unknown-block="true"]');
      expect(fallback).toBeTruthy();
      expect(fallback?.tagName).toBe("P");
      expect(fallback?.textContent).toBe("fallback text");
    });

    it("renderInlineMarks does not duplicate text when marks present", () => {
      // regression test for the text duplication bug in
      // renderInlineMarks. Previously `nodes` was initialized to
      // `[textContent]` and then each mark was pushed, so the rendered
      // output would contain textContent + concatenation of mark.text
      // (i.e., every character rendered twice).
      //
      // The fix: when marks are present, render ONLY the marks (which
      // MUST cover the full text span per the updated contract comment).
      // When marks are absent, render textContent as-is (unchanged).
      //
      // The backend G0 frozen contract does not emit inline_marks today,
      // so this test uses a synthetic marks array to guard the future
      // parser-version bump path.
      const marks = [
        { kind: "strong" as const, text: "Bold" },
        { kind: "emphasis" as const, text: " and italic" },
        { kind: "inline_code" as const, text: " + code" },
      ];
      const fullText = marks.map((m) => m.text).join("");

      const blocksWithMarks: ReaderStructuredSourceBlock[] = [
        {
          block_id: "b1",
          block_type: "paragraph",
          text_content: fullText,
          payload_json: {},
          parent_block_id: null,
          order_index: 0,
          source_range: { line_start: 1, line_end: 1 },
          inline_marks: marks,
        },
      ];

      const { container } = render(
        <StructuredSourceRenderer blocks={blocksWithMarks} />,
      );

      const paragraph = container.querySelector('[data-block-id="b1"]');
      expect(paragraph).toBeTruthy();
      // The rendered text must equal exactly the concatenation of mark
      // texts — NOT textContent + marks (which would double every char).
      expect(paragraph?.textContent).toBe(fullText);

      // Verify each mark kind rendered to its semantic element.
      expect(paragraph?.querySelector("strong")?.textContent).toBe("Bold");
      expect(paragraph?.querySelector("em")?.textContent).toBe(" and italic");
      expect(paragraph?.querySelector("code")?.textContent).toBe(" + code");
    });

    it("renderInlineMarks returns textContent when marks are absent (unchanged)", () => {
      // regression guard — the no-marks fast path must still
      // return textContent as a plain string (this is the path the G0
      // frozen backend actually takes today).
      const { container } = render(
        <StructuredSourceRenderer blocks={SIMPLE_PARAGRAPH_BLOCKS} />,
      );

      const paragraph = container.querySelector('[data-block-id="b1"]');
      expect(paragraph?.textContent).toBe(
        SIMPLE_PARAGRAPH_BLOCKS[0]?.text_content ?? "",
      );
      // No inline mark elements should be rendered.
      expect(paragraph?.querySelector("strong")).toBeNull();
      expect(paragraph?.querySelector("em")).toBeNull();
      expect(paragraph?.querySelector("code")).toBeNull();
    });
  });
});
