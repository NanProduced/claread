"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { computeUtf16FNV1a } from "@claread/contracts";
import {
  READER_PLATE_SNAPSHOT_SCHEMA_KIND,
  READER_TEXT_RANGE_HASH_ALGORITHM,
  READER_TEXT_RANGE_OFFSET_UNIT,
  type ReaderEventResponseDto,
  type ReaderGrammarNoteMarkDto,
  type ReaderPlateSnapshotDto,
  type ReaderUnitNodeDto,
  type ReaderVocabularyMarkDto,
} from "@/types/api/reader-plate";
import type { ReloadContext } from "@/lib/reader-plate-snapshot/polling";
import { ReaderRecordPlateSurface } from "@/components/reader/plate/ReaderRecordPlateSurface";

// ---------------------------------------------------------------------------
// T4.2a-PUX-R4-R2.1D — Real Surface E2E harness (test-only, env-gated).
//
// Mounts a REAL ReaderRecordPlateSurface and drives it through its public
// props: `snapshot`, `pendingReloadContext`, `onReloadContextConsumed`.
//
// The harness does NOT call editor.tf.replaceNodes / setValue / removeNodes
// directly. All mutations flow through the Surface's real reload pipeline:
//   snapshot change → value swap effect → mergeIncrementalProjection
//   → targeted_apply (replaceNodes batch) OR fallback_full_reload (setValue)
//
// `window.__spikeSurface` exposes:
//   - reloadWith(nextSnapshot, events, fence): sets pendingReloadContext + snapshot
//   - reloadFallback(nextSnapshot): sets pendingReloadContext with layer_published event
//   - changeGeneration(generation): sets snapshot with new generation
//   - getSnapshot(): returns current snapshot
//   - makeUpdatedSnapshot(options): builds next snapshot with user_asset on specified segment
//   - makeGenerationSnapshot(generation): builds next snapshot with new generation
//
// Fixture layout (two segments for sibling-paragraph test):
//   seg_1: "Institutional memory shapes policy choices." (vocab mark + grammar mark)
//   seg_2: "This is a second test sentence." (no marks initially)
// ---------------------------------------------------------------------------

const SOURCE_TEXT_1 = "Institutional memory shapes policy choices.";
const SOURCE_TEXT_2 = "This is a second test sentence.";
const FULL_SOURCE_TEXT = `${SOURCE_TEXT_1} ${SOURCE_TEXT_2}`;
const TRANSLATION_TEXT_1 = "制度记忆会塑造政策选择。";
const TRANSLATION_TEXT_2 = "这是第二个测试句子。";

// seg_1 covers [0, 44]; space at 44; seg_2 covers [45, 75].
const SEG_1_START = 0;
const SEG_1_END = SOURCE_TEXT_1.length; // 44
const SEG_2_START = SEG_1_END + 1; // 45
const SEG_2_END = FULL_SOURCE_TEXT.length; // 75

declare global {
  interface Window {
    __spikeSurface?: {
      reloadWith: (
        nextSnapshot: ReaderPlateSnapshotDto,
        events: ReaderEventResponseDto[],
        fence: { generation: number; baseId: string } | null,
      ) => void;
      reloadFallback: (nextSnapshot: ReaderPlateSnapshotDto) => void;
      changeGeneration: (generation: number) => void;
      getSnapshot: () => ReaderPlateSnapshotDto | null;
      makeUpdatedSnapshot: (options?: {
        userAssetNote?: string;
        assetSegmentId?: "seg_1" | "seg_2";
        assetId?: string;
        generation?: number;
      }) => ReaderPlateSnapshotDto;
      makeGenerationSnapshot: (generation: number) => ReaderPlateSnapshotDto;
    };
    __spikeSurfaceReady?: boolean;
  }
}

// ---------------------------------------------------------------------------
// Fixture builders
// ---------------------------------------------------------------------------

function makeVocabularyMark(
  overrides: Partial<ReaderVocabularyMarkDto> = {},
): ReaderVocabularyMarkDto {
  return {
    mark_id: "vocab_mark_1",
    layer_id: "layer_vocab_1",
    item_type: "phrase_gloss",
    anchor_segment_id: "seg_1",
    start_offset: 14,
    end_offset: 20,
    selected_text: "memory",
    segment_start_utf16: 14,
    segment_end_utf16: 20,
    starts_here: true,
    ends_here: true,
    phrase: "memory",
    phrase_type: "collocation",
    gloss: "记忆",
    example: "Institutional memory shapes choices.",
    ...overrides,
  } as ReaderVocabularyMarkDto;
}

