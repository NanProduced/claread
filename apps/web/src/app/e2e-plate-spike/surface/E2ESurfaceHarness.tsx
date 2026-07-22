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
// T4.2a-PUX-R4-R2.1D / R2.1E — Real Surface E2E harness (test-only, env-gated).
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
//   - R2.1E: makeLayerRevisionSnapshot(options): builds same-topology revised snapshot
//   - R2.1E: makeStructuralChangeSnapshot(): builds snapshot with extra sentence_analysis block
//   - R2.1E: makeValidLayerPublishedEvent(layerType, sequence): builds valid 7-field payload
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
      loadSnapshot: (nextSnapshot: ReaderPlateSnapshotDto) => void;
      getSnapshot: () => ReaderPlateSnapshotDto | null;
      makeUpdatedSnapshot: (options?: {
        userAssetNote?: string;
        assetSegmentId?: "seg_1" | "seg_2";
        assetId?: string;
        generation?: number;
      }) => ReaderPlateSnapshotDto;
      makeGenerationSnapshot: (generation: number) => ReaderPlateSnapshotDto;
      // R2.1E: layer_published revision and structural change builders.
      makeLayerRevisionSnapshot: (options?: {
        translationText?: string;
        grammarNote?: string;
        analysisText?: string;
        vocabularyGloss?: string;
      }) => ReaderPlateSnapshotDto;
      makeStructuralChangeSnapshot: () => ReaderPlateSnapshotDto;
      makeValidLayerPublishedEvent: (
        layerType?:
          | "translation"
          | "vocabulary"
          | "grammar_note"
          | "sentence_analysis",
        sequence?: number,
      ) => ReaderEventResponseDto;
      // R2.2-P2a: multi-anchor grammar fixture builders.
      makeMultiAnchorGrammarSnapshot: () => ReaderPlateSnapshotDto;
      makeSeg2GrammarRevisionSnapshot: (options?: {
        grammarNote?: string;
      }) => ReaderPlateSnapshotDto;
      // R2.2-P2c-R1: grammar first-publish fixture builders.
      makeGrammarFirstPublishSnapshot: (options?: {
        anchorSegmentId?: "seg_1" | "seg_2";
        grammarNote?: string;
        layerId?: string;
        itemIds?: string[];
      }) => ReaderPlateSnapshotDto;
      makeGrammarFirstPublishEvent: (options?: {
        layerId?: string;
        anchorSegmentId?: "seg_1" | "seg_2";
        itemIds?: string[];
        unitId?: string;
        generation?: number;
      }) => ReaderEventResponseDto;
      makeMultiDescriptorGrammarFirstPublishSnapshot: (options?: {
        layerId?: string;
        grammarNoteSeg1?: string;
        grammarNoteSeg2?: string;
      }) => ReaderPlateSnapshotDto;
      makeMultiDescriptorGrammarFirstPublishEvent: (options?: {
        layerId?: string;
        unitId?: string;
        generation?: number;
      }) => ReaderEventResponseDto;
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
    phrase_type: "fixed_collocation",
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
        covered_anchor_segment_ids: ["seg_1"],
        source_text_hash: "seg_hash_1",
        children: [{ text: TRANSLATION_TEXT_1 }],
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
        group_id: "group_translation_2",
        covered_anchor_segment_ids: ["seg_2"],
        source_text_hash: "seg_hash_2",
        children: [{ text: TRANSLATION_TEXT_2 }],
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

/**
 * R2.1E: Build a VALID `layer_published` event with the full 7-field payload.
 * Unlike `makeLayerPublishedEvent` (which has an invalid partial payload used
 * to trigger fallback), this produces a payload that passes the merger's
 * `parseLayerPublishedPayload` + `validateLayerPublishedPayload` checks.
 */
function makeValidLayerPublishedEvent(
  layerType:
    | "translation"
    | "vocabulary"
    | "grammar_note"
    | "sentence_analysis" = "translation",
  sequence = 9,
): ReaderEventResponseDto {
  return {
    id: `evt_${sequence}`,
    reading_record_id: "record_1",
    sequence,
    event_type: "layer_published",
    payload: {
      record_id: "record_1",
      base_id: "base_1",
      layer_id: `layer_${layerType}_1`,
      layer_type: layerType,
      target_scope: "unit",
      target_key: "unit_1",
      generation: 1,
    },
    created_at: "2026-06-24T02:00:00Z",
  } as never;
}

