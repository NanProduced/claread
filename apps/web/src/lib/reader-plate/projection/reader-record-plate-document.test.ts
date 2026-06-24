import { describe, expect, it } from "vitest";
import { computeUtf16FNV1a } from "@claread/contracts";

import {
  READER_PLATE_SNAPSHOT_SCHEMA_KIND,
  READER_TEXT_RANGE_HASH_ALGORITHM,
  READER_TEXT_RANGE_OFFSET_UNIT,
  type ReaderEnhancementProgressDto,
  type ReaderPlateSnapshotDto,
  type ReaderSnapshotUserAssetDto,
  type ReaderUnitNodeDto,
  type ReaderVocabularyMarkDto,
  type ReaderGrammarNoteMarkDto,
} from "@/types/api/reader-plate";

import {
  READER_RECORD_PLATE_DOCUMENT_SCHEMA_VERSION,
  projectReaderPlateSnapshotToReaderRecordPlateDocument,
  type ReaderRecordPlateAnchorSegmentNode,
  type ReaderRecordPlateSourceBlockNode,
  type ReaderRecordPlateTranslationBlockNode,
} from "./reader-record-plate-document";

const FIRST_TEXT = "Institutional memory shapes policy choices.";
const SECOND_TEXT = "Those choices persist.";
const SEPARATOR_TEXT = "\n\n";
const SECOND_START = FIRST_TEXT.length + SEPARATOR_TEXT.length;

function makeVocabularyMark(
  overrides: Partial<ReaderVocabularyMarkDto> = {},
): ReaderVocabularyMarkDto {
  return {
    mark_id: "vocab_mark_1",
    layer_id: "layer_vocab_1",
    item_type: "phrase_gloss",
    anchor_segment_id: "seg_1",
    start_offset: 0,
    end_offset: 20,
    selected_text: "Institutional memory",
    segment_start_utf16: 0,
    segment_end_utf16: 20,
    starts_here: true,
    ends_here: true,
    phrase: "Institutional memory",
    phrase_type: "collocation",
    gloss: "制度记忆",
    example: "Institutional memory shapes future choices.",
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
    pattern: "subject + verb + object",
    note: "shapes acts as the predicate verb.",
    ...overrides,
  };
}

function makeUserAsset(
  overrides: Partial<ReaderSnapshotUserAssetDto> = {},
): ReaderSnapshotUserAssetDto {
  return {
    asset_id: "asset_highlight_1",
    asset_type: "highlight",
    owner: "user",
    reading_record_id: "record_1",
    generation: 1,
    anchor: {
      anchor_type: "text_range",
      base_id: "base_1",
      unit_id: "unit_1",
      anchor_segment_id: "seg_1",
      sentence_id: "sent_1",
      segment_type: "sentence",
      offset_unit: READER_TEXT_RANGE_OFFSET_UNIT,
      start_offset: 0,
      end_offset: 20,
      selected_text: "Institutional memory",
      text_hash: computeUtf16FNV1a("Institutional memory"),
      hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
    },
    created_at: "2026-06-24T01:00:00Z",
    updated_at: "2026-06-24T01:00:00Z",
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
    base_end_utf16: SECOND_START + SECOND_TEXT.length,
    text_hash: "unit_hash",
    hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
    children: [
      {
        type: "reader_source_block",
        owner: "stable",
        base_id: "base_1",
        unit_id: "unit_1",
        base_start_utf16: 0,
        base_end_utf16: SECOND_START + SECOND_TEXT.length,
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
            base_start_utf16: 0,
            base_end_utf16: FIRST_TEXT.length,
            unit_start_utf16: 0,
            unit_end_utf16: FIRST_TEXT.length,
            text_hash: "seg_1_hash",
            hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
            children: [
              {
                text: FIRST_TEXT,
                owner: "stable",
                lock_source: true,
                source_role: "segment_text",
                base_start_utf16: 0,
                base_end_utf16: FIRST_TEXT.length,
                anchor_segment_id: "seg_1",
                segment_start_utf16: 0,
                segment_end_utf16: FIRST_TEXT.length,
                reader_vocabulary_marks: [makeVocabularyMark()],
                reader_grammar_note_marks: [makeGrammarMark()],
              },
            ],
          },
          {
            text: SEPARATOR_TEXT,
            owner: "stable",
            lock_source: true,
            source_role: "separator",
            base_start_utf16: FIRST_TEXT.length,
            base_end_utf16: SECOND_START,
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
            base_start_utf16: SECOND_START,
            base_end_utf16: SECOND_START + SECOND_TEXT.length,
            unit_start_utf16: SECOND_START,
            unit_end_utf16: SECOND_START + SECOND_TEXT.length,
            text_hash: "seg_2_hash",
            hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
            children: [
              {
                text: SECOND_TEXT,
                owner: "stable",
                lock_source: true,
                source_role: "segment_text",
                base_start_utf16: SECOND_START,
                base_end_utf16: SECOND_START + SECOND_TEXT.length,
                anchor_segment_id: "seg_2",
                segment_start_utf16: 0,
                segment_end_utf16: SECOND_TEXT.length,
              },
            ],
          },
        ],
      },
      {
        type: "reader_translation",
        owner: "system_ai",
        layer_id: "layer_translation_1",
        layer_version: 1,
        base_id: "base_1",
        unit_id: "unit_1",
        target_scope: "unit",
        target_key: "unit_1",
        target_language: "zh",
        confidence: "normal",
        notes: [],
        children: [{ text: "制度记忆会塑造政策选择，这些选择会持续存在。" }],
      },
      {
        type: "reader_sentence_analysis",
        owner: "system_ai",
        analysis_id: "analysis_seg_1",
        layer_id: "layer_sentence_analysis_1",
        layer_version: 1,
        base_id: "base_1",
        unit_id: "unit_1",
        target_scope: "unit",
        target_key: "unit_1",
        anchor_segment_id: "seg_1",
        selected_text: FIRST_TEXT,
        label: "subject driving predicate",
        analysis: "Institutional memory is the subject; shapes is the predicate.",
        chunks: [
          { order: 1, label: "subject", text: "Institutional memory" },
          { order: 2, label: "predicate", text: "shapes policy choices" },
        ],
        children: [
          {
            text: "Institutional memory is the subject; shapes is the predicate.",
          },
        ],
      },
    ],
  };
}

