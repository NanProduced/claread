/**
 * Tests for T4.2a-PUX-R4-R2: incremental-projection-merger pure function.
 *
 * Covers:
 * - G1 user_assets: upsert → targeted_apply (replace paragraph block)
 * - G2 ask_supplements: upsert → targeted_apply (replace callout block)
 * - G2 ask_supplements: delete → targeted_apply (remove callout block)
 * - G3 record_metadata: status_changed → targeted_apply (empty operations)
 * - Fail-closed: missing payload, unknown section/operation, fence mismatch,
 *   target not found, generation changed, base changed, layer_published event,
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

    it("G1 + layer_published: fallback (whole batch fails)", () => {
      const prevSnapshot = makeSnapshot({ lastEventSequence: 1 });
      const nextSnapshot = makeSnapshot({ lastEventSequence: 3 });

      const g1Event = makeRepresentationEvent(
        "projection_ops",
        "user_assets",
        "upsert",
        ["asset_1"],
        { sequence: 2 },
      );
      const layerEvent: ReaderEventResponseDto = {
        id: "evt_3",
        reading_record_id: "rec_1",
        sequence: 3,
        event_type: "layer_published",
        payload: {},
        created_at: "2026-07-14T00:00:00Z",
      };

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
      expect(result.reason).toBe("layer_published_not_supported");
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
