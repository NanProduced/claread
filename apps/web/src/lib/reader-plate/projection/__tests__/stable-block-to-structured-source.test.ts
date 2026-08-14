/**
 * Pure-function tests for `adaptStableBlocksToStructuredSource`.
 *
 * Verifies the fail-safe projection from the wide BFF DTO
 * (`ReaderStableDocumentBlockDto`, `block_type: string`) to the G0
 * Structured Source contract (`ReaderStructuredSourceBlock`, narrow union):
 *   - known block_type mapping
 *   - unknown block_type fallback to "paragraph" + diagnostic
 *   - links carried through payload → payload_json
 *   - source_range extracted from source_refs (+ utf16 carryover)
 *   - parent_block_id / order_index preserved
 *   - empty input → empty output
 *   - text_content: null preserved (not coerced)
 *
 * Reference: apps/web/docs/reader-ia.md §5 合同与 Fixture
 */

import { describe, expect, it } from "vitest";

import type { ReaderStableDocumentBlockDto } from "@/types/api/reader-plate";

import { adaptStableBlocksToStructuredSource } from "../stable-block-to-structured-source";

function makeBlock(
  overrides: Partial<ReaderStableDocumentBlockDto> & Pick<ReaderStableDocumentBlockDto, "block_id">,
): ReaderStableDocumentBlockDto {
  return {
    block_id: overrides.block_id,
    parent_block_id: overrides.parent_block_id ?? null,
    order_index: overrides.order_index ?? 0,
    block_type: overrides.block_type ?? "paragraph",
    text_content: overrides.text_content ?? null,
    payload: overrides.payload ?? {},
    source_refs: overrides.source_refs ?? {},
    quality: overrides.quality ?? {},
    canonical_text_start_utf16: overrides.canonical_text_start_utf16 ?? null,
    canonical_text_end_utf16: overrides.canonical_text_end_utf16 ?? null,
    interpretation_policy: overrides.interpretation_policy ?? {},
  };
}