function makeGrammarMark(
  overrides: Partial<ReaderGrammarNoteMarkDto> = {},
): ReaderGrammarNoteMarkDto {
  return {
    mark_id: "grammar_mark_1",
    item_id: "grammar_item_1",
    owner: "system_ai",
    layer_id: "layer_grammar_1",
    item_type: "grammar_note",
    anchor_segment_id: "seg_1",
    start_offset: 21,
    end_offset: 27,
    selected_text: "shapes",
    segment_start_utf16: 21,
    segment_end_utf16: 27,
    starts_here: true,
    ends_here: true,
    span_index: 0,
    span_count: 1,
    show_note_chip: true,
    grammar_point: "predicate verb",
    pattern: "subject + verb",
    note: "shapes is the predicate verb.",
    ...overrides,
  };
}

function makeUnit(): ReaderUnitNodeDto {
  return {
    type: "reader_unit",
    owner: "stable",
    base_id: "base_1",
    unit_id: "unit_1",
    order_index: 1,
    unit_type: "body",
    boundary_quality: "normal",
    base_start_utf16: 0,
    base_end_utf16: FULL_SOURCE_TEXT.length,
    text_hash: "unit_hash",
    hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
    children: [
      {
        type: "reader_source_block",
        owner: "stable",
        base_id: "base_1",
        unit_id: "unit_1",
        base_start_utf16: 0,
        base_end_utf16: FULL_SOURCE_TEXT.length,
        children: [
          {
            type: "reader_anchor_segment",
            owner: "stable",
            base_id: "base_1",
            unit_id: "unit_1",
            anchor_segment_id: "seg_1",
            sentence_id: "sent_1",
            segment_type: "sentence",
            boundary_quality: "normal",
            base_start_utf16: SEG_1_START,
            base_end_utf16: SEG_1_END,
            unit_start_utf16: SEG_1_START,
            unit_end_utf16: SEG_1_END,
            text_hash: "seg_hash_1",
            hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
            children: [
              {
                text: SOURCE_TEXT_1,
                owner: "stable",
                lock_source: true,
                source_role: "segment_text",
                base_start_utf16: SEG_1_START,
                base_end_utf16: SEG_1_END,
                anchor_segment_id: "seg_1",
                segment_start_utf16: SEG_1_START,
                segment_end_utf16: SEG_1_END,
                reader_vocabulary_marks: [makeVocabularyMark()],
                reader_grammar_note_marks: [makeGrammarMark()],
              },
            ],
          },
          {
            type: "reader_anchor_segment",
            owner: "stable",
            base_id: "base_1",
            unit_id: "unit_1",
            anchor_segment_id: "seg_2",
            sentence_id: "sent_2",
            segment_type: "sentence",
            boundary_quality: "normal",
            base_start_utf16: SEG_2_START,
            base_end_utf16: SEG_2_END,
            unit_start_utf16: SEG_2_START,
            unit_end_utf16: SEG_2_END,
            text_hash: "seg_hash_2",
            hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
            children: [
              {
                text: SOURCE_TEXT_2,
                owner: "stable",
                lock_source: true,
                source_role: "segment_text",
                base_start_utf16: SEG_2_START,
                base_end_utf16: SEG_2_END,
                anchor_segment_id: "seg_2",
                segment_start_utf16: SEG_2_START,
                segment_end_utf16: SEG_2_END,
                reader_vocabulary_marks: [],
                reader_grammar_note_marks: [],
              },
            ],
          },
        ],
      },
      {
        type: "reader_translation_group",
        owner: "system_ai",
        layer_id: "layer_translation_1",
        layer_version: 1,
        base_id: "base_1",
        unit_id: "unit_1",
        target_scope: "unit",
        target_key: "unit_1",
        group_id: "group_translation_1",
        covered_anchor_segment_ids: ["seg_1", "seg_2"],
        source_text_hash: "unit_hash_1",
        children: [{ text: `${TRANSLATION_TEXT_1} ${TRANSLATION_TEXT_2}` }],
      },
      {
        type: "reader_sentence_analysis",
        owner: "system_ai",
        analysis_id: "analysis_1",
        layer_id: "layer_sentence_analysis_1",
        layer_version: 1,
        base_id: "base_1",
        unit_id: "unit_1",
        target_scope: "unit",
        target_key: "unit_1",
        anchor_segment_id: "seg_1",
        selected_text: SOURCE_TEXT_1,
        label: "subject and predicate",
        analysis: "Institutional memory is the subject.",
        chunks: [
          { order: 1, label: "subject", text: "Institutional memory" },
        ],
        children: [{ text: "Institutional memory is the subject." }],
      },
    ],
  };
}

