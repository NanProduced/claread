/**
 * Tests for T4.2a-PUX-R4-R2 / R2.1E: incremental-projection-merger pure function.
 *
 * Covers:
 * - G1 user_assets: upsert → targeted_apply (replace paragraph block)
 * - G2 ask_supplements: upsert → targeted_apply (replace callout block)
 * - G2 ask_supplements: delete → targeted_apply (remove callout block)
 * - G3 record_metadata: status_changed → targeted_apply (empty operations)
 * - R2.1E layer_published: same-topology revision → targeted_apply (changed-block-only replace)
 * - R2.1E layer_published: new / deleted / reordered / missing block_id / fence mismatch → fallback
 * - R2.1E layer_published: mixed batch (layer_published + projection_ops) → fallback
 * - Fail-closed: missing payload, unknown section/operation, fence mismatch,
 *   target not found, generation changed, base changed,
 *   non-representation event, no trigger events, delete target missing.
 */

import type { Descendant } from "platejs";
import { describe, expect, it } from "vitest";

import { mergeIncrementalProjection } from "@/lib/reader-plate-snapshot/incremental-projection-merger";
import type {
  ReaderEventResponseDto,
  ReaderPlateSnapshotDto,
  ReaderSnapshotAskSupplementDto,
  ReaderSnapshotUserAssetDto,
} from "@/types/api/reader-plate";

// ---------------------------------------------------------------------------
// Fixture builders
// ---------------------------------------------------------------------------

const BASE_ID = "base_test_001";
const GENERATION = 1;

function makeTextRangeAnchor(anchorSegmentId: string) {
  return {
    anchor_type: "text_range" as const,
    base_id: BASE_ID,
    unit_id: "unit_1",
    anchor_segment_id: anchorSegmentId,
    sentence_id: "sentence_1",
    segment_type: "sentence" as const,
    offset_unit: "utf16" as const,
    start_offset: 0,
    end_offset: 10,
    selected_text: "hello",
    text_hash: "hash_001",
    hash_algorithm: "fnv1a32-utf16" as const,
  };
}

function makeUserAsset(
  assetId: string,
  anchorSegmentId: string,
  options: { deletedAt?: string | null; noteText?: string } = {},
): ReaderSnapshotUserAssetDto {
  return {
    asset_id: assetId,
    asset_type: "highlight",
    owner: "user",
    reading_record_id: "rec_1",
    generation: GENERATION,
    anchor: makeTextRangeAnchor(anchorSegmentId),
    note_text: options.noteText ?? null,
    color: "#ff0000",
    created_at: "2026-07-14T00:00:00Z",
    updated_at: "2026-07-14T00:00:00Z",
    deleted_at: options.deletedAt ?? null,
  };
}

function makeSupplement(
  supplementId: string,
  anchorSegmentId: string,
): ReaderSnapshotAskSupplementDto {
  return {
    supplement_id: supplementId,
    owner: "ask_supplement",
    anchor: makeTextRangeAnchor(anchorSegmentId),
    content: {
      supplement_type: "grammar_note",
      title: "Test Supplement",
      content_md: "Test content",
      lifecycle_status: "persisted",
    },
    created_at: "2026-07-14T00:00:00Z",
  };
}

function makeSnapshot(options: {
  userAssets?: ReaderSnapshotUserAssetDto[];
  askSupplements?: ReaderSnapshotAskSupplementDto[];
  generation?: number;
  baseId?: string;
  lastEventSequence?: number;
  displayTitleZh?: string | null;
}): ReaderPlateSnapshotDto {
  return {
    schema_kind: "reader_plate_snapshot",
    snapshot_id: `snap_${options.lastEventSequence ?? 1}`,
    snapshot_taken_at: "2026-07-14T00:00:00Z",
    last_event_sequence: options.lastEventSequence ?? 1,
    record_id: "rec_1",
    record: {
      title: "Test Record",
      display_title_zh: options.displayTitleZh ?? "测试标题",
      title_generation_status: "succeeded",
      title_generation_error_code: null,
      title_generation_error_message: null,
      reading_goal: "daily_reading",
      reading_variant: "intensive_reading",
      created_at: "2026-07-14T00:00:00Z",
      source_type: "url",
      source_metadata: {},
      generation: options.generation ?? GENERATION,
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
    },
    base: {
      base_id: options.baseId ?? BASE_ID,
      content_sha256: "sha256_test",
      canonicalizer_version: "v1",
      builder_version: "v1",
      segmenter_version: "v1",
      text_length_utf16: 100,
      hash_algorithm: "fnv1a32-utf16",
    },
    navigation: { units: [] },
    anchor_segments: [
      {
        anchor_segment_id: "seg_1",
        sentence_id: "sentence_1",
        paragraph_id: "para_1",
        unit_id: "unit_1",
        order_index: 0,
        unit_order_index: 0,
        segment_type: "sentence",
        boundary_quality: "normal",
        base_start_utf16: 0,
        base_end_utf16: 10,
        unit_start_utf16: 0,
        unit_end_utf16: 10,
        text_hash: "hash_001",
        hash_algorithm: "fnv1a32-utf16",
      },
    ],
    enhancement_layers: [],
    ask_supplements: options.askSupplements ?? [],
    user_assets: options.userAssets ?? [],
    parsed_decisions: [],
    value: [],
  };
}

function makeRepresentationEvent(
  eventType: "projection_ops" | "record_state_changed",
  section: string,
  operation: string,
  targetKeys: string[],
  options: {
    generation?: number;
    baseId?: string;
    schemaVersion?: number;
    sequence?: number;
  } = {},
): ReaderEventResponseDto {
  return {
    id: `evt_${options.sequence ?? 1}`,
    reading_record_id: "rec_1",
    sequence: options.sequence ?? 1,
    event_type: eventType,
    payload: {
      schema_version: options.schemaVersion ?? 1,
      representation_section: section,
      operation,
      target_keys: targetKeys,
      generation: options.generation ?? GENERATION,
      base_id: options.baseId ?? BASE_ID,
    },
    created_at: "2026-07-14T00:00:00Z",
  };
}

function makePlateNode(id: string, text: string): Descendant {
  return {
    type: "reader_paragraph",
    id,
    children: [{ text }],
    data: {},
  } as unknown as Descendant;
}

function makeCalloutNode(id: string, text: string): Descendant {
  return {
    type: "reader_callout",
    id,
    variant: "supplement",
    icon: "💬",
    children: [{ text }],
    data: {},
  } as unknown as Descendant;
}

// R2.1E: layer_published fixture builders.

const LAYER_UNIT_ID = "unit_1";

function makeLayerParagraphNode(
  anchorSegmentId: string,
  text: string,
  unitId: string = LAYER_UNIT_ID,
): Descendant {
  return {
    type: "paragraph",
    id: `paragraph:${anchorSegmentId}`,
    children: [{ text }],
    data: { anchorSegmentId, unitId },
  } as unknown as Descendant;
}

/**
 * R2.2-P1: Build a paragraph node carrying vocabulary marks on its text leaf.
 *
 * vocabulary first-publish / revision changes the projected paragraph by
 * mutating `reader_vocabulary_marks` data on the source leaf — the paragraph
 * block_id (`paragraph:{anchorSegmentId}`) and the block ordering stay
 * identical, but the leaf's marks array changes. This mirrors the real
 * projection path in `reader-record-plate-document.ts` where
 * `splitTextLeafByMarks` produces children with `marks` arrays.
 */
