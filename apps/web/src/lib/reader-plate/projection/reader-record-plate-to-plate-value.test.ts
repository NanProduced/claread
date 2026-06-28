import { describe, expect, it } from "vitest";

import type {
  ReaderRecordPlateBlockquoteBlock,
  ReaderRecordPlateCalloutBlock,
  ReaderRecordPlateDocument,
  ReaderRecordPlateGrammarMark,
  ReaderRecordPlateParagraphBlock,
  ReaderRecordPlateSentenceAnalysisBlock,
  ReaderRecordPlateTextLeaf,
  ReaderRecordPlateTranslationTextLeaf,
  ReaderRecordPlateUserHighlightMark,
  ReaderRecordPlateUserNoteMark,
  ReaderRecordPlateVocabularyMark,
} from "./reader-record-plate-document";
import {
  marksToPlateProps,
  projectReaderRecordPlateToPlateValue,
  READER_BLOCKQUOTE_TYPE,
  READER_CALLOUT_TYPE,
  READER_PARAGRAPH_TYPE,
  READER_SENTENCE_ANALYSIS_CHUNK_TYPE,
  READER_SENTENCE_ANALYSIS_CHUNKS_TYPE,
  READER_SENTENCE_ANALYSIS_TYPE,
  textLeafToPlateTextNode,
  translationLeafToPlateTextNode,
} from "./reader-record-plate-to-plate-value";

// --- Mark fixtures ---

function makeVocabularyMark(
  overrides: Partial<ReaderRecordPlateVocabularyMark> = {},
): ReaderRecordPlateVocabularyMark {
  return {
    id: "vocab_mark_1",
    layerId: "layer_vocab_1",
    kind: "phrase_gloss",
    owner: "system_ai",
    anchor: {
      anchorType: "text_range",
      baseId: "base_1",
      unitId: "unit_1",
      anchorSegmentId: "seg_1",
      sentenceId: "sent_1",
      segmentType: "sentence",
      offsetUnit: "utf16",
      unitStartOffset: 0,
      unitEndOffset: 20,
      segmentStartOffset: 0,
      segmentEndOffset: 20,
      selectedText: "Institutional memory",
      textHash: "hash_1",
      hashAlgorithm: "fnv1a32-utf16",
    },
    startsHere: true,
    endsHere: true,
    vocabulary: {
      itemType: "phrase_gloss",
      phrase: "Institutional memory",
      phraseType: "collocation",
      gloss: "制度记忆",
      example: "Institutional memory shapes future choices.",
    },
    ...overrides,
  };
}

function makeGrammarMark(
  overrides: Partial<ReaderRecordPlateGrammarMark> = {},
): ReaderRecordPlateGrammarMark {
  return {
    id: "grammar_mark_1",
    layerId: "layer_grammar_1",
    kind: "grammar_note",
    owner: "system_ai",
    anchor: {
      anchorType: "text_range",
      baseId: "base_1",
      unitId: "unit_1",
      anchorSegmentId: "seg_1",
      sentenceId: "sent_1",
      segmentType: "sentence",
      offsetUnit: "utf16",
      unitStartOffset: 21,
      unitEndOffset: 27,
      segmentStartOffset: 21,
      segmentEndOffset: 27,
      selectedText: "shapes",
      textHash: "hash_2",
      hashAlgorithm: "fnv1a32-utf16",
    },
    startsHere: true,
    endsHere: true,
    itemId: "grammar_item_1",
    spanIndex: 0,
    spanCount: 1,
    showCue: true,
    grammarPoint: "predicate verb",
    pattern: "subject + verb + object",
    note: "shapes acts as the predicate verb.",
    ...overrides,
  };
}

function makeUserHighlightMark(
  overrides: Partial<ReaderRecordPlateUserHighlightMark> = {},
): ReaderRecordPlateUserHighlightMark {
  return {
    id: "user_highlight_1",
    kind: "user_highlight",
    owner: "user",
    assetId: "asset_1",
    assetType: "highlight",
    anchor: {
      anchorType: "text_range",
      baseId: "base_1",
      unitId: "unit_1",
      anchorSegmentId: "seg_1",
      sentenceId: "sent_1",
      segmentType: "sentence",
      offsetUnit: "utf16",
      unitStartOffset: 0,
      unitEndOffset: 20,
      segmentStartOffset: 0,
      segmentEndOffset: 20,
      selectedText: "Institutional memory",
      textHash: "hash_3",
      hashAlgorithm: "fnv1a32-utf16",
    },
    updatedAt: "2026-06-24T01:00:00Z",
    ...overrides,
  };
}