function makeSnapshot(
  progress?: ReaderEnhancementProgressDto,
  userAssets: ReaderSnapshotUserAssetDto[] = [],
): ReaderPlateSnapshotDto {
  return {
    schema_kind: READER_PLATE_SNAPSHOT_SCHEMA_KIND,
    snapshot_id: "snapshot_1",
    snapshot_taken_at: "2026-06-24T00:00:00Z",
    last_event_sequence: 7,
    record_id: "record_1",
    record: {
      title: "Projection Spike Fixture",
      created_at: "2026-06-24T00:00:00Z",
      source_type: "plain_text",
      source_metadata: {},
      generation: 1,
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
    },
    base: {
      base_id: "base_1",
      content_sha256: "a".repeat(64),
      canonicalizer_version: "test",
      builder_version: "test",
      segmenter_version: "test",
      text_length_utf16: SECOND_START + SECOND_TEXT.length,
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
          base_end_utf16: SECOND_START + SECOND_TEXT.length,
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
        base_start_utf16: 0,
        base_end_utf16: FIRST_TEXT.length,
        unit_start_utf16: 0,
        unit_end_utf16: FIRST_TEXT.length,
        text_hash: "seg_1_hash",
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
        base_start_utf16: SECOND_START,
        base_end_utf16: SECOND_START + SECOND_TEXT.length,
        unit_start_utf16: SECOND_START,
        unit_end_utf16: SECOND_START + SECOND_TEXT.length,
        text_hash: "seg_2_hash",
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      },
    ],
    enhancement_layers: [],
    enhancement_progress: progress,
    ask_supplements: [],
    user_assets: userAssets,
    parsed_decisions: [
      {
        unit_id: "unit_1",
        policy_code: "parsed",
        parsed_state: "parsed",
        rationale_code: "all_layers_present",
      },
    ],
    value: [makeUnit()],
  };
}

function sourceBlock(document = projectReaderPlateSnapshotToReaderRecordPlateDocument(makeSnapshot())) {
  const child = document.children[0].children[0];
  expect(child.type).toBe("reader_record_source_block");
  return child as ReaderRecordPlateSourceBlockNode;
}

function firstSegment(
  document = projectReaderPlateSnapshotToReaderRecordPlateDocument(makeSnapshot()),
) {
  const source = sourceBlock(document);
  const child = source.children[0];
  expect("type" in child && child.type).toBe("reader_record_anchor_segment");
  return child as ReaderRecordPlateAnchorSegmentNode;
}

