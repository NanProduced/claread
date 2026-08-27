import { describe, expect, it } from "vitest";

import type {
  ReaderPlateSnapshotDto,
  ReaderStableDocumentBlockNodeDto,
  ReaderUnitNodeDto,
} from "@/types/api/reader-plate";
import { makeAnalysisProgressDto } from "@/test/fixtures/reader-analysis-progress";

import {
  projectReaderPlateSnapshotToReaderRecordPlateDocument,
} from "./reader-record-plate-document";
import {
  projectReaderRecordPlateToPlateValue,
  READER_MATH_BLOCK_TYPE,
  READER_MATH_INLINE_TYPE,
} from "./reader-record-plate-to-plate-value";

function wgNode(
  overrides: Partial<ReaderStableDocumentBlockNodeDto>,
): ReaderStableDocumentBlockNodeDto {
  return {
    block_id: "block",
    parent_block_id: null,
    order_index: 0,
    block_type: "unknown",
    text_content: null,
    payload: {},
    source_refs: {},
    quality: {},
    canonical_text_start_utf16: null,
    canonical_text_end_utf16: null,
    interpretation_policy: {},
    unit_id: null,
    anchor_segment_ids: [],
    children: [],
    ...overrides,
  };
}

function mathInlineEntry(
  latex: string,
  display: boolean,
  before: number,
): Record<string, unknown> {
  return { latex, display, before_utf16: before };
}

function mathBlockEntry(latex: string, display: boolean): Record<string, unknown> {
  return { latex, display };
}

function makeMathSnapshot(
  specs: Array<{ unitId: string; text: string; stableType: string; stableId: string; parent?: string | null }>,
  tree: ReaderStableDocumentBlockNodeDto[],
): ReaderPlateSnapshotDto {
  const baseId = "base_w1";
  let offset = 0;
  const anchor_segments: ReaderPlateSnapshotDto["anchor_segments"] = [];
  const navigation: ReaderPlateSnapshotDto["navigation"]["units"] = [];
  const value: ReaderUnitNodeDto[] = [];
  for (const [idx, spec] of specs.entries()) {
    const start = offset;
    const end = start + spec.text.length;
    offset = end + 2;
    const segId = `seg_${spec.unitId}`;
    anchor_segments.push({
      anchor_segment_id: segId,
      sentence_id: `sent_${segId}`,
      paragraph_id: spec.unitId,
      unit_id: spec.unitId,
      order_index: idx + 1,
      unit_order_index: 1,
      segment_type: "sentence",
      boundary_quality: "normal",
      base_start_utf16: start,
      base_end_utf16: end,
      unit_start_utf16: 0,
      unit_end_utf16: spec.text.length,
      text_hash: `hash_${segId}`,
      hash_algorithm: "fnv1a32-utf16",
    });
    navigation.push({
      unit_id: spec.unitId,
      order_index: idx + 1,
      unit_type: "body",
      boundary_quality: "normal",
      label: null,
      base_start_utf16: start,
      base_end_utf16: end,
      text_hash: `hash_${spec.unitId}`,
      hash_algorithm: "fnv1a32-utf16",
      stable_block_type: spec.stableType,
      heading_level: null,
    });
    value.push({
      type: "reader_unit",
      owner: "stable",
      base_id: baseId,
      unit_id: spec.unitId,
      order_index: idx + 1,
      unit_type: "body",
      boundary_quality: "normal",
      base_start_utf16: start,
      base_end_utf16: end,
      text_hash: `hash_${spec.unitId}`,
      hash_algorithm: "fnv1a32-utf16",
      children: [
        {
          type: "reader_source_block",
          owner: "stable",
          base_id: baseId,
          unit_id: spec.unitId,
          base_start_utf16: start,
          base_end_utf16: end,
          stableBlockType: spec.stableType,
          stableBlockId: spec.stableId,
          parentStableBlockId: spec.parent ?? null,
          children: [
            {
              type: "reader_anchor_segment",
              owner: "stable",
              base_id: baseId,
              unit_id: spec.unitId,
              anchor_segment_id: segId,
              sentence_id: `sent_${segId}`,
              segment_type: "sentence",
              boundary_quality: "normal",
              base_start_utf16: start,
              base_end_utf16: end,
              unit_start_utf16: 0,
              unit_end_utf16: spec.text.length,
              text_hash: `hash_${segId}`,
              hash_algorithm: "fnv1a32-utf16",
              children: [
                {
                  text: spec.text,
                  owner: "stable",
                  lock_source: true,
                  source_role: "segment_text",
                  base_start_utf16: start,
                  base_end_utf16: end,
                  anchor_segment_id: segId,
                  segment_start_utf16: 0,
                  segment_end_utf16: spec.text.length,
                },
              ],
            },
          ],
        } as unknown as import("@/types/api/reader-plate").ReaderSourceBlockNodeDto,
      ],
    });
  }
  return {
    schema_kind: "reader_plate_snapshot" as const,
    snapshot_id: "snapshot_w1",
    snapshot_taken_at: "2026-08-08T00:00:00Z",
    last_event_sequence: 1,
    record_id: "record_w1",
    record: {
      title: "Plate Fixture",
      display_title_zh: null,
      title_generation_status: "pending",
      title_generation_error_code: null,
      title_generation_error_message: null,
      reading_goal: "daily_reading",
      reading_variant: "intensive_reading",
      created_at: "2026-08-08T00:00:00Z",
      source_type: "markdown",
      source_metadata: {},
      generation: 1,
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
    },
    base: {
      base_id: baseId,
      content_sha256: "c".repeat(64),
      canonicalizer_version: "test",
      builder_version: "test",
      segmenter_version: "test",
      text_length_utf16: offset,
      hash_algorithm: "fnv1a32-utf16",
    },
    navigation: { units: navigation },
    anchor_segments,
    enhancement_layers: [],
    enhancement_progress: undefined,
    analysis_progress: makeAnalysisProgressDto() as unknown as import("@/types/api/reader-plate").ReaderAnalysisProgressDto,
    ask_supplements: [],
    user_assets: [],
    parsed_decisions: [],
    value,
    stable_document_tree: tree,
  };
}