function makeUserNoteMark(
  overrides: Partial<ReaderRecordPlateUserNoteMark> = {},
): ReaderRecordPlateUserNoteMark {
  return {
    id: "user_note_1",
    kind: "user_note",
    owner: "user",
    assetId: "asset_2",
    assetType: "reader_note",
    noteText: "这是一个测试笔记",
    anchor: {
      anchorType: "text_range",
      baseId: "base_1",
      unitId: "unit_1",
      anchorSegmentId: "seg_1",
      sentenceId: "sent_1",
      segmentType: "sentence",
      offsetUnit: "utf16",
      unitStartOffset: 0,
      unitEndOffset: 20,
      segmentStartOffset: 0,
      segmentEndOffset: 20,
      selectedText: "Institutional memory",
      textHash: "hash_4",
      hashAlgorithm: "fnv1a32-utf16",
    },
    updatedAt: "2026-06-24T01:00:00Z",
    ...overrides,
  };
}

// --- Leaf fixtures ---

function makeTextLeaf(
  overrides: Partial<ReaderRecordPlateTextLeaf> = {},
): ReaderRecordPlateTextLeaf {
  return {
    text: "Institutional memory",
    owner: "stable",
    lockSource: true,
    sourceRole: "segment_text",
    baseRange: { startUtf16: 0, endUtf16: 20 },
    anchorSegmentId: "seg_1",
    segmentRange: { startUtf16: 0, endUtf16: 20 },
    marks: [],
    ...overrides,
  };
}

function makeTranslationLeaf(
  overrides: Partial<ReaderRecordPlateTranslationTextLeaf> = {},
): ReaderRecordPlateTranslationTextLeaf {
  return {
    text: "制度记忆会塑造政策选择。",
    owner: "system_ai",
    sourceRole: "unit_translation_text",
    ...overrides,
  };
}

// --- Block fixtures ---

function makeParagraphBlock(
  overrides: Partial<ReaderRecordPlateParagraphBlock> = {},
): ReaderRecordPlateParagraphBlock {
  return {
    type: "paragraph",
    id: "paragraph:seg_1",
    children: [makeTextLeaf()],
    data: {
      anchorSegmentId: "seg_1",
      sentenceId: "sent_1",
      unitId: "unit_1",
      baseId: "base_1",
      baseRange: { startUtf16: 0, endUtf16: 20 },
      unitRange: { startUtf16: 0, endUtf16: 20 },
      textHash: "seg_1_hash",
      hashAlgorithm: "fnv1a32-utf16",
      segmentType: "sentence",
      boundaryQuality: "normal",
    },
    ...overrides,
  };
}

function makeBlockquoteBlock(
  overrides: Partial<ReaderRecordPlateBlockquoteBlock> = {},
): ReaderRecordPlateBlockquoteBlock {
  return {
    type: "blockquote",
    id: "blockquote:layer_translation_1:unit_1",
    children: [makeTranslationLeaf()],
    data: {
      unitId: "unit_1",
      layerId: "layer_translation_1",
      layerVersion: 1,
      targetLanguage: "zh",
      confidence: "normal",
      notes: [],
    },
    ...overrides,
  };
}

function makeCalloutBlock(
  overrides: Partial<ReaderRecordPlateCalloutBlock> = {},
): ReaderRecordPlateCalloutBlock {
  return {
    type: "callout",
    id: "callout:grammar:grammar_item_1",
    variant: "grammar",
    icon: "📖",
    children: [{ type: "p", children: [{ text: "shapes acts as the predicate verb." }] }],
    data: {
      anchorSegmentId: "seg_1",
      unitId: "unit_1",
      layerId: "layer_grammar_1",
      itemId: "grammar_item_1",
      grammarPoint: "predicate verb",
      pattern: "subject + verb + object",
      note: "shapes acts as the predicate verb.",
    },
    ...overrides,
  };
}