/**
 * R2.1E: Build a same-topology layer revision snapshot.
 *
 * Maps over the base snapshot's unit children and overrides one layer's
 * content text while preserving the block topology (same block IDs, same
 * order, same count). This is the "non-structural revision" case where the
 * R2.1E merger should return `targeted_apply` with changed-block-only
 * replace operations.
 *
 * - translationText: overrides `reader_translation_group.children[0].text`
 * - grammarNote: overrides `reader_grammar_note_marks[].note` in source block
 * - analysisText: overrides `reader_sentence_analysis.analysis` + `.children[0].text`
 */
function makeLayerRevisionSnapshot(
  base: ReaderPlateSnapshotDto,
  options: {
    translationText?: string;
    grammarNote?: string;
    analysisText?: string;
    vocabularyGloss?: string;
  } = {},
): ReaderPlateSnapshotDto {
  const unit = base.value[0] as ReaderUnitNodeDto;
  const revisedUnit: ReaderUnitNodeDto = {
    ...unit,
    children: unit.children.map((child) => {
      if (
        child.type === "reader_translation_group" &&
        options.translationText !== undefined
      ) {
        return {
          ...child,
          children: [{ text: options.translationText }],
        };
      }
      if (
        child.type === "reader_sentence_analysis" &&
        options.analysisText !== undefined
      ) {
        return {
          ...child,
          analysis: options.analysisText,
          children: [{ text: options.analysisText }],
        };
      }
      if (
        child.type === "reader_source_block" &&
        (options.grammarNote !== undefined || options.vocabularyGloss !== undefined)
      ) {
        return {
          ...child,
          children: child.children.map((seg) => {
            if (!("type" in seg)) return seg;
            if (seg.type !== "reader_anchor_segment") return seg;
            return {
              ...seg,
              children: seg.children.map((leaf) => ({
                ...leaf,
                reader_grammar_note_marks:
                  options.grammarNote !== undefined
                    ? (leaf.reader_grammar_note_marks ?? []).map((mark) => ({
                        ...mark,
                        note: options.grammarNote!,
                      }))
                    : leaf.reader_grammar_note_marks,
                reader_vocabulary_marks:
                  options.vocabularyGloss !== undefined
                    ? (leaf.reader_vocabulary_marks ?? []).map((mark) => ({
                        ...mark,
                        gloss: options.vocabularyGloss!,
                      }))
                    : leaf.reader_vocabulary_marks,
              })),
            };
          }),
        };
      }
      return child;
    }),
  };
  return {
    ...base,
    snapshot_id: "snapshot_layer_revision",
    last_event_sequence: 9,
    value: [revisedUnit],
  };
}

/**
 * R2.1E: Build a structural-change snapshot that adds a SECOND
 * `reader_sentence_analysis` node to the unit's children.
 *
 * This changes the block topology (new block ID, increased count) so the
 * R2.1E merger must return `fallback_full_reload` with reason
 * `unit_block_set_changed`.
 */
function makeStructuralChangeSnapshot(
  base: ReaderPlateSnapshotDto,
): ReaderPlateSnapshotDto {
  const unit = base.value[0] as ReaderUnitNodeDto;
  const revisedUnit: ReaderUnitNodeDto = {
    ...unit,
    children: [
      ...unit.children,
      {
        type: "reader_sentence_analysis",
        owner: "system_ai",
        analysis_id: "analysis_2",
        layer_id: "layer_sentence_analysis_2",
        layer_version: 1,
        base_id: "base_1",
        unit_id: "unit_1",
        target_scope: "unit",
        target_key: "unit_1",
        anchor_segment_id: "seg_1",
        selected_text: SOURCE_TEXT_1,
        label: "second analysis",
        analysis: "Second analysis text (structural change).",
        chunks: [
          { order: 1, label: "whole", text: SOURCE_TEXT_1 },
        ],
        children: [{ text: "Second analysis text (structural change)." }],
      },
    ],
  };
  return {
    ...base,
    snapshot_id: "snapshot_structural_change",
    last_event_sequence: 9,
    value: [revisedUnit],
  };
}

// ---------------------------------------------------------------------------
// Harness component
// ---------------------------------------------------------------------------

/**
 * R2.2-P2a: Build a snapshot with grammar_note marks on BOTH seg_1 and seg_2.
 *
 * The base harness fixture only has grammar marks on seg_1. This builder
 * adds a grammar mark to seg_2 so the projection produces two independent
 * callout-group blocks (one per anchor). This is used to verify Method A2
 * cross-anchor group splitting and independent expansion state.
 *
 * seg_1 grammar item: grammar_item_1 (existing)
 * seg_2 grammar item: grammar_item_2 (new, on "second" at offset 10–16)
 */