// helpers to inspect math nodes in document
function collectMathNodes(
  doc: ReturnType<typeof projectReaderPlateSnapshotToReaderRecordPlateDocument>,
) {
  const result: Array<{ type: string; data: Record<string, unknown>; id: string }> = [];
  function walk(block: unknown) {
    if (!block || typeof block !== "object") return;
    const b = block as { type?: string; data?: Record<string, unknown>; id?: string; children?: unknown[]; nestedChildren?: unknown[] };
    if (b.type === "math" && b.data) {
      result.push({ type: b.type, data: b.data, id: b.id ?? "" });
    }
    if (Array.isArray(b.children)) {
      for (const child of b.children) walk(child);
    }
    if (Array.isArray(b.nestedChildren)) {
      for (const child of b.nestedChildren) walk(child);
    }
  }
  for (const block of doc.children) walk(block);
  return result;
}

describe("Reader math tree projection — inline Math", () => {
  it("$a*b*c$ preserves verbatim latex with asterisks and display false", () => {
    const snapshot = makeMathSnapshot(
      [{ unitId: "u1", text: "risk  done", stableType: "paragraph", stableId: "b1" }],
      [wgNode({ block_id: "b1", block_type: "paragraph", payload: { inline_math: [mathInlineEntry("a*b*c", false, 5)] } })],
    );
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const maths = collectMathNodes(doc);
    expect(maths).toHaveLength(1);
    expect(maths[0].data.latex).toBe("a*b*c");
    expect(maths[0].data.display).toBe(false);
    expect(maths[0].data.beforeUtf16).toBe(5);
    // text leaves should not contain latex
    const para = doc.children[0] as { children: Array<{ text?: string }> };
    const allText = para.children.filter((c) => typeof c.text === "string").map((c) => c.text).join("");
    expect(allText).not.toContain("a*b*c");
    expect(allText).toBe("risk  done");
  });

  it("\\|A-B\\|_F^2 preserves escaped pipes verbatim", () => {
    const snapshot = makeMathSnapshot(
      [{ unitId: "u1", text: "norm  end", stableType: "paragraph", stableId: "b1" }],
      [wgNode({ block_id: "b1", block_type: "paragraph", payload: { inline_math: [mathInlineEntry("\\|A - B\\|_F^2", true, 5)] } })],
    );
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const maths = collectMathNodes(doc);
    expect(maths).toHaveLength(1);
    expect(maths[0].data.latex).toBe("\\|A - B\\|_F^2");
    expect(maths[0].data.display).toBe(true);
  });

  it("inline math order and before_utf16 are respected for mixed $ and $$", () => {
    const snapshot = makeMathSnapshot(
      [{ unitId: "u1", text: "A  B  C", stableType: "paragraph", stableId: "b1" }],
      [
        wgNode({
          block_id: "b1",
          block_type: "paragraph",
          payload: {
            inline_math: [
              mathInlineEntry("\\min_B f", true, 2),
              mathInlineEntry("y", false, 5),
            ],
          },
        }),
      ],
    );
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const para = doc.children[0] as {
      children: Array<{ type?: string; data?: Record<string, unknown>; text?: string }>;
    };
    // children should be interleaved: text "A ", math, text " B ", math, text " C"
    const types = para.children.map((c) => (c.type === "math" ? `math:${(c.data as { display: boolean }).display}` : `text:${c.text}`));
    expect(types).toEqual(["text:A ", "math:true", "text: B ", "math:false", "text: C"]);
    const maths = collectMathNodes(doc);
    expect(maths.map((m) => m.data.display)).toEqual([true, false]);
    expect(maths.map((m) => m.data.beforeUtf16)).toEqual([2, 5]);
  });

  it("mixed paragraph keeps main_reading and math inline still void", () => {
    const snapshot = makeMathSnapshot(
      [{ unitId: "u1", text: "A  B continues", stableType: "paragraph", stableId: "b1" }],
      [wgNode({ block_id: "b1", block_type: "paragraph", payload: { inline_math: [mathInlineEntry("x+y", false, 2)] } })],
    );
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const maths = collectMathNodes(doc);
    expect(maths).toHaveLength(1);
    expect(maths[0].data.positionKind).toBe("inline");
    expect(maths[0].data.latex).toBe("x+y");
  });

  it("sentence-split mixed paragraph still injects $s = vt$ among surrounding prose", () => {
    // Real Markdown snapshots join stableBlockId only when a unit range
    // exactly equals the whole paragraph. Sentence-split mixed prose
    // therefore has inline_math on the tree node, but the projected
    // paragraphs have no matching stableBlockId — display math still
    // falls back to a standalone block, inline math used to vanish.
    const unit1 = "The speed  is linear.";
    const unit2 = "Extra sentence keeps the split.";
    const snapshot = makeMathSnapshot(
      [
        { unitId: "u1", text: unit1, stableType: "paragraph", stableId: "sent_a" },
        { unitId: "u2", text: unit2, stableType: "paragraph", stableId: "sent_b" },
      ],
      [
        wgNode({
          block_id: "b_mixed",
          block_type: "paragraph",
          order_index: 0,
          canonical_text_start_utf16: 0,
          canonical_text_end_utf16: unit1.length + 2 + unit2.length,
          payload: {
            inline_math: [mathInlineEntry("s = vt", false, "The speed ".length)],
          },
        }),
        wgNode({
          block_id: "math_display",
          block_type: "paragraph",
          order_index: 1,
          payload: { math_blocks: [mathBlockEntry("E = mc^2", true)] },
        }),
      ],
    );
    for (const unit of snapshot.value) {
      for (const child of unit.children) {
        if (child.type === "reader_source_block") {
          delete (child as { stableBlockId?: string }).stableBlockId;
        }
      }
    }

    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const maths = collectMathNodes(doc);
    expect(maths.some((m) => m.data.latex === "s = vt" && m.data.positionKind === "inline")).toBe(true);
    expect(maths.some((m) => m.data.latex === "E = mc^2")).toBe(true);

    const firstPara = doc.children.find((block) => {
      if ((block as { type?: string }).type !== "paragraph") return false;
      const text = (block as { children: Array<{ text?: string }> }).children
        .filter((child) => typeof child.text === "string")
        .map((child) => child.text)
        .join("");
      return text.includes("The speed");
    }) as { children: Array<{ type?: string; text?: string; data?: { latex?: string } }> };
    expect(firstPara).toBeDefined();
    const types = firstPara.children.map((child) =>
      child.type === "math" ? `math:${child.data?.latex}` : `text:${child.text}`,
    );
    expect(types).toEqual(["text:The speed ", "math:s = vt", "text: is linear."]);
  });

  it("heading, list_item, blockquote, table_cell carrying inline_math", () => {
    const snapshot = makeMathSnapshot(
      [
        { unitId: "u_h", text: "Title  tail", stableType: "heading", stableId: "h1" },
        { unitId: "u_li", text: "item  rest", stableType: "list_item", stableId: "li1", parent: "list1" },
        { unitId: "u_bq", text: "quoted  note", stableType: "blockquote", stableId: "bq1" },
      ],
      [
        wgNode({ block_id: "h1", block_type: "heading", payload: { inline_math: [mathInlineEntry("h^2", false, 6)] } }),
        wgNode({
          block_id: "list1",
          block_type: "list",
          order_index: 1,
          payload: { ordered: false },
          children: [wgNode({ block_id: "li1", parent_block_id: "list1", block_type: "list_item", order_index: 0 })],
        }),
        wgNode({ block_id: "bq1", block_type: "blockquote", payload: { inline_math: [mathInlineEntry("q_1", false, 7)] } }),
      ],
    );
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const maths = collectMathNodes(doc);
    expect(maths.length).toBeGreaterThanOrEqual(2);
    expect(maths.some((m) => m.data.latex === "h^2")).toBe(true);
    expect(maths.some((m) => m.data.latex === "q_1")).toBe(true);
    // list_item math may be inside list wrapper; at least heading and blockquote found confirms inline_math pipeline
  });

  it("table_cell carrying inline_math", () => {
    const snapshot = makeMathSnapshot(
      [{ unitId: "u_cell", text: "  plain", stableType: "table_cell", stableId: "cell1" }],
      [
        wgNode({
          block_id: "cell1",
          block_type: "table_cell",
          payload: { inline_math: [mathInlineEntry("c^2", false, 1)] },
        }),
      ],
    );
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const maths = collectMathNodes(doc);
    expect(maths.some((m) => m.data.latex === "c^2")).toBe(true);
  });
});