function makeSentenceAnalysisBlock(
  overrides: Partial<ReaderRecordPlateSentenceAnalysisBlock> = {},
): ReaderRecordPlateSentenceAnalysisBlock {
  return {
    type: "sentence_analysis",
    id: "sentence_analysis:analysis_seg_1",
    icon: "🔍",
    children: [{ type: "p", children: [{ text: "Institutional memory is the subject." }] }],
    data: {
      anchorSegmentId: "seg_1",
      unitId: "unit_1",
      layerId: "layer_sentence_analysis_1",
      analysisId: "analysis_seg_1",
      label: "subject driving predicate",
      analysis: "Institutional memory is the subject.",
      chunks: [{ order: 1, label: "subject", text: "Institutional memory" }],
    },
    ...overrides,
  };
}

function makeDocument(
  children: ReaderRecordPlateDocument["children"],
): ReaderRecordPlateDocument {
  return {
    type: "reader_record_plate_document",
    schemaVersion: "reader-record-plate-document/v1",
    record: {
      recordId: "record_1",
      title: "测试文章",
      generation: 1,
      productState: "readable_enhancing",
      readinessState: "article_ready",
    },
    snapshot: {
      snapshotId: "snapshot_1",
      snapshotTakenAt: "2026-06-24T01:00:00Z",
      lastEventSequence: 1,
    },
    base: {
      baseId: "base_1",
      contentSha256: "sha256_1",
      textLengthUtf16: 100,
      hashAlgorithm: "fnv1a32-utf16",
    },
    progress: {
      overallStatus: "ready",
      layers: [],
    },
    children,
  };
}

// --- Tests ---

describe("marksToPlateProps", () => {
  it("converts vocabulary mark (startsHere=true) with data", () => {
    const mark = makeVocabularyMark({ startsHere: true });
    const props = marksToPlateProps([mark]);

    expect(props.vocabulary).toBe(true);
    expect(props.vocabulary_data).toEqual(mark);
  });

  it("converts vocabulary mark (startsHere=false) with data for continuation styling", () => {
    const mark = makeVocabularyMark({ startsHere: false });
    const props = marksToPlateProps([mark]);

    expect(props.vocabulary).toBe(true);
    expect(props.vocabulary_data).toEqual(mark);
  });

  it("converts grammar mark (startsHere=true) with data", () => {
    const mark = makeGrammarMark({ startsHere: true });
    const props = marksToPlateProps([mark]);

    expect(props.grammar).toBe(true);
    expect(props.grammar_data).toEqual(mark);
  });

  it("converts grammar mark (startsHere=false) with data for continuation styling", () => {
    const mark = makeGrammarMark({ startsHere: false });
    const props = marksToPlateProps([mark]);

    expect(props.grammar).toBe(true);
    expect(props.grammar_data).toEqual(mark);
  });

  it("converts user_highlight mark (startsHere=true) with data", () => {
    const mark = makeUserHighlightMark();
    const props = marksToPlateProps([mark]);

    expect(props.user_highlight).toBe(true);
    expect(props.user_highlight_data).toEqual(mark);
  });

  it("converts user_note mark (startsHere=true) with data", () => {
    const mark = makeUserNoteMark();
    const props = marksToPlateProps([mark]);

    expect(props.user_note).toBe(true);
    expect(props.user_note_data).toEqual(mark);
  });

  it("converts multiple marks on the same leaf", () => {
    const vocabMark = makeVocabularyMark({ startsHere: true });
    const grammarMark = makeGrammarMark({ startsHere: true });
    const highlightMark = makeUserHighlightMark();
    const noteMark = makeUserNoteMark();

    const props = marksToPlateProps([
      vocabMark,
      grammarMark,
      highlightMark,
      noteMark,
    ]);

    expect(props.vocabulary).toBe(true);
    expect(props.vocabulary_data).toEqual(vocabMark);
    expect(props.grammar).toBe(true);
    expect(props.grammar_data).toEqual(grammarMark);
    expect(props.user_highlight).toBe(true);
    expect(props.user_highlight_data).toEqual(highlightMark);
    expect(props.user_note).toBe(true);
    expect(props.user_note_data).toEqual(noteMark);
  });

  it("returns empty object for empty marks array", () => {
    const props = marksToPlateProps([]);
    expect(props).toEqual({});
  });
});