function makeMultiAnchorGrammarSnapshot(
  base: ReaderPlateSnapshotDto,
): ReaderPlateSnapshotDto {
  const unit = base.value[0] as ReaderUnitNodeDto;
  const seg2GrammarMark = makeGrammarMark({
    mark_id: "grammar_mark_2",
    item_id: "grammar_item_2",
    anchor_segment_id: "seg_2",
    start_offset: 10,
    end_offset: 16,
    selected_text: "second",
    segment_start_utf16: 10,
    segment_end_utf16: 16,
    grammar_point: "ordinal adjective",
    pattern: "ordinal + noun",
    note: "second modifies test sentence.",
  });

  const revisedUnit: ReaderUnitNodeDto = {
    ...unit,
    children: unit.children.map((child) => {
      if (child.type !== "reader_source_block") return child;
      return {
        ...child,
        children: child.children.map((seg) => {
          if (!("type" in seg) || seg.type !== "reader_anchor_segment") return seg;
          if (seg.anchor_segment_id !== "seg_2") return seg;
          return {
            ...seg,
            children: seg.children.map((leaf) => ({
              ...leaf,
              reader_grammar_note_marks: [seg2GrammarMark],
            })),
          };
        }),
      };
    }),
  };

  return {
    ...base,
    snapshot_id: "snapshot_multi_anchor_grammar",
    last_event_sequence: 9,
    value: [revisedUnit],
  };
}

/**
 * R2.2-P2a: Build a same-topology revision that changes ONLY seg_2's
 * grammar note text, leaving seg_1's grammar marks untouched.
 *
 * This is used to verify that a targeted_apply on seg_2's callout-group
 * preserves seg_1's callout-group expansion state.
 */
function makeSeg2GrammarRevisionSnapshot(
  base: ReaderPlateSnapshotDto,
  options: { grammarNote?: string } = {},
): ReaderPlateSnapshotDto {
  const unit = base.value[0] as ReaderUnitNodeDto;
  const revisedUnit: ReaderUnitNodeDto = {
    ...unit,
    children: unit.children.map((child) => {
      if (child.type !== "reader_source_block") return child;
      return {
        ...child,
        children: child.children.map((seg) => {
          if (!("type" in seg) || seg.type !== "reader_anchor_segment") return seg;
          if (seg.anchor_segment_id !== "seg_2") return seg;
          return {
            ...seg,
            children: seg.children.map((leaf) => ({
              ...leaf,
              reader_grammar_note_marks: (leaf.reader_grammar_note_marks ?? []).map(
                (mark) => ({
                  ...mark,
                  note: options.grammarNote ?? mark.note,
                }),
              ),
            })),
          };
        }),
      };
    }),
  };

  return {
    ...base,
    snapshot_id: "snapshot_seg2_grammar_revision",
    last_event_sequence: 10,
    value: [revisedUnit],
  };
}

// ---------------------------------------------------------------------------
// T4.2a-PUX-R4-R2.2-P2c-R1: Grammar first-publish fixture builders.
//
// 这些 builder 构造 grammar_note 首发场景的 next snapshot 和 layer_published
// 事件。首发场景的关键约束：
//   1. prev 中目标 anchor 没有任何 grammar callout-group。
//   2. next 中目标 anchor 出现 grammar callout-group。
//   3. callout-group 必须紧随对应 paragraph 之后（C7: insert path =
//      prev paragraph index + 1），因此需要抑制 translation group 的
//      blockquote 投影（将 covered_anchor_segment_ids 置空，使投影走
//      fallback paragraph 路径，每个 anchor segment 独成一个 paragraph）。
//   4. 事件的 insertions[].layer_id / anchor_segment_id / item_ids 必须与
//      snapshot 投影产生的 callout-group 内容严格一致（C5 校验）。
//
// item_id 格式（后端 P2b 合同）：f"{layer_id}:grammar_note:{item_index}"
// 投影侧 buildGrammarCalloutBlocks 直接使用 mark.item_id 作为 callout
// block ID 后缀，因此 snapshot 中的 item_id 必须与事件 descriptor 的
// item_ids 完全一致。
//
// 测试（Task 6）负责构造 prev snapshot：取 next snapshot 并剥离目标
// anchor 的 grammar marks 后通过 loadSnapshot 加载。
// ---------------------------------------------------------------------------