describe("adaptStableBlocksToStructuredSource", () => {
  it("maps known block_type values to the narrow union", () => {
    const knownTypes = [
      "heading",
      "paragraph",
      "blockquote",
      "thematic_break",
      "list",
      "list_item",
      "code_block",
      "table",
      "table_row",
      "table_cell",
      "footnote",
    ] as const;

    const blocks = knownTypes.map((t, i) =>
      makeBlock({ block_id: `b${i}`, block_type: t, order_index: i }),
    );

    const out = adaptStableBlocksToStructuredSource(blocks);

    expect(out).toHaveLength(knownTypes.length);
    for (let i = 0; i < knownTypes.length; i++) {
      expect(out[i]!.block_type).toBe(knownTypes[i]);
      // No fallback warning for known types.
      expect(out[i]!.payload_json.adaptation_warning).toBeUndefined();
      expect(out[i]!.payload_json.original_block_type).toBeUndefined();
    }
  });

  it("falls back unknown block_type to paragraph with diagnostic", () => {
    const block = makeBlock({
      block_id: "b1",
      block_type: "some_future_block_type",
      text_content: "hello",
    });

    const out = adaptStableBlocksToStructuredSource([block]);

    expect(out).toHaveLength(1);
    expect(out[0]!.block_type).toBe("paragraph");
    expect(out[0]!.payload_json.original_block_type).toBe(
      "some_future_block_type",
    );
    expect(out[0]!.payload_json.adaptation_warning).toContain(
      "some_future_block_type",
    );
    expect(out[0]!.text_content).toBe("hello");
  });

  it("falls back non-string block_type to paragraph (coerced to empty string)", () => {
    // Build directly: makeBlock uses `??` which would coerce undefined to "paragraph".
    const block: ReaderStableDocumentBlockDto = {
      block_id: "b1",
      parent_block_id: null,
      order_index: 0,
      block_type: undefined as unknown as string,
      text_content: "x",
      payload: {},
      source_refs: {},
      quality: {},
      canonical_text_start_utf16: null,
      canonical_text_end_utf16: null,
      interpretation_policy: {},
    };

    const out = adaptStableBlocksToStructuredSource([block]);

    expect(out).toHaveLength(1);
    expect(out[0]!.block_type).toBe("paragraph");
    // Non-string block_type is coerced to "" before the warning is built.
    expect(out[0]!.payload_json.adaptation_warning).toContain('Unknown block_type ""');
    expect(out[0]!.payload_json.original_block_type).toBe("");
  });

  it("extracts links from payload into payload_json", () => {
    const links = [
      { text: "Claread", href: "https://claread.example" },
      { text: "docs", href: "https://docs.claread.example" },
    ];
    const block = makeBlock({
      block_id: "b1",
      block_type: "paragraph",
      payload: { links, level: 0 },
    });

    const out = adaptStableBlocksToStructuredSource([block]);

    expect(out[0]!.payload_json.links).toEqual(links);
    expect(out[0]!.payload_json.level).toBe(0);
  });

  it("extracts line_start / line_end from source_refs into source_range", () => {
    const block = makeBlock({
      block_id: "b1",
      block_type: "paragraph",
      source_refs: { line_start: 5, line_end: 7 },
      canonical_text_start_utf16: 100,
      canonical_text_end_utf16: 200,
    });

    const out = adaptStableBlocksToStructuredSource([block]);

    expect(out[0]!.source_range).toEqual({
      line_start: 5,
      line_end: 7,
      utf16_start: 100,
      utf16_end: 200,
    });
  });

  it("falls back source_range to 0/0 when source_refs missing", () => {
    const block = makeBlock({
      block_id: "b1",
      block_type: "paragraph",
      source_refs: {},
    });

    const out = adaptStableBlocksToStructuredSource([block]);

    expect(out[0]!.source_range).toEqual({
      line_start: 0,
      line_end: 0,
    });
  });

  it("falls back source_range to 0/0 when line values are non-finite", () => {
    const block = makeBlock({
      block_id: "b1",
      block_type: "paragraph",
      source_refs: { line_start: "abc", line_end: NaN },
    });

    const out = adaptStableBlocksToStructuredSource([block]);

    expect(out[0]!.source_range.line_start).toBe(0);
    expect(out[0]!.source_range.line_end).toBe(0);
  });

  it("preserves parent_block_id and order_index", () => {
    const blocks = [
      makeBlock({
        block_id: "b1",
        parent_block_id: null,
        order_index: 0,
        block_type: "list",
      }),
      makeBlock({
        block_id: "b2",
        parent_block_id: "b1",
        order_index: 1,
        block_type: "list_item",
      }),
    ];

    const out = adaptStableBlocksToStructuredSource(blocks);

    expect(out[0]!.parent_block_id).toBeNull();
    expect(out[0]!.order_index).toBe(0);
    expect(out[1]!.parent_block_id).toBe("b1");
    expect(out[1]!.order_index).toBe(1);
  });

  it("returns empty array for empty input", () => {
    expect(adaptStableBlocksToStructuredSource([])).toEqual([]);
  });

  it("returns empty array for non-array input", () => {
    expect(
      adaptStableBlocksToStructuredSource(
        undefined as unknown as ReaderStableDocumentBlockDto[],
      ),
    ).toEqual([]);
  });

  it("preserves text_content: null without coercion", () => {
    const block = makeBlock({
      block_id: "b1",
      block_type: "thematic_break",
      text_content: null,
    });

    const out = adaptStableBlocksToStructuredSource([block]);

    expect(out[0]!.text_content).toBeNull();
  });

  it("preserves text_content string value", () => {
    const block = makeBlock({
      block_id: "b1",
      block_type: "heading",
      text_content: "Title",
    });

    const out = adaptStableBlocksToStructuredSource([block]);

    expect(out[0]!.text_content).toBe("Title");
  });

  it("extracts quality.warnings into payload_json.quality_warnings", () => {
    const warnings = [
      { code: "raw_html_block", message: "Raw HTML", blocks_freeze: false },
    ];
    const block = makeBlock({
      block_id: "b1",
      block_type: "paragraph",
      quality: { warnings },
    });

    const out = adaptStableBlocksToStructuredSource([block]);

    expect(out[0]!.payload_json.quality_warnings).toEqual(warnings);
  });

  it("does not mutate the input block objects", () => {
    const block = makeBlock({
      block_id: "b1",
      block_type: "paragraph",
      payload: { links: [{ text: "x", href: "https://x.example" }] },
      source_refs: { line_start: 1, line_end: 1 },
    });
    const originalPayloadSnapshot = { ...block.payload };
    const originalSourceRefsSnapshot = { ...block.source_refs };

    adaptStableBlocksToStructuredSource([block]);

    expect(block.payload).toEqual(originalPayloadSnapshot);
    expect(block.source_refs).toEqual(originalSourceRefsSnapshot);
  });

  it("omits utf16 fields when canonical offsets are null", () => {
    const block = makeBlock({
      block_id: "b1",
      block_type: "paragraph",
      source_refs: { line_start: 2, line_end: 2 },
      canonical_text_start_utf16: null,
      canonical_text_end_utf16: null,
    });

    const out = adaptStableBlocksToStructuredSource([block]);

    expect(out[0]!.source_range.utf16_start).toBeUndefined();
    expect(out[0]!.source_range.utf16_end).toBeUndefined();
  });
});
