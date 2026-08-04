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
// R1: stable paragraph inline marks must survive snapshot → document
// projection. Before R1, `stableBlockType === "paragraph"` was NOT in
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

    const marks = paragraph.children.flatMap(
      (leaf) => leaf.inlineMarks ?? [],
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
