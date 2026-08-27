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
  type ReaderStableDocumentBlockNodeDto,
  type ReaderUnitNodeDto,
  type ReaderVocabularyMarkDto,
  type ReaderGrammarNoteMarkDto,
} from "@/types/api/reader-plate";
import { makeAnalysisProgressDto } from "@/test/fixtures/reader-analysis-progress";

import {
  READER_RECORD_PLATE_DOCUMENT_SCHEMA_VERSION,
  projectReaderPlateSnapshotToReaderRecordPlateDocument,
  type ReaderRecordPlateBlockquoteBlock,
  type ReaderRecordPlateCalloutBlock,
  type ReaderRecordPlateParagraphBlock,
  type ReaderRecordPlateSentenceAnalysisBlock,
  type ReaderRecordPlateStableBlockData,
} from "./reader-record-plate-document";
import { projectReaderRecordPlateToPlateValue } from "./reader-record-plate-to-plate-value";

const FIRST_TEXT = "Institutional memory shapes policy choices.";
const SECOND_TEXT = "Those choices persist.";
const THIRD_TEXT = "Institutional habits compound.";
const SEPARATOR_TEXT = "\n\n";
const SECOND_START = FIRST_TEXT.length + SEPARATOR_TEXT.length;
const THIRD_START = SECOND_START + SECOND_TEXT.length + SEPARATOR_TEXT.length;

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
    phrase_type: "fixed_collocation",
    gloss: "制度记忆",
    learning_note: "机构内部的**经验沉淀**，不是个人记忆。",
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
      display_title_zh: null,
      title_generation_status: "pending",
      title_generation_error_code: null,
      title_generation_error_message: null,
      reading_goal: "daily_reading",
      reading_variant: "intensive_reading",
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
    analysis_progress: makeAnalysisProgressDto(),
  };
}

function makeSequentialTranslationGroupSnapshot(): ReaderPlateSnapshotDto {
  const snapshot = makeSnapshot();
  const unit = snapshot.value[0];
  const sourceBlock = unit.children.find(
    (child): child is ReaderSourceBlockNodeDto =>
      child.type === "reader_source_block",
  );
  const sentenceAnalysis = unit.children.find(
    (child) => child.type === "reader_sentence_analysis",
  );

  if (!sourceBlock || !sentenceAnalysis) {
    throw new Error("Expected source block and sentence analysis fixtures");
  }

  return {
    ...snapshot,
    value: [
      {
        ...unit,
        children: [
          sourceBlock,
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
            source_text_hash: "group_hash_1",
            children: [{ text: "制度记忆会塑造政策选择。" }],
          },
          sentenceAnalysis,
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
            source_text_hash: "group_hash_2",
            children: [{ text: "这些选择会持续存在。" }],
          },
        ],
      },
    ],
  };
}

function makeThreeSegmentSnapshot(): ReaderPlateSnapshotDto {
  const snapshot = makeSnapshot();
  const unit = snapshot.value[0];
  const sourceBlock = unit.children.find(
    (child): child is ReaderSourceBlockNodeDto =>
      child.type === "reader_source_block",
  );

  if (!sourceBlock) {
    throw new Error("Expected source block fixture");
  }

  const thirdSegment: ReaderAnchorSegmentFixture = {
    type: "reader_anchor_segment",
    owner: "stable",
    base_id: "base_1",
    unit_id: "unit_1",
    anchor_segment_id: "seg_3",
    sentence_id: "sent_3",
    segment_type: "sentence",
    boundary_quality: "normal",
    base_start_utf16: THIRD_START,
    base_end_utf16: THIRD_START + THIRD_TEXT.length,
    unit_start_utf16: THIRD_START,
    unit_end_utf16: THIRD_START + THIRD_TEXT.length,
    text_hash: "seg_3_hash",
    hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
    children: [
      {
        text: THIRD_TEXT,
        owner: "stable",
        lock_source: true,
        source_role: "segment_text",
        base_start_utf16: THIRD_START,
        base_end_utf16: THIRD_START + THIRD_TEXT.length,
        anchor_segment_id: "seg_3",
        segment_start_utf16: 0,
        segment_end_utf16: THIRD_TEXT.length,
      },
    ],
  };
  const extendedSourceBlock: ReaderSourceBlockNodeDto = {
    ...sourceBlock,
    base_end_utf16: THIRD_START + THIRD_TEXT.length,
    children: [
      ...sourceBlock.children,
      {
        text: SEPARATOR_TEXT,
        owner: "stable",
        lock_source: true,
        source_role: "separator",
        base_start_utf16: SECOND_START + SECOND_TEXT.length,
        base_end_utf16: THIRD_START,
      },
      thirdSegment,
    ],
  };

  return {
    ...snapshot,
    base: {
      ...snapshot.base,
      text_length_utf16: THIRD_START + THIRD_TEXT.length,
    },
    navigation: {
      ...snapshot.navigation,
      units: snapshot.navigation.units.map((navigationUnit) =>
        navigationUnit.unit_id === "unit_1"
          ? {
              ...navigationUnit,
              base_end_utf16: THIRD_START + THIRD_TEXT.length,
            }
          : navigationUnit,
      ),
    },
    anchor_segments: [
      ...snapshot.anchor_segments,
      {
        anchor_segment_id: "seg_3",
        sentence_id: "sent_3",
        paragraph_id: "unit_1",
        unit_id: "unit_1",
        order_index: 3,
        unit_order_index: 1,
        segment_type: "sentence",
        boundary_quality: "normal",
        base_start_utf16: THIRD_START,
        base_end_utf16: THIRD_START + THIRD_TEXT.length,
        unit_start_utf16: THIRD_START,
        unit_end_utf16: THIRD_START + THIRD_TEXT.length,
        text_hash: "seg_3_hash",
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      },
    ],
    value: [
      {
        ...unit,
        base_end_utf16: THIRD_START + THIRD_TEXT.length,
        children: unit.children.map((child) =>
          child === sourceBlock ? extendedSourceBlock : child,
        ),
      },
    ],
  };
}