/** 默认首发 grammar layer ID（区别于初始 fixture 的 layer_grammar_1）。 */
const GRAMMAR_FIRST_PUBLISH_LAYER_ID = "layer_grammar_first_publish";

/**
 * 根据 anchor segment 选择合适的 grammar mark 偏移量。
 * seg_1 锚定 "shapes"（offset 21–27），seg_2 锚定 "second"（offset 10–16）。
 */
function grammarMarkOffsetsForSegment(
  anchorSegmentId: "seg_1" | "seg_2",
): Pick<
  ReaderGrammarNoteMarkDto,
  | "start_offset"
  | "end_offset"
  | "selected_text"
  | "segment_start_utf16"
  | "segment_end_utf16"
  | "grammar_point"
  | "pattern"
> {
  if (anchorSegmentId === "seg_1") {
    return {
      start_offset: 21,
      end_offset: 27,
      selected_text: "shapes",
      segment_start_utf16: 21,
      segment_end_utf16: 27,
      grammar_point: "predicate verb",
      pattern: "subject + verb",
    };
  }
  return {
    start_offset: 10,
    end_offset: 16,
    selected_text: "second",
    segment_start_utf16: 10,
    segment_end_utf16: 16,
    grammar_point: "ordinal adjective",
    pattern: "ordinal + noun",
  };
}

/**
 * P2c-R1: 构造 grammar 首发 next snapshot。
 *
 * 在 base snapshot 基础上：
 *   - 抑制 translation group 的 blockquote 投影（covered_anchor_segment_ids
 *     置空），使每个 anchor segment 走 fallback paragraph 路径，保证
 *     callout-group 紧随 paragraph（C7 path = paragraph_index + 1）。
 *   - 剥离所有 segment 的既有 grammar marks（clean slate）。
 *   - 在目标 anchor segment 上放置新的 grammar marks（使用指定 layer_id
 *     和 item_ids），投影后产生 callout-group:{unitId}:{anchorSegmentId}。
 *
 * 测试需构造 prev：取此函数返回值，剥离目标 anchor 的 grammar marks，
 * 通过 loadSnapshot 加载后再调用 reloadWith。
 */
function makeGrammarFirstPublishSnapshot(
  base: ReaderPlateSnapshotDto,
  options: {
    anchorSegmentId?: "seg_1" | "seg_2";
    grammarNote?: string;
    layerId?: string;
    itemIds?: string[];
  } = {},
): ReaderPlateSnapshotDto {
  const {
    anchorSegmentId = "seg_1",
    grammarNote = "Grammar first-publish note.",
    layerId = GRAMMAR_FIRST_PUBLISH_LAYER_ID,
    itemIds = [`${layerId}:grammar_note:0`],
  } = options;

  const offsets = grammarMarkOffsetsForSegment(anchorSegmentId);

  // 为目标 anchor 构造 grammar marks，每个 item_id 对应一条 mark。
  const grammarMarks: ReaderGrammarNoteMarkDto[] = itemIds.map(
    (itemId, index) =>
      makeGrammarMark({
        mark_id: `grammar_mark_first_publish_${anchorSegmentId}_${index}`,
        item_id: itemId,
        layer_id: layerId,
        anchor_segment_id: anchorSegmentId,
        note: grammarNote,
        ...offsets,
      }),
  );

  const unit = base.value[0] as ReaderUnitNodeDto;
  const revisedUnit: ReaderUnitNodeDto = {
    ...unit,
    children: unit.children.map((child) => {
      // 抑制 translation group span，避免 blockquote 出现在 paragraph 和
      // callout-group 之间（C7 要求 callout-group 位于 paragraph_index + 1）。
      if (child.type === "reader_translation_group") {
        return { ...child, covered_anchor_segment_ids: [] };
      }
      if (child.type !== "reader_source_block") return child;
      return {
        ...child,
        children: child.children.map((seg) => {
          if (!("type" in seg) || seg.type !== "reader_anchor_segment") return seg;
          const isTarget = seg.anchor_segment_id === anchorSegmentId;
          return {
            ...seg,
            children: seg.children.map((leaf) => ({
              ...leaf,
              // 首发场景：目标 anchor 放置新 grammar marks，其余 anchor 剥离 grammar。
              reader_grammar_note_marks: isTarget ? grammarMarks : [],
            })),
          };
        }),
      };
    }),
  };

  return {
    ...base,
    snapshot_id: "snapshot_grammar_first_publish",
    last_event_sequence: 9,
    value: [revisedUnit],
  };
}