function makeLayerParagraphNodeWithVocabularyMarks(
  anchorSegmentId: string,
  text: string,
  marks: Array<{
    id: string;
    kind: "vocab_highlight" | "phrase_gloss" | "context_gloss";
    gloss: string;
  }>,
  unitId: string = LAYER_UNIT_ID,
): Descendant {
  return {
    type: "paragraph",
    id: `paragraph:${anchorSegmentId}`,
    children: [
      {
        text,
        marks: marks.map((m) => ({
          id: m.id,
          kind: m.kind,
          vocabulary:
            m.kind === "vocab_highlight"
              ? {
                  itemType: "vocab_highlight",
                  headword: m.gloss,
                  briefExplanation: m.gloss,
                }
              : m.kind === "phrase_gloss"
                ? {
                    itemType: "phrase_gloss",
                    phrase: m.gloss,
                    phraseType: "fixed_collocation",
                    gloss: m.gloss,
                  }
                : {
                    itemType: "context_gloss",
                    display: m.gloss,
                    gloss: m.gloss,
                  },
        })),
      },
    ],
    data: { anchorSegmentId, unitId },
  } as unknown as Descendant;
}

function makeLayerBlockquoteNode(
  layerId: string,
  groupId: string,
  text: string,
  unitId: string = LAYER_UNIT_ID,
): Descendant {
  return {
    type: "blockquote",
    id: `blockquote:${layerId}:${groupId}`,
    children: [{ text }],
    data: { unitId, layerId, groupId },
  } as unknown as Descendant;
}

function makeLayerGrammarCalloutNode(
  itemId: string,
  text: string,
  unitId: string = LAYER_UNIT_ID,
  layerId: string = "layer_grammar_1",
): Descendant {
  return {
    type: "callout",
    id: `callout:grammar:${itemId}`,
    variant: "grammar",
    icon: "📖",
    children: [{ text }],
    data: { unitId, layerId, itemId, anchorSegmentId: "seg_1" },
  } as unknown as Descendant;
}

function makeLayerSentenceAnalysisNode(
  analysisId: string,
  text: string,
  unitId: string = LAYER_UNIT_ID,
  layerId: string = "layer_sentence_analysis_1",
): Descendant {
  return {
    type: "sentence_analysis",
    id: `sentence_analysis:${analysisId}`,
    icon: "🔬",
    children: [{ text }],
    data: { unitId, layerId, analysisId, anchorSegmentId: "seg_1" },
  } as unknown as Descendant;
}

/**
 * Build a `layer_published` representation event with the v1 payload schema
 * (record_id, base_id, layer_id, layer_type, target_scope, target_key,
 * generation). Used to exercise the R2.1E changed-block-only path.
 */