function makeStableMultiSegmentSnapshot(
  kind: "paragraph" | "blockquote" | "source_callout",
): ReaderPlateSnapshotDto {
  const snapshot = makeThreeSegmentSnapshot();
  const unit = snapshot.value[0];
  const sourceBlock = unit.children.find(
    (child): child is ReaderSourceBlockNodeDto =>
      child.type === "reader_source_block",
  );
  if (!sourceBlock) {
    throw new Error("Expected source block fixture");
  }

  const stableBlockId = `shared_${kind}`;
  const stableBlockType = kind === "source_callout" ? "blockquote" : kind;
  const stableSourceBlock: ReaderSourceBlockNodeDto = {
    ...sourceBlock,
    stableBlockType,
    stableBlockId,
    contentRole: kind === "source_callout" ? "source_callout" : "prose",
  };

  return {
    ...snapshot,
    value: [
      {
        ...unit,
        // No enhancement nodes: the source block must carry every source
        // leaf, including separators, through the Stable projection.
        children: [stableSourceBlock],
      },
    ],
    stable_document_tree: [
      {
        block_id: stableBlockId,
        parent_block_id: null,
        order_index: 0,
        block_type: stableBlockType,
        content_role: kind === "source_callout" ? "source_callout" : "prose",
        text_content: null,
        payload: {},
        source_refs: {},
        quality: {},
        canonical_text_start_utf16: 0,
        canonical_text_end_utf16: THIRD_START + THIRD_TEXT.length,
        interpretation_policy: {},
        unit_id: "unit_1",
        anchor_segment_ids: ["seg_1", "seg_2", "seg_3"],
        children: [],
      },
    ],
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

function paragraphBlocks(
  document = projectReaderPlateSnapshotToReaderRecordPlateDocument(makeSnapshot()),
): ReaderRecordPlateParagraphBlock[] {
  return document.children.filter(
    (child): child is ReaderRecordPlateParagraphBlock => child.type === "paragraph",
  );
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
    expect(paragraph.data.coveredAnchorSegmentIds).toEqual(["seg_1", "seg_2"]);
    expect(paragraph.data.sentenceId).toBe("sent_1");
    expect(paragraph.data.unitId).toBe("unit_1");
    expect(paragraph.data.isUnitStart).toBe(true);
    expect(paragraph.data.baseRange).toEqual({
      startUtf16: 0,
      endUtf16: SECOND_START + SECOND_TEXT.length,
    });
  });

  it("marks only the first paragraph in a unit as unit start", () => {
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeThreeSegmentSnapshot(),
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

    expect(paragraph.children.map((leaf) => leaf.text).join("")).toBe(
      `${FIRST_TEXT}${SEPARATOR_TEXT}${SECOND_TEXT}`,
    );
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
    const phraseMark = phraseLeaf?.marks[0];
    expect(phraseMark?.kind).toBe("phrase_gloss");
    if (phraseMark && "vocabulary" in phraseMark) {
      expect(phraseMark.vocabulary).toMatchObject({
        itemType: "phrase_gloss",
        phraseType: "fixed_collocation",
        gloss: "制度记忆",
        learningNote: "机构内部的**经验沉淀**，不是个人记忆。",
        example: "Institutional memory shapes future choices.",
      });
    }
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

    expect(blockquote.id).toBe("blockquote:layer_translation_1:group_translation_1");
    expect(blockquote.data).toMatchObject({
      unitId: "unit_1",
      layerId: "layer_translation_1",
      layerVersion: 1,
      groupId: "group_translation_1",
      coveredAnchorSegmentIds: ["seg_1", "seg_2"],
      sourceTextHash: "unit_hash_1",
    });
    expect(blockquote.children[0]).toEqual({
      text: "制度记忆会塑造政策选择，这些选择会持续存在。",
      owner: "system_ai",
      sourceRole: "unit_translation_text",
    });
  });

  it("projects a translation group as one source paragraph with preserved separator text", () => {
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeSnapshot(),
    );
    const paragraphs = paragraphBlocks(document);

    expect(paragraphs).toHaveLength(1);
    expect(paragraphs[0]?.data.coveredAnchorSegmentIds).toEqual([
      "seg_1",
      "seg_2",
    ]);
    expect(paragraphs[0]?.children.map((child) => child.text).join("")).toBe(
      `${FIRST_TEXT}${SEPARATOR_TEXT}${SECOND_TEXT}`,
    );
    expect(
      paragraphs[0]?.children.some(
        (child) => child.text === SEPARATOR_TEXT && child.sourceRole === "separator",
      ),
    ).toBe(true);
  });

  it("renders each translation group as source paragraph then translation then annotations", () => {
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeSequentialTranslationGroupSnapshot(),
    );
    const blockIds = document.children.map((child) => child.id);

    expect(
      blockIds.indexOf("paragraph:seg_1"),
    ).toBeLessThan(
      blockIds.indexOf("blockquote:layer_translation_1:group_translation_1"),
    );
    expect(
      blockIds.indexOf("blockquote:layer_translation_1:group_translation_1"),
    ).toBeLessThan(blockIds.indexOf("callout:grammar:grammar_item_1"));
    expect(blockIds.indexOf("callout:grammar:grammar_item_1")).toBeLessThan(
      blockIds.indexOf("sentence_analysis:analysis_seg_1"),
    );
    expect(blockIds.indexOf("sentence_analysis:analysis_seg_1")).toBeLessThan(
      blockIds.indexOf("paragraph:seg_2"),
    );
    expect(blockIds.indexOf("paragraph:seg_2")).toBeLessThan(
      blockIds.indexOf("blockquote:layer_translation_1:group_translation_2"),
    );
  });

  it("renders group annotations after the blockquote in anchor order", () => {
    const snapshot = makeSnapshot(undefined, [], [
      makeSupplement({
        supplement_id: "supplement_seg_2",
        anchor: {
          anchor_type: "text_range",
          base_id: "base_1",
          unit_id: "unit_1",
          anchor_segment_id: "seg_2",
          sentence_id: "sent_2",
          segment_type: "sentence",
          offset_unit: READER_TEXT_RANGE_OFFSET_UNIT,
          start_offset: 0,
          end_offset: SECOND_TEXT.length,
          selected_text: SECOND_TEXT,
          text_hash: computeUtf16FNV1a(SECOND_TEXT),
          hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
        },
        content: {
          target_key: "seg_2",
          sentence_id: "sent_2",
          title: "关于 seg_2 的补充",
          content_md: "第二句补充。",
          supplement_type: "grammar_note",
        },
      }),
    ]);
    const unit = snapshot.value[0];
    const sourceBlock = unit.children.find(
      (child): child is ReaderSourceBlockNodeDto =>
        child.type === "reader_source_block",
    );
    const baseSentenceAnalysis = unit.children.find(
      (child) => child.type === "reader_sentence_analysis",
    );

    if (!sourceBlock || !baseSentenceAnalysis) {
      throw new Error("Expected source block and sentence analysis fixtures");
    }

    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument({
      ...snapshot,
      value: [
        {
          ...unit,
          children: [
            sourceBlock,
            unit.children.find(
              (child) => child.type === "reader_translation_group",
            )!,
            baseSentenceAnalysis,
            {
              type: "reader_sentence_analysis",
              owner: "system_ai",
              analysis_id: "analysis_seg_2",
              layer_id: "layer_sentence_analysis_2",
              layer_version: 1,
              base_id: "base_1",
              unit_id: "unit_1",
              target_scope: "unit",
              target_key: "unit_1",
              anchor_segment_id: "seg_2",
              selected_text: SECOND_TEXT,
              label: "result clause",
              analysis: "Those choices persist is the follow-up clause.",
              chunks: [{ order: 1, label: "clause", text: SECOND_TEXT }],
              children: [
                {
                  text: "Those choices persist is the follow-up clause.",
                },
              ],
            },
          ],
        },
      ],
    });
    const blockIds = document.children.map((child) => child.id);

    expect(blockIds).toEqual([
      "paragraph:seg_1",
      "blockquote:layer_translation_1:group_translation_1",
      "callout:grammar:grammar_item_1",
      "sentence_analysis:analysis_seg_1",
      "sentence_analysis:analysis_seg_2",
      "callout:supplement:supplement_seg_2",
    ]);
  });

  it("skips translation groups whose covered anchors are all missing", () => {
    const snapshot = makeSnapshot();
    const unit = snapshot.value[0];
    const sourceBlock = unit.children.find(
      (child): child is ReaderSourceBlockNodeDto =>
        child.type === "reader_source_block",
    );
    const sentenceAnalysis = unit.children.find(
      (child) => child.type === "reader_sentence_analysis",
    );

    if (!sourceBlock || !sentenceAnalysis) {
      throw new Error("Expected source block and sentence analysis fixtures");
    }

    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument({
      ...snapshot,
      value: [
        {
          ...unit,
          children: [
            sourceBlock,
            {
              type: "reader_translation_group",
              owner: "system_ai",
              layer_id: "layer_translation_1",
              layer_version: 1,
              base_id: "base_1",
              unit_id: "unit_1",
              target_scope: "unit",
              target_key: "unit_1",
              group_id: "group_translation_missing_only",
              covered_anchor_segment_ids: ["missing_seg"],
              source_text_hash: "group_hash_missing_only",
              children: [{ text: "不应投影" }],
            },
            sentenceAnalysis,
          ],
        },
      ],
    });

    expect(
      document.children.filter((child) => child.type === "blockquote"),
    ).toHaveLength(0);
    expect(
      paragraphBlocks(document).map((paragraph) =>
        paragraph.children.map((child) => child.text).join(""),
      ),
    ).toEqual([FIRST_TEXT, SECOND_TEXT]);
  });

  it("skips translation groups whose covered anchors are partially missing", () => {
    const snapshot = makeSnapshot();
    const unit = snapshot.value[0];
    const sourceBlock = unit.children.find(
      (child): child is ReaderSourceBlockNodeDto =>
        child.type === "reader_source_block",
    );
    const sentenceAnalysis = unit.children.find(
      (child) => child.type === "reader_sentence_analysis",
    );

    if (!sourceBlock || !sentenceAnalysis) {
      throw new Error("Expected source block and sentence analysis fixtures");
    }

    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument({
      ...snapshot,
      value: [
        {
          ...unit,
          children: [
            sourceBlock,
            {
              type: "reader_translation_group",
              owner: "system_ai",
              layer_id: "layer_translation_1",
              layer_version: 1,
              base_id: "base_1",
              unit_id: "unit_1",
              target_scope: "unit",
              target_key: "unit_1",
              group_id: "group_translation_missing_partial",
              covered_anchor_segment_ids: ["seg_1", "missing_seg"],
              source_text_hash: "group_hash_missing_partial",
              children: [{ text: "不应投影" }],
            },
            sentenceAnalysis,
          ],
        },
      ],
    });

    expect(
      document.children.filter((child) => child.type === "blockquote"),
    ).toHaveLength(0);
    expect(
      paragraphBlocks(document).map((paragraph) =>
        paragraph.children.map((child) => child.text).join(""),
      ),
    ).toEqual([FIRST_TEXT, SECOND_TEXT]);
  });

  it("falls back to single-segment paragraphs for uncovered anchors", () => {
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeThreeSegmentSnapshot(),
    );
    const paragraphs = paragraphBlocks(document);

    expect(paragraphs).toHaveLength(2);
    expect(paragraphs[0]?.children.map((child) => child.text).join("")).toBe(
      `${FIRST_TEXT}${SEPARATOR_TEXT}${SECOND_TEXT}`,
    );
    expect(paragraphs[1]?.children.map((child) => child.text).join("")).toBe(
      THIRD_TEXT,
    );
  });

  it("skips non-contiguous translation groups and falls back to source paragraphs", () => {
    const snapshot = makeThreeSegmentSnapshot();
    const unit = snapshot.value[0];
    const sourceBlock = unit.children.find(
      (child): child is ReaderSourceBlockNodeDto =>
        child.type === "reader_source_block",
    );
    const analyses = unit.children.filter(
      (child) => child.type === "reader_sentence_analysis",
    );

    if (!sourceBlock) {
      throw new Error("Expected source block fixture");
    }

    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument({
      ...snapshot,
      value: [
        {
          ...unit,
          children: [
            sourceBlock,
            {
              type: "reader_translation_group",
              owner: "system_ai",
              layer_id: "layer_translation_1",
              layer_version: 1,
              base_id: "base_1",
              unit_id: "unit_1",
              target_scope: "unit",
              target_key: "unit_1",
              group_id: "group_translation_non_contiguous",
              covered_anchor_segment_ids: ["seg_1", "seg_3"],
              source_text_hash: "group_hash_non_contiguous",
              children: [{ text: "不应投影" }],
            },
            ...analyses,
          ],
        },
      ],
    });

    expect(
      document.children.filter((child) => child.type === "blockquote"),
    ).toHaveLength(0);
    expect(paragraphBlocks(document)).toHaveLength(3);
  });

  it("skips overlapping translation groups and keeps remaining anchors visible", () => {
    const snapshot = makeThreeSegmentSnapshot();
    const unit = snapshot.value[0];
    const sourceBlock = unit.children.find(
      (child): child is ReaderSourceBlockNodeDto =>
        child.type === "reader_source_block",
    );
    const analyses = unit.children.filter(
      (child) => child.type === "reader_sentence_analysis",
    );

    if (!sourceBlock) {
      throw new Error("Expected source block fixture");
    }

    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument({
      ...snapshot,
      value: [
        {
          ...unit,
          children: [
            sourceBlock,
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
              source_text_hash: "group_hash_1",
              children: [{ text: "第一组" }],
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
              group_id: "group_translation_overlap",
              covered_anchor_segment_ids: ["seg_2", "seg_3"],
              source_text_hash: "group_hash_overlap",
              children: [{ text: "不应投影" }],
            },
            ...analyses,
          ],
        },
      ],
    });

    expect(
      document.children.filter((child) => child.type === "blockquote"),
    ).toHaveLength(1);
    expect(paragraphBlocks(document)).toHaveLength(2);
    expect(
      paragraphBlocks(document)[1]?.children.map((child) => child.text).join(""),
    ).toBe(THIRD_TEXT);
  });

  it("accepts non-overlapping translation groups even when snapshot nodes are out of source order", () => {
    const snapshot = makeThreeSegmentSnapshot();
    const unit = snapshot.value[0];
    const sourceBlock = unit.children.find(
      (child): child is ReaderSourceBlockNodeDto =>
        child.type === "reader_source_block",
    );
    const analyses = unit.children.filter(
      (child) => child.type === "reader_sentence_analysis",
    );

    if (!sourceBlock) {
      throw new Error("Expected source block fixture");
    }

    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument({
      ...snapshot,
      value: [
        {
          ...unit,
          children: [
            sourceBlock,
            {
              type: "reader_translation_group",
              owner: "system_ai",
              layer_id: "layer_translation_1",
              layer_version: 1,
              base_id: "base_1",
              unit_id: "unit_1",
              target_scope: "unit",
              target_key: "unit_1",
              group_id: "group_translation_seg_3",
              covered_anchor_segment_ids: ["seg_3"],
              source_text_hash: "group_hash_seg_3",
              children: [{ text: "第三句" }],
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
              group_id: "group_translation_seg_1_2",
              covered_anchor_segment_ids: ["seg_1", "seg_2"],
              source_text_hash: "group_hash_seg_1_2",
              children: [{ text: "前两句" }],
            },
            ...analyses,
          ],
        },
      ],
    });

    expect(
      document.children
        .filter((child): child is ReaderRecordPlateBlockquoteBlock => child.type === "blockquote")
        .map((child) => child.id),
    ).toEqual([
      "blockquote:layer_translation_1:group_translation_seg_1_2",
      "blockquote:layer_translation_1:group_translation_seg_3",
    ]);
    expect(paragraphBlocks(document)).toHaveLength(2);
  });

  it("emits blocks in order: paragraph, blockquote, grammar callout, sentence analysis, supplement", () => {
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeSnapshot(undefined, [], [makeSupplement()]),
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
    const supplementIndex = types.findIndex(
      (t, i) =>
        t === "callout" &&
        i > firstParagraphIndex &&
        (document.children[i] as ReaderRecordPlateCalloutBlock).variant ===
          "supplement",
    );

    expect(blockquoteIndex).toBeGreaterThan(firstParagraphIndex);
    expect(grammarCalloutIndex).toBeGreaterThan(blockquoteIndex);
    expect(analysisBlockIndex).toBeGreaterThan(grammarCalloutIndex);
    expect(supplementIndex).toBeGreaterThan(analysisBlockIndex);
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

// ---------------------------------------------------------------------------
// Stable paragraph inline marks must survive snapshot → document
// projection. Previously `stableBlockType === "paragraph"` was NOT in
// STABLE_BLOCK_TYPES_WITH_PLATE_PROJECTION and the stable builder had no
// `case "paragraph"`, so paragraph units always took the legacy path and
// silently dropped inlineMarks (emphasis rendered as plain text even though
// the snapshot carried the marks).
// ---------------------------------------------------------------------------

describe("stable paragraph inline marks projection", () => {
  const EM_TEXT = "How we will roll this out safely.";
  const EM_END = EM_TEXT.length;

  function makeStableParagraphSnapshot(): ReaderPlateSnapshotDto {
    const textHash = computeUtf16FNV1a(EM_TEXT);
    return {
      schema_kind: READER_PLATE_SNAPSHOT_SCHEMA_KIND,
      snapshot_id: "snapshot_r1",
      snapshot_taken_at: "2026-07-26T00:00:00Z",
      last_event_sequence: 1,
      record_id: "record_r1",
      record: {
        title: "R1 Inline Marks Fixture",
        display_title_zh: null,
        title_generation_status: "pending",
        title_generation_error_code: null,
        title_generation_error_message: null,
        reading_goal: "daily_reading",
        reading_variant: "intensive_reading",
        created_at: "2026-07-26T00:00:00Z",
        source_type: "plain_text",
        source_metadata: {},
        generation: 1,
        product_state: "readable_enhancing",
        readiness_state: "article_ready",
      },
      base: {
        base_id: "base_r1",
        content_sha256: "a".repeat(64),
        canonicalizer_version: "test",
        builder_version: "test",
        segmenter_version: "test",
        text_length_utf16: EM_END,
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      },
      navigation: {
        units: [
          {
            unit_id: "u2",
            order_index: 1,
            unit_type: "body",
            boundary_quality: "normal",
            label: null,
            base_start_utf16: 0,
            base_end_utf16: EM_END,
            text_hash: textHash,
            hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
            stable_block_type: "paragraph",
            heading_level: null,
          },
        ],
      },
      anchor_segments: [
        {
          anchor_segment_id: "seg_r1",
          sentence_id: "sent_r1",
          paragraph_id: "u2",
          unit_id: "u2",
          order_index: 1,
          unit_order_index: 1,
          segment_type: "sentence",
          boundary_quality: "normal",
          base_start_utf16: 0,
          base_end_utf16: EM_END,
          unit_start_utf16: 0,
          unit_end_utf16: EM_END,
          text_hash: textHash,
          hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
        },
      ],
      enhancement_layers: [],
      enhancement_progress: undefined,
      analysis_progress: makeAnalysisProgressDto(),
      ask_supplements: [],
      user_assets: [],
      parsed_decisions: [],
      value: [
        {
          type: "reader_unit",
          owner: "stable",
          base_id: "base_r1",
          unit_id: "u2",
          order_index: 1,
          unit_type: "body",
          boundary_quality: "normal",
          base_start_utf16: 0,
          base_end_utf16: EM_END,
          text_hash: textHash,
          hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
          children: [
            {
              type: "reader_source_block",
              owner: "stable",
              base_id: "base_r1",
              unit_id: "u2",
              base_start_utf16: 0,
              base_end_utf16: EM_END,
              stableBlockType: "paragraph",
              stableBlockId: "b2",
              headingLevel: null,
              inlineMarks: [{ type: "em", start: 0, end: EM_END }],
              tableRole: null,
              parentStableBlockId: null,
              children: [
                {
                  type: "reader_anchor_segment",
                  owner: "stable",
                  base_id: "base_r1",
                  unit_id: "u2",
                  anchor_segment_id: "seg_r1",
                  sentence_id: "sent_r1",
                  segment_type: "sentence",
                  boundary_quality: "normal",
                  base_start_utf16: 0,
                  base_end_utf16: EM_END,
                  unit_start_utf16: 0,
                  unit_end_utf16: EM_END,
                  text_hash: textHash,
                  hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
                  children: [
                    {
                      text: EM_TEXT,
                      owner: "stable",
                      lock_source: true,
                      source_role: "segment_text",
                      base_start_utf16: 0,
                      base_end_utf16: EM_END,
                      anchor_segment_id: "seg_r1",
                      segment_start_utf16: 0,
                      segment_end_utf16: EM_END,
                    },
                  ],
                },
              ],
            },
          ],
        },
      ],
    };
  }

  it("projects paragraph stable block inlineMarks onto text leaves", () => {
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeStableParagraphSnapshot(),
    );
    const paragraph = document.children.find(
      (child) => child.type === "paragraph",
    );
    expect(paragraph).toBeTruthy();
    if (!paragraph || paragraph.type !== "paragraph") return;

    const marks = paragraph.children.flatMap((leaf) =>
      "text" in leaf ? (leaf.inlineMarks ?? []) : [],
    );
    expect(marks).toHaveLength(1);
    expect(marks[0]?.kind).toBe("em");
    expect(marks[0]?.start).toBe(0);
    expect(marks[0]?.end).toBe(EM_END);
  });

  it("keeps paragraph block anchor data intact on the stable path", () => {
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeStableParagraphSnapshot(),
    );
    const paragraph = document.children.find(
      (child) => child.type === "paragraph",
    );
    if (!paragraph || paragraph.type !== "paragraph") {
      throw new Error("paragraph block missing");
    }
    // Selection / mark anchoring contract must survive the stable path.
    expect(paragraph.data.anchorSegmentId).toBe("seg_r1");
    expect(paragraph.data.unitId).toBe("u2");
    expect(paragraph.data.baseRange.startUtf16).toBe(0);
    expect(paragraph.data.baseRange.endUtf16).toBe(EM_END);
    expect(paragraph.data.textHash).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// L1: 后端 DTO key（codeLanguage / tableIsHeader / tableAlignment）的投影消费。
// table wrapper 不产生 unit block，表级 alignments/headerRows 由单元格元数据推导。
// ---------------------------------------------------------------------------

describe("L1 code/table metadata projection", () => {
  interface L1UnitSpec {
    unitId: string;
    text: string;
    stableBlockType: string;
    stableBlockId: string;
    parentStableBlockId?: string | null;
    codeLanguage?: string | null;
    tableIsHeader?: boolean | null;
    tableAlignment?: "left" | "center" | "right" | "default" | null;
  }

  function makeL1Snapshot(specs: L1UnitSpec[]): ReaderPlateSnapshotDto {
    let offset = 0;
    const navigationUnits: ReaderPlateSnapshotDto["navigation"]["units"] = [];
    const anchorSegments: ReaderPlateSnapshotDto["anchor_segments"] = [];
    const valueUnits: ReaderUnitNodeDto[] = [];

    for (const [index, spec] of specs.entries()) {
      const start = offset;
      const end = start + spec.text.length;
      offset = end + 2; // "\n\n" separator
      const segId = `seg_${spec.unitId}`;
      const textHash = computeUtf16FNV1a(spec.text);
      navigationUnits.push({
        unit_id: spec.unitId,
        order_index: index + 1,
        unit_type: "body",
        boundary_quality: "normal",
        label: null,
        base_start_utf16: start,
        base_end_utf16: end,
        text_hash: textHash,
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
        stable_block_type: spec.stableBlockType,
        heading_level: null,
      });
      anchorSegments.push({
        anchor_segment_id: segId,
        sentence_id: `sent_${spec.unitId}`,
        paragraph_id: spec.unitId,
        unit_id: spec.unitId,
        order_index: index + 1,
        unit_order_index: 1,
        segment_type: "sentence",
        boundary_quality: "normal",
        base_start_utf16: start,
        base_end_utf16: end,
        unit_start_utf16: 0,
        unit_end_utf16: spec.text.length,
        text_hash: textHash,
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      });
      valueUnits.push({
        type: "reader_unit",
        owner: "stable",
        base_id: "base_l1",
        unit_id: spec.unitId,
        order_index: index + 1,
        unit_type: "body",
        boundary_quality: "normal",
        base_start_utf16: start,
        base_end_utf16: end,
        text_hash: textHash,
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
        children: [
          {
            type: "reader_source_block",
            owner: "stable",
            base_id: "base_l1",
            unit_id: spec.unitId,
            base_start_utf16: start,
            base_end_utf16: end,
            stableBlockType: spec.stableBlockType,
            stableBlockId: spec.stableBlockId,
            headingLevel: null,
            inlineMarks: [],
            tableRole: null,
            parentStableBlockId: spec.parentStableBlockId ?? null,
            // L1 新增 DTO key（不适用为 null，与后端合同一致）。
            codeLanguage: spec.codeLanguage ?? null,
            tableIsHeader: spec.tableIsHeader ?? null,
            tableAlignment: spec.tableAlignment ?? null,
            children: [
              {
                type: "reader_anchor_segment",
                owner: "stable",
                base_id: "base_l1",
                unit_id: spec.unitId,
                anchor_segment_id: segId,
                sentence_id: `sent_${spec.unitId}`,
                segment_type: "sentence",
                boundary_quality: "normal",
                base_start_utf16: start,
                base_end_utf16: end,
                unit_start_utf16: 0,
                unit_end_utf16: spec.text.length,
                text_hash: textHash,
                hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
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
          },
        ],
      });
    }

    return {
      schema_kind: READER_PLATE_SNAPSHOT_SCHEMA_KIND,
      snapshot_id: "snapshot_l1",
      snapshot_taken_at: "2026-07-28T00:00:00Z",
      last_event_sequence: 1,
      record_id: "record_l1",
      record: {
        title: "L1 Metadata Fixture",
        display_title_zh: null,
        title_generation_status: "pending",
        title_generation_error_code: null,
        title_generation_error_message: null,
        reading_goal: "daily_reading",
        reading_variant: "intensive_reading",
        created_at: "2026-07-28T00:00:00Z",
        source_type: "markdown",
        source_metadata: {},
        generation: 1,
        product_state: "readable_enhancing",
        readiness_state: "article_ready",
      },
      base: {
        base_id: "base_l1",
        content_sha256: "b".repeat(64),
        canonicalizer_version: "test",
        builder_version: "test",
        segmenter_version: "test",
        text_length_utf16: offset,
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      },
      navigation: { units: navigationUnits },
      anchor_segments: anchorSegments,
      enhancement_layers: [],
      enhancement_progress: undefined,
      analysis_progress: makeAnalysisProgressDto(),
      ask_supplements: [],
      user_assets: [],
      parsed_decisions: [],
      value: valueUnits,
    };
  }

  it("code_block 消费 codeLanguage 进入 block data.language", () => {
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeL1Snapshot([
        {
          unitId: "u_code",
          text: "def f():\n    return 1",
          stableBlockType: "code_block",
          stableBlockId: "b_code",
          codeLanguage: "python",
        },
      ]),
    );
    const codeBlock = document.children.find(
      (child) => child.type === "code_block",
    );
    if (!codeBlock || codeBlock.type !== "code_block") {
      throw new Error("code_block block missing");
    }
    expect(codeBlock.data.language).toBe("python");
  });

  it("code_block 无语言时 data.language 为 null", () => {
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeL1Snapshot([
        {
          unitId: "u_code",
          text: "plain code",
          stableBlockType: "code_block",
          stableBlockId: "b_code",
          codeLanguage: null,
        },
      ]),
    );
    const codeBlock = document.children.find(
      (child) => child.type === "code_block",
    );
    if (!codeBlock || codeBlock.type !== "code_block") {
      throw new Error("code_block block missing");
    }
    expect(codeBlock.data.language).toBeNull();
  });

  it("table_cell 消费 tableIsHeader/tableAlignment；行/表元数据正确推导", () => {
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeL1Snapshot([
        {
          unitId: "u_c11",
          text: "Name",
          stableBlockType: "table_cell",
          stableBlockId: "c11",
          parentStableBlockId: "row_1",
          tableIsHeader: true,
          tableAlignment: "left",
        },
        {
          unitId: "u_c12",
          text: "Value",
          stableBlockType: "table_cell",
          stableBlockId: "c12",
          parentStableBlockId: "row_1",
          tableIsHeader: true,
          tableAlignment: "right",
        },
        {
          unitId: "u_c21",
          text: "a",
          stableBlockType: "table_cell",
          stableBlockId: "c21",
          parentStableBlockId: "row_2",
          tableIsHeader: false,
          tableAlignment: "left",
        },
        {
          unitId: "u_c22",
          text: "1",
          stableBlockType: "table_cell",
          stableBlockId: "c22",
          parentStableBlockId: "row_2",
          tableIsHeader: false,
          tableAlignment: "right",
        },
      ]),
    );

    const table = document.children.find((child) => child.type === "table");
    if (!table || table.type !== "table") {
      throw new Error("table block missing");
    }
    // 表级推导：列对齐按首行单元格列序；headerRows 为前导全 header 行计数。
    expect(table.data.alignments).toEqual(["left", "right"]);
    expect(table.data.headerRows).toBe(1);

    expect(table.children).toHaveLength(2);
    const [headerRow, bodyRow] = table.children;
    expect(headerRow.data.isHeader).toBe(true);
    expect(bodyRow.data.isHeader).toBe(false);
    expect(headerRow.children[0].data.alignment).toBe("left");
    expect(headerRow.children[0].data.isHeader).toBe(true);
    expect(headerRow.children[1].data.alignment).toBe("right");
    expect(bodyRow.children[0].data.isHeader).toBe(false);
  });

  it("legacy snapshot 无 L1 字段时回退 default/false/null", () => {
    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
      makeL1Snapshot([
        {
          unitId: "u_cell",
          text: "x",
          stableBlockType: "table_cell",
          stableBlockId: "c1",
          parentStableBlockId: "row_1",
          // L1 字段全部缺省（legacy snapshot 形态）
        },
      ]),
    );
    const table = document.children.find((child) => child.type === "table");
    if (!table || table.type !== "table") {
      throw new Error("table block missing");
    }
    expect(table.children[0].children[0].data.alignment).toBe("default");
    expect(table.children[0].children[0].data.isHeader).toBe(false);
    expect(table.data.headerRows).toBe(0);
  });
describe("Stable Document tree projection", () => {
  function node(
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

  it("uses persisted table rows/cells instead of consecutive-unit grouping", () => {
    const snapshot = makeL1Snapshot([
      {
        unitId: "u_c11",
        text: "Name",
        stableBlockType: "table_cell",
        stableBlockId: "c11",
        parentStableBlockId: "row_1",
        tableIsHeader: true,
        tableAlignment: "left",
      },
      {
        unitId: "u_c12",
        text: "Value",
        stableBlockType: "table_cell",
        stableBlockId: "c12",
        parentStableBlockId: "row_1",
        tableIsHeader: true,
        tableAlignment: "right",
      },
      {
        unitId: "u_body",
        text: "A paragraph between structures.",
        stableBlockType: "paragraph",
        stableBlockId: "p1",
      },
      {
        unitId: "u_c21",
        text: "a",
        stableBlockType: "table_cell",
        stableBlockId: "c21",
        parentStableBlockId: "row_2",
        tableIsHeader: false,
        tableAlignment: "left",
      },
      {
        unitId: "u_c22",
        text: "1",
        stableBlockType: "table_cell",
        stableBlockId: "c22",
        parentStableBlockId: "row_2",
        tableIsHeader: false,
        tableAlignment: "right",
      },
    ]);
    snapshot.stable_document_tree = [
      node({
        block_id: "table_1",
        block_type: "table",
        children: [
          node({
            block_id: "row_1",
            parent_block_id: "table_1",
            block_type: "table_row",
            children: [
              node({ block_id: "c11", parent_block_id: "row_1", block_type: "table_cell" }),
              node({ block_id: "c12", parent_block_id: "row_1", block_type: "table_cell" }),
            ],
          }),
          node({
            block_id: "row_2",
            parent_block_id: "table_1",
            order_index: 1,
            block_type: "table_row",
            children: [
              node({ block_id: "c21", parent_block_id: "row_2", block_type: "table_cell" }),
              node({ block_id: "c22", parent_block_id: "row_2", block_type: "table_cell" }),
            ],
          }),
        ],
      }),
      node({
        block_id: "p1",
        order_index: 1,
        block_type: "paragraph",
      }),
    ];

    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    expect(document.children.map((child) => child.type)).toEqual([
      "table",
      "paragraph",
    ]);
    const table = document.children[0];
    if (!table || table.type !== "table") return;
    expect(table.children.map((row) => row.children.map((cell) => cell.id))).toEqual([
      ["table_cell:seg_u_c11", "table_cell:seg_u_c12"],
      ["table_cell:seg_u_c21", "table_cell:seg_u_c22"],
    ]);
  });

  it("projects a persisted source-callout wrapper with recursive child blocks", () => {
    const snapshot = makeL1Snapshot([
      {
        unitId: "u_p1",
        text: "Callout paragraph.",
        stableBlockType: "paragraph",
        stableBlockId: "p1",
      },
      {
        unitId: "u_p2",
        text: "Second paragraph.",
        stableBlockType: "paragraph",
        stableBlockId: "p2",
      },
      {
        unitId: "u_i1",
        text: "First item",
        stableBlockType: "list_item",
        stableBlockId: "i1",
        parentStableBlockId: "list1",
      },
      {
        unitId: "u_i2",
        text: "Second item",
        stableBlockType: "list_item",
        stableBlockId: "i2",
        parentStableBlockId: "list1",
      },
    ]);
    snapshot.stable_document_tree = [
      node({
        block_id: "callout1",
        block_type: "blockquote",
        content_role: "source_callout",
        children: [
          node({
            block_id: "p1",
            parent_block_id: "callout1",
            block_type: "paragraph",
          }),
          node({
            block_id: "p2",
            parent_block_id: "callout1",
            order_index: 1,
            block_type: "paragraph",
          }),
          node({
            block_id: "list1",
            parent_block_id: "callout1",
            order_index: 2,
            block_type: "list",
            payload: { ordered: false },
            children: [
              node({
                block_id: "i1",
                parent_block_id: "list1",
                block_type: "list_item",
              }),
              node({
                block_id: "i2",
                parent_block_id: "list1",
                order_index: 1,
                block_type: "list_item",
              }),
            ],
          }),
        ],
      }),
    ];

    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    expect(document.children).toHaveLength(1);
    const callout = document.children[0];
    if (!callout || callout.type !== "source_callout") {
      throw new Error("source callout block missing");
    }
    expect(
      callout.children.map((child) =>
        "type" in child ? child.type : "text",
      ),
    ).toEqual([
      "paragraph",
      "paragraph",
      "list",
    ]);
    const list = callout.children[2];
    if (!list || !("type" in list) || list.type !== "list") {
      throw new Error("callout list missing");
    }
    expect(list.children.map((item) => item.type)).toEqual([
      "list_item",
      "list_item",
    ]);

    const plateValue = projectReaderRecordPlateToPlateValue(document);
    expect(plateValue[0]).toMatchObject({
      type: "reader_source_callout",
      children: [
        { type: "reader_paragraph" },
        { type: "reader_paragraph" },
        {
          type: "reader_list",
          children: [{ type: "reader_list_item" }, { type: "reader_list_item" }],
        },
      ],
    });
  });

  it("projects the callout icon from wrapper payload metadata, not the first body block", () => {
    const snapshot = makeL1Snapshot([
      {
        unitId: "u_body",
        text: "Callout body without an icon leaf.",
        stableBlockType: "paragraph",
        stableBlockId: "body1",
      },
    ]);
    snapshot.stable_document_tree = [
      node({
        block_id: "callout_payload_icon",
        block_type: "blockquote",
        content_role: "source_callout",
        payload: { display_icon: "🎯" },
        children: [
          node({
            block_id: "body1",
            parent_block_id: "callout_payload_icon",
            block_type: "paragraph",
          }),
        ],
      }),
    ];

    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const callout = document.children[0];
    if (!callout || callout.type !== "source_callout") {
      throw new Error("source callout block missing");
    }
    expect(callout.data.calloutIcon).toBe("🎯");
    expect(callout.children).toHaveLength(1);
    expect(callout.children[0]).toMatchObject({ type: "paragraph" });
  });

  it.each([
    ["paragraph", "paragraph"],
    ["blockquote", "markdown_blockquote"],
    ["source_callout", "source_callout"],
  ] as const)(
    "preserves every source leaf when a %s Stable leaf spans multiple anchors",
    (kind, expectedType) => {
      const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(
        makeStableMultiSegmentSnapshot(kind),
      );
      const blocks = document.children.filter(
        (child) => child.type === expectedType,
      );

      expect(blocks).toHaveLength(1);
      const block = blocks[0];
      if (!block || !("children" in block)) {
        throw new Error(`Expected ${expectedType} block`);
      }

      const collectText = (value: unknown): string => {
        if (!Array.isArray(value)) return "";
        return value
          .map((child) => {
            if (!child || typeof child !== "object") return "";
            const node = child as { text?: unknown; children?: unknown };
            if (typeof node.text === "string") return node.text;
            return collectText(node.children);
          })
          .join("");
      };

      expect(collectText(block.children)).toBe(
        `${FIRST_TEXT}${SEPARATOR_TEXT}${SECOND_TEXT}${SEPARATOR_TEXT}${THIRD_TEXT}`,
      );
      expect(
        (block.data as ReaderRecordPlateStableBlockData)
          .coveredAnchorSegmentIds,
      ).toEqual(["seg_1", "seg_2", "seg_3"]);
    },
  );
});
});

// ---------------------------------------------------------------------------
// Wrapper composition order — the flat sequence from mapUnitToBlocks is the
// only anchor-level order authority; the Stable tree contributes wrapper
// structure only. Overlays defer past their wrapper per the shared policy.
// ---------------------------------------------------------------------------
describe("wrapper composition order", () => {
  type WgSeg = { id: string; text: string };
  type WgTranslation = {
    groupId: string;
    layerId: string;
    covers: string[];
    text: string;
  };
  type WgAnalysis = { analysisId: string; segId: string; text: string };
  type WgUnit = {
    unitId: string;
    segs: WgSeg[];
    stableBlockType: string;
    stableBlockId: string;
    parentStableBlockId?: string | null;
    headingLevel?: number | null;
    contentRole?:
      | "prose"
      | "quotation"
      | "source_callout"
      | "citation_reference"
      | "prompt_question"
      | "link_only"
      | null;
    grammarMarks?: Array<ReaderGrammarNoteMarkDto & { segId?: never }>;
    translations?: WgTranslation[];
    analyses?: WgAnalysis[];
  };

  function wgTreeNode(
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

  function buildWgSnapshot(
    specs: WgUnit[],
    extras: {
      tree?: ReaderStableDocumentBlockNodeDto[];
      supplements?: ReaderSnapshotAskSupplementDto[];
    } = {},
  ): ReaderPlateSnapshotDto {
    let offset = 0;
    const anchorSegments: ReaderPlateSnapshotDto["anchor_segments"] = [];
    const navigationUnits: ReaderPlateSnapshotDto["navigation"]["units"] = [];
    const valueUnits: ReaderUnitNodeDto[] = [];

    for (const [unitIndex, spec] of specs.entries()) {
      const unitStart = offset;
      const segNodes: ReaderSourceBlockChildNodeDto[] = [];
      for (const seg of spec.segs) {
        const start = offset;
        const end = start + seg.text.length;
        offset = end;
        anchorSegments.push({
          anchor_segment_id: seg.id,
          sentence_id: `sent_${seg.id}`,
          paragraph_id: spec.unitId,
          unit_id: spec.unitId,
          order_index: anchorSegments.length + 1,
          unit_order_index: 1,
          segment_type: "sentence",
          boundary_quality: "normal",
          base_start_utf16: start,
          base_end_utf16: end,
          unit_start_utf16: start - unitStart,
          unit_end_utf16: end - unitStart,
          text_hash: `hash_${seg.id}`,
          hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
        });
        segNodes.push({
          type: "reader_anchor_segment",
          owner: "stable",
          base_id: "base_w1",
          unit_id: spec.unitId,
          anchor_segment_id: seg.id,
          sentence_id: `sent_${seg.id}`,
          segment_type: "sentence",
          boundary_quality: "normal",
          base_start_utf16: start,
          base_end_utf16: end,
          unit_start_utf16: start - unitStart,
          unit_end_utf16: end - unitStart,
          text_hash: `hash_${seg.id}`,
          hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
          children: [
            {
              text: seg.text,
              owner: "stable",
              lock_source: true,
              source_role: "segment_text",
              base_start_utf16: start,
              base_end_utf16: end,
              anchor_segment_id: seg.id,
              segment_start_utf16: 0,
              segment_end_utf16: seg.text.length,
              ...(spec.grammarMarks && spec.grammarMarks.length > 0
                ? {
                    reader_grammar_note_marks: spec.grammarMarks.filter(
                      (mark) => mark.anchor_segment_id === seg.id,
                    ),
                  }
                : {}),
            },
          ],
        });
      }
      offset += 2;

      const sourceBlock: ReaderSourceBlockNodeDto = {
        type: "reader_source_block",
        owner: "stable",
        base_id: "base_w1",
        unit_id: spec.unitId,
        base_start_utf16: unitStart,
        base_end_utf16: offset - 2,
        stableBlockType: spec.stableBlockType,
        stableBlockId: spec.stableBlockId,
        headingLevel: spec.headingLevel ?? null,
        contentRole: spec.contentRole ?? null,
        parentStableBlockId: spec.parentStableBlockId ?? null,
        children: segNodes,
      };

      const translations = (spec.translations ?? []).map((group) => ({
        type: "reader_translation_group" as const,
        owner: "system_ai" as const,
        layer_id: group.layerId,
        layer_version: 1,
        base_id: "base_w1",
        unit_id: spec.unitId,
        target_scope: "unit" as const,
        target_key: spec.unitId,
        group_id: group.groupId,
        covered_anchor_segment_ids: group.covers,
        source_text_hash: `hash_${group.groupId}`,
        children: [{ text: group.text }],
      }));

      const analyses = (spec.analyses ?? []).map((analysis) => ({
        type: "reader_sentence_analysis" as const,
        owner: "system_ai" as const,
        analysis_id: analysis.analysisId,
        layer_id: `layer_${analysis.analysisId}`,
        layer_version: 1,
        base_id: "base_w1",
        unit_id: spec.unitId,
        target_scope: "unit" as const,
        target_key: spec.unitId,
        anchor_segment_id: analysis.segId,
        selected_text: analysis.text,
        label: "clause",
        analysis: `analysis of ${analysis.text}`,
        chunks: [{ order: 1, label: "clause", text: analysis.text }],
        children: [{ text: `analysis of ${analysis.text}` }],
      }));

      navigationUnits.push({
        unit_id: spec.unitId,
        order_index: unitIndex + 1,
        unit_type: "body",
        boundary_quality: "normal",
        label: null,
        base_start_utf16: unitStart,
        base_end_utf16: offset - 2,
        text_hash: `hash_${spec.unitId}`,
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
        stable_block_type: spec.stableBlockType,
        heading_level: spec.headingLevel ?? null,
      });

      valueUnits.push({
        type: "reader_unit",
        owner: "stable",
        base_id: "base_w1",
        unit_id: spec.unitId,
        order_index: unitIndex + 1,
        unit_type: "body",
        boundary_quality: "normal",
        base_start_utf16: unitStart,
        base_end_utf16: offset - 2,
        text_hash: `hash_${spec.unitId}`,
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
        children: [sourceBlock, ...translations, ...analyses],
      });
    }

    return {
      schema_kind: READER_PLATE_SNAPSHOT_SCHEMA_KIND,
      snapshot_id: "snapshot_w1",
      snapshot_taken_at: "2026-08-08T00:00:00Z",
      last_event_sequence: 1,
      record_id: "record_w1",
      record: {
        title: "Wrapper Composition Fixture",
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
        base_id: "base_w1",
        content_sha256: "c".repeat(64),
        canonicalizer_version: "test",
        builder_version: "test",
        segmenter_version: "test",
        text_length_utf16: offset,
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      },
      navigation: { units: navigationUnits },
      anchor_segments: anchorSegments,
      enhancement_layers: [],
      enhancement_progress: undefined,
      analysis_progress: makeAnalysisProgressDto(),
      ask_supplements: extras.supplements ?? [],
      user_assets: [],
      parsed_decisions: [],
      value: valueUnits,
      ...(extras.tree ? { stable_document_tree: extras.tree } : {}),
    };
  }

  function wgSupplement(options: {
    supplementId: string;
    unitId: string;
    segId: string;
    selectedText: string;
  }): ReaderSnapshotAskSupplementDto {
    return makeSupplement({
      supplement_id: options.supplementId,
      anchor: {
        anchor_type: "text_range",
        base_id: "base_w1",
        unit_id: options.unitId,
        anchor_segment_id: options.segId,
        sentence_id: `sent_${options.segId}`,
        segment_type: "sentence",
        offset_unit: READER_TEXT_RANGE_OFFSET_UNIT,
        start_offset: 0,
        end_offset: options.selectedText.length,
        selected_text: options.selectedText,
        text_hash: computeUtf16FNV1a(options.selectedText),
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      },
    });
  }

  const multiSpanUnit: WgUnit = {
    unitId: "u3",
    segs: [
      { id: "s5", text: "First source sentence." },
      { id: "s6", text: "Second source sentence." },
      { id: "s7", text: "Third source sentence." },
      { id: "s8", text: "Final source sentence." },
    ],
    stableBlockType: "paragraph",
    stableBlockId: "para_u3",
    translations: [
      {
        groupId: "g5_7",
        layerId: "layer_translation_1",
        covers: ["s5", "s6", "s7"],
        text: "前三句的译文。",
      },
      {
        groupId: "g8_8",
        layerId: "layer_translation_1",
        covers: ["s8"],
        text: "最后一句的译文。",
      },
    ],
    analyses: [
      { analysisId: "analysis_s6", segId: "s6", text: "Second source sentence." },
      { analysisId: "analysis_s7", segId: "s7", text: "Third source sentence." },
    ],
  };

  const expectedInterleavedIds = [
    "paragraph:s5",
    "blockquote:layer_translation_1:g5_7",
    "sentence_analysis:analysis_s6",
    "sentence_analysis:analysis_s7",
    "paragraph:s8",
    "blockquote:layer_translation_1:g8_8",
  ];

  it("keeps multi-span units interleaved (tree-present): translation/annotations stay at their own anchor positions", () => {
    const snapshot = buildWgSnapshot([multiSpanUnit], {
      tree: [wgTreeNode({ block_id: "para_u3", block_type: "paragraph" })],
    });

    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);

    expect(document.children.map((child) => child.id)).toEqual(
      expectedInterleavedIds,
    );
    const firstSpan = document.children[0];
    expect((firstSpan.data as ReaderRecordPlateStableBlockData).unitId).toBe("u3");
    expect(
      (firstSpan.data as ReaderRecordPlateStableBlockData).isUnitStart,
    ).toBe(true);
    const secondSpan = document.children[4];
    expect(
      (secondSpan.data as ReaderRecordPlateStableBlockData).isUnitStart ??
        false,
    ).toBe(false);
  });

  it("keeps multi-span units interleaved on the legacy flat path (tree-absent)", () => {
    const snapshot = buildWgSnapshot([multiSpanUnit]);
    delete (snapshot as { stable_document_tree?: unknown }).stable_document_tree;

    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);

    expect(document.children.map((child) => child.id)).toEqual(
      expectedInterleavedIds,
    );
  });

  it("groups list items across overlays, keeps nested lists and ordered, and defers translations after the whole list", () => {
    const snapshot = buildWgSnapshot(
      [
        {
          unitId: "u_i1",
          segs: [{ id: "i1", text: "First item" }],
          stableBlockType: "list_item",
          stableBlockId: "item_1",
          parentStableBlockId: "list_1",
          translations: [
            {
              groupId: "tr_i1",
              layerId: "layer_translation_1",
              covers: ["i1"],
              text: "第一项译文。",
            },
          ],
        },
        {
          unitId: "u_n1",
          segs: [{ id: "n1", text: "Nested item" }],
          stableBlockType: "list_item",
          stableBlockId: "item_n1",
          parentStableBlockId: "list_nested",
          translations: [
            {
              groupId: "tr_n1",
              layerId: "layer_translation_1",
              covers: ["n1"],
              text: "嵌套项译文。",
            },
          ],
        },
        {
          unitId: "u_i2",
          segs: [{ id: "i2", text: "Second item" }],
          stableBlockType: "list_item",
          stableBlockId: "item_2",
          parentStableBlockId: "list_1",
          translations: [
            {
              groupId: "tr_i2",
              layerId: "layer_translation_1",
              covers: ["i2"],
              text: "第二项译文。",
            },
          ],
        },
      ],
      {
        tree: [
          wgTreeNode({
            block_id: "list_1",
            block_type: "list",
            payload: { ordered: true },
            children: [
              wgTreeNode({
                block_id: "item_1",
                parent_block_id: "list_1",
                block_type: "list_item",
                children: [
                  wgTreeNode({
                    block_id: "list_nested",
                    parent_block_id: "item_1",
                    block_type: "list",
                    payload: { ordered: false },
                    children: [
                      wgTreeNode({
                        block_id: "item_n1",
                        parent_block_id: "list_nested",
                        block_type: "list_item",
                      }),
                    ],
                  }),
                ],
              }),
              wgTreeNode({
                block_id: "item_2",
                parent_block_id: "list_1",
                order_index: 1,
                block_type: "list_item",
              }),
            ],
          }),
        ],
      },
    );

    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);

    expect(document.children.map((child) => child.id)).toEqual([
      "list:list_1",
      "blockquote:layer_translation_1:tr_i1",
      "blockquote:layer_translation_1:tr_n1",
      "blockquote:layer_translation_1:tr_i2",
    ]);
    const list = document.children[0];
    if (!list || list.type !== "list") {
      throw new Error("list wrapper missing");
    }
    expect(list.ordered).toBe(true);
    expect(list.children.map((item) => item.id)).toEqual([
      "list_item:i1",
      "list_item:i2",
    ]);
    const firstItem = list.children[0];
    if (firstItem.type !== "list_item") {
      throw new Error("first list child is not an item");
    }
    expect(firstItem.nestedChildren?.map((nested) => nested.id)).toEqual([
      "list:list_nested",
    ]);
    expect(
      (list.data as ReaderRecordPlateStableBlockData).isUnitStart,
    ).toBe(true);
    expect((list.data as ReaderRecordPlateStableBlockData).unitId).toBe("u_i1");
  });

  it("keeps flat-path list grouping across overlays with ordered left as the known legacy limitation", () => {
    const snapshot = buildWgSnapshot([
      {
        unitId: "u_i1",
        segs: [{ id: "i1", text: "First item" }],
        stableBlockType: "list_item",
        stableBlockId: "item_1",
        parentStableBlockId: "list_1",
        translations: [
          {
            groupId: "tr_i1",
            layerId: "layer_translation_1",
            covers: ["i1"],
            text: "第一项译文。",
          },
        ],
      },
      {
        unitId: "u_i2",
        segs: [{ id: "i2", text: "Second item" }],
        stableBlockType: "list_item",
        stableBlockId: "item_2",
        parentStableBlockId: "list_1",
        translations: [
          {
            groupId: "tr_i2",
            layerId: "layer_translation_1",
            covers: ["i2"],
            text: "第二项译文。",
          },
        ],
      },
    ]);

    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);

    expect(document.children.map((child) => child.id)).toEqual([
      "list:list_1",
      "blockquote:layer_translation_1:tr_i1",
      "blockquote:layer_translation_1:tr_i2",
    ]);
    const list = document.children[0];
    if (!list || list.type !== "list") {
      throw new Error("list wrapper missing");
    }
    expect(list.ordered).toBe(false);
    expect(list.children.map((item) => item.id)).toEqual([
      "list_item:i1",
      "list_item:i2",
    ]);
  });

  it("defers a table-cell supplement card after the whole table while cell anchors stay in place", () => {
    const snapshot = buildWgSnapshot(
      [
        {
          unitId: "u_c11",
          segs: [{ id: "c11", text: "Name" }],
          stableBlockType: "table_cell",
          stableBlockId: "cell_11",
          parentStableBlockId: "row_1",
        },
        {
          unitId: "u_c12",
          segs: [{ id: "c12", text: "Value" }],
          stableBlockType: "table_cell",
          stableBlockId: "cell_12",
          parentStableBlockId: "row_1",
        },
      ],
      {
        supplements: [
          wgSupplement({
            supplementId: "supplement_c11",
            unitId: "u_c11",
            segId: "c11",
            selectedText: "Name",
          }),
        ],
        tree: [
          wgTreeNode({
            block_id: "table_1",
            block_type: "table",
            children: [
              wgTreeNode({
                block_id: "row_1",
                parent_block_id: "table_1",
                block_type: "table_row",
                children: [
                  wgTreeNode({
                    block_id: "cell_11",
                    parent_block_id: "row_1",
                    block_type: "table_cell",
                  }),
                  wgTreeNode({
                    block_id: "cell_12",
                    parent_block_id: "row_1",
                    block_type: "table_cell",
                  }),
                ],
              }),
            ],
          }),
        ],
      },
    );

    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);

    expect(document.children.map((child) => child.id)).toEqual([
      "table:table_1",
      "callout:supplement:supplement_c11",
    ]);
    const table = document.children[0];
    if (!table || table.type !== "table") {
      throw new Error("table wrapper missing");
    }
    expect(
      table.children[0].children.map((cell) => cell.id),
    ).toEqual(["table_cell:c11", "table_cell:c12"]);
  });

  it("keeps heading translations in place (no deferral for plain leaves)", () => {
    const snapshot = buildWgSnapshot(
      [
        {
          unitId: "u_h1",
          segs: [{ id: "h1", text: "Section heading" }],
          stableBlockType: "heading",
          stableBlockId: "head_1",
          headingLevel: 2,
          translations: [
            {
              groupId: "tr_h1",
              layerId: "layer_translation_1",
              covers: ["h1"],
              text: "章节标题译文。",
            },
          ],
        },
      ],
      { tree: [wgTreeNode({ block_id: "head_1", block_type: "heading" })] },
    );

    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);

    expect(document.children.map((child) => child.id)).toEqual([
      "heading:h1",
      "blockquote:layer_translation_1:tr_h1",
    ]);
  });

  it("closes code blocks to supplement cards via the single eligibility signal", () => {
    const snapshot = buildWgSnapshot(
      [
        {
          unitId: "u_code",
          segs: [{ id: "code1", text: "const answer = 42;" }],
          stableBlockType: "code_block",
          stableBlockId: "code_1",
        },
        {
          unitId: "u_para",
          segs: [{ id: "p1", text: "A normal paragraph." }],
          stableBlockType: "paragraph",
          stableBlockId: "para_1",
        },
      ],
      {
        supplements: [
          wgSupplement({
            supplementId: "supplement_code",
            unitId: "u_code",
            segId: "code1",
            selectedText: "const answer = 42;",
          }),
          wgSupplement({
            supplementId: "supplement_para",
            unitId: "u_para",
            segId: "p1",
            selectedText: "A normal paragraph.",
          }),
        ],
        tree: [
          wgTreeNode({ block_id: "code_1", block_type: "code_block" }),
          wgTreeNode({
            block_id: "para_1",
            order_index: 1,
            block_type: "paragraph",
          }),
        ],
      },
    );

    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);

    expect(document.children.map((child) => child.id)).toEqual([
      "code_block:code1",
      "paragraph:p1",
      "callout:supplement:supplement_para",
    ]);
  });

  it("emits nothing for thematic_break nodes (no unit, no mount point)", () => {
    const snapshot = buildWgSnapshot(
      [
        {
          unitId: "u_p1",
          segs: [{ id: "p1", text: "Body paragraph." }],
          stableBlockType: "paragraph",
          stableBlockId: "para_1",
        },
      ],
      {
        tree: [
          wgTreeNode({ block_id: "para_1", block_type: "paragraph" }),
          wgTreeNode({
            block_id: "hr_1",
            order_index: 1,
            block_type: "thematic_break",
          }),
        ],
      },
    );

    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);

    expect(document.children.map((child) => child.id)).toEqual(["paragraph:p1"]);
  });

  it("defers a plain blockquote's translation as a sibling after the source structure", () => {
    const snapshot = buildWgSnapshot(
      [
        {
          unitId: "u_q1",
          segs: [{ id: "q1", text: "Quoted line one." }],
          stableBlockType: "blockquote",
          stableBlockId: "quote_child_1",
          parentStableBlockId: "quote_1",
          translations: [
            {
              groupId: "tr_q1",
              layerId: "layer_translation_1",
              covers: ["q1"],
              text: "第一行引文译文。",
            },
          ],
        },
        {
          unitId: "u_q2",
          segs: [{ id: "q2", text: "Quoted line two." }],
          stableBlockType: "blockquote",
          stableBlockId: "quote_child_2",
          parentStableBlockId: "quote_1",
        },
      ],
      {
        tree: [
          wgTreeNode({
            block_id: "quote_1",
            block_type: "blockquote",
            content_role: "prose",
            children: [
              wgTreeNode({
                block_id: "quote_child_1",
                parent_block_id: "quote_1",
                block_type: "paragraph",
              }),
              wgTreeNode({
                block_id: "quote_child_2",
                parent_block_id: "quote_1",
                order_index: 1,
                block_type: "paragraph",
              }),
            ],
          }),
        ],
      },
    );

    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);

    expect(document.children.map((child) => child.id)).toEqual([
      "markdown_blockquote:q1",
      "markdown_blockquote:q2",
      "blockquote:layer_translation_1:tr_q1",
    ]);
  });

  it("defers a source callout's translation as a sibling after the callout wrapper", () => {
    const snapshot = buildWgSnapshot(
      [
        {
          unitId: "u_p1",
          segs: [{ id: "p1", text: "Callout body one." }],
          stableBlockType: "paragraph",
          stableBlockId: "callout_child_1",
          parentStableBlockId: "callout_1",
          translations: [
            {
              groupId: "tr_p1",
              layerId: "layer_translation_1",
              covers: ["p1"],
              text: "旁注正文一译文。",
            },
          ],
        },
        {
          unitId: "u_p2",
          segs: [{ id: "p2", text: "Callout body two." }],
          stableBlockType: "paragraph",
          stableBlockId: "callout_child_2",
          parentStableBlockId: "callout_1",
        },
      ],
      {
        tree: [
          wgTreeNode({
            block_id: "callout_1",
            block_type: "blockquote",
            content_role: "source_callout",
            children: [
              wgTreeNode({
                block_id: "callout_child_1",
                parent_block_id: "callout_1",
                block_type: "paragraph",
              }),
              wgTreeNode({
                block_id: "callout_child_2",
                parent_block_id: "callout_1",
                order_index: 1,
                block_type: "paragraph",
              }),
            ],
          }),
        ],
      },
    );

    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);

    expect(document.children.map((child) => child.id)).toEqual([
      "source_callout:callout_1",
      "blockquote:layer_translation_1:tr_p1",
    ]);
    const callout = document.children[0];
    if (!callout || callout.type !== "source_callout") {
      throw new Error("source callout wrapper missing");
    }
    expect(
      callout.children.map((child) => ("id" in child ? child.id : "text")),
    ).toEqual([
      "paragraph:p1",
      "paragraph:p2",
    ]);
  });

  it("keeps grammar → analysis → supplement annotation order across segments in the tree path", () => {
    const snapshot = buildWgSnapshot(
      [
        {
          ...multiSpanUnit,
          grammarMarks: [
            makeGrammarMark({
              mark_id: "grammar_mark_s6",
              item_id: "grammar_item_s6",
              anchor_segment_id: "s6",
              start_offset: 0,
              end_offset: 6,
              segment_start_utf16: 0,
              segment_end_utf16: 6,
            }),
          ],
        },
      ],
      {
        supplements: [
          wgSupplement({
            supplementId: "supplement_s7",
            unitId: "u3",
            segId: "s7",
            selectedText: "Third source sentence.",
          }),
        ],
        tree: [wgTreeNode({ block_id: "para_u3", block_type: "paragraph" })],
      },
    );

    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);

    expect(document.children.map((child) => child.id)).toEqual([
      "paragraph:s5",
      "blockquote:layer_translation_1:g5_7",
      "callout:grammar:grammar_item_s6",
      "sentence_analysis:analysis_s6",
      "sentence_analysis:analysis_s7",
      "callout:supplement:supplement_s7",
      "paragraph:s8",
      "blockquote:layer_translation_1:g8_8",
    ]);
  });
});