describe("textLeafToPlateTextNode", () => {
  it("converts separator leaf (no marks) to plain text node", () => {
    const leaf = makeTextLeaf({ marks: [], text: "plain text" });
    const node = textLeafToPlateTextNode(leaf);

    expect(node.text).toBe("plain text");
    expect(node.vocabulary).toBeUndefined();
    expect(node.grammar).toBeUndefined();
    expect(node.user_highlight).toBeUndefined();
    expect(node.user_note).toBeUndefined();
  });

  it("converts leaf with vocabulary mark", () => {
    const mark = makeVocabularyMark({ startsHere: true });
    const leaf = makeTextLeaf({ marks: [mark], text: "Institutional" });
    const node = textLeafToPlateTextNode(leaf);

    expect(node.text).toBe("Institutional");
    expect(node.vocabulary).toBe(true);
    expect(node.vocabulary_data).toEqual(mark);
  });

  it("converts leaf with multiple marks", () => {
    const vocabMark = makeVocabularyMark({ startsHere: true });
    const grammarMark = makeGrammarMark({ startsHere: true });
    const leaf = makeTextLeaf({ marks: [vocabMark, grammarMark] });
    const node = textLeafToPlateTextNode(leaf);

    expect(node.text).toBe("Institutional memory");
    expect(node.vocabulary).toBe(true);
    expect(node.grammar).toBe(true);
    expect(node.vocabulary_data).toEqual(vocabMark);
    expect(node.grammar_data).toEqual(grammarMark);
  });

  it("preserves vocabulary and grammar data on continuation leaves split by overlapping marks", () => {
    const vocabStart = makeVocabularyMark({
      id: "vocab_split",
      startsHere: true,
      endsHere: false,
    });
    const vocabContinuation = makeVocabularyMark({
      ...vocabStart,
      startsHere: false,
      endsHere: true,
    });
    const grammarStart = makeGrammarMark({
      id: "grammar_split",
      startsHere: true,
      endsHere: false,
    });
    const grammarContinuation = makeGrammarMark({
      ...grammarStart,
      startsHere: false,
      endsHere: true,
    });
    const leafNodes = [
      textLeafToPlateTextNode(
        makeTextLeaf({ text: "Institutional ", marks: [vocabStart] }),
      ),
      textLeafToPlateTextNode(
        makeTextLeaf({ text: "memory", marks: [vocabContinuation, grammarStart] }),
      ),
      textLeafToPlateTextNode(
        makeTextLeaf({ text: " shapes", marks: [grammarContinuation] }),
      ),
    ];

    expect(leafNodes[0]?.vocabulary_data).toEqual(vocabStart);
    expect(leafNodes[1]?.vocabulary_data).toEqual(vocabContinuation);
    expect(leafNodes[1]?.grammar_data).toEqual(grammarStart);
    expect(leafNodes[2]?.grammar_data).toEqual(grammarContinuation);
  });
});

describe("translationLeafToPlateTextNode", () => {
  it("converts translation leaf with owner and sourceRole", () => {
    const leaf = makeTranslationLeaf();
    const node = translationLeafToPlateTextNode(leaf);

    expect(node.text).toBe("制度记忆会塑造政策选择。");
    expect(node.translation_owner).toBe("system_ai");
    expect(node.translation_sourceRole).toBe("unit_translation_text");
  });
});