describe("projectReaderPlateSnapshotToReaderRecordPlateDocument", () => {
  it("projects snapshot metadata and stable unit/source/anchor ids", () => {
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeSnapshot(),
    );

    expect(document).toMatchObject({
      type: "reader_record_plate_document",
      schemaVersion: READER_RECORD_PLATE_DOCUMENT_SCHEMA_VERSION,
      record: {
        recordId: "record_1",
        title: "Projection Spike Fixture",
        generation: 1,
      },
      snapshot: {
        snapshotId: "snapshot_1",
        lastEventSequence: 7,
      },
      base: {
        baseId: "base_1",
        hashAlgorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      },
    });

    const unit = document.children[0];
    expect(unit.id).toBe("unit:unit_1");
    expect(unit.unitId).toBe("unit_1");
    expect(unit.parsedDecision).toMatchObject({
      state: "parsed",
      policyCode: "parsed",
    });

    const segment = firstSegment(document);
    expect(segment.id).toBe("anchor_segment:seg_1");
    expect(segment.anchorSegmentId).toBe("seg_1");
    expect(segment.sentenceId).toBe("sent_1");
    expect(segment.baseRange).toEqual({ startUtf16: 0, endUtf16: FIRST_TEXT.length });
  });

  it("projects vocabulary and grammar annotations as text marks plus cues", () => {
    const segment = firstSegment();
    const phraseLeaf = segment.children.find((leaf) =>
      leaf.marks.some((mark) => mark.kind === "phrase_gloss"),
    );
    const grammarLeaf = segment.children.find((leaf) =>
      leaf.marks.some((mark) => mark.kind === "grammar_note"),
    );

    expect(segment.children.map((leaf) => leaf.text).join("")).toBe(FIRST_TEXT);
    expect(phraseLeaf?.text).toBe("Institutional memory");
    expect(grammarLeaf?.text).toBe("shapes");
    expect(phraseLeaf?.marks[0]).toMatchObject({
      id: "vocab_mark_1",
      layerId: "layer_vocab_1",
      anchor: {
        baseId: "base_1",
        unitId: "unit_1",
        anchorSegmentId: "seg_1",
        segmentStartOffset: 0,
        segmentEndOffset: 20,
      },
    });
    expect(phraseLeaf?.marks[0].anchor.selectedText).toBe("Institutional memory");
    expect(phraseLeaf?.marks[0].anchor.textHash).toBe(
      computeUtf16FNV1a("Institutional memory"),
    );
    expect(phraseLeaf?.marks[0].anchor.textHash).not.toBe(segment.textHash);

    expect(grammarLeaf?.marks[0].kind).toBe("grammar_note");
    expect(grammarLeaf?.marks[0].anchor.selectedText).toBe("shapes");
    expect(grammarLeaf?.marks[0].anchor.textHash).toBe(computeUtf16FNV1a("shapes"));
    expect(grammarLeaf?.marks[0].anchor.textHash).not.toBe(segment.textHash);

    const grammarCue = segment.cues.find(
      (cue) => cue.type === "reader_record_grammar_cue",
    );
    expect(grammarCue).toMatchObject({
      id: "grammar_note:grammar_item_1",
      itemId: "grammar_item_1",
      grammarPoint: "predicate verb",
    });
  });

  it("projects user assets as user-owned highlight marks and comment cues", () => {
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeSnapshot(undefined, [
        makeUserAsset(),
        makeUserAsset({
          asset_id: "asset_note_1",
          asset_type: "note",
          anchor: {
            anchor_type: "text_range",
            base_id: "base_1",
            unit_id: "unit_1",
            anchor_segment_id: "seg_1",
            sentence_id: "sent_1",
            segment_type: "sentence",
            offset_unit: READER_TEXT_RANGE_OFFSET_UNIT,
            start_offset: 21,
            end_offset: 27,
            selected_text: "shapes",
            text_hash: computeUtf16FNV1a("shapes"),
            hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
          },
        }),
      ]),
    );
    const segment = firstSegment(document);

    const highlightedLeaf = segment.children.find((leaf) =>
      leaf.marks.some((mark) => mark.kind === "user_highlight"),
    );
    const userHighlight = highlightedLeaf?.marks.find(
      (mark) => mark.kind === "user_highlight",
    );
    expect(highlightedLeaf?.text).toBe("Institutional memory");
    expect(userHighlight).toMatchObject({
      id: "user_highlight:asset_highlight_1",
      owner: "user",
      assetId: "asset_highlight_1",
      assetType: "highlight",
      anchor: {
        baseId: "base_1",
        unitId: "unit_1",
        anchorSegmentId: "seg_1",
        unitStartOffset: 0,
        unitEndOffset: 20,
        segmentStartOffset: 0,
        segmentEndOffset: 20,
        selectedText: "Institutional memory",
      },
    });

    const commentCue = segment.cues.find(
      (cue) => cue.type === "reader_record_user_comment_cue",
    );
    expect(commentCue).toMatchObject({
      id: "user_comment:asset_note_1",
      owner: "user",
      assetId: "asset_note_1",
      assetType: "note",
      label: "笔记",
      anchor: {
        anchorSegmentId: "seg_1",
        unitStartOffset: 21,
        unitEndOffset: 27,
        segmentStartOffset: 21,
        segmentEndOffset: 27,
        selectedText: "shapes",
      },
    });
  });

  it("keeps unit translation as a unit translation block instead of hanging it under the first segment", () => {
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeSnapshot(),
    );
    const unit = document.children[0];

    expect(unit.children.map((child) => child.type)).toEqual([
      "reader_record_source_block",
      "reader_record_unit_translation",
    ]);

    const translation = unit.children[1] as ReaderRecordPlateTranslationBlockNode;
    expect(translation).toMatchObject({
      type: "reader_record_unit_translation",
      id: "translation:layer_translation_1:unit_1",
      placement: "unit",
      targetScope: "unit",
      targetKey: "unit_1",
      unitId: "unit_1",
      targetLanguage: "zh",
    });
    expect(translation.children[0]).toEqual({
      text: "制度记忆会塑造政策选择，这些选择会持续存在。",
      owner: "system_ai",
      sourceRole: "unit_translation_text",
    });

    const segment = firstSegment(document);
    expect(segment.children.map((leaf) => leaf.text).join("")).toBe(FIRST_TEXT);
    expect(segment.children.every((leaf) => leaf.sourceRole === "segment_text")).toBe(
      true,
    );
    expect(JSON.stringify(segment)).not.toContain("reader_record_unit_translation");
  });

  it("projects sentence analysis as structure cues, not document-flow cards", () => {
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeSnapshot(),
    );
    const unit = document.children[0];
    const segment = firstSegment(document);

    expect(unit.children.map((child) => child.type)).not.toContain(
      "reader_sentence_analysis",
    );
    expect(unit.cues).toEqual(segment.cues);
    expect(
      segment.cues.find(
        (cue) => cue.type === "reader_record_sentence_analysis_cue",
      ),
    ).toMatchObject({
      id: "sentence_analysis:analysis_seg_1",
      layerId: "layer_sentence_analysis_1",
      anchorSegmentId: "seg_1",
      label: "subject driving predicate",
      chunks: [
        { order: 1, label: "subject", text: "Institutional memory" },
        { order: 2, label: "predicate", text: "shapes policy choices" },
      ],
    });
  });

  it("projects enhancement progress to document and matching unit activity state", () => {
    const progress: ReaderEnhancementProgressDto = {
      overall_status: "readable_enhancing",
      layers: [
        {
          capability: "translation",
          layer_type: "translation",
          status: "processing",
          job_id: "job_translation_1",
          target_scope: "unit",
          target_key: "unit_1",
        },
        {
          capability: "vocabulary",
          layer_type: "vocabulary",
          status: "queued",
          job_id: "job_vocab_1",
          target_scope: "anchor_segment",
          target_key: "seg_2",
        },
        {
          capability: "grammar",
          layer_type: "grammar_note",
          status: "failed",
          job_id: "job_grammar_record",
          target_scope: "record",
          target_key: "record_1",
          failure_code: "timeout",
          failure_message: "Timed out",
        },
      ],
    };

    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeSnapshot(progress),
    );

    expect(document.progress).toMatchObject({
      overallStatus: "readable_enhancing",
      layers: [
        {
          id: "progress:translation:unit:unit_1:job_translation_1",
          status: "processing",
        },
        {
          id: "progress:vocabulary:anchor_segment:seg_2:job_vocab_1",
          status: "queued",
        },
        {
          id: "progress:grammar:record:record_1:job_grammar_record",
          status: "failed",
          failureCode: "timeout",
        },
      ],
    });
    expect(document.children[0].progress.map((layer) => layer.id)).toEqual([
      "progress:translation:unit:unit_1:job_translation_1",
      "progress:vocabulary:anchor_segment:seg_2:job_vocab_1",
    ]);
  });
});