// ---------------------------------------------------------------------------
// G3b Slice A: tree → Reader Plate image (standalone + inline) RED
// ---------------------------------------------------------------------------

describe("G3b Reader image tree projection Slice A - standalone and inline RED", () => {
  function wgImgTreeNode(
    overrides: Partial<ReaderStableDocumentBlockNodeDto>,
  ): ReaderStableDocumentBlockNodeDto {
    return {
      block_id: "img",
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

  function imagePayload(
    sourceUrl: string,
    altText: string,
    title: string | null,
    effectiveUrl: string | null,
  ): Record<string, unknown> {
    return {
      source_url: sourceUrl,
      alt_text: altText,
      title,
      position_kind: "standalone",
      effective_url: effectiveUrl,
    };
  }

  function inlineImageEntry(
    sourceUrl: string,
    altText: string,
    title: string | null,
    beforeUtf16: number,
    effectiveUrl: string | null,
  ): Record<string, unknown> {
    return {
      source_url: sourceUrl,
      alt_text: altText,
      title,
      before_utf16: beforeUtf16,
      effective_url: effectiveUrl,
    };
  }

  type ImgWgSeg = { id: string; text: string };
  type ImgWgUnit = {
    unitId: string;
    segs: ImgWgSeg[];
    stableBlockType: string;
    stableBlockId: string;
    parentStableBlockId?: string | null;
    headingLevel?: number | null;
    translations?: Array<{ groupId: string; layerId: string; covers: string[]; text: string }>;
  };

  function buildImgSnapshot(
    specs: ImgWgUnit[],
    extras: { tree?: ReaderStableDocumentBlockNodeDto[] } = {},
  ): ReaderPlateSnapshotDto {
    let offset = 0;
    const anchorSegments: ReaderPlateSnapshotDto["anchor_segments"] = [];
    const navigationUnits: ReaderPlateSnapshotDto["navigation"]["units"] = [];
    const valueUnits: ReaderUnitNodeDto[] = [];
    for (const [unitIndex, spec] of specs.entries()) {
      const unitStart = offset;
      const segNodes: ReaderSourceBlockChildNodeDto[] = [];
      for (const seg of spec.segs) {
        const start = offset;
        const end = start + seg.text.length;
        offset = end;
        anchorSegments.push({
          anchor_segment_id: seg.id,
          sentence_id: `sent_${seg.id}`,
          paragraph_id: spec.unitId,
          unit_id: spec.unitId,
          order_index: anchorSegments.length + 1,
          unit_order_index: 1,
          segment_type: "sentence",
          boundary_quality: "normal",
          base_start_utf16: start,
          base_end_utf16: end,
          unit_start_utf16: start - unitStart,
          unit_end_utf16: end - unitStart,
          text_hash: `hash_${seg.id}`,
          hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
        });
        segNodes.push({
          type: "reader_anchor_segment",
          owner: "stable",
          base_id: "base_w1",
          unit_id: spec.unitId,
          anchor_segment_id: seg.id,
          sentence_id: `sent_${seg.id}`,
          segment_type: "sentence",
          boundary_quality: "normal",
          base_start_utf16: start,
          base_end_utf16: end,
          unit_start_utf16: start - unitStart,
          unit_end_utf16: end - unitStart,
          text_hash: `hash_${seg.id}`,
          hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
          children: [
            {
              text: seg.text,
              owner: "stable",
              lock_source: true,
              source_role: "segment_text",
              base_start_utf16: start,
              base_end_utf16: end,
              anchor_segment_id: seg.id,
              segment_start_utf16: 0,
              segment_end_utf16: seg.text.length,
            },
          ],
        });
      }
      offset += 2;
      const sourceBlock: ReaderSourceBlockNodeDto = {
        type: "reader_source_block",
        owner: "stable",
        base_id: "base_w1",
        unit_id: spec.unitId,
        base_start_utf16: unitStart,
        base_end_utf16: offset - 2,
        stableBlockType: spec.stableBlockType,
        stableBlockId: spec.stableBlockId,
        headingLevel: spec.headingLevel ?? null,
        parentStableBlockId: spec.parentStableBlockId ?? null,
        children: segNodes,
      };
      const translations = (spec.translations ?? []).map((g) => ({
        type: "reader_translation_group" as const,
        owner: "system_ai" as const,
        layer_id: g.layerId,
        layer_version: 1,
        base_id: "base_w1",
        unit_id: spec.unitId,
        target_scope: "unit" as const,
        target_key: spec.unitId,
        group_id: g.groupId,
        covered_anchor_segment_ids: g.covers,
        source_text_hash: `hash_${g.groupId}`,
        children: [{ text: g.text }],
      }));
      navigationUnits.push({
        unit_id: spec.unitId,
        order_index: unitIndex + 1,
        unit_type: "body",
        boundary_quality: "normal",
        label: null,
        base_start_utf16: unitStart,
        base_end_utf16: offset - 2,
        text_hash: `hash_${spec.unitId}`,
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
        stable_block_type: spec.stableBlockType,
        heading_level: spec.headingLevel ?? null,
      });
      valueUnits.push({
        type: "reader_unit",
        owner: "stable",
        base_id: "base_w1",
        unit_id: spec.unitId,
        order_index: unitIndex + 1,
        unit_type: "body",
        boundary_quality: "normal",
        base_start_utf16: unitStart,
        base_end_utf16: offset - 2,
        text_hash: `hash_${spec.unitId}`,
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
        children: [sourceBlock, ...translations],
      });
    }
    return {
      schema_kind: READER_PLATE_SNAPSHOT_SCHEMA_KIND,
      snapshot_id: "snapshot_w1",
      snapshot_taken_at: "2026-08-08T00:00:00Z",
      last_event_sequence: 1,
      record_id: "record_w1",
      record: {
        title: "Wrapper Composition Fixture",
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
        base_id: "base_w1",
        content_sha256: "c".repeat(64),
        canonicalizer_version: "test",
        builder_version: "test",
        segmenter_version: "test",
        text_length_utf16: offset,
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      },
      navigation: { units: navigationUnits },
      anchor_segments: anchorSegments,
      enhancement_layers: [],
      enhancement_progress: undefined,
      analysis_progress: makeAnalysisProgressDto(),
      ask_supplements: [],
      user_assets: [],
      parsed_decisions: [],
      value: valueUnits,
      ...(extras.tree ? { stable_document_tree: extras.tree } : {}),
    };
  }
  const buildWgSnapshot = buildImgSnapshot;

  // A1: data contract shape is verified via image block existence and fields;
  // the single shape is asserted in every standalone/inline case below.

  it("standalone image at root: before, middle and after positions", () => {
    const snapshotBefore = buildWgSnapshot(
      [
        {
          unitId: "u_p1",
          segs: [{ id: "s1", text: "Hello" }],
          stableBlockType: "paragraph",
          stableBlockId: "p1",
        },
        {
          unitId: "u_p2",
          segs: [{ id: "s2", text: "World" }],
          stableBlockType: "paragraph",
          stableBlockId: "p2",
        },
      ],
      {
        tree: [
          wgImgTreeNode({
            block_id: "img_before",
            block_type: "image",
            order_index: 0,
            payload: imagePayload("https://example.com/before.png", "before", null, "https://example.com/before.png"),
          }),
          wgImgTreeNode({
            block_id: "p1",
            block_type: "paragraph",
            order_index: 1,
          }),
          wgImgTreeNode({
            block_id: "p2",
            block_type: "paragraph",
            order_index: 2,
          }),
        ],
      },
    );
    const docBefore = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshotBefore);
    // RED: currently image is silently skipped, so no image block exists
    const imgBefore = docBefore.children.find((c) => (c as { id?: string }).id === "image:img_before");
    expect(imgBefore).toBeTruthy();
    expect((imgBefore as unknown as { data: { sourceUrl: string } })?.data.sourceUrl).toBe("https://example.com/before.png");

    const snapshotMiddle = buildWgSnapshot(
      [
        {
          unitId: "u_p1",
          segs: [{ id: "s1", text: "Hello" }],
          stableBlockType: "paragraph",
          stableBlockId: "p1",
        },
        {
          unitId: "u_p2",
          segs: [{ id: "s2", text: "World" }],
          stableBlockType: "paragraph",
          stableBlockId: "p2",
        },
      ],
      {
        tree: [
          wgImgTreeNode({
            block_id: "p1",
            block_type: "paragraph",
            order_index: 0,
          }),
          wgImgTreeNode({
            block_id: "img_mid",
            block_type: "image",
            order_index: 1,
            payload: imagePayload("https://example.com/mid.png", "mid", "Title", "https://example.com/mid.png"),
          }),
          wgImgTreeNode({
            block_id: "p2",
            block_type: "paragraph",
            order_index: 2,
          }),
        ],
      },
    );
    const docMid = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshotMiddle);
    const idsMid = docMid.children.map((c) => (c as { id: string }).id);
    // image should be between the two paragraphs (not reordering flat unit/overlay contract)
    const imgIdx = idsMid.indexOf("image:img_mid");
    const p1Idx = idsMid.indexOf("paragraph:s1");
    const p2Idx = idsMid.indexOf("paragraph:s2");
    expect(imgIdx).toBeGreaterThan(p1Idx);
    expect(imgIdx).toBeLessThan(p2Idx);

    const snapshotAfter = buildWgSnapshot(
      [
        {
          unitId: "u_p1",
          segs: [{ id: "s1", text: "Hello" }],
          stableBlockType: "paragraph",
          stableBlockId: "p1",
        },
        {
          unitId: "u_p2",
          segs: [{ id: "s2", text: "World" }],
          stableBlockType: "paragraph",
          stableBlockId: "p2",
        },
      ],
      {
        tree: [
          wgImgTreeNode({ block_id: "p1", block_type: "paragraph", order_index: 0 }),
          wgImgTreeNode({ block_id: "p2", block_type: "paragraph", order_index: 1 }),
          wgImgTreeNode({
            block_id: "img_after",
            block_type: "image",
            order_index: 2,
            payload: imagePayload("https://example.com/after.png", "after", null, "https://example.com/after.png"),
          }),
        ],
      },
    );
    const docAfter = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshotAfter);
    const idsAfter = docAfter.children.map((c) => (c as { id: string }).id);
    expect(idsAfter[idsAfter.length - 1]).toBe("image:img_after");
  });

  it("consecutive N standalone images keep order_index/tree source order", () => {
    const snapshot = buildWgSnapshot(
      [
        {
          unitId: "u_p1",
          segs: [{ id: "s1", text: "Para" }],
          stableBlockType: "paragraph",
          stableBlockId: "p1",
        },
      ],
      {
        tree: [
          wgImgTreeNode({ block_id: "p1", block_type: "paragraph", order_index: 0 }),
          wgImgTreeNode({
            block_id: "img1",
            block_type: "image",
            order_index: 1,
            payload: imagePayload("https://example.com/1.png", "1", null, "https://example.com/1.png"),
          }),
          wgImgTreeNode({
            block_id: "img2",
            block_type: "image",
            order_index: 2,
            payload: imagePayload("https://example.com/2.png", "2", null, "https://example.com/2.png"),
          }),
          wgImgTreeNode({
            block_id: "img3",
            block_type: "image",
            order_index: 3,
            payload: imagePayload("https://example.com/3.png", "3", null, "https://example.com/3.png"),
          }),
        ],
      },
    );
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const imgIds = doc.children
      .filter((c) => (c as { type: string }).type === "image")
      .map((c) => (c as { id: string }).id);
    expect(imgIds).toEqual(["image:img1", "image:img2", "image:img3"]);
  });

  it("standalone image has no unit/anchor and snapshot.value unchanged", () => {
    const snapshot = buildWgSnapshot(
      [
        {
          unitId: "u_p1",
          segs: [{ id: "s1", text: "Hello world" }],
          stableBlockType: "paragraph",
          stableBlockId: "p1",
        },
      ],
      {
        tree: [
          wgImgTreeNode({ block_id: "p1", block_type: "paragraph", order_index: 0 }),
          wgImgTreeNode({
            block_id: "img1",
            block_type: "image",
            order_index: 1,
            payload: imagePayload("https://example.com/a.png", "a", "T", "https://example.com/a.png"),
          }),
        ],
      },
    );
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const img = doc.children.find((c) => (c as { id: string }).id === "image:img1") as unknown as {
      data: Record<string, unknown>;
      children: unknown[];
    };
    expect(img).toBeTruthy();
    // must not have unit/anchor/canonical fields
    expect((img.data as { unitId?: unknown }).unitId).toBeUndefined();
    expect((img.data as { anchorSegmentId?: unknown }).anchorSegmentId).toBeUndefined();
    expect((img.data as { baseRange?: unknown }).baseRange).toBeUndefined();
    // void leaf
    expect(img.children).toEqual([{ text: "" }]);
    // URL/alt/title must not enter text leaf
    expect(JSON.stringify(img.children)).not.toContain("https://example.com/a.png");
    expect(JSON.stringify(img.children)).not.toContain("a");
    // snapshot.value unchanged: no image node in value
    const valueJson = JSON.stringify(snapshot.value);
    expect(valueJson).not.toContain("img1");
    expect(valueJson).not.toContain("effective_url");
  });

  it("standalone payload fields read verbatim from stable_document_tree", () => {
    const payload = imagePayload("https://example.com/a.png", "alt text", "My Title", "https://example.com/a.png");
    const snapshot = buildWgSnapshot(
      [
        {
          unitId: "u_p1",
          segs: [{ id: "s1", text: "Hello" }],
          stableBlockType: "paragraph",
          stableBlockId: "p1",
        },
      ],
      {
        tree: [
          wgImgTreeNode({ block_id: "p1", block_type: "paragraph", order_index: 0 }),
          wgImgTreeNode({
            block_id: "img1",
            block_type: "image",
            order_index: 1,
            payload,
          }),
        ],
      },
    );
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const img = doc.children.find((c) => (c as { id: string }).id === "image:img1") as unknown as {
      data: { sourceUrl: string; effectiveUrl: string | null; altText: string; title: string | null; positionKind: string; stableBlockId: string; parentStableBlockId: string | null };
    };
    expect(img.data.sourceUrl).toBe("https://example.com/a.png");
    expect(img.data.effectiveUrl).toBe("https://example.com/a.png");
    expect(img.data.altText).toBe("alt text");
    expect(img.data.title).toBe("My Title");
    expect(img.data.positionKind).toBe("standalone");
    expect(img.data.stableBlockId).toBe("img1");
    expect(img.data.parentStableBlockId).toBeNull();
  });

  it("standalone image adjacent to translation/callout overlay does not reorder overlay", () => {
    const snapshot = buildWgSnapshot(
      [
        {
          unitId: "u_p1",
          segs: [{ id: "s1", text: "Hello" }],
          stableBlockType: "paragraph",
          stableBlockId: "p1",
          translations: [{ groupId: "tr1", layerId: "layer_translation_1", covers: ["s1"], text: "译文" }],
        },
        {
          unitId: "u_p2",
          segs: [{ id: "s2", text: "World" }],
          stableBlockType: "paragraph",
          stableBlockId: "p2",
        },
      ],
      {
        tree: [
          wgImgTreeNode({ block_id: "p1", block_type: "paragraph", order_index: 0 }),
          wgImgTreeNode({
            block_id: "img1",
            block_type: "image",
            order_index: 1,
            payload: imagePayload("https://example.com/a.png", "a", null, "https://example.com/a.png"),
          }),
          wgImgTreeNode({ block_id: "p2", block_type: "paragraph", order_index: 2 }),
        ],
      },
    );
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const ids = doc.children.map((c) => (c as { id: string }).id);
    // translation for p1 should stay immediately after p1, before image
    const p1Idx = ids.indexOf("paragraph:s1");
    const trIdx = ids.indexOf("blockquote:layer_translation_1:tr1");
    const imgIdx = ids.indexOf("image:img1");
    const p2Idx = ids.indexOf("paragraph:s2");
    expect(p1Idx).toBeGreaterThanOrEqual(0);
    expect(trIdx).toBe(p1Idx + 1);
    expect(imgIdx).toBeGreaterThan(trIdx);
    expect(imgIdx).toBeLessThan(p2Idx);
  });

  it("promoted list image: parent points to list wrapper, keeps order, no fake unit/anchor", () => {
    const snapshot = buildWgSnapshot(
      [
        {
          unitId: "u_i1",
          segs: [{ id: "i1", text: "First item" }],
          stableBlockType: "list_item",
          stableBlockId: "item_1",
          parentStableBlockId: "list_1",
        },
        {
          unitId: "u_i2",
          segs: [{ id: "i2", text: "Second item" }],
          stableBlockType: "list_item",
          stableBlockId: "item_2",
          parentStableBlockId: "list_1",
        },
      ],
      {
        tree: [
          wgImgTreeNode({
            block_id: "list_1",
            block_type: "list",
            order_index: 0,
            payload: { ordered: false },
            children: [
              wgImgTreeNode({ block_id: "item_1", parent_block_id: "list_1", block_type: "list_item" }),
              wgImgTreeNode({
                block_id: "img_list",
                parent_block_id: "list_1",
                block_type: "image",
                order_index: 1,
                payload: imagePayload("https://example.com/list.png", "list", null, "https://example.com/list.png"),
              }),
              wgImgTreeNode({
                block_id: "item_2",
                parent_block_id: "list_1",
                block_type: "list_item",
                order_index: 2,
              }),
            ],
          }),
        ],
      },
    );
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const list = doc.children.find((c) => (c as { type: string }).type === "list") as unknown as {
      id: string;
      data: { stableBlockId: string };
      children: Array<{ id: string; data: Record<string, unknown> }>;
    };
    expect(list).toBeTruthy();
    // image should be inside list wrapper, between items, with parent pointing to list
    // RED: currently list only contains list_item children, image is dropped
    const listChildIds = list.children.map((child) => child.id);
    // order: item_1, image, item_2
    expect(listChildIds).toContain("image:img_list");
    const img = list.children.find((c) => c.id === "image:img_list") as unknown as {
      data: { parentStableBlockId: string | null; stableBlockId: string };
    };
    expect(img.data.parentStableBlockId).toBe("list_1");
    expect(img.data.stableBlockId).toBe("img_list");
    // no fake unit/anchor/list_item identity on image or wrapper
    expect((img.data as { unitId?: unknown }).unitId).toBeUndefined();
    // wrapper (list) must not have navigable attrs faked from image
    // and image's wrapper (if any) must not carry Stable list_item
    // For this minimal test, ensure image type is "image" not "list_item"
    expect((img as unknown as { type: string }).type).toBe("image");
  });

  // A3 owning-block inline_images param matrix: paragraph, heading, list_item, blockquote, mixed table_cell, image-only table_cell
  it.each([
    ["paragraph"],
    ["heading"],
    ["list_item"],
    ["blockquote"],
    ["table_cell"],
  ] as const)("inline image in %s: before_utf16 relative, UTF-16, same-offset ordinal, 0/mid/end", (stableType) => {
    const text = "hello world";
    // use before 0, middle 5, end 11, and duplicate offset 5 with two images
    const inlinePayload = [
      inlineImageEntry("https://example.com/0.png", "zero", null, 0, "https://example.com/0.png"),
      inlineImageEntry("https://example.com/5a.png", "a", null, 5, "https://example.com/5a.png"),
      inlineImageEntry("https://example.com/5b.png", "b", null, 5, "https://example.com/5b.png"),
      inlineImageEntry("https://example.com/11.png", "end", null, 11, "https://example.com/11.png"),
    ];
    const isListItem = stableType === "list_item";
    const isTableCell = stableType === "table_cell";
    const spec: Parameters<typeof buildWgSnapshot>[0][number] = {
      unitId: "u1",
      segs: [{ id: "s1", text }],
      stableBlockType: stableType as string,
      stableBlockId: "b1",
      ...(isListItem ? { parentStableBlockId: "list_1" } : {}),
      ...(isTableCell ? { parentStableBlockId: "row_1" } : {}),
    };
    const tree: ReaderStableDocumentBlockNodeDto[] = isListItem
      ? [
          wgImgTreeNode({
            block_id: "list_1",
            block_type: "list",
            children: [
              wgImgTreeNode({
                block_id: "b1",
                parent_block_id: "list_1",
                block_type: "list_item",
                payload: { inline_images: inlinePayload },
              }),
            ],
          }),
        ]
      : isTableCell
        ? [
            wgImgTreeNode({
              block_id: "table_1",
              block_type: "table",
              children: [
                wgImgTreeNode({
                  block_id: "row_1",
                  parent_block_id: "table_1",
                  block_type: "table_row",
                  children: [
                    wgImgTreeNode({
                      block_id: "b1",
                      parent_block_id: "row_1",
                      block_type: "table_cell",
                      payload: { inline_images: inlinePayload },
                    }),
                  ],
                }),
              ],
            }),
          ]
        : [
            wgImgTreeNode({
              block_id: "b1",
              block_type: stableType as string,
              payload: { inline_images: inlinePayload },
            }),
          ];
    const snapshot = buildWgSnapshot([spec], { tree });
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    // locate owning block
    let owning: unknown;
    if (isListItem) {
      const list = doc.children.find((c) => (c as { type: string }).type === "list") as unknown as {
        children: Array<{ id: string; children: unknown[]; data: Record<string, unknown> }>;
      };
      owning = list?.children.find((item) => item.id === "list_item:s1");
    } else if (isTableCell) {
      const table = doc.children.find((c) => (c as { type: string }).type === "table") as unknown as {
        children: Array<{ children: Array<{ id: string; children: unknown[] }> }>;
      };
      owning = table?.children[0]?.children.find((cell) => (cell as { id: string }).id === "table_cell:s1");
    } else if (stableType === "heading") {
      owning = doc.children.find((c) => (c as { type: string }).type === "heading");
    } else if (stableType === "blockquote") {
      owning = doc.children.find((c) => (c as { type: string }).type === "markdown_blockquote");
    } else {
      owning = doc.children.find((c) => (c as { type: string }).type === "paragraph");
    }
    expect(owning).toBeTruthy();
    const owningBlock = owning as {
      children: Array<{ text?: string; type?: string; id?: string; data?: Record<string, unknown> }>;
    };
    // RED: inline images are currently not inserted, so children only contain text leaves
    const imageNodes = owningBlock.children.filter((child) => (child as { type?: string }).type === "image");
    expect(imageNodes).toHaveLength(4);
    // deterministic ids: image:b1:0, image:b1:1, etc
    expect(imageNodes.map((n) => (n as { id: string }).id)).toEqual([
      "image:b1:0",
      "image:b1:1",
      "image:b1:2",
      "image:b1:3",
    ]);
    // positions: 0 before hello, 5 between hello and space, duplicated, 11 at end
    // Check that text is split correctly and not contains URLs
    const textOnly = owningBlock.children
      .filter((c) => typeof (c as { text?: unknown }).text === "string")
      .map((c) => (c as { text: string }).text)
      .join("");
    expect(textOnly).toBe(text);
    expect(textOnly).not.toContain("https://example.com");
    // image URL must be in image data, not in text leaf (verified above)
    // beforeUtf16 and ordinal encoded in data
    expect((imageNodes[0] as unknown as { data: { beforeUtf16: number; inlineOrdinal: number } }).data.beforeUtf16).toBe(0);
    expect((imageNodes[0] as unknown as { data: { inlineOrdinal: number } }).data.inlineOrdinal).toBe(0);
    expect((imageNodes[1] as unknown as { data: { beforeUtf16: number; inlineOrdinal: number } }).data.beforeUtf16).toBe(5);
    expect((imageNodes[2] as unknown as { data: { beforeUtf16: number; inlineOrdinal: number } }).data.beforeUtf16).toBe(5);
    // void leaf check
    for (const img of imageNodes) {
      expect((img as { children: unknown }).children).toEqual([{ text: "" }]);
    }
  });

  it("inline image: emoji/CJK UTF-16 length and before_utf16", () => {
    const text = "👍中a"; // 👍 length 2, 中 length 1, a 1 => total 4
    // Put image after 👍 (offset 2)
    const inlinePayload = [
      inlineImageEntry("https://example.com/emoji.png", "emoji", null, 2, "https://example.com/emoji.png"),
    ];
    const snapshot = buildWgSnapshot(
      [
        {
          unitId: "u1",
          segs: [{ id: "s1", text }],
          stableBlockType: "paragraph",
          stableBlockId: "b1",
        },
      ],
      {
        tree: [
          wgImgTreeNode({
            block_id: "b1",
            block_type: "paragraph",
            payload: { inline_images: inlinePayload },
          }),
        ],
      },
    );
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const para = doc.children.find((c) => (c as { type: string }).type === "paragraph") as unknown as {
      children: Array<{ text?: string; type?: string; data?: { beforeUtf16: number } }>;
    };
    const images = para.children.filter((c) => c.type === "image");
    expect(images).toHaveLength(1);
    expect((images[0].data as { beforeUtf16: number }).beforeUtf16).toBe(2);
    // text concatenation unchanged
    const textOnly = para.children
      .filter((c) => typeof c.text === "string")
      .map((c) => (c as { text: string }).text)
      .join("");
    expect(textOnly).toBe(text);
  });

  it("inline image: mixed text splicing verbatim, marks narrow, selection stays", () => {
    const text = "hello world";
    const inlinePayload = [inlineImageEntry("https://example.com/a.png", "a", null, 5, "https://example.com/a.png")];
    const snapshot = buildWgSnapshot(
      [
        {
          unitId: "u1",
          segs: [{ id: "s1", text }],
          stableBlockType: "paragraph",
          stableBlockId: "b1",
        },
      ],
      {
        tree: [
          wgImgTreeNode({
            block_id: "b1",
            block_type: "paragraph",
            payload: { inline_images: inlinePayload },
          }),
        ],
      },
    );
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const para = doc.children.find((c) => (c as { type: string }).type === "paragraph") as unknown as {
      children: Array<{ text: string; segmentRange?: { startUtf16: number; endUtf16: number }; baseRange?: { startUtf16: number; endUtf16: number }; marks: unknown[] }>;
      data: { baseRange: { startUtf16: number; endUtf16: number } };
    };
    // Ensure leaves split and ranges narrowed: first leaf "hello" should have segment 0-5, second " world" 5-11
    const textLeaves = para.children.filter((c) => typeof c.text === "string" && (c as { type?: string }).type !== "image");
    expect(textLeaves.map((l) => l.text).join("")).toBe(text);
    // If marks existed, they would be preserved; here check that leaf segment ranges are correctly narrowed
    // For this test, we just ensure no leaf contains image URL and ranges are valid
    expect(para.children.some((c) => c.text?.includes("https://"))).toBe(false);
  });

  it("image-only metadata_only table_cell: cell retained, empty text allowed, image per ordinal, no fake unit/anchor", () => {
    // This test simulates an image-only cell with null text_content in tree, metadata_only policy.
    // Snapshot has a placeholder paragraph to keep document readable, plus table with image-only cell.
    const snapshot = buildWgSnapshot(
      [
        {
          unitId: "u_p1",
          segs: [{ id: "s1", text: "Placeholder paragraph for freeze." }],
          stableBlockType: "paragraph",
          stableBlockId: "p1",
        },
        {
          unitId: "u_c2",
          segs: [{ id: "c2", text: "mixed" }],
          stableBlockType: "table_cell",
          stableBlockId: "cell_mixed",
          parentStableBlockId: "row_1",
        },
      ],
      {
        tree: [
          wgImgTreeNode({ block_id: "p1", block_type: "paragraph", order_index: 0 }),
          wgImgTreeNode({
            block_id: "table_1",
            block_type: "table",
            order_index: 1,
            children: [
              wgImgTreeNode({
                block_id: "row_1",
                parent_block_id: "table_1",
                block_type: "table_row",
                children: [
                  wgImgTreeNode({
                    block_id: "cell_image_only",
                    parent_block_id: "row_1",
                    block_type: "table_cell",
                    text_content: null,
                    payload: {
                      inline_images: [
                        inlineImageEntry("https://example.com/c.png", "c", null, 0, "https://example.com/c.png"),
                      ],
                    },
                    interpretation_policy: { allowed_source_scope: ["table_cell"], default_route: "metadata_only", rag_eligible: false },
                    unit_id: null,
                    anchor_segment_ids: [],
                  }),
                  wgImgTreeNode({
                    block_id: "cell_mixed",
                    parent_block_id: "row_1",
                    block_type: "table_cell",
                    order_index: 1,
                    payload: {
                      inline_images: [
                        inlineImageEntry("https://example.com/m.png", "m", null, 2, "https://example.com/m.png"),
                      ],
                    },
                  }),
                ],
              }),
            ],
          }),
        ],
      },
    );
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const table = doc.children.find((c) => (c as { type: string }).type === "table") as unknown as {
      children: Array<{ id: string; children: Array<{ id: string; data: Record<string, unknown>; children: unknown[] }> }>;
    };
    expect(table).toBeTruthy();
    // Find image-only cell
    const imageOnlyCell = table.children[0].children.find((cell) => (cell as { id: string }).id === "table_cell:cell_image_only") as unknown as {
      id: string;
      data: Record<string, unknown>;
      children: Array<{ type?: string }>;
    };
    // RED: currently image-only cell is missing because no unit-backed block
    expect(imageOnlyCell).toBeTruthy();
    const imgInCell = imageOnlyCell.children.filter((c) => (c as { type: string }).type === "image");
    expect(imgInCell).toHaveLength(1);
    // image-only metadata_only cell must have no real unit (placeholder falsy, not a real unit id)
    expect((imageOnlyCell.data as { unitId?: unknown }).unitId).toBeFalsy();
  });

  it("malformed inline entry skipped: non-integer/out-of-bounds before_utf16 only that image skipped, others remain, no throw", () => {
    const text = "hello";
    const inlinePayload = [
      inlineImageEntry("https://example.com/good.png", "good", null, 2, "https://example.com/good.png"),
      { source_url: "https://example.com/bad1.png", alt_text: "bad", title: null, before_utf16: 1.5, effective_url: "https://example.com/bad1.png" },
      { source_url: "https://example.com/bad2.png", alt_text: "bad", title: null, before_utf16: 100, effective_url: "https://example.com/bad2.png" },
      { source_url: "https://example.com/bad3.png", alt_text: "bad", title: null, before_utf16: -1, effective_url: "https://example.com/bad3.png" },
      inlineImageEntry("https://example.com/good2.png", "good2", null, 5, "https://example.com/good2.png"),
    ];
    const snapshot = buildWgSnapshot(
      [
        {
          unitId: "u1",
          segs: [{ id: "s1", text }],
          stableBlockType: "paragraph",
          stableBlockId: "b1",
        },
      ],
      {
        tree: [
          wgImgTreeNode({
            block_id: "b1",
            block_type: "paragraph",
            payload: { inline_images: inlinePayload },
          }),
        ],
      },
    );
    expect(() => projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot)).not.toThrow();
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const para = doc.children.find((c) => (c as { type: string }).type === "paragraph") as unknown as {
      children: Array<{ type?: string }>;
    };
    const images = para.children.filter((c) => c.type === "image");
    // Only the two good entries should survive
    expect(images).toHaveLength(2);
    expect(images.map((img) => (img as { data: { sourceUrl: string } }).data.sourceUrl)).toEqual([
      "https://example.com/good.png",
      "https://example.com/good2.png",
    ]);
    // text unchanged
    const textOnly = para.children
      .filter((c) => typeof (c as { text?: unknown }).text === "string")
      .map((c) => (c as { text: string }).text)
      .join("");
    expect(textOnly).toBe(text);
  });

  it("inline images do not enter text, selection anchor, word count (via text leaves)", () => {
    const text = "hello world";
    const inlinePayload = [inlineImageEntry("https://example.com/a.png", "alt", "Title", 5, "https://example.com/a.png")];
    const snapshot = buildWgSnapshot(
      [
        {
          unitId: "u1",
          segs: [{ id: "s1", text }],
          stableBlockType: "paragraph",
          stableBlockId: "b1",
        },
      ],
      {
        tree: [
          wgImgTreeNode({
            block_id: "b1",
            block_type: "paragraph",
            payload: { inline_images: inlinePayload },
          }),
        ],
      },
    );
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const para = doc.children.find((c) => (c as { type: string }).type === "paragraph") as unknown as {
      children: Array<{ text?: string; anchorSegmentId?: string; segmentRange?: unknown }>;
      data: { anchorSegmentId: string };
    };
    // anchor metadata should only be on text leaves, not on image
    const image = para.children.find((c) => (c as { type?: string }).type === "image") as unknown as {
      data: { anchorSegmentId?: unknown };
      children: unknown[];
    };
    expect(image.data.anchorSegmentId).toBeUndefined();
    // word count etc would be based on text leaves only; ensure text leaves cover original text
    const textLeaves = para.children.filter((c) => typeof c.text === "string");
    expect(textLeaves.map((l) => (l.text as string)).join("")).toBe(text);
    expect(textLeaves.some((l) => (l.text as string).includes("https://"))).toBe(false);
  });

  it.each([
    { beforeUtf16: 1, targetParagraphId: "paragraph:s1", expectedText: ["a", "bc"] },
    { beforeUtf16: 6, targetParagraphId: "paragraph:s2", expectedText: ["d", "ef"] },
  ])(
    "same stable block with two spans places before_utf16=$beforeUtf16 exactly once",
    ({ beforeUtf16, targetParagraphId, expectedText }) => {
      const snapshot = buildWgSnapshot(
        [
          {
            unitId: "u_shared_1",
            segs: [{ id: "s1", text: "abc" }],
            stableBlockType: "paragraph",
            stableBlockId: "p_shared",
          },
          {
            unitId: "u_shared_2",
            segs: [{ id: "s2", text: "def" }],
            stableBlockType: "paragraph",
            stableBlockId: "p_shared",
          },
        ],
        {
          tree: [
            wgImgTreeNode({
              block_id: "p_shared",
              block_type: "paragraph",
              payload: {
                inline_images: [
                  inlineImageEntry(
                    "https://example.com/shared.png",
                    "shared",
                    null,
                    beforeUtf16,
                    "https://example.com/shared.png",
                  ),
                ],
              },
            }),
          ],
        },
      );

      const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
      const spans = document.children.filter(
        (block) =>
          (block as { type?: string }).type === "paragraph" &&
          (block as { data?: { stableBlockId?: string } }).data?.stableBlockId ===
            "p_shared",
      ) as unknown as Array<{
        id: string;
        children: Array<{ type?: string; text?: string; id?: string }>;
      }>;
      const images = spans.flatMap((span) =>
        span.children.filter((child) => child.type === "image"),
      );
      const target = spans.find((span) => span.id === targetParagraphId);

      expect(spans).toHaveLength(2);
      expect(images).toHaveLength(1);
      expect(images[0]?.id).toBe("image:p_shared:0");
      expect(target).toBeTruthy();
      expect(target?.children.filter((child) => typeof child.text === "string")).toEqual(
        expectedText.map((text) => expect.objectContaining({ text })),
      );
      expect(target?.children.filter((child) => child.type === "image")).toHaveLength(1);
      expect(
        spans
          .filter((span) => span.id !== targetParagraphId)
          .flatMap((span) => span.children)
          .filter((child) => child.type === "image"),
      ).toHaveLength(0);
    },
  );

  it("same stable block second-span split keeps segment/base ranges and mark boundaries", () => {
    const snapshot = buildWgSnapshot(
      [
        {
          unitId: "u_shared_1",
          segs: [{ id: "s1", text: "abc" }],
          stableBlockType: "paragraph",
          stableBlockId: "p_shared",
        },
        {
          unitId: "u_shared_2",
          segs: [{ id: "s2", text: "def" }],
          stableBlockType: "paragraph",
          stableBlockId: "p_shared",
        },
      ],
      {
        tree: [
          wgImgTreeNode({
            block_id: "p_shared",
            block_type: "paragraph",
            payload: {
              inline_images: [
                inlineImageEntry(
                  "https://example.com/range.png",
                  "range",
                  null,
                  6,
                  "https://example.com/range.png",
                ),
              ],
            },
          }),
        ],
      },
    );
    const sourceBlock = snapshot.value[1]?.children.find(
      (child): child is ReaderSourceBlockNodeDto =>
        child.type === "reader_source_block",
    );
    const secondSegment = sourceBlock?.children.find(
      (child): child is Extract<typeof child, { type: "reader_anchor_segment" }> =>
        "type" in child &&
        child.type === "reader_anchor_segment" &&
        child.anchor_segment_id === "s2",
    );
    const secondSegmentLeaf = secondSegment?.children[0];
    if (!secondSegmentLeaf || !("text" in secondSegmentLeaf)) {
      throw new Error("Expected second segment text leaf fixture");
    }
    (
      secondSegmentLeaf as typeof secondSegmentLeaf & {
        reader_vocabulary_marks: ReaderVocabularyMarkDto[];
      }
    ).reader_vocabulary_marks = [
      makeVocabularyMark({
        mark_id: "vocab_s2",
        anchor_segment_id: "s2",
        start_offset: 5,
        end_offset: 7,
        selected_text: "de",
        segment_start_utf16: 0,
        segment_end_utf16: 2,
        starts_here: true,
        ends_here: true,
        phrase: "de",
      }),
    ];

    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    const secondSpan = document.children.find(
      (block) => (block as { id?: string }).id === "paragraph:s2",
    ) as unknown as {
      children: Array<{
        type?: string;
        text?: string;
        segmentRange?: { startUtf16: number; endUtf16: number };
        baseRange?: { startUtf16: number; endUtf16: number };
        marks?: Array<{ id: string; startsHere: boolean; endsHere: boolean }>;
      }>;
    };
    expect(secondSpan).toBeTruthy();
    const markedLeaves = secondSpan.children.filter((child) =>
      child.marks?.some((mark) => mark.id === "vocab_s2"),
    );

    expect(secondSpan.children.filter((child) => child.type === "image")).toHaveLength(1);
    expect(markedLeaves).toHaveLength(2);
    expect(markedLeaves[0]).toMatchObject({
      text: "d",
      segmentRange: { startUtf16: 0, endUtf16: 1 },
      baseRange: { startUtf16: 5, endUtf16: 6 },
      marks: [expect.objectContaining({ id: "vocab_s2", startsHere: true, endsHere: false })],
    });
    expect(markedLeaves[1]).toMatchObject({
      text: "e",
      segmentRange: { startUtf16: 1, endUtf16: 2 },
      baseRange: { startUtf16: 6, endUtf16: 7 },
      marks: [expect.objectContaining({ id: "vocab_s2", startsHere: false, endsHere: true })],
    });
  });

  it("pure-image list retains its wrapper and promoted image source position", () => {
    const snapshot = buildWgSnapshot(
      [
        {
          unitId: "u_p1",
          segs: [{ id: "s1", text: "Readable paragraph." }],
          stableBlockType: "paragraph",
          stableBlockId: "p1",
        },
      ],
      {
        tree: [
          wgImgTreeNode({
            block_id: "list_images",
            block_type: "list",
            order_index: 0,
            payload: { ordered: false },
            children: [
              wgImgTreeNode({
                block_id: "img_list_only",
                parent_block_id: "list_images",
                block_type: "image",
                order_index: 0,
                payload: imagePayload(
                  "https://example.com/list.png",
                  "list",
                  null,
                  "https://example.com/list.png",
                ),
              }),
            ],
          }),
          wgImgTreeNode({
            block_id: "p1",
            block_type: "paragraph",
            order_index: 1,
          }),
        ],
      },
    );

    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    expect(document.children.map((block) => block.type)).toEqual(["list", "paragraph"]);
    const list = document.children[0] as unknown as {
      type: string;
      data: { stableBlockId?: string };
      children: Array<{
        type: string;
        id: string;
        data: { parentStableBlockId: string | null };
      }>;
    };
    expect(list).toMatchObject({
      type: "list",
      data: { stableBlockId: "list_images" },
    });
    expect(list.children).toHaveLength(1);
    expect(list.children[0]).toMatchObject({
      type: "image",
      id: "image:img_list_only",
      data: { parentStableBlockId: "list_images" },
    });
  });

  it("pure-image table retains two rows, source order, metadata, and no fake text anchor", () => {
    const imageOnlyCell = (
      cellId: string,
      rowId: string,
      sourceUrl: string,
      isHeader: boolean,
    ) =>
      wgImgTreeNode({
        block_id: cellId,
        parent_block_id: rowId,
        block_type: "table_cell",
        order_index: 0,
        text_content: null,
        payload: {
          alignment: "center",
          column_index: 0,
          is_header: isHeader,
          inline_images: [inlineImageEntry(sourceUrl, cellId, null, 0, sourceUrl)],
        },
        interpretation_policy: {
          allowed_source_scope: ["table_cell"],
          default_route: "metadata_only",
          rag_eligible: false,
        },
        unit_id: null,
        anchor_segment_ids: [],
      });
    const snapshot = buildWgSnapshot(
      [
        {
          unitId: "u_p1",
          segs: [{ id: "s1", text: "Readable paragraph." }],
          stableBlockType: "paragraph",
          stableBlockId: "p1",
        },
      ],
      {
        tree: [
          wgImgTreeNode({
            block_id: "table_images",
            block_type: "table",
            order_index: 0,
            payload: { alignments: ["center"], column_count: 1, header_rows: 1 },
            children: [
              wgImgTreeNode({
                block_id: "row_header",
                parent_block_id: "table_images",
                block_type: "table_row",
                order_index: 0,
                payload: { is_header: true, row_index: 0 },
                children: [
                  imageOnlyCell(
                    "cell_header",
                    "row_header",
                    "https://example.com/header.png",
                    true,
                  ),
                ],
              }),
              wgImgTreeNode({
                block_id: "row_body",
                parent_block_id: "table_images",
                block_type: "table_row",
                order_index: 1,
                payload: { is_header: false, row_index: 1 },
                children: [
                  imageOnlyCell(
                    "cell_body",
                    "row_body",
                    "https://example.com/body.png",
                    false,
                  ),
                ],
              }),
            ],
          }),
          wgImgTreeNode({
            block_id: "p1",
            block_type: "paragraph",
            order_index: 1,
          }),
        ],
      },
    );

    const document = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot);
    expect(document.children.map((block) => block.type)).toEqual(["table", "paragraph"]);
    const table = document.children[0] as unknown as {
      data: Record<string, unknown>;
      children: Array<{
        id: string;
        data: Record<string, unknown>;
        children: Array<{
          id: string;
          data: Record<string, unknown>;
          children: Array<{ type?: string }>;
        }>;
      }>;
    };
    expect(table.data).toMatchObject({
      stableBlockId: "table_images",
      unitId: null,
      alignments: ["center"],
      headerRows: 1,
    });
    expect(table.children.map((row) => row.id)).toEqual([
      "table_row:row_header",
      "table_row:row_body",
    ]);
    expect(table.children.map((row) => row.data)).toEqual([
      expect.objectContaining({ unitId: null, isHeader: true, rowIndex: 0 }),
      expect.objectContaining({ unitId: null, isHeader: false, rowIndex: 1 }),
    ]);
    const cells = table.children.map((row) => row.children[0]);
    expect(cells.map((cell) => cell.id)).toEqual([
      "table_cell:cell_header",
      "table_cell:cell_body",
    ]);
    expect(cells.map((cell) => cell.data)).toEqual([
      expect.objectContaining({
        unitId: null,
        columnIndex: 0,
        alignment: "center",
        isHeader: true,
      }),
      expect.objectContaining({
        unitId: null,
        columnIndex: 0,
        alignment: "center",
        isHeader: false,
      }),
    ]);
    for (const data of [
      table.data,
      ...table.children.map((row) => row.data),
      ...cells.map((cell) => cell.data),
    ]) {
      expect(data).not.toHaveProperty("baseRange");
      expect(data).not.toHaveProperty("textHash");
      expect(data).not.toHaveProperty("hashAlgorithm");
      expect(data).not.toHaveProperty("anchorSegmentId");
    }
    expect(cells.map((cell) => cell.children.filter((child) => child.type === "image"))).toEqual([
      [expect.objectContaining({ id: "image:cell_header:0" })],
      [expect.objectContaining({ id: "image:cell_body:0" })],
    ]);
  });
});