describe("projectReaderRecordPlateToPlateValue", () => {
  it("converts paragraph block to reader_paragraph element", () => {
    const block = makeParagraphBlock();
    const value = projectReaderRecordPlateToPlateValue(makeDocument([block]));

    expect(value).toHaveLength(1);
    const element = value[0] as Record<string, unknown>;
    expect(element.type).toBe(READER_PARAGRAPH_TYPE);
    expect(element.id).toBe(block.id);
    expect(element.data).toEqual(block.data);
    expect(Array.isArray(element.children)).toBe(true);
    expect((element.children as unknown[]).length).toBe(1);
    const textNode = (element.children as unknown[])[0] as Record<string, unknown>;
    expect(textNode.text).toBe("Institutional memory");
  });

  it("converts paragraph block with marked leaf", () => {
    const vocabMark = makeVocabularyMark({ startsHere: true });
    const leaf = makeTextLeaf({ marks: [vocabMark] });
    const block = makeParagraphBlock({ children: [leaf] });
    const value = projectReaderRecordPlateToPlateValue(makeDocument([block]));

    const element = value[0] as Record<string, unknown>;
    const textNode = (element.children as unknown[])[0] as Record<string, unknown>;
    expect(textNode.vocabulary).toBe(true);
    expect(textNode.vocabulary_data).toEqual(vocabMark);
  });

  it("converts blockquote block to reader_blockquote element", () => {
    const block = makeBlockquoteBlock();
    const value = projectReaderRecordPlateToPlateValue(makeDocument([block]));

    expect(value).toHaveLength(1);
    const element = value[0] as Record<string, unknown>;
    expect(element.type).toBe(READER_BLOCKQUOTE_TYPE);
    expect(element.id).toBe(block.id);
    expect(element.data).toEqual(block.data);
    const textNode = (element.children as unknown[])[0] as Record<string, unknown>;
    expect(textNode.text).toBe("制度记忆会塑造政策选择。");
    expect(textNode.translation_owner).toBe("system_ai");
    expect(textNode.translation_sourceRole).toBe("unit_translation_text");
  });

  it("converts callout block to reader_callout element, passing children directly", () => {
    const children = [
      { type: "p", children: [{ text: "note text" }] },
    ];
    const block = makeCalloutBlock({ children });
    const value = projectReaderRecordPlateToPlateValue(makeDocument([block]));

    expect(value).toHaveLength(1);
    const element = value[0] as Record<string, unknown>;
    expect(element.type).toBe(READER_CALLOUT_TYPE);
    expect(element.id).toBe(block.id);
    expect(element.data).toEqual(block.data);
    expect(element.variant).toBe("grammar");
    expect(element.icon).toBe("📖");
    // children should be passed directly (same array reference)
    expect(element.children).toBe(children);
  });

  it("converts sentence analysis block to reader_sentence_analysis element", () => {
    const children = [
      { type: "p", children: [{ text: "analysis text" }] },
    ];
    const block = makeSentenceAnalysisBlock({ children });
    const value = projectReaderRecordPlateToPlateValue(makeDocument([block]));

    expect(value).toHaveLength(1);
    const element = value[0] as Record<string, unknown>;
    expect(element.type).toBe(READER_SENTENCE_ANALYSIS_TYPE);
    expect(element.id).toBe(block.id);
    expect(element.data).toEqual(block.data);
    expect(element.icon).toBe("🔍");
    expect(element.children).not.toBe(children);
    const projectedChildren = element.children as Array<Record<string, unknown>>;
    expect(projectedChildren[0]?.type).toBe(READER_SENTENCE_ANALYSIS_CHUNKS_TYPE);
    const chunkList = projectedChildren[0] as {
      children: Array<Record<string, unknown>>;
    };
    expect(chunkList.children[0]?.type).toBe(READER_SENTENCE_ANALYSIS_CHUNK_TYPE);
    expect(chunkList.children[0]?.data).toEqual(block.data.chunks[0]);
    expect(
      ((chunkList.children[0]?.children as Array<Record<string, unknown>>)[0]
        ?.text),
    ).toBe("Institutional memory");
    expect(projectedChildren[1]).toBe(children[0]);
  });

  it("handles empty paragraph children by filling empty text node", () => {
    const block = makeParagraphBlock({ children: [] });
    const value = projectReaderRecordPlateToPlateValue(makeDocument([block]));

    const element = value[0] as Record<string, unknown>;
    const children = element.children as unknown[];
    expect(children).toHaveLength(1);
    expect((children[0] as Record<string, unknown>).text).toBe("");
  });

  it("handles empty blockquote children by filling empty text node", () => {
    const block = makeBlockquoteBlock({ children: [] });
    const value = projectReaderRecordPlateToPlateValue(makeDocument([block]));

    const element = value[0] as Record<string, unknown>;
    const children = element.children as unknown[];
    expect(children).toHaveLength(1);
    expect((children[0] as Record<string, unknown>).text).toBe("");
  });

  it("converts multiple blocks in order", () => {
    const paragraph = makeParagraphBlock();
    const blockquote = makeBlockquoteBlock();
    const callout = makeCalloutBlock();
    const analysis = makeSentenceAnalysisBlock();
    const value = projectReaderRecordPlateToPlateValue(
      makeDocument([paragraph, blockquote, callout, analysis]),
    );

    expect(value).toHaveLength(4);
    expect((value[0] as Record<string, unknown>).type).toBe(READER_PARAGRAPH_TYPE);
    expect((value[1] as Record<string, unknown>).type).toBe(READER_BLOCKQUOTE_TYPE);
    expect((value[2] as Record<string, unknown>).type).toBe(READER_CALLOUT_TYPE);
    expect((value[3] as Record<string, unknown>).type).toBe(READER_SENTENCE_ANALYSIS_TYPE);
  });

  it("returns empty array for document with no children", () => {
    const value = projectReaderRecordPlateToPlateValue(makeDocument([]));
    expect(value).toEqual([]);
  });
});