/**
 * P2c-R1: 构造 grammar 首发 layer_published 事件（P2b 扩展 payload）。
 *
 * payload 包含全部 10 个字段：7 个 base 字段 + 3 个 P2b 扩展字段
 * （schema_version, operation, insertions）。insertions 中的 layer_id /
 * anchor_segment_id / item_ids 必须与 makeGrammarFirstPublishSnapshot
 * 产出的 snapshot 投影内容严格一致，否则 merger C5 校验失败。
 */
function makeGrammarFirstPublishEvent(
  options: {
    layerId?: string;
    anchorSegmentId?: "seg_1" | "seg_2";
    itemIds?: string[];
    unitId?: string;
    generation?: number;
  } = {},
): ReaderEventResponseDto {
  const {
    layerId = GRAMMAR_FIRST_PUBLISH_LAYER_ID,
    anchorSegmentId = "seg_1",
    itemIds = [`${layerId}:grammar_note:0`],
    unitId = "unit_1",
    generation = 1,
  } = options;

  return {
    id: "evt_grammar_first_publish",
    reading_record_id: "record_1",
    sequence: 9,
    event_type: "layer_published",
    payload: {
      record_id: "record_1",
      base_id: "base_1",
      layer_id: layerId,
      layer_type: "grammar_note",
      target_scope: "unit",
      target_key: unitId,
      generation,
      // P2b 扩展字段
      schema_version: 1,
      operation: "insert_after_anchor",
      insertions: [
        {
          unit_id: unitId,
          anchor_segment_id: anchorSegmentId,
          kind: "grammar_note",
          layer_id: layerId,
          item_ids: itemIds,
        },
      ],
    },
    created_at: "2026-06-24T02:00:00Z",
  } as never;
}

/**
 * P2c-R1: 构造多 descriptor grammar 首发 next snapshot。
 *
 * 在 seg_1 和 seg_2 上各放置一条 grammar mark（同一 layer_id，不同
 * item_id），投影后产生两个独立 callout-group。用于测试多 descriptor
 * path 降序插入场景。
 *
 * 投影顺序（translation 抑制后）：
 *   0: paragraph:seg_1
 *   1: callout-group:unit_1:seg_1  ← insert path = 0 + 1 = 1
 *   2: sentence_analysis:analysis_1
 *   3: paragraph:seg_2
 *   4: callout-group:unit_1:seg_2  ← insert path = 3 + 1 = 4
 *
 * merger 按 path 降序应用：先插 [4]（seg_2），再插 [1]（seg_1）。
 */
function makeMultiDescriptorGrammarFirstPublishSnapshot(
  base: ReaderPlateSnapshotDto,
  options: {
    layerId?: string;
    grammarNoteSeg1?: string;
    grammarNoteSeg2?: string;
  } = {},
): ReaderPlateSnapshotDto {
  const {
    layerId = GRAMMAR_FIRST_PUBLISH_LAYER_ID,
    grammarNoteSeg1 = "Grammar first-publish note for seg_1.",
    grammarNoteSeg2 = "Grammar first-publish note for seg_2.",
  } = options;

  const seg1Offsets = grammarMarkOffsetsForSegment("seg_1");
  const seg2Offsets = grammarMarkOffsetsForSegment("seg_2");

  const seg1GrammarMark = makeGrammarMark({
    mark_id: "grammar_mark_first_publish_seg_1_0",
    item_id: `${layerId}:grammar_note:0`,
    layer_id: layerId,
    anchor_segment_id: "seg_1",
    note: grammarNoteSeg1,
    ...seg1Offsets,
  });

  const seg2GrammarMark = makeGrammarMark({
    mark_id: "grammar_mark_first_publish_seg_2_1",
    item_id: `${layerId}:grammar_note:1`,
    layer_id: layerId,
    anchor_segment_id: "seg_2",
    note: grammarNoteSeg2,
    ...seg2Offsets,
  });

  const unit = base.value[0] as ReaderUnitNodeDto;
  const revisedUnit: ReaderUnitNodeDto = {
    ...unit,
    children: unit.children.map((child) => {
      if (child.type === "reader_translation_group") {
        return { ...child, covered_anchor_segment_ids: [] };
      }
      if (child.type !== "reader_source_block") return child;
      return {
        ...child,
        children: child.children.map((seg) => {
          if (!("type" in seg) || seg.type !== "reader_anchor_segment") return seg;
          const grammarMarks =
            seg.anchor_segment_id === "seg_1"
              ? [seg1GrammarMark]
              : seg.anchor_segment_id === "seg_2"
                ? [seg2GrammarMark]
                : [];
          return {
            ...seg,
            children: seg.children.map((leaf) => ({
              ...leaf,
              reader_grammar_note_marks: grammarMarks,
            })),
          };
        }),
      };
    }),
  };

  return {
    ...base,
    snapshot_id: "snapshot_multi_descriptor_grammar_first_publish",
    last_event_sequence: 9,
    value: [revisedUnit],
  };
}