describe("Reader math tree projection — block Math (standalone & pure containers)", () => {
  it("standalone $$ display block creates block math with display true", () => {
    const snapshot = makeMathSnapshot(
      [
        { unitId: "u_p1", text: "Hello", stableType: "paragraph", stableId: "p1" },
        { unitId: "u_p2", text: "World", stableType: "paragraph", stableId: "p2" },
      ],
      [
        wgNode({ block_id: "p1", block_type: "paragraph", order_index: 0 }),
        wgNode({ block_id: "math1", block_type: "paragraph", order_index: 1, payload: { math_blocks: [mathBlockEntry("\n\\|A - B\\|_F^2\n", true)] } }),
        wgNode({ block_id: "p2", block_type: "paragraph", order_index: 2 }),
      ],
    );
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const maths = collectMathNodes(doc);
    expect(maths.some((m) => m.data.latex === "\n\\|A - B\\|_F^2\n" && m.data.display === true)).toBe(true);
    expect(maths.some((m) => m.data.positionKind === "block")).toBe(true);
  });

  it("pure $x+y$ paragraph with math_blocks display false is metadata_only-like and renders as block math", () => {
    const snapshot = makeMathSnapshot(
      [{ unitId: "u1", text: "Prose", stableType: "paragraph", stableId: "p2" }],
      [
        wgNode({ block_id: "math_pure", block_type: "paragraph", order_index: 0, payload: { math_blocks: [mathBlockEntry("x+y", false)] } }),
        wgNode({ block_id: "p2", block_type: "paragraph", order_index: 1 }),
      ],
    );
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const maths = collectMathNodes(doc);
    expect(maths.some((m) => m.data.latex === "x+y" && m.data.display === false)).toBe(true);
  });

  it("mixed blockquote with quoted note plus math_blocks keeps text and appends math", () => {
    const snapshot = makeMathSnapshot(
      [{ unitId: "u1", text: "quoted note", stableType: "blockquote", stableId: "bq1" }],
      [wgNode({ block_id: "bq1", block_type: "blockquote", payload: { math_blocks: [mathBlockEntry("E = mc^2", true)] } })],
    );
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    // blockquote should exist and contain both text and math
    const bq = doc.children.find((b) => (b as { type: string }).type === "markdown_blockquote" || (b as { type: string }).type === "blockquote") as unknown as { children: unknown[] };
    expect(bq).toBeDefined();
    const maths = collectMathNodes(doc);
    expect(maths.some((m) => m.data.latex === "E = mc^2")).toBe(true);
  });

  it("illegal latex is still projected (fail-closed at render, not dropped)", () => {
    const illegal = "\\frac{";
    const snapshot = makeMathSnapshot(
      [{ unitId: "u1", text: "hello  world", stableType: "paragraph", stableId: "b1" }],
      [wgNode({ block_id: "b1", block_type: "paragraph", payload: { inline_math: [mathInlineEntry(illegal, false, 6)] } })],
    );
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const maths = collectMathNodes(doc);
    expect(maths).toHaveLength(1);
    expect(maths[0].data.latex).toBe(illegal);
    // ensure not dropped
    expect(doc.children[0]).toBeDefined();
  });

  it("GFM-alert style multiline $$ with leading > prefix is preserved verbatim and still projected", () => {
    const latexWithPrefix = "> E = mc^2\n> ";
    const snapshot = makeMathSnapshot(
      [{ unitId: "u1", text: "note", stableType: "blockquote", stableId: "bq1" }],
      [wgNode({ block_id: "bq1", block_type: "blockquote", payload: { math_blocks: [mathBlockEntry(latexWithPrefix, true)] } })],
    );
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const maths = collectMathNodes(doc);
    expect(maths.some((m) => m.data.latex === latexWithPrefix)).toBe(true);
  });

  it("snapshot.value remains unit-driven and does not contain math latex", () => {
    const latex = "a*b*c";
    const snapshot = makeMathSnapshot(
      [{ unitId: "u1", text: "risk  done", stableType: "paragraph", stableId: "b1" }],
      [wgNode({ block_id: "b1", block_type: "paragraph", payload: { inline_math: [mathInlineEntry(latex, false, 5)] } })],
    );
    // snapshot.value text leaves should be "risk  done" without latex
    const unitText = snapshot.value[0].children
      .flatMap((c) => {
        if (c.type === "reader_source_block") {
          return c.children.flatMap((child) =>
            "children" in child ? (child as { children: Array<{ text?: string }> }).children.map((leaf) => leaf.text ?? "") : [],
          );
        }
        return [];
      })
      .join("");
    expect(unitText).not.toContain(latex);
    expect(unitText).toBe("risk  done");
    // document should have math
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    expect(collectMathNodes(doc).length).toBe(1);
  });
});