function makeLayerPublishedEvent(
  layerType: "translation" | "vocabulary" | "grammar_note" | "sentence_analysis",
  targetKey: string = LAYER_UNIT_ID,
  options: {
    layerId?: string;
    generation?: number;
    baseId?: string;
    recordId?: string;
    sequence?: number;
    targetScope?: string;
  } = {},
): ReaderEventResponseDto {
  const {
    layerId = `layer_${layerType}_1`,
    generation = GENERATION,
    baseId = BASE_ID,
    recordId = "rec_1",
    sequence = 2,
    targetScope = "unit",
  } = options;
  return {
    id: `evt_${sequence}`,
    reading_record_id: recordId,
    sequence,
    event_type: "layer_published",
    payload: {
      record_id: recordId,
      base_id: baseId,
      layer_id: layerId,
      layer_type: layerType,
      target_scope: targetScope,
      target_key: targetKey,
      generation,
    },
    created_at: "2026-07-14T00:00:00Z",
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("mergeIncrementalProjection", () => {
  const snapshotFence = { generation: GENERATION, baseId: BASE_ID };

  // --- G1: user_assets ---

  describe("G1 user_assets", () => {
    it("upsert: returns targeted_apply with replace paragraph block", () => {
      const prevSnapshot = makeSnapshot({
        userAssets: [makeUserAsset("asset_1", "seg_1")],
        lastEventSequence: 1,
      });
      const nextSnapshot = makeSnapshot({
        userAssets: [
          makeUserAsset("asset_1", "seg_1", { noteText: "updated note" }),
        ],
        lastEventSequence: 2,
      });
      const event = makeRepresentationEvent(
        "projection_ops",
        "user_assets",
        "upsert",
        ["asset_1"],
        { sequence: 2 },
      );

      const prevChildren = [
        makePlateNode("paragraph:seg_1", "hello"),
      ];
      const nextChildren = [
        makePlateNode("paragraph:seg_1", "hello (updated)"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("targeted_apply");
      if (result.kind !== "targeted_apply") return;
      expect(result.operations).toHaveLength(1);
      expect(result.operations[0].type).toBe("replace");
      expect(result.operations[0].path).toEqual([0]);
      expect(result.operations[0].blockId).toBe("paragraph:seg_1");
      expect(result.operations[0].nodes).toEqual([nextChildren[0]]);
      expect(result.affectedTargetKeys).toEqual(["asset_1"]);
      expect(result.preservedInteraction.preserveSelection).toBe(true);
      expect(result.preservedInteraction.preserveScroll).toBe(true);
    });

    it("delete: returns targeted_apply with replace paragraph block", () => {
      const prevSnapshot = makeSnapshot({
        userAssets: [makeUserAsset("asset_1", "seg_1")],
        lastEventSequence: 1,
      });
      // Asset is gone from next snapshot (or marked deleted_at)
      const nextSnapshot = makeSnapshot({
        userAssets: [],
        lastEventSequence: 2,
      });
      const event = makeRepresentationEvent(
        "projection_ops",
        "user_assets",
        "delete",
        ["asset_1"],
        { sequence: 2 },
      );

      const prevChildren = [
        makePlateNode("paragraph:seg_1", "hello with mark"),
      ];
      const nextChildren = [
        makePlateNode("paragraph:seg_1", "hello"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("targeted_apply");
      if (result.kind !== "targeted_apply") return;
      expect(result.operations).toHaveLength(1);
      expect(result.operations[0].type).toBe("replace");
      expect(result.operations[0].blockId).toBe("paragraph:seg_1");
    });
  });

  // --- G2: ask_supplements ---

  describe("G2 ask_supplements", () => {
    it("upsert: returns targeted_apply with replace callout block", () => {
      const prevSnapshot = makeSnapshot({
        askSupplements: [makeSupplement("supp_1", "seg_1")],
        lastEventSequence: 1,
      });
      const nextSnapshot = makeSnapshot({
        askSupplements: [makeSupplement("supp_1", "seg_1")],
        lastEventSequence: 2,
      });
      const event = makeRepresentationEvent(
        "projection_ops",
        "ask_supplements",
        "upsert",
        ["supp_1"],
        { sequence: 2 },
      );

      const prevChildren = [
        makeCalloutNode("callout:supplement:supp_1", "old content"),
      ];
      const nextChildren = [
        makeCalloutNode("callout:supplement:supp_1", "new content"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("targeted_apply");
      if (result.kind !== "targeted_apply") return;
      expect(result.operations).toHaveLength(1);
      expect(result.operations[0].type).toBe("replace");
      expect(result.operations[0].path).toEqual([0]);
      expect(result.operations[0].blockId).toBe(
        "callout:supplement:supp_1",
      );
      expect(result.operations[0].nodes).toEqual([nextChildren[0]]);
    });

    it("delete: returns targeted_apply with remove callout block", () => {
      const prevSnapshot = makeSnapshot({
        askSupplements: [makeSupplement("supp_1", "seg_1")],
        lastEventSequence: 1,
      });
      const nextSnapshot = makeSnapshot({
        askSupplements: [],
        lastEventSequence: 2,
      });
      const event = makeRepresentationEvent(
        "projection_ops",
        "ask_supplements",
        "delete",
        ["supp_1"],
        { sequence: 2 },
      );

      const prevChildren = [
        makeCalloutNode("callout:supplement:supp_1", "content"),
      ];
      const nextChildren: Descendant[] = [];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("targeted_apply");
      if (result.kind !== "targeted_apply") return;
      expect(result.operations).toHaveLength(1);
      expect(result.operations[0].type).toBe("remove");
      expect(result.operations[0].path).toEqual([0]);
      expect(result.operations[0].nodes).toBeUndefined();
    });

    it("reactivate: returns targeted_apply with replace callout block", () => {
      const prevSnapshot = makeSnapshot({
        askSupplements: [makeSupplement("supp_1", "seg_1")],
        lastEventSequence: 1,
      });
      const nextSnapshot = makeSnapshot({
        askSupplements: [makeSupplement("supp_1", "seg_1")],
        lastEventSequence: 2,
      });
      const event = makeRepresentationEvent(
        "projection_ops",
        "ask_supplements",
        "reactivate",
        ["supp_1"],
        { sequence: 2 },
      );

      const prevChildren = [
        makeCalloutNode("callout:supplement:supp_1", "old"),
      ];
      const nextChildren = [
        makeCalloutNode("callout:supplement:supp_1", "reactivated"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("targeted_apply");
      if (result.kind !== "targeted_apply") return;
      expect(result.operations[0].type).toBe("replace");
    });
  });

  // --- G3: record_metadata ---

  describe("G3 record_metadata", () => {
    it("status_changed: returns targeted_apply with empty operations", () => {
      const prevSnapshot = makeSnapshot({
        displayTitleZh: "旧标题",
        lastEventSequence: 1,
      });
      const nextSnapshot = makeSnapshot({
        displayTitleZh: "新标题",
        lastEventSequence: 2,
      });
      const event = makeRepresentationEvent(
        "record_state_changed",
        "record_metadata",
        "status_changed",
        ["display_title_zh"],
        { sequence: 2 },
      );

      const prevChildren = [makePlateNode("paragraph:seg_1", "hello")];
      const nextChildren = [makePlateNode("paragraph:seg_1", "hello")];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("targeted_apply");
      if (result.kind !== "targeted_apply") return;
      expect(result.operations).toHaveLength(0);
      expect(result.affectedTargetKeys).toEqual(["display_title_zh"]);
      expect(result.preservedInteraction.preserveSelection).toBe(true);
    });

    it("title_generation_status: returns targeted_apply with empty operations", () => {
      const prevSnapshot = makeSnapshot({
        displayTitleZh: null,
        lastEventSequence: 1,
      });
      prevSnapshot.record.title_generation_status = "pending";

      const nextSnapshot = makeSnapshot({
        displayTitleZh: "生成完成标题",
        lastEventSequence: 2,
      });
      nextSnapshot.record.title_generation_status = "succeeded";

      const event = makeRepresentationEvent(
        "record_state_changed",
        "record_metadata",
        "status_changed",
        ["title_generation_status"],
        { sequence: 2 },
      );

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren: [makePlateNode("paragraph:seg_1", "hello")],
        nextChildren: [makePlateNode("paragraph:seg_1", "hello")],
        snapshotFence,
      });

      expect(result.kind).toBe("targeted_apply");
      if (result.kind !== "targeted_apply") return;
      expect(result.operations).toHaveLength(0);
    });
  });

  // --- Batch semantics ---

  describe("batch semantics", () => {
    it("orders multiple supplement deletes from the highest original path", () => {
      const prevSnapshot = makeSnapshot({
        askSupplements: [
          makeSupplement("supp_early", "seg_1"),
          makeSupplement("supp_late", "seg_1"),
        ],
        lastEventSequence: 1,
      });
      const nextSnapshot = makeSnapshot({
        askSupplements: [],
        lastEventSequence: 3,
      });
      const earlyDelete = makeRepresentationEvent(
        "projection_ops",
        "ask_supplements",
        "delete",
        ["supp_early"],
        { sequence: 2 },
      );
      const lateDelete = makeRepresentationEvent(
        "projection_ops",
        "ask_supplements",
        "delete",
        ["supp_late"],
        { sequence: 3 },
      );

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [earlyDelete, lateDelete],
        prevChildren: [
          makePlateNode("paragraph:seg_1", "source"),
          makeCalloutNode("callout:supplement:supp_early", "early"),
          makePlateNode("paragraph:seg_2", "middle"),
          makeCalloutNode("callout:supplement:supp_late", "late"),
        ],
        nextChildren: [
          makePlateNode("paragraph:seg_1", "source"),
          makePlateNode("paragraph:seg_2", "middle"),
        ],
        snapshotFence,
      });

      expect(result.kind).toBe("targeted_apply");
      if (result.kind !== "targeted_apply") return;
      expect(result.operations.map((operation) => operation.path)).toEqual([
        [3],
        [1],
      ]);
      expect(result.operations.every((operation) => operation.type === "remove")).toBe(
        true,
      );
    });
    it("multiple G1 events on same block: deduplicates to one replace", () => {
      const prevSnapshot = makeSnapshot({
        userAssets: [
          makeUserAsset("asset_1", "seg_1"),
          makeUserAsset("asset_2", "seg_1"),
        ],
        lastEventSequence: 1,
      });
      const nextSnapshot = makeSnapshot({
        userAssets: [
          makeUserAsset("asset_1", "seg_1", { noteText: "updated" }),
          makeUserAsset("asset_2", "seg_1"),
        ],
        lastEventSequence: 3,
      });

      const event1 = makeRepresentationEvent(
        "projection_ops",
        "user_assets",
        "upsert",
        ["asset_1"],
        { sequence: 2 },
      );
      const event2 = makeRepresentationEvent(
        "projection_ops",
        "user_assets",
        "upsert",
        ["asset_2"],
        { sequence: 3 },
      );

      const prevChildren = [makePlateNode("paragraph:seg_1", "hello")];
      const nextChildren = [
        makePlateNode("paragraph:seg_1", "hello updated"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event1, event2],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("targeted_apply");
      if (result.kind !== "targeted_apply") return;
      // Both events target the same paragraph block → deduplicated to 1 op
      expect(result.operations).toHaveLength(1);
      expect(result.affectedTargetKeys).toEqual(["asset_1", "asset_2"]);
    });

    it("G1 + G2 + G3 mixed batch: returns all operations", () => {
      const prevSnapshot = makeSnapshot({
        userAssets: [makeUserAsset("asset_1", "seg_1")],
        askSupplements: [makeSupplement("supp_1", "seg_1")],
        lastEventSequence: 1,
      });
      const nextSnapshot = makeSnapshot({
        userAssets: [
          makeUserAsset("asset_1", "seg_1", { noteText: "updated" }),
        ],
        askSupplements: [makeSupplement("supp_1", "seg_1")],
        displayTitleZh: "新标题",
        lastEventSequence: 4,
      });

      const g1Event = makeRepresentationEvent(
        "projection_ops",
        "user_assets",
        "upsert",
        ["asset_1"],
        { sequence: 2 },
      );
      const g2Event = makeRepresentationEvent(
        "projection_ops",
        "ask_supplements",
        "upsert",
        ["supp_1"],
        { sequence: 3 },
      );
      const g3Event = makeRepresentationEvent(
        "record_state_changed",
        "record_metadata",
        "status_changed",
        ["display_title_zh"],
        { sequence: 4 },
      );

      const prevChildren = [
        makePlateNode("paragraph:seg_1", "hello"),
        makeCalloutNode("callout:supplement:supp_1", "old content"),
      ];
      const nextChildren = [
        makePlateNode("paragraph:seg_1", "hello updated"),
        makeCalloutNode("callout:supplement:supp_1", "new content"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [g1Event, g2Event, g3Event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("targeted_apply");
      if (result.kind !== "targeted_apply") return;
      expect(result.operations).toHaveLength(2);
      expect(result.affectedTargetKeys).toEqual([
        "asset_1",
        "supp_1",
        "display_title_zh",
      ]);
    });

    it("G1 + layer_published: fallback (mixed batch fails)", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 3 });

      const g1Event = makeRepresentationEvent(
        "projection_ops",
        "user_assets",
        "upsert",
        ["asset_1"],
        { sequence: 2 },
      );
      const layerEvent = makeLayerPublishedEvent("translation", LAYER_UNIT_ID, {
        sequence: 3,
      });

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [g1Event, layerEvent],
        prevChildren: [],
        nextChildren: [],
        snapshotFence,
      });

      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("non_layer_published_in_batch");
    });
  });

  // --- R2.1E: layer_published changed-block-only apply ---

  describe("layer_published changed-block-only", () => {
    it("translation revision with same block topology: targeted_apply replaces changed blockquote", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("translation", LAYER_UNIT_ID, {
        sequence: 2,
      });

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
        makeLayerBlockquoteNode("layer_translation_1", "group_1", "old translation"),
      ];
      const nextChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
        makeLayerBlockquoteNode("layer_translation_1", "group_1", "new translation"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("targeted_apply");
      if (result.kind !== "targeted_apply") return;
      expect(result.operations).toHaveLength(1);
      expect(result.operations[0].type).toBe("replace");
      expect(result.operations[0].path).toEqual([1]);
      expect(result.operations[0].blockId).toBe(
        "blockquote:layer_translation_1:group_1",
      );
      expect(result.operations[0].nodes).toEqual([nextChildren[1]]);
      expect(result.affectedTargetKeys).toEqual([LAYER_UNIT_ID]);
      expect(result.preservedInteraction.preserveSelection).toBe(true);
      expect(result.preservedInteraction.preserveScroll).toBe(true);
      expect(result.preservedInteraction.preserveGrammarAccordion).toBe(true);
    });

    it("vocabulary revision with same block topology: targeted_apply replaces changed paragraph", () => {
      // R2.2-P1: This test now verifies REAL vocabulary mark data changes
      // (previously prev/next were identical, producing 0 ops). The paragraph
      // block_id stays the same, but the leaf's marks array differs — the
      // merger must detect this as a semantic change and emit a replace op.
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("vocabulary", LAYER_UNIT_ID, {
        sequence: 2,
      });

      const prevChildren = [
        makeLayerParagraphNodeWithVocabularyMarks("seg_1", "source text", [
          { id: "vocab_1", kind: "phrase_gloss", gloss: "记忆" },
        ]),
        makeLayerGrammarCalloutNode("item_1", "grammar note"),
      ];
      // vocabulary revision changes the projected paragraph (mark data) but
      // the block_id sequence remains identical.
      const nextChildren = [
        makeLayerParagraphNodeWithVocabularyMarks("seg_1", "source text", [
          { id: "vocab_1", kind: "phrase_gloss", gloss: "记忆 (修订)" },
        ]),
        makeLayerGrammarCalloutNode("item_1", "grammar note"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("targeted_apply");
      if (result.kind !== "targeted_apply") return;
      expect(result.operations).toHaveLength(1);
      expect(result.operations[0].type).toBe("replace");
      expect(result.operations[0].path).toEqual([0]);
      expect(result.operations[0].blockId).toBe("paragraph:seg_1");
      expect(result.operations[0].nodes).toEqual([nextChildren[0]]);
      expect(result.affectedTargetKeys).toEqual([LAYER_UNIT_ID]);
      expect(result.preservedInteraction.preserveSelection).toBe(true);
      expect(result.preservedInteraction.preserveScroll).toBe(true);
      expect(result.preservedInteraction.preserveGrammarAccordion).toBe(true);
    });

    it("vocabulary first-publish with vocab_highlight mark change: targeted_apply replaces paragraph", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("vocabulary", LAYER_UNIT_ID, {
        sequence: 2,
      });

      // prev: paragraph with no vocabulary marks (vocabulary layer not yet
      // published). next: same paragraph with a new vocab_highlight mark.
      // This is the vocabulary first-publish scenario.
      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
      ];
      const nextChildren = [
        makeLayerParagraphNodeWithVocabularyMarks("seg_1", "source text", [
          { id: "vocab_1", kind: "vocab_highlight", gloss: "memory" },
        ]),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("targeted_apply");
      if (result.kind !== "targeted_apply") return;
      expect(result.operations).toHaveLength(1);
      expect(result.operations[0].type).toBe("replace");
      expect(result.operations[0].path).toEqual([0]);
      expect(result.operations[0].blockId).toBe("paragraph:seg_1");
      expect(result.operations[0].nodes).toEqual([nextChildren[0]]);
      expect(result.affectedTargetKeys).toEqual([LAYER_UNIT_ID]);
    });

    it("vocabulary first-publish with context_gloss mark change: targeted_apply replaces paragraph", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("vocabulary", LAYER_UNIT_ID, {
        sequence: 2,
      });

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
      ];
      const nextChildren = [
        makeLayerParagraphNodeWithVocabularyMarks("seg_1", "source text", [
          { id: "vocab_ctx_1", kind: "context_gloss", gloss: "制度记忆" },
        ]),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("targeted_apply");
      if (result.kind !== "targeted_apply") return;
      expect(result.operations).toHaveLength(1);
      expect(result.operations[0].blockId).toBe("paragraph:seg_1");
    });

    it("vocabulary first-publish with multiple mark kinds: targeted_apply replaces paragraph", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("vocabulary", LAYER_UNIT_ID, {
        sequence: 2,
      });

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
      ];
      const nextChildren = [
        makeLayerParagraphNodeWithVocabularyMarks("seg_1", "source text", [
          { id: "vocab_1", kind: "vocab_highlight", gloss: "memory" },
          { id: "vocab_2", kind: "phrase_gloss", gloss: "shapes" },
          { id: "vocab_3", kind: "context_gloss", gloss: "policy choices" },
        ]),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("targeted_apply");
      if (result.kind !== "targeted_apply") return;
      expect(result.operations).toHaveLength(1);
      expect(result.operations[0].blockId).toBe("paragraph:seg_1");
    });

    it("vocabulary first-publish with multi-paragraph unit: only changed paragraph replaced", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("vocabulary", LAYER_UNIT_ID, {
        sequence: 2,
      });

      // Two paragraphs in the unit; only the first gets vocabulary marks.
      const prevChildren = [
        makeLayerParagraphNode("seg_1", "first paragraph"),
        makeLayerParagraphNode("seg_2", "second paragraph"),
      ];
      const nextChildren = [
        makeLayerParagraphNodeWithVocabularyMarks("seg_1", "first paragraph", [
          { id: "vocab_1", kind: "phrase_gloss", gloss: "记忆" },
        ]),
        makeLayerParagraphNode("seg_2", "second paragraph"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("targeted_apply");
      if (result.kind !== "targeted_apply") return;
      expect(result.operations).toHaveLength(1);
      expect(result.operations[0].blockId).toBe("paragraph:seg_1");
      expect(result.operations[0].path).toEqual([0]);
    });

    it("vocabulary first-publish with non-target unit block change: fallback_full_reload", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("vocabulary", LAYER_UNIT_ID, {
        sequence: 2,
      });

      // Target unit paragraph gets vocabulary marks, BUT a non-target unit
      // block also changes — P1-A guard must reject this.
      // The blockquote uses a DIFFERENT unitId so it is treated as a
      // non-target block; any semantic change to it must trigger fallback.
      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
        makeLayerBlockquoteNode(
          "layer_translation_other",
          "group_other",
          "other unit translation",
          "unit_other",
        ),
      ];
      const nextChildren = [
        makeLayerParagraphNodeWithVocabularyMarks("seg_1", "source text", [
          { id: "vocab_1", kind: "phrase_gloss", gloss: "记忆" },
        ]),
        makeLayerBlockquoteNode(
          "layer_translation_other",
          "group_other",
          "other unit translation (CHANGED)",
          "unit_other",
        ),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("fallback_full_reload");
    });

    it("vocabulary first-publish with block count mismatch: fallback_full_reload", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("vocabulary", LAYER_UNIT_ID, {
        sequence: 2,
      });

      // prev has 1 block, next has 2 blocks — structural change.
      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
      ];
      const nextChildren = [
        makeLayerParagraphNodeWithVocabularyMarks("seg_1", "source text", [
          { id: "vocab_1", kind: "phrase_gloss", gloss: "记忆" },
        ]),
        makeLayerGrammarCalloutNode("item_1", "new grammar callout"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("fallback_full_reload");
    });

    it("vocabulary first-publish with fence mismatch (generation): fallback_full_reload", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("vocabulary", LAYER_UNIT_ID, {
        sequence: 2,
        generation: 99, // mismatch with snapshotFence.generation
      });

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
      ];
      const nextChildren = [
        makeLayerParagraphNodeWithVocabularyMarks("seg_1", "source text", [
          { id: "vocab_1", kind: "phrase_gloss", gloss: "记忆" },
        ]),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("fence_mismatch_in_batch");
    });

    it("vocabulary first-publish with mixed batch (layer_published + projection_ops): fallback_full_reload", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const vocabEvent = makeLayerPublishedEvent("vocabulary", LAYER_UNIT_ID, {
        sequence: 2,
      });
      const representationEvent = makeRepresentationEvent(
        "projection_ops",
        "user_assets",
        "upsert",
        ["asset_1"],
        { sequence: 3 },
      );

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
      ];
      const nextChildren = [
        makeLayerParagraphNodeWithVocabularyMarks("seg_1", "source text", [
          { id: "vocab_1", kind: "phrase_gloss", gloss: "记忆" },
        ]),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [vocabEvent, representationEvent],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("non_layer_published_in_batch");
    });

    it("vocabulary first-publish with target unit not found: fallback_full_reload", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      // target_key points to a unit that doesn't exist in children.
      const event = makeLayerPublishedEvent("vocabulary", "unit_nonexistent", {
        sequence: 2,
      });

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
      ];
      const nextChildren = [
        makeLayerParagraphNodeWithVocabularyMarks("seg_1", "source text", [
          { id: "vocab_1", kind: "phrase_gloss", gloss: "记忆" },
        ]),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      // When target unit doesn't exist in children, ALL blocks are treated
      // as non-target. The paragraph children change (vocabulary marks added)
      // is detected by P1-A.2 as an unrepresented change in a non-target
      // block. This is still fail-closed (fallback_full_reload); the exact
      // reason depends on which P1-A guard fires first.
      expect(result.kind).toBe("fallback_full_reload");
    });

    it("vocabulary first-publish with invalid payload (missing target_key): fallback_full_reload", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("vocabulary", LAYER_UNIT_ID, {
        sequence: 2,
      });
      // Corrupt the payload to remove target_key.
      (event.payload as { target_key?: string }).target_key = "";

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
      ];
      const nextChildren = [
        makeLayerParagraphNodeWithVocabularyMarks("seg_1", "source text", [
          { id: "vocab_1", kind: "phrase_gloss", gloss: "记忆" },
        ]),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("invalid_layer_published_payload");
    });

    it("grammar_note revision with same topology: targeted_apply replaces changed callout", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("grammar_note", LAYER_UNIT_ID, {
        sequence: 2,
      });

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
        makeLayerGrammarCalloutNode("item_1", "old note"),
      ];
      const nextChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
        makeLayerGrammarCalloutNode("item_1", "new note"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("targeted_apply");
      if (result.kind !== "targeted_apply") return;
      expect(result.operations).toHaveLength(1);
      expect(result.operations[0].type).toBe("replace");
      expect(result.operations[0].blockId).toBe("callout:grammar:item_1");
      expect(result.operations[0].nodes).toEqual([nextChildren[1]]);
      // preserveGrammarAccordion must be true so Surface keeps expansion.
      expect(result.preservedInteraction.preserveGrammarAccordion).toBe(true);
    });

    it("sentence_analysis revision with same topology: targeted_apply replaces changed block", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent(
        "sentence_analysis",
        LAYER_UNIT_ID,
        { sequence: 2 },
      );

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
        makeLayerSentenceAnalysisNode("analysis_1", "old analysis"),
      ];
      const nextChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
        makeLayerSentenceAnalysisNode("analysis_1", "new analysis"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("targeted_apply");
      if (result.kind !== "targeted_apply") return;
      expect(result.operations).toHaveLength(1);
      expect(result.operations[0].type).toBe("replace");
      expect(result.operations[0].blockId).toBe(
        "sentence_analysis:analysis_1",
      );
    });

    it("multi-block revision: only changed blocks are replaced, unchanged skipped", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("translation", LAYER_UNIT_ID, {
        sequence: 2,
      });

      // Three blocks, two changed.
      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source one"),
        makeLayerBlockquoteNode("layer_translation_1", "group_1", "old translation 1"),
        makeLayerBlockquoteNode("layer_translation_1", "group_2", "old translation 2"),
      ];
      const nextChildren = [
        makeLayerParagraphNode("seg_1", "source one"),
        makeLayerBlockquoteNode("layer_translation_1", "group_1", "new translation 1"),
        makeLayerBlockquoteNode("layer_translation_1", "group_2", "old translation 2"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("targeted_apply");
      if (result.kind !== "targeted_apply") return;
      expect(result.operations).toHaveLength(1);
      expect(result.operations[0].blockId).toBe(
        "blockquote:layer_translation_1:group_1",
      );
      expect(result.operations[0].path).toEqual([1]);
    });

    it("new translation blockquote in nextChildren: fallback", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("translation", LAYER_UNIT_ID, {
        sequence: 2,
      });

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
      ];
      const nextChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
        makeLayerBlockquoteNode("layer_translation_1", "group_1", "new translation"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("unit_block_set_changed");
    });

    it("new grammar callout in nextChildren: fallback", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("grammar_note", LAYER_UNIT_ID, {
        sequence: 2,
      });

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
      ];
      const nextChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
        makeLayerGrammarCalloutNode("item_new", "new grammar note"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("unit_block_set_changed");
    });

    it("new sentence_analysis block in nextChildren: fallback", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent(
        "sentence_analysis",
        LAYER_UNIT_ID,
        { sequence: 2 },
      );

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
      ];
      const nextChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
        makeLayerSentenceAnalysisNode("analysis_new", "new analysis"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("unit_block_set_changed");
    });

    it("deleted block in nextChildren: fallback", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("translation", LAYER_UNIT_ID, {
        sequence: 2,
      });

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
        makeLayerBlockquoteNode("layer_translation_1", "group_1", "translation"),
      ];
      const nextChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("unit_block_set_changed");
    });

    it("reordered blocks with same set: fallback", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("translation", LAYER_UNIT_ID, {
        sequence: 2,
      });

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source one"),
        makeLayerBlockquoteNode("layer_translation_1", "group_1", "translation 1"),
        makeLayerBlockquoteNode("layer_translation_1", "group_2", "translation 2"),
      ];
      // Same set but different order.
      const nextChildren = [
        makeLayerParagraphNode("seg_1", "source one"),
        makeLayerBlockquoteNode("layer_translation_1", "group_2", "translation 2"),
        makeLayerBlockquoteNode("layer_translation_1", "group_1", "translation 1"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("unit_block_order_changed");
    });

    it("missing block id on a node: fallback", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("translation", LAYER_UNIT_ID, {
        sequence: 2,
      });

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
        {
          type: "blockquote",
          // no id
          children: [{ text: "translation" }],
          data: { unitId: LAYER_UNIT_ID },
        } as unknown as Descendant,
      ];
      const nextChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
        {
          type: "blockquote",
          // no id
          children: [{ text: "translation" }],
          data: { unitId: LAYER_UNIT_ID },
        } as unknown as Descendant,
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("unit_block_identity_missing");
    });

    it("fence mismatch (generation): fallback", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("translation", LAYER_UNIT_ID, {
        sequence: 2,
        generation: 999,
      });

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
      ];
      const nextChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("fence_mismatch_in_batch");
    });

    it("fence mismatch (base_id): fallback", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("translation", LAYER_UNIT_ID, {
        sequence: 2,
        baseId: "base_mismatch",
      });

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
      ];
      const nextChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("fence_mismatch_in_batch");
    });

    it("invalid payload (missing target_key): fallback", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("translation", LAYER_UNIT_ID, {
        sequence: 2,
      });
      // Strip target_key to simulate malformed payload.
      const malformedEvent: ReaderEventResponseDto = {
        ...event,
        payload: {
          ...(event.payload as Record<string, unknown>),
          target_key: "",
        },
      };

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
      ];
      const nextChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [malformedEvent],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("invalid_layer_published_payload");
    });

    it("unsupported target_scope (anchor_segment): fallback", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("translation", "seg_1", {
        sequence: 2,
        targetScope: "anchor_segment",
      });

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
      ];
      const nextChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("unsupported_target_scope");
    });

    it("unknown layer_type: fallback", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("translation", LAYER_UNIT_ID, {
        sequence: 2,
      });
      const malformedEvent: ReaderEventResponseDto = {
        ...event,
        payload: {
          ...(event.payload as Record<string, unknown>),
          layer_type: "summary",
        },
      };

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
      ];
      const nextChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [malformedEvent],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("invalid_layer_published_payload");
    });

    it("target unit not found in prevChildren: fallback", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("translation", "unit_missing", {
        sequence: 2,
      });

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text", "unit_other"),
      ];
      const nextChildren = [
        makeLayerParagraphNode("seg_1", "source text", "unit_other"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("unit_not_found");
    });

    it("record_id mismatch between event and snapshot: fallback", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("translation", LAYER_UNIT_ID, {
        sequence: 2,
        recordId: "rec_other",
      });

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
      ];
      const nextChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("record_mismatch");
    });

    it("duplicate (unit_id, layer_type) events deduplicate to one operation set", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 3 });
      const event1 = makeLayerPublishedEvent("translation", LAYER_UNIT_ID, {
        sequence: 2,
      });
      const event2 = makeLayerPublishedEvent("translation", LAYER_UNIT_ID, {
        sequence: 3,
      });

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
        makeLayerBlockquoteNode("layer_translation_1", "group_1", "old"),
      ];
      const nextChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
        makeLayerBlockquoteNode("layer_translation_1", "group_1", "new"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event1, event2],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("targeted_apply");
      if (result.kind !== "targeted_apply") return;
      // Deduplicated: only one replace operation.
      expect(result.operations).toHaveLength(1);
      expect(result.operations[0].blockId).toBe(
        "blockquote:layer_translation_1:group_1",
      );
    });

    it("multi-unit batch: each unit evaluated independently, both targeted_apply", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 3 });
      const event1 = makeLayerPublishedEvent("translation", "unit_1", {
        sequence: 2,
      });
      const event2 = makeLayerPublishedEvent("translation", "unit_2", {
        sequence: 3,
      });

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source one", "unit_1"),
        makeLayerBlockquoteNode("layer_translation_1", "group_1", "old 1", "unit_1"),
        makeLayerParagraphNode("seg_2", "source two", "unit_2"),
        makeLayerBlockquoteNode("layer_translation_2", "group_2", "old 2", "unit_2"),
      ];
      const nextChildren = [
        makeLayerParagraphNode("seg_1", "source one", "unit_1"),
        makeLayerBlockquoteNode("layer_translation_1", "group_1", "new 1", "unit_1"),
        makeLayerParagraphNode("seg_2", "source two", "unit_2"),
        makeLayerBlockquoteNode("layer_translation_2", "group_2", "new 2", "unit_2"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event1, event2],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("targeted_apply");
      if (result.kind !== "targeted_apply") return;
      expect(result.operations).toHaveLength(2);
      const blockIds = result.operations.map((op) => op.blockId).sort();
      expect(blockIds).toEqual([
        "blockquote:layer_translation_1:group_1",
        "blockquote:layer_translation_2:group_2",
      ]);
    });

    it("multi-unit batch: one unit structural change falls back whole batch", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 3 });
      const event1 = makeLayerPublishedEvent("translation", "unit_1", {
        sequence: 2,
      });
      const event2 = makeLayerPublishedEvent("translation", "unit_2", {
        sequence: 3,
      });

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source one", "unit_1"),
        makeLayerBlockquoteNode("layer_translation_1", "group_1", "old 1", "unit_1"),
        makeLayerParagraphNode("seg_2", "source two", "unit_2"),
      ];
      // unit_2 has a NEW blockquote in nextChildren — structural change.
      const nextChildren = [
        makeLayerParagraphNode("seg_1", "source one", "unit_1"),
        makeLayerBlockquoteNode("layer_translation_1", "group_1", "new 1", "unit_1"),
        makeLayerParagraphNode("seg_2", "source two", "unit_2"),
        makeLayerBlockquoteNode("layer_translation_2", "group_2", "new 2", "unit_2"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event1, event2],
        prevChildren,
        nextChildren,
        snapshotFence,
      });

      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("unit_block_set_changed");
    });

    it("snapshotFence null: payload fence check skipped, still validates topology", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
      const event = makeLayerPublishedEvent("translation", LAYER_UNIT_ID, {
        sequence: 2,
      });

      const prevChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
        makeLayerBlockquoteNode("layer_translation_1", "group_1", "old"),
      ];
      const nextChildren = [
        makeLayerParagraphNode("seg_1", "source text"),
        makeLayerBlockquoteNode("layer_translation_1", "group_1", "new"),
      ];

      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren,
        nextChildren,
        snapshotFence: null,
      });

      expect(result.kind).toBe("targeted_apply");
      if (result.kind !== "targeted_apply") return;
      expect(result.operations).toHaveLength(1);
    });

    // P1-A: unrepresented change detection — any change outside the
    // event's target unit MUST cause fallback, otherwise the cursor
    // advances past the unrepresented change and the UI is stuck.
    describe("unrepresented change in non-target block", () => {
      it("non-target unit blockquote content change: fallback", () => {
        const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
        const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
        const event = makeLayerPublishedEvent("translation", "unit_1", {
          sequence: 2,
        });

        const prevChildren = [
          makeLayerParagraphNode("seg_1", "source one", "unit_1"),
          makeLayerBlockquoteNode("layer_translation_1", "group_1", "old 1", "unit_1"),
          makeLayerParagraphNode("seg_2", "source two", "unit_2"),
          makeLayerBlockquoteNode("layer_translation_2", "group_2", "old 2", "unit_2"),
        ];
        // unit_2 blockquote content changed but event only targets unit_1.
        const nextChildren = [
          makeLayerParagraphNode("seg_1", "source one", "unit_1"),
          makeLayerBlockquoteNode("layer_translation_1", "group_1", "new 1", "unit_1"),
          makeLayerParagraphNode("seg_2", "source two", "unit_2"),
          makeLayerBlockquoteNode("layer_translation_2", "group_2", "sneaky change", "unit_2"),
        ];

        const result = mergeIncrementalProjection({
          prevSnapshot,
          nextSnapshot,
          triggerEvents: [event],
          prevChildren,
          nextChildren,
          snapshotFence,
        });

        expect(result.kind).toBe("fallback_full_reload");
      });

      it("non-target unit paragraph content change: fallback", () => {
        const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
        const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
        const event = makeLayerPublishedEvent("translation", "unit_1", {
          sequence: 2,
        });

        const prevChildren = [
          makeLayerParagraphNode("seg_1", "source one", "unit_1"),
          makeLayerBlockquoteNode("layer_translation_1", "group_1", "old 1", "unit_1"),
          makeLayerParagraphNode("seg_2", "source two", "unit_2"),
          makeLayerBlockquoteNode("layer_translation_2", "group_2", "old 2", "unit_2"),
        ];
        // unit_2 paragraph content changed but event only targets unit_1.
        const nextChildren = [
          makeLayerParagraphNode("seg_1", "source one", "unit_1"),
          makeLayerBlockquoteNode("layer_translation_1", "group_1", "new 1", "unit_1"),
          makeLayerParagraphNode("seg_2", "source two CHANGED", "unit_2"),
          makeLayerBlockquoteNode("layer_translation_2", "group_2", "old 2", "unit_2"),
        ];

        const result = mergeIncrementalProjection({
          prevSnapshot,
          nextSnapshot,
          triggerEvents: [event],
          prevChildren,
          nextChildren,
          snapshotFence,
        });

        expect(result.kind).toBe("fallback_full_reload");
      });

      it("non-target unit new block added: fallback", () => {
        const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
        const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
        const event = makeLayerPublishedEvent("translation", "unit_1", {
          sequence: 2,
        });

        const prevChildren = [
          makeLayerParagraphNode("seg_1", "source one", "unit_1"),
          makeLayerBlockquoteNode("layer_translation_1", "group_1", "old 1", "unit_1"),
          makeLayerParagraphNode("seg_2", "source two", "unit_2"),
        ];
        // unit_2 has a NEW blockquote in nextChildren — unrepresented
        // structural change in non-target unit.
        const nextChildren = [
          makeLayerParagraphNode("seg_1", "source one", "unit_1"),
          makeLayerBlockquoteNode("layer_translation_1", "group_1", "new 1", "unit_1"),
          makeLayerParagraphNode("seg_2", "source two", "unit_2"),
          makeLayerBlockquoteNode("layer_translation_2", "group_2", "new 2", "unit_2"),
        ];

        const result = mergeIncrementalProjection({
          prevSnapshot,
          nextSnapshot,
          triggerEvents: [event],
          prevChildren,
          nextChildren,
          snapshotFence,
        });

        expect(result.kind).toBe("fallback_full_reload");
      });

      it("projection length mismatch (extra block at end): fallback", () => {
        const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
        const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
        const event = makeLayerPublishedEvent("translation", LAYER_UNIT_ID, {
          sequence: 2,
        });

        const prevChildren = [
          makeLayerParagraphNode("seg_1", "source text"),
          makeLayerBlockquoteNode("layer_translation_1", "group_1", "old"),
        ];
        // nextChildren has an extra block — length mismatch means
        // unrepresented structural change.
        const nextChildren = [
          makeLayerParagraphNode("seg_1", "source text"),
          makeLayerBlockquoteNode("layer_translation_1", "group_1", "new"),
          makeLayerGrammarCalloutNode("grammar_item_1", "extra callout"),
        ];

        const result = mergeIncrementalProjection({
          prevSnapshot,
          nextSnapshot,
          triggerEvents: [event],
          prevChildren,
          nextChildren,
          snapshotFence,
        });

        expect(result.kind).toBe("fallback_full_reload");
      });

      it("non-target unit block_id changed: fallback", () => {
        const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
        const nextSnapshot = makeSnapshot({ lastEventSequence: 2 });
        const event = makeLayerPublishedEvent("translation", "unit_1", {
          sequence: 2,
        });

        const prevChildren = [
          makeLayerParagraphNode("seg_1", "source one", "unit_1"),
          makeLayerBlockquoteNode("layer_translation_1", "group_1", "old 1", "unit_1"),
          makeLayerParagraphNode("seg_2", "source two", "unit_2"),
          makeLayerBlockquoteNode("layer_translation_2", "group_2", "old 2", "unit_2"),
        ];
        // unit_2 blockquote has a DIFFERENT layer_id (new layer published
        // but no event for unit_2) — block_id changed.
        const nextChildren = [
          makeLayerParagraphNode("seg_1", "source one", "unit_1"),
          makeLayerBlockquoteNode("layer_translation_1", "group_1", "new 1", "unit_1"),
          makeLayerParagraphNode("seg_2", "source two", "unit_2"),
          makeLayerBlockquoteNode("layer_translation_3", "group_2", "old 2", "unit_2"),
        ];

        const result = mergeIncrementalProjection({
          prevSnapshot,
          nextSnapshot,
          triggerEvents: [event],
          prevChildren,
          nextChildren,
          snapshotFence,
        });

        expect(result.kind).toBe("fallback_full_reload");
      });
    });
  });

  // --- Fail-closed cases ---

  describe("fail-closed", () => {
    it("no trigger events: fallback", () => {
      const snapshot = makeSnapshot({ lastEventSequence: 1 });
      const result = mergeIncrementalProjection({
        prevSnapshot: snapshot,
        nextSnapshot: snapshot,
        triggerEvents: [],
        prevChildren: [],
        nextChildren: [],
        snapshotFence,
      });
      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("no_trigger_events");
    });

    it("generation changed: fallback", () => {
      const prevSnapshot = makeSnapshot({
        generation: 1,
        lastEventSequence: 1,
      });
      const nextSnapshot = makeSnapshot({
        generation: 2,
        lastEventSequence: 2,
      });
      const event = makeRepresentationEvent(
        "projection_ops",
        "user_assets",
        "upsert",
        ["asset_1"],
        { generation: 2, sequence: 2 },
      );
      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren: [],
        nextChildren: [],
        snapshotFence,
      });
      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("generation_changed");
    });

    it("base changed: fallback", () => {
      const prevSnapshot = makeSnapshot({
        baseId: "base_A",
        lastEventSequence: 1,
      });
      const nextSnapshot = makeSnapshot({
        baseId: "base_B",
        lastEventSequence: 2,
      });
      const event = makeRepresentationEvent(
        "projection_ops",
        "user_assets",
        "upsert",
        ["asset_1"],
        { baseId: "base_B", sequence: 2 },
      );
      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren: [],
        nextChildren: [],
        snapshotFence: { generation: GENERATION, baseId: "base_A" },
      });
      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("base_changed");
    });

    it("fence mismatch in payload: fallback", () => {
      const snapshot = makeSnapshot({ lastEventSequence: 1 });
      const event = makeRepresentationEvent(
        "projection_ops",
        "user_assets",
        "upsert",
        ["asset_1"],
        { generation: 999, baseId: "base_mismatch", sequence: 2 },
      );
      const result = mergeIncrementalProjection({
        prevSnapshot: snapshot,
        nextSnapshot: snapshot,
        triggerEvents: [event],
        prevChildren: [],
        nextChildren: [],
        snapshotFence,
      });
      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("fence_mismatch_in_batch");
    });

    it("missing payload: fallback", () => {
      const snapshot = makeSnapshot({ lastEventSequence: 1 });
      const event: ReaderEventResponseDto = {
        id: "evt_2",
        reading_record_id: "rec_1",
        sequence: 2,
        event_type: "projection_ops",
        payload: {} as Record<string, unknown>,
        created_at: "2026-07-14T00:00:00Z",
      };
      const result = mergeIncrementalProjection({
        prevSnapshot: snapshot,
        nextSnapshot: snapshot,
        triggerEvents: [event],
        prevChildren: [],
        nextChildren: [],
        snapshotFence,
      });
      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("invalid_payload");
    });

    it("unknown section: fallback", () => {
      const snapshot = makeSnapshot({ lastEventSequence: 1 });
      const event = makeRepresentationEvent(
        "projection_ops",
        "unknown_section",
        "upsert",
        ["key_1"],
        { sequence: 2 },
      );
      const result = mergeIncrementalProjection({
        prevSnapshot: snapshot,
        nextSnapshot: snapshot,
        triggerEvents: [event],
        prevChildren: [],
        nextChildren: [],
        snapshotFence,
      });
      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("unknown_section:unknown_section");
    });

    it("unknown operation: fallback", () => {
      const snapshot = makeSnapshot({ lastEventSequence: 1 });
      const event = makeRepresentationEvent(
        "projection_ops",
        "user_assets",
        "unknown_op",
        ["asset_1"],
        { sequence: 2 },
      );
      const result = mergeIncrementalProjection({
        prevSnapshot: snapshot,
        nextSnapshot: snapshot,
        triggerEvents: [event],
        prevChildren: [],
        nextChildren: [],
        snapshotFence,
      });
      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("unknown_operation:unknown_op");
    });

    it("unknown schema version: fallback", () => {
      const snapshot = makeSnapshot({ lastEventSequence: 1 });
      const event = makeRepresentationEvent(
        "projection_ops",
        "user_assets",
        "upsert",
        ["asset_1"],
        { schemaVersion: 99, sequence: 2 },
      );
      const result = mergeIncrementalProjection({
        prevSnapshot: snapshot,
        nextSnapshot: snapshot,
        triggerEvents: [event],
        prevChildren: [],
        nextChildren: [],
        snapshotFence,
      });
      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("unknown_schema_version:99");
    });

    it("non-representation event in batch: fallback", () => {
      const snapshot = makeSnapshot({ lastEventSequence: 1 });
      const event: ReaderEventResponseDto = {
        id: "evt_2",
        reading_record_id: "rec_1",
        sequence: 2,
        event_type: "article_ready",
        payload: {},
        created_at: "2026-07-14T00:00:00Z",
      };
      const result = mergeIncrementalProjection({
        prevSnapshot: snapshot,
        nextSnapshot: snapshot,
        triggerEvents: [event],
        prevChildren: [],
        nextChildren: [],
        snapshotFence,
      });
      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("non_representation_event_in_batch");
    });

    it("G1 target not in prevChildren: fallback", () => {
      const prevSnapshot = makeSnapshot({
        userAssets: [makeUserAsset("asset_1", "seg_missing")],
        lastEventSequence: 1,
      });
      const nextSnapshot = makeSnapshot({
        userAssets: [makeUserAsset("asset_1", "seg_missing")],
        lastEventSequence: 2,
      });
      const event = makeRepresentationEvent(
        "projection_ops",
        "user_assets",
        "upsert",
        ["asset_1"],
        { sequence: 2 },
      );
      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren: [makePlateNode("paragraph:other", "text")],
        nextChildren: [makePlateNode("paragraph:other", "text")],
        snapshotFence,
      });
      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("target_not_found");
    });

    it("G1 target not in nextChildren: fallback", () => {
      const prevSnapshot = makeSnapshot({
        userAssets: [makeUserAsset("asset_1", "seg_1")],
        lastEventSequence: 1,
      });
      const nextSnapshot = makeSnapshot({
        userAssets: [makeUserAsset("asset_1", "seg_1")],
        lastEventSequence: 2,
      });
      const event = makeRepresentationEvent(
        "projection_ops",
        "user_assets",
        "upsert",
        ["asset_1"],
        { sequence: 2 },
      );
      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren: [makePlateNode("paragraph:seg_1", "text")],
        nextChildren: [], // paragraph:seg_1 missing from next
        snapshotFence,
      });
      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("target_not_found");
    });

    it("G2 delete target missing from prev: fallback", () => {
      const prevSnapshot = makeSnapshot({
        askSupplements: [makeSupplement("supp_1", "seg_1")],
        lastEventSequence: 1,
      });
      const nextSnapshot = makeSnapshot({
        askSupplements: [],
        lastEventSequence: 2,
      });
      const event = makeRepresentationEvent(
        "projection_ops",
        "ask_supplements",
        "delete",
        ["supp_1"],
        { sequence: 2 },
      );
      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren: [], // callout:supplement:supp_1 not in prev
        nextChildren: [],
        snapshotFence,
      });
      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("delete_target_missing");
    });

    it("G2 upsert target not in prev (new supplement): fallback", () => {
      const prevSnapshot = makeSnapshot({
        askSupplements: [],
        lastEventSequence: 1,
      });
      const nextSnapshot = makeSnapshot({
        askSupplements: [makeSupplement("supp_new", "seg_1")],
        lastEventSequence: 2,
      });
      const event = makeRepresentationEvent(
        "projection_ops",
        "ask_supplements",
        "upsert",
        ["supp_new"],
        { sequence: 2 },
      );
      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren: [], // callout:supplement:supp_new not in prev
        nextChildren: [
          makeCalloutNode("callout:supplement:supp_new", "new"),
        ],
        snapshotFence,
      });
      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("target_not_found");
    });

    it("G3 unknown metadata field: fallback", () => {
      const snapshot = makeSnapshot({ lastEventSequence: 1 });
      const event = makeRepresentationEvent(
        "record_state_changed",
        "record_metadata",
        "status_changed",
        ["unknown_field"],
        { sequence: 2 },
      );
      const result = mergeIncrementalProjection({
        prevSnapshot: snapshot,
        nextSnapshot: snapshot,
        triggerEvents: [event],
        prevChildren: [],
        nextChildren: [],
        snapshotFence,
      });
      expect(result.kind).toBe("fallback_full_reload");
      if (result.kind !== "fallback_full_reload") return;
      expect(result.reason).toBe("unknown_metadata_field:unknown_field");
    });

    it("snapshotFence null: still validates payload fence against nothing (passes)", () => {
      const prevSnapshot = makeSnapshot({
        userAssets: [makeUserAsset("asset_1", "seg_1")],
        lastEventSequence: 1,
      });
      const nextSnapshot = makeSnapshot({
        userAssets: [makeUserAsset("asset_1", "seg_1")],
        lastEventSequence: 2,
      });
      const event = makeRepresentationEvent(
        "projection_ops",
        "user_assets",
        "upsert",
        ["asset_1"],
        { sequence: 2 },
      );
      const result = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot,
        triggerEvents: [event],
        prevChildren: [makePlateNode("paragraph:seg_1", "hello")],
        nextChildren: [makePlateNode("paragraph:seg_1", "hello")],
        snapshotFence: null,
      });
      // With null fence, payload fence check is skipped — should pass
      expect(result.kind).toBe("targeted_apply");
    });
  });
});