/**
 * P2c-R1: 构造多 descriptor grammar 首发 layer_published 事件。
 *
 * 包含 2 个 insertion descriptor（seg_1 + seg_2），同一 unit_id、同一
 * layer_id。item_ids 与 snapshot 投影产生的 callout-group 内容一致。
 */
function makeMultiDescriptorGrammarFirstPublishEvent(
  options: {
    layerId?: string;
    unitId?: string;
    generation?: number;
  } = {},
): ReaderEventResponseDto {
  const {
    layerId = GRAMMAR_FIRST_PUBLISH_LAYER_ID,
    unitId = "unit_1",
    generation = 1,
  } = options;

  return {
    id: "evt_multi_descriptor_grammar_first_publish",
    reading_record_id: "record_1",
    sequence: 9,
    event_type: "layer_published",
    payload: {
      record_id: "record_1",
      base_id: "base_1",
      layer_id: layerId,
      layer_type: "grammar_note",
      target_scope: "unit",
      target_key: unitId,
      generation,
      schema_version: 1,
      operation: "insert_after_anchor",
      insertions: [
        {
          unit_id: unitId,
          anchor_segment_id: "seg_1",
          kind: "grammar_note",
          layer_id: layerId,
          item_ids: [`${layerId}:grammar_note:0`],
        },
        {
          unit_id: unitId,
          anchor_segment_id: "seg_2",
          kind: "grammar_note",
          layer_id: layerId,
          item_ids: [`${layerId}:grammar_note:1`],
        },
      ],
    },
    created_at: "2026-06-24T02:00:00Z",
  } as never;
}

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
      loadSnapshot: (nextSnapshot: ReaderPlateSnapshotDto) => {
        // Set snapshot without a reload context — triggers the normal
        // snapshot change effect (full reload via setValue). Used by
        // P2a E2E tests to load a custom initial fixture.
        setSnapshot(nextSnapshot);
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
      // R2.1E: layer_published revision + structural change builders.
      makeLayerRevisionSnapshot: (options = {}) => {
        return makeLayerRevisionSnapshot(snapshotRef.current, options);
      },
      makeStructuralChangeSnapshot: () => {
        return makeStructuralChangeSnapshot(snapshotRef.current);
      },
      makeValidLayerPublishedEvent: (layerType = "translation", sequence = 9) => {
        return makeValidLayerPublishedEvent(layerType, sequence);
      },
      // R2.2-P2a: multi-anchor grammar fixture builders.
      makeMultiAnchorGrammarSnapshot: () => {
        return makeMultiAnchorGrammarSnapshot(snapshotRef.current);
      },
      makeSeg2GrammarRevisionSnapshot: (options = {}) => {
        return makeSeg2GrammarRevisionSnapshot(snapshotRef.current, options);
      },
      // R2.2-P2c-R1: grammar first-publish fixture builders.
      makeGrammarFirstPublishSnapshot: (options = {}) => {
        return makeGrammarFirstPublishSnapshot(snapshotRef.current, options);
      },
      makeGrammarFirstPublishEvent: (options = {}) => {
        return makeGrammarFirstPublishEvent(options);
      },
      makeMultiDescriptorGrammarFirstPublishSnapshot: (options = {}) => {
        return makeMultiDescriptorGrammarFirstPublishSnapshot(
          snapshotRef.current,
          options,
        );
      },
      makeMultiDescriptorGrammarFirstPublishEvent: (options = {}) => {
        return makeMultiDescriptorGrammarFirstPublishEvent(options);
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
  makeValidLayerPublishedEvent,
  makeLayerRevisionSnapshot,
  makeStructuralChangeSnapshot,
  makeGrammarFirstPublishSnapshot,
  makeGrammarFirstPublishEvent,
  makeMultiDescriptorGrammarFirstPublishSnapshot,
  makeMultiDescriptorGrammarFirstPublishEvent,
};
