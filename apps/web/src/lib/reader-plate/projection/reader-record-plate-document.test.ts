import { describe, expect, it } from "vitest";
import { computeUtf16FNV1a } from "@claread/contracts";

import {
  READER_PLATE_SNAPSHOT_SCHEMA_KIND,
  READER_TEXT_RANGE_HASH_ALGORITHM,
  READER_TEXT_RANGE_OFFSET_UNIT,
  type ReaderEnhancementProgressDto,
  type ReaderPlateSnapshotDto,
  type ReaderSnapshotAskSupplementDto,
  type ReaderSnapshotUserAssetDto,
  type ReaderSourceBlockChildNodeDto,
  type ReaderSourceBlockNodeDto,
  type ReaderUnitNodeDto,
  type ReaderVocabularyMarkDto,
  type ReaderGrammarNoteMarkDto,
} from "@/types/api/reader-plate";

import {
  READER_RECORD_PLATE_DOCUMENT_SCHEMA_VERSION,
  projectReaderPlateSnapshotToReaderRecordPlateDocument,
  type ReaderRecordPlateBlockquoteBlock,
  type ReaderRecordPlateCalloutBlock,
  type ReaderRecordPlateParagraphBlock,
  type ReaderRecordPlateSentenceAnalysisBlock,
} from "./reader-record-plate-document";

const FIRST_TEXT = "Institutional memory shapes policy choices.";
const SECOND_TEXT = "Those choices persist.";
const SEPARATOR_TEXT = "\n\n";
const SECOND_START = FIRST_TEXT.length + SEPARATOR_TEXT.length;

type ReaderAnchorSegmentFixture = Extract<
  ReaderSourceBlockChildNodeDto,
  { type: "reader_anchor_segment" }
>;

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

function makeSupplement(
  overrides: Partial<ReaderSnapshotAskSupplementDto> & {
    content?: Record<string, unknown>;
  } = {},
): ReaderSnapshotAskSupplementDto {
  const selectedText = "Institutional memory";
  const content: Record<string, unknown> = {
    supplement_type: "grammar_note",
    title: "关于 Institutional memory 的补充",
    content_md: "Institutional memory 指组织内部积累的经验与习惯。",
    target_key: "seg_1",
    sentence_id: "sent_1",
    schema_version: "reader_ask_supplement/v1",
    created_from_turn_run_id: "turn_run_1",
    lifecycle_status: "persisted",
    record_id: "record_1",
    base_id: "base_1",
    generation: 1,
    ...overrides.content,
  };
  return {
    supplement_id: "supplement_1",
    owner: "ask_supplement",
    anchor: {
      anchor_type: "text_range",
      base_id: "base_1",
      unit_id: "unit_1",
      anchor_segment_id: "seg_1",
      sentence_id: "sent_1",
      segment_type: "sentence",
      offset_unit: READER_TEXT_RANGE_OFFSET_UNIT,
      start_offset: 0,
      end_offset: selectedText.length,
      selected_text: selectedText,
      text_hash: computeUtf16FNV1a(selectedText),
      hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
    },
    content,
    created_at: "2026-06-24T02:00:00Z",
    ...overrides,
  };
}