function makeSnapshot(
  options: {
    userAssetNote?: string;
    assetSegmentId?: "seg_1" | "seg_2";
    assetId?: string;
    generation?: number;
    snapshotId?: string;
    lastEventSequence?: number;
  } = {},
): ReaderPlateSnapshotDto {
  const {
    userAssetNote,
    assetSegmentId = "seg_1",
    assetId = "asset_highlight_1",
    generation = 1,
    snapshotId = "snapshot_1",
    lastEventSequence = 8,
  } = options;

  // Build user_asset only if note is provided.
  const userAssets = userAssetNote
    ? [
        {
          asset_id: assetId,
          asset_type: "highlight" as const,
          owner: "user" as const,
          reading_record_id: "record_1",
          generation,
          anchor: {
            anchor_type: "text_range" as const,
            base_id: "base_1",
            unit_id: "unit_1",
            anchor_segment_id: assetSegmentId,
            sentence_id: assetSegmentId === "seg_1" ? "sent_1" : "sent_2",
            segment_type: "sentence" as const,
            offset_unit: READER_TEXT_RANGE_OFFSET_UNIT,
            start_offset: assetSegmentId === "seg_1" ? 14 : 10,
            end_offset: assetSegmentId === "seg_1" ? 20 : 16,
            selected_text:
              assetSegmentId === "seg_1" ? "memory" : "second",
            text_hash: computeUtf16FNV1a(
              assetSegmentId === "seg_1" ? "memory" : "second",
            ),
            hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
          },
          created_at: "2026-06-24T01:00:00Z",
          updated_at: "2026-06-24T01:00:00Z",
          note_text: userAssetNote,
        } as never,
      ]
    : [];

  return {
    schema_kind: READER_PLATE_SNAPSHOT_SCHEMA_KIND,
    snapshot_id: snapshotId,
    snapshot_taken_at: "2026-06-24T00:00:00Z",
    last_event_sequence: lastEventSequence,
    record_id: "record_1",
    record: {
      title: "Reader Record Plate Surface Fixture",
      display_title_zh: null,
      title_generation_status: "pending",
      title_generation_error_code: null,
      title_generation_error_message: null,
      reading_goal: "daily_reading",
      reading_variant: "intensive_reading",
      created_at: "2026-06-24T00:00:00Z",
      source_type: "plain_text",
      source_metadata: {},
      generation,
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
    },
    base: {
      base_id: "base_1",
      content_sha256: "a".repeat(64),
      canonicalizer_version: "test",
      builder_version: "test",
      segmenter_version: "test",
      text_length_utf16: FULL_SOURCE_TEXT.length,
      hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
    },
    navigation: {
      units: [
        {
          unit_id: "unit_1",
          order_index: 1,
          unit_type: "body",
          boundary_quality: "normal",
          label: null,
          base_start_utf16: 0,
          base_end_utf16: FULL_SOURCE_TEXT.length,
          text_hash: "unit_hash",
          hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
        },
      ],
    },
    anchor_segments: [
      {
        anchor_segment_id: "seg_1",
        sentence_id: "sent_1",
        paragraph_id: "unit_1",
        unit_id: "unit_1",
        order_index: 1,
        unit_order_index: 1,
        segment_type: "sentence",
        boundary_quality: "normal",
        base_start_utf16: SEG_1_START,
        base_end_utf16: SEG_1_END,
        unit_start_utf16: SEG_1_START,
        unit_end_utf16: SEG_1_END,
        text_hash: "seg_hash_1",
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      },
      {
        anchor_segment_id: "seg_2",
        sentence_id: "sent_2",
        paragraph_id: "unit_1",
        unit_id: "unit_1",
        order_index: 2,
        unit_order_index: 1,
        segment_type: "sentence",
        boundary_quality: "normal",
        base_start_utf16: SEG_2_START,
        base_end_utf16: SEG_2_END,
        unit_start_utf16: SEG_2_START,
        unit_end_utf16: SEG_2_END,
        text_hash: "seg_hash_2",
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      },
    ],
    enhancement_layers: [],
    enhancement_progress: {
      overall_status: "readable_enhancing",
      layers: [
        {
          capability: "translation",
          layer_type: "translation",
          status: "succeeded",
          layer_id: "layer_translation_1",
          target_scope: "unit",
          target_key: "unit_1",
        },
        {
          capability: "grammar",
          layer_type: "grammar_note",
          status: "processing",
          job_id: "job_grammar_1",
          target_scope: "anchor_segment",
          target_key: "seg_1",
        },
      ],
    },
    ask_supplements: [],
    user_assets: userAssets,
    parsed_decisions: [],
    value: [makeUnit()],
  };
}