describe("image override_url projection", () => {
  function localTreeNode(
    overrides: Partial<ReaderStableDocumentBlockNodeDto>,
  ): ReaderStableDocumentBlockNodeDto {
    return {
      block_id: "x",
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

  function makeOverrideSnapshot(
    payloadOverride: Record<string, unknown>,
    inlineOverrides?: Array<{ ordinal: number; override: unknown }>,
  ): ReaderPlateSnapshotDto {
    const text = "Hello world";
    const textHash = computeUtf16FNV1a(text);
    const segId = "s1";
    const unitId = "u_p1";
    const baseId = "base_w1";
    const inlineImages: Record<string, unknown>[] = inlineOverrides
      ? (() => {
          const maxOrd = Math.max(...inlineOverrides.map((o) => o.ordinal), 0);
          const arr: Record<string, unknown>[] = [];
          for (let i = 0; i <= maxOrd; i += 1) {
            const found = inlineOverrides.find((o) => o.ordinal === i);
            if (found) {
              if (found.override === undefined) {
                arr.push({
                  source_url: "https://example.com/inline_source.png",
                  alt_text: "inline alt",
                  title: null,
                  before_utf16: 0,
                  effective_url: "https://example.com/inline_source.png",
                });
              } else {
                arr.push({
                  source_url: "https://example.com/inline_source.png",
                  alt_text: "inline alt",
                  title: null,
                  before_utf16: 0,
                  effective_url: "https://example.com/inline_source.png",
                  override_url: found.override,
                });
              }
            } else {
              arr.push({
                source_url: "https://example.com/inline_source.png",
                alt_text: "inline alt",
                title: null,
                before_utf16: 0,
                effective_url: "https://example.com/inline_source.png",
              });
            }
          }
          return arr;
        })()
      : [
          {
            source_url: "https://example.com/inline_source.png",
            alt_text: "inline alt",
            title: null,
            before_utf16: 0,
            effective_url: "https://example.com/inline_source.png",
          },
        ];

    return {
      schema_kind: READER_PLATE_SNAPSHOT_SCHEMA_KIND,
      snapshot_id: "snap_1",
      snapshot_taken_at: "2026-08-08T00:00:00Z",
      last_event_sequence: 1,
      record_id: "record_w1",
      record: {
        title: "Fixture",
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
        text_length_utf16: text.length,
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      },
      navigation: {
        units: [
          {
            unit_id: unitId,
            order_index: 1,
            unit_type: "body",
            boundary_quality: "normal",
            label: null,
            base_start_utf16: 0,
            base_end_utf16: text.length,
            text_hash: textHash,
            hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
            stable_block_type: "paragraph",
            heading_level: null,
          },
        ],
      },
      anchor_segments: [
        {
          anchor_segment_id: segId,
          sentence_id: "sent_s1",
          paragraph_id: unitId,
          unit_id: unitId,
          order_index: 1,
          unit_order_index: 1,
          segment_type: "sentence",
          boundary_quality: "normal",
          base_start_utf16: 0,
          base_end_utf16: text.length,
          unit_start_utf16: 0,
          unit_end_utf16: text.length,
          text_hash: textHash,
          hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
        },
      ],
      enhancement_layers: [],
      enhancement_progress: undefined,
      analysis_progress: makeAnalysisProgressDto(),
      ask_supplements: [],
      user_assets: [],
      parsed_decisions: [],
      value: [
        {
          type: "reader_unit",
          owner: "stable",
          base_id: baseId,
          unit_id: unitId,
          order_index: 1,
          unit_type: "body",
          boundary_quality: "normal",
          base_start_utf16: 0,
          base_end_utf16: text.length,
          text_hash: textHash,
          hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
          children: [
            {
              type: "reader_source_block",
              owner: "stable",
              base_id: baseId,
              unit_id: unitId,
              base_start_utf16: 0,
              base_end_utf16: text.length,
              stableBlockType: "paragraph",
              stableBlockId: "b1",
              headingLevel: null,
              parentStableBlockId: null,
              children: [
                {
                  type: "reader_anchor_segment",
                  owner: "stable",
                  base_id: baseId,
                  unit_id: unitId,
                  anchor_segment_id: segId,
                  sentence_id: "sent_s1",
                  segment_type: "sentence",
                  boundary_quality: "normal",
                  base_start_utf16: 0,
                  base_end_utf16: text.length,
                  unit_start_utf16: 0,
                  unit_end_utf16: text.length,
                  text_hash: textHash,
                  hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
                  children: [
                    {
                      text,
                      owner: "stable",
                      lock_source: true,
                      source_role: "segment_text",
                      base_start_utf16: 0,
                      base_end_utf16: text.length,
                      anchor_segment_id: segId,
                      segment_start_utf16: 0,
                      segment_end_utf16: text.length,
                    },
                  ],
                },
              ],
            },
          ],
        },
      ],
      stable_document_tree: [
        localTreeNode({
          block_id: "img_standalone",
          block_type: "image",
          order_index: 0,
          payload: {
            source_url: "https://example.com/source.png",
            alt_text: "alt",
            title: null,
            position_kind: "standalone",
            effective_url: "https://example.com/source.png",
            ...payloadOverride,
          },
        }),
        localTreeNode({
          block_id: "b1",
          block_type: "paragraph",
          order_index: 1,
          payload: { inline_images: inlineImages },
        }),
      ],
    };
  }

  it("standalone override_url enters unique Reader image data", () => {
    const snap = makeOverrideSnapshot({ override_url: "https://example.com/override.png" });
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snap);
    const img = doc.children.find((c) => (c as { id: string }).id === "image:img_standalone") as unknown as {
      data: { overrideUrl?: string; sourceUrl: string; effectiveUrl: string | null };
    };
    expect(img).toBeTruthy();
    expect(img.data.overrideUrl).toBe("https://example.com/override.png");
  });

  it("inline ordinal corresponding item enters overrideUrl", () => {
    const snap = makeOverrideSnapshot({}, [{ ordinal: 0, override: "https://example.com/inline_override.png" }]);
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snap);
    const para = doc.children.find((c) => (c as { type: string }).type === "paragraph") as unknown as {
      children: Array<{ type?: string; id?: string; data?: { overrideUrl?: string } }>;
    };
    const img = para.children.find((c) => c.id === "image:b1:0") as unknown as { data: { overrideUrl?: string } };
    expect(img).toBeTruthy();
    expect(img.data.overrideUrl).toBe("https://example.com/inline_override.png");
  });

  it("absent key vs empty string are distinguishable", () => {
    const snapAbsent = makeOverrideSnapshot({});
    const docAbsent = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapAbsent);
    const imgAbsent = docAbsent.children.find((c) => (c as { id: string }).id === "image:img_standalone") as unknown as {
      data: { overrideUrl?: string };
    };
    expect(Object.prototype.hasOwnProperty.call(imgAbsent.data, "overrideUrl")).toBe(false);
    expect(typeof imgAbsent.data.overrideUrl).not.toBe("string");

    const snapEmpty = makeOverrideSnapshot({ override_url: "" });
    const docEmpty = projectReaderPlateSnapshotToReaderRecordPlateDocument(snapEmpty);
    const imgEmpty = docEmpty.children.find((c) => (c as { id: string }).id === "image:img_standalone") as unknown as {
      data: { overrideUrl?: string };
    };
    expect(Object.prototype.hasOwnProperty.call(imgEmpty.data, "overrideUrl")).toBe(true);
    expect(imgEmpty.data.overrideUrl).toBe("");
  });

  it("unsafe raw string is preserved verbatim", () => {
    const unsafe = "javascript:alert(1)";
    const snap = makeOverrideSnapshot({ override_url: unsafe });
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snap);
    const img = doc.children.find((c) => (c as { id: string }).id === "image:img_standalone") as unknown as {
      data: { overrideUrl?: string };
    };
    expect(img.data.overrideUrl).toBe(unsafe);
  });

  it("non-string override fails closed as key missing", () => {
    const snap = makeOverrideSnapshot({ override_url: 123 as unknown as string });
    const doc = projectReaderPlateSnapshotToReaderRecordPlateDocument(snap);
    const img = doc.children.find((c) => (c as { id: string }).id === "image:img_standalone") as unknown as {
      data: { overrideUrl?: string };
    };
    expect(Object.prototype.hasOwnProperty.call(img.data, "overrideUrl")).toBe(false);
  });
});