function makeSnapshot(
  progress?: ReaderEnhancementProgressDto,
  userAssets: ReaderSnapshotUserAssetDto[] = [],
  askSupplements: ReaderSnapshotAskSupplementDto[] = [],
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
    ask_supplements: askSupplements,
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

function firstParagraph(
  document = projectReaderPlateSnapshotToReaderRecordPlateDocument(makeSnapshot()),
): ReaderRecordPlateParagraphBlock {
  const block = document.children.find((child) => child.type === "paragraph");
  if (!block) {
    throw new Error("Expected a paragraph block");
  }
  return block as ReaderRecordPlateParagraphBlock;
}

function firstCallout(
  variant?: "grammar" | "supplement",
  document = projectReaderPlateSnapshotToReaderRecordPlateDocument(makeSnapshot()),
): ReaderRecordPlateCalloutBlock {
  const block = document.children.find(
    (child) =>
      child.type === "callout" &&
      (variant === undefined ||
        (child as ReaderRecordPlateCalloutBlock).variant === variant),
  );
  if (!block) {
    throw new Error(`Expected a ${variant ?? "callout"} block`);
  }
  return block as ReaderRecordPlateCalloutBlock;
}

function firstSentenceAnalysis(
  document = projectReaderPlateSnapshotToReaderRecordPlateDocument(makeSnapshot()),
): ReaderRecordPlateSentenceAnalysisBlock {
  const block = document.children.find((child) => child.type === "sentence_analysis");
  if (!block) {
    throw new Error("Expected a sentence_analysis block");
  }
  return block as ReaderRecordPlateSentenceAnalysisBlock;
}

function firstBlockquote(
  document = projectReaderPlateSnapshotToReaderRecordPlateDocument(makeSnapshot()),
): ReaderRecordPlateBlockquoteBlock {
  const block = document.children.find((child) => child.type === "blockquote");
  if (!block) {
    throw new Error("Expected a blockquote block");
  }
  return block as ReaderRecordPlateBlockquoteBlock;
}

describe("projectReaderPlateSnapshotToReaderRecordPlateDocument", () => {
  it("projects snapshot metadata and stable paragraph ids", () => {
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

    const paragraph = firstParagraph(document);
    expect(paragraph.id).toBe("paragraph:seg_1");
    expect(paragraph.data.anchorSegmentId).toBe("seg_1");
    expect(paragraph.data.sentenceId).toBe("sent_1");
    expect(paragraph.data.unitId).toBe("unit_1");
    expect(paragraph.data.isUnitStart).toBe(true);
    expect(paragraph.data.baseRange).toEqual({
      startUtf16: 0,
      endUtf16: FIRST_TEXT.length,
    });
  });

  it("marks only the first paragraph in a unit as unit start", () => {
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeSnapshot(),
    );
    const paragraphs = document.children.filter(
      (child): child is ReaderRecordPlateParagraphBlock =>
        child.type === "paragraph",
    );

    expect(paragraphs).toHaveLength(2);
    expect(paragraphs[0]?.data.isUnitStart).toBe(true);
    expect(paragraphs[1]?.data.isUnitStart).toBeUndefined();
  });

  it("projects vocabulary and grammar annotations as text marks", () => {
    const paragraph = firstParagraph();
    const phraseLeaf = paragraph.children.find((leaf) =>
      leaf.marks.some((mark) => mark.kind === "phrase_gloss"),
    );
    const grammarLeaf = paragraph.children.find((leaf) =>
      leaf.marks.some((mark) => mark.kind === "grammar_note"),
    );

    expect(paragraph.children.map((leaf) => leaf.text).join("")).toBe(FIRST_TEXT);
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

    expect(grammarLeaf?.marks[0].kind).toBe("grammar_note");
    expect(grammarLeaf?.marks[0].anchor.selectedText).toBe("shapes");
    expect(grammarLeaf?.marks[0].anchor.textHash).toBe(computeUtf16FNV1a("shapes"));
  });

  it("projects grammar notes as callout blocks with grammar variant", () => {
    const grammarCallout = firstCallout("grammar");

    expect(grammarCallout.id).toBe("callout:grammar:grammar_item_1");
    expect(grammarCallout.variant).toBe("grammar");
    expect(grammarCallout.icon).toBe("📖");
    expect(grammarCallout.data).toMatchObject({
      anchorSegmentId: "seg_1",
      unitId: "unit_1",
      layerId: "layer_grammar_1",
      itemId: "grammar_item_1",
      grammarPoint: "predicate verb",
      pattern: "subject + verb + object",
      note: "shapes acts as the predicate verb.",
    });
    expect(grammarCallout.children[0]).toMatchObject({
      type: "p",
      children: [{ text: "shapes acts as the predicate verb." }],
    });
  });

  it("projects sentence analysis as independent sentence_analysis blocks", () => {
    const analysisBlock = firstSentenceAnalysis();

    expect(analysisBlock.type).toBe("sentence_analysis");
    expect(analysisBlock.id).toBe("sentence_analysis:analysis_seg_1");
    expect(analysisBlock.icon).toBe("🔍");
    expect(analysisBlock.data).toMatchObject({
      anchorSegmentId: "seg_1",
      unitId: "unit_1",
      layerId: "layer_sentence_analysis_1",
      analysisId: "analysis_seg_1",
      label: "subject driving predicate",
      analysis:
        "Institutional memory is the subject; shapes is the predicate.",
    });
    expect(analysisBlock.data.chunks).toEqual([
      {
        order: 1,
        label: "subject",
        text: "Institutional memory",
        sourceMatch: {
          anchorSegmentId: "seg_1",
          startOffset: 0,
          endOffset: 20,
          markId: "sentence_chunk:analysis_seg_1:1:subject",
        },
      },
      {
        order: 2,
        label: "predicate",
        text: "shapes policy choices",
        sourceMatch: {
          anchorSegmentId: "seg_1",
          startOffset: 21,
          endOffset: 42,
          markId: "sentence_chunk:analysis_seg_1:2:predicate",
        },
      },
    ]);
    expect(analysisBlock.children[0]).toMatchObject({
      type: "p",
      children: [
        { text: "Institutional memory is the subject; shapes is the predicate." },
      ],
    });
  });

  it("projects user highlight assets as user-owned highlight marks", () => {
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeSnapshot(undefined, [makeUserAsset()]),
    );
    const paragraph = firstParagraph(document);

    const highlightedLeaf = paragraph.children.find((leaf) =>
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
  });

  it("projects unit translation as a blockquote block", () => {
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeSnapshot(),
    );
    const blockquote = firstBlockquote(document);

    expect(blockquote.id).toBe("blockquote:layer_translation_1:unit_1");
    expect(blockquote.data).toMatchObject({
      unitId: "unit_1",
      layerId: "layer_translation_1",
      layerVersion: 1,
      targetLanguage: "zh",
      confidence: "normal",
    });
    expect(blockquote.children[0]).toEqual({
      text: "制度记忆会塑造政策选择，这些选择会持续存在。",
      owner: "system_ai",
      sourceRole: "unit_translation_text",
    });
  });

  it("emits blocks in order: paragraph, grammar callout, sentence analysis, blockquote", () => {
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeSnapshot(),
    );

    const types = document.children.map((child) => child.type);
    expect(types).toContain("paragraph");
    expect(types).toContain("blockquote");
    expect(types).toContain("callout");
    expect(types).toContain("sentence_analysis");

    const firstParagraphIndex = types.indexOf("paragraph");
    const grammarCalloutIndex = types.findIndex(
      (t, i) =>
        t === "callout" &&
        i > firstParagraphIndex &&
        (document.children[i] as ReaderRecordPlateCalloutBlock).variant ===
          "grammar",
    );
    const analysisBlockIndex = types.findIndex(
      (t, i) => t === "sentence_analysis" && i > firstParagraphIndex,
    );
    const blockquoteIndex = types.indexOf("blockquote");

    expect(grammarCalloutIndex).toBeGreaterThan(firstParagraphIndex);
    expect(analysisBlockIndex).toBeGreaterThan(firstParagraphIndex);
    expect(blockquoteIndex).toBeGreaterThan(firstParagraphIndex);
  });

  it("deduplicates grammar callouts when the same grammar item appears on multiple leaves", () => {
    const unit = makeUnit();
    const sourceBlock = unit.children.find(
      (child) => child.type === "reader_source_block",
    ) as ReaderSourceBlockNodeDto | undefined;
    if (!sourceBlock || sourceBlock.type !== "reader_source_block") {
      throw new Error("Expected source block fixture");
    }
    const firstSegment = sourceBlock.children.find(
      (child) => "type" in child && child.type === "reader_anchor_segment",
    ) as ReaderAnchorSegmentFixture | undefined;
    if (!firstSegment) {
      throw new Error("Expected first segment fixture");
    }
    const firstLeaf = firstSegment.children[0];
    if (!("reader_grammar_note_marks" in firstLeaf)) {
      throw new Error("Expected segment text leaf fixture");
    }
    firstLeaf.reader_grammar_note_marks = [
      makeGrammarMark(),
      makeGrammarMark({
        mark_id: "grammar_mark_1_continuation",
        starts_here: false,
        ends_here: true,
      }),
    ];
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument({
      ...makeSnapshot(),
      value: [unit],
    });
    const grammarCallouts = document.children.filter(
      (child): child is ReaderRecordPlateCalloutBlock =>
        child.type === "callout" &&
        (child as ReaderRecordPlateCalloutBlock).variant === "grammar",
    );

    expect(grammarCallouts).toHaveLength(1);
    expect(grammarCallouts[0]?.id).toBe("callout:grammar:grammar_item_1");
  });

  it("projects enhancement progress to document-level progress", () => {
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
  });

  it("produces flat children array without unit or source block wrappers", () => {
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeSnapshot(),
    );

    for (const child of document.children) {
      expect(child.type).not.toBe("reader_record_unit");
      expect(child.type).not.toBe("reader_record_source_block");
      expect(child.type).not.toBe("reader_record_anchor_segment");
      expect(child.type).not.toBe("reader_record_unit_translation");
      expect(child.type).not.toBe("reader_sentence_analysis");
    }
  });

  it("projects ask supplements as callout blocks with supplement variant", () => {
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeSnapshot(undefined, [], [makeSupplement()]),
    );
    const supplementCallout = firstCallout("supplement", document);

    expect(supplementCallout.id).toBe("callout:supplement:supplement_1");
    expect(supplementCallout.variant).toBe("supplement");
    expect(supplementCallout.icon).toBe("💬");
    expect(supplementCallout.data).toMatchObject({
      anchorSegmentId: "seg_1",
      unitId: "unit_1",
      layerId: "ask_supplement:supplement_1",
      supplementId: "supplement_1",
      supplementType: "grammar_note",
      supplementTitle: "关于 Institutional memory 的补充",
      supplementContentMd:
        "Institutional memory 指组织内部积累的经验与习惯。",
      supplementCreatedAt: "2026-06-24T02:00:00Z",
      createdFromTurnRunId: "turn_run_1",
      lifecycleStatus: "persisted",
    });
    expect(supplementCallout.children[0]).toMatchObject({
      type: "p",
      children: [{ text: "Institutional memory 指组织内部积累的经验与习惯。" }],
    });
  });

  it("emits supplement callout after sentence analysis in block order", () => {
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeSnapshot(undefined, [], [makeSupplement()]),
    );

    const types = document.children.map((child) => child.type);
    const supplementIndex = types.findIndex(
      (t, i) =>
        t === "callout" &&
        (document.children[i] as ReaderRecordPlateCalloutBlock).variant ===
          "supplement",
    );
    const analysisIndex = types.findIndex((t) => t === "sentence_analysis");

    expect(supplementIndex).toBeGreaterThanOrEqual(0);
    expect(analysisIndex).toBeGreaterThanOrEqual(0);
    expect(supplementIndex).toBeGreaterThan(analysisIndex);
  });

  it("skips supplements whose anchor is null or unit-scoped", () => {
    const nullAnchor = makeSupplement({ anchor: null });
    const unitAnchor = makeSupplement({
      anchor: {
        anchor_type: "unit",
        base_id: "base_1",
        unit_id: "unit_1",
        text_hash: "unit_hash",
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      },
    });
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeSnapshot(undefined, [], [nullAnchor, unitAnchor]),
    );

    const supplementCallouts = document.children.filter(
      (child): child is ReaderRecordPlateCalloutBlock =>
        child.type === "callout" &&
        (child as ReaderRecordPlateCalloutBlock).variant === "supplement",
    );
    expect(supplementCallouts).toHaveLength(0);
  });

  it("skips supplements whose anchor text hash does not match selected_text", () => {
    const mismatched = makeSupplement({
      anchor: {
        anchor_type: "text_range",
        base_id: "base_1",
        unit_id: "unit_1",
        anchor_segment_id: "seg_1",
        sentence_id: "sent_1",
        segment_type: "sentence",
        offset_unit: READER_TEXT_RANGE_OFFSET_UNIT,
        start_offset: 0,
        end_offset: 5,
        selected_text: "wrong",
        text_hash: "deadbeef",
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      },
    });
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeSnapshot(undefined, [], [mismatched]),
    );

    const supplementCallouts = document.children.filter(
      (child): child is ReaderRecordPlateCalloutBlock =>
        child.type === "callout" &&
        (child as ReaderRecordPlateCalloutBlock).variant === "supplement",
    );
    expect(supplementCallouts).toHaveLength(0);
  });

  it("skips supplements whose anchor_segment_id is not in snapshot.anchor_segments", () => {
    const orphan = makeSupplement({
      anchor: {
        anchor_type: "text_range",
        base_id: "base_1",
        unit_id: "unit_1",
        anchor_segment_id: "seg_orphan",
        sentence_id: "sent_orphan",
        segment_type: "sentence",
        offset_unit: READER_TEXT_RANGE_OFFSET_UNIT,
        start_offset: 0,
        end_offset: 5,
        selected_text: "wrong",
        text_hash: computeUtf16FNV1a("wrong"),
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      },
    });
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeSnapshot(undefined, [], [orphan]),
    );

    const supplementCallouts = document.children.filter(
      (child): child is ReaderRecordPlateCalloutBlock =>
        child.type === "callout" &&
        (child as ReaderRecordPlateCalloutBlock).variant === "supplement",
    );
    expect(supplementCallouts).toHaveLength(0);
  });

  it("projects multiple supplements on the same segment in snapshot order", () => {
    const first = makeSupplement({
      supplement_id: "supplement_a",
      created_at: "2026-06-24T02:00:00Z",
    });
    const second = makeSupplement({
      supplement_id: "supplement_b",
      created_at: "2026-06-24T03:00:00Z",
      content: {
        title: "第二个补充",
        content_md: "第二条补充内容。",
        supplement_type: "grammar_note",
      },
    });
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeSnapshot(undefined, [], [first, second]),
    );

    const supplementCallouts = document.children.filter(
      (child): child is ReaderRecordPlateCalloutBlock =>
        child.type === "callout" &&
        (child as ReaderRecordPlateCalloutBlock).variant === "supplement",
    );
    expect(supplementCallouts.map((c) => c.data.supplementId)).toEqual([
      "supplement_a",
      "supplement_b",
    ]);
  });
});