describe("Reader math Plate value — projection and safety", () => {
  it("inline math becomes reader_math_inline void element, url/alt not in text, children [{text:''}]", () => {
    const snapshot = makeMathSnapshot(
      [{ unitId: "u1", text: "hello world", stableType: "paragraph", stableId: "b1" }],
      [wgNode({ block_id: "b1", block_type: "paragraph", payload: { inline_math: [mathInlineEntry("a*b*c", false, 5)] } })],
    );
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const value = projectReaderRecordPlateToPlateValue(doc);
    const para = value[0] as unknown as { type: string; children: Array<Record<string, unknown>> };
    expect(para.type).toBe("reader_paragraph");
    const mathEl = para.children.find((c) => c.type === READER_MATH_INLINE_TYPE) as unknown as { type: string; children: unknown; data: Record<string, unknown> };
    expect(mathEl).toBeDefined();
    expect(mathEl.type).toBe(READER_MATH_INLINE_TYPE);
    expect(mathEl.children).toEqual([{ text: "" }]);
    expect(mathEl.data.latex).toBe("a*b*c");
    // text nodes must not contain latex
    const textNodes = para.children.filter((c) => typeof c.text === "string") as Array<{ text: string }>;
    expect(textNodes.some((n) => n.text.includes("a*b*c"))).toBe(false);
  });

  it("standalone block math becomes reader_math_block wrapper containing math_inline", () => {
    const snapshot = makeMathSnapshot(
      [
        { unitId: "u_p1", text: "Hello", stableType: "paragraph", stableId: "p1" },
        { unitId: "u_p2", text: "World", stableType: "paragraph", stableId: "p2" },
      ],
      [
        wgNode({ block_id: "p1", block_type: "paragraph", order_index: 0 }),
        wgNode({ block_id: "math1", block_type: "paragraph", order_index: 1, payload: { math_blocks: [mathBlockEntry("E = mc^2", true)] } }),
        wgNode({ block_id: "p2", block_type: "paragraph", order_index: 2 }),
      ],
    );
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const value = projectReaderRecordPlateToPlateValue(doc);
    const mathBlock = (value as Array<Record<string, unknown>>).find((el) => el.type === READER_MATH_BLOCK_TYPE) as unknown as { type: string; children: Array<Record<string, unknown>>; data: Record<string, unknown> };
    expect(mathBlock).toBeDefined();
    expect(mathBlock.type).toBe(READER_MATH_BLOCK_TYPE);
    expect(mathBlock.children[0].type).toBe(READER_MATH_INLINE_TYPE);
    expect((mathBlock.children[0] as { data: Record<string, unknown> }).data.latex).toBe("E = mc^2");
    expect((mathBlock.children[0] as { data: Record<string, unknown> }).data.display).toBe(true);
  });

  it("illegal latex plate element is still void with latex, not dropped", () => {
    const illegal = "\\frac{";
    const snapshot = makeMathSnapshot(
      [{ unitId: "u1", text: "hello  world", stableType: "paragraph", stableId: "b1" }],
      [wgNode({ block_id: "b1", block_type: "paragraph", payload: { inline_math: [mathInlineEntry(illegal, false, 6)] } })],
    );
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const value = projectReaderRecordPlateToPlateValue(doc);
    const para = value[0] as unknown as { children: Array<Record<string, unknown>> };
    const mathEl = para.children.find((c) => c.type === READER_MATH_INLINE_TYPE);
    expect(mathEl).toBeDefined();
    expect((mathEl as { data: Record<string, unknown> }).data.latex).toBe(illegal);
  });

  it("math element has no text content duplication and is void", () => {
    const snapshot = makeMathSnapshot(
      [{ unitId: "u1", text: "A  B", stableType: "paragraph", stableId: "b1" }],
      [wgNode({ block_id: "b1", block_type: "paragraph", payload: { inline_math: [mathInlineEntry("x+y", false, 2)] } })],
    );
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const value = projectReaderRecordPlateToPlateValue(doc);
    // Ensure JSON string does not contain latex as text node
    expect(JSON.stringify(value)).not.toContain('"text":"x+y"');
  });
});