function makeRepresentationEvent(
  section: string,
  operation: string,
  targetKeys: string[],
  sequence = 9,
  generation = 1,
): ReaderEventResponseDto {
  return {
    id: `evt_${sequence}`,
    reading_record_id: "record_1",
    sequence,
    event_type: "projection_ops",
    payload: {
      schema_version: 1,
      representation_section: section,
      operation,
      target_keys: targetKeys,
      generation,
      base_id: "base_1",
    },
    created_at: "2026-06-24T02:00:00Z",
  } as never;
}

function makeLayerPublishedEvent(
  sequence = 9,
): ReaderEventResponseDto {
  return {
    id: `evt_${sequence}`,
    reading_record_id: "record_1",
    sequence,
    event_type: "layer_published",
    payload: { layer_type: "translation" },
    created_at: "2026-06-24T02:00:00Z",
  } as never;
}

// ---------------------------------------------------------------------------
// Harness component
// ---------------------------------------------------------------------------

export default function E2ESurfaceHarness() {
  // Initial snapshot has NO user_assets — so vocabulary mark click is not
  // intercepted by a user_highlight_data handler.
  const [snapshot, setSnapshot] = useState<ReaderPlateSnapshotDto>(() =>
    makeSnapshot(),
  );
  const [reloadContext, setReloadContext] = useState<ReloadContext | null>(null);
  const snapshotRef = useRef(snapshot);
  snapshotRef.current = snapshot;

  const handleReloadContextConsumed = useCallback(() => {
    setReloadContext(null);
  }, []);

  useEffect(() => {
    window.__spikeSurface = {
      reloadWith: (nextSnapshot, events, fence) => {
        const ctx: ReloadContext = {
          cursor: 8,
          events,
          triggerClassification: {
            kind: "reload_snapshot",
            reason: "user_asset_written",
          },
          acceptedSnapshotFence: fence,
          reason: "user_asset_written",
        };
        setReloadContext(ctx);
        setSnapshot(nextSnapshot);
      },
      reloadFallback: (nextSnapshot) => {
        const ctx: ReloadContext = {
          cursor: 8,
          events: [makeLayerPublishedEvent()],
          triggerClassification: {
            kind: "reload_snapshot",
            reason: "layer_published",
          },
          acceptedSnapshotFence: { generation: 1, baseId: "base_1" },
          reason: "layer_published",
        };
        setReloadContext(ctx);
        setSnapshot(nextSnapshot);
      },
      changeGeneration: (generation: number) => {
        // Generation change: new snapshot with different generation.
        // No reload context needed — the generation effect handles cleanup.
        setSnapshot((prev) => ({
          ...prev,
          snapshot_id: `snapshot_gen_${generation}`,
          last_event_sequence: prev.last_event_sequence + 1,
          record: {
            ...prev.record,
            generation,
          },
        }));
      },
      getSnapshot: () => snapshotRef.current,
      makeUpdatedSnapshot: (options = {}) => {
        const prev = snapshotRef.current;
        const generation = options.generation ?? prev.record.generation;
        return makeSnapshot({
          userAssetNote: options.userAssetNote ?? "new note",
          assetSegmentId: options.assetSegmentId ?? "seg_1",
          assetId: options.assetId ?? "asset_highlight_1",
          generation,
          snapshotId: "snapshot_2",
          lastEventSequence: 9,
        });
      },
      makeGenerationSnapshot: (generation: number) => {
        return makeSnapshot({
          userAssetNote: "gen change note",
          generation,
          snapshotId: `snapshot_gen_${generation}`,
          lastEventSequence: 10,
        });
      },
    };
    window.__spikeSurfaceReady = true;
  }, []);

  return (
    <div className="min-h-screen bg-white p-8">
      <div data-testid="e2e-surface-harness-root">
        <ReaderRecordPlateSurface
          snapshot={snapshot}
          pendingReloadContext={reloadContext}
          onReloadContextConsumed={handleReloadContextConsumed}
        />
      </div>
    </div>
  );
}

// Export fixture builders for potential future use.
export {
  makeSnapshot,
  makeRepresentationEvent,
  makeLayerPublishedEvent,
};
