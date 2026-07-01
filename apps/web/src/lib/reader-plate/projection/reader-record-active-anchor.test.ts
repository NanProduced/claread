import { computeUtf16FNV1a } from "@claread/contracts";
import { describe, expect, it } from "vitest";

import type { ReaderRecordAnchorDraft } from "./reader-record-anchor-draft";
import {
  userEditorialAssetAnchorDraftForActiveAnchor,
  type UserEditorialAssetAnchorDraft,
} from "./reader-record-active-anchor";
import type {
  ReaderRecordPlateDocument,
  ReaderRecordPlateGrammarMark,
  ReaderRecordPlateTextAnchor,
  ReaderRecordPlateVocabularyMark,
} from "./reader-record-plate-document";

function textAnchor(
  overrides: Partial<ReaderRecordPlateTextAnchor> = {},
): ReaderRecordPlateTextAnchor {
  const selectedText = overrides.selectedText ?? "memory";
  return {
    anchorType: "text_range",
    baseId: "base_1",
    unitId: "unit_1",
    anchorSegmentId: "seg_1",
    sentenceId: "sent_1",
    segmentType: "sentence",
    offsetUnit: "utf16",
    unitStartOffset: 14,
    unitEndOffset: 20,
    segmentStartOffset: 14,
    segmentEndOffset: 20,
    selectedText,
    textHash: computeUtf16FNV1a(selectedText),
    hashAlgorithm: "fnv1a32-utf16",
    ...overrides,
  };
}

function makeDocument(
  overrides: Partial<ReaderRecordPlateDocument> = {},
): ReaderRecordPlateDocument {
  const vocabAnchor = textAnchor();
  const grammarAnchor = textAnchor({
    unitStartOffset: 21,
    unitEndOffset: 27,
    segmentStartOffset: 21,
    segmentEndOffset: 27,
    selectedText: "shapes",
    textHash: computeUtf16FNV1a("shapes"),
  });
  const vocabularyMark: ReaderRecordPlateVocabularyMark = {
    id: "vocab_mark_1",
    layerId: "layer_vocab_1",
    kind: "phrase_gloss",
    owner: "system_ai",
    anchor: vocabAnchor,
    startsHere: true,
    endsHere: true,
    vocabulary: {
      itemType: "phrase_gloss",
      phrase: "memory",
      phraseType: "collocation",
      gloss: "记忆",
      example: null,
    },
  };
  const grammarMark: ReaderRecordPlateGrammarMark = {
    id: "grammar_mark_1",
    layerId: "layer_grammar_1",
    kind: "grammar_note",
    owner: "system_ai",
    anchor: grammarAnchor,
    startsHere: true,
    endsHere: true,
    itemId: "grammar_item_1",
    spanIndex: 0,
    spanCount: 1,
    showCue: true,
    grammarPoint: "predicate verb",
    pattern: "subject + verb",
    note: "shapes is the predicate verb.",
  };

  const document: ReaderRecordPlateDocument = {
    type: "reader_record_plate_document",
    schemaVersion: "reader-record-plate-document/v1",
    record: {
      recordId: "record_1",
      title: "Active Anchor Fixture",
      generation: 2,
      productState: "readable_enhancing",
      readinessState: "article_ready",
    },
    snapshot: {
      snapshotId: "snapshot_1",
      snapshotTakenAt: "2026-06-24T00:00:00Z",
      lastEventSequence: 8,
    },
    base: {
      baseId: "base_1",
      contentSha256: "a".repeat(64),
      textLengthUtf16: 42,
      hashAlgorithm: "fnv1a32-utf16",
    },
    progress: {
      overallStatus: "ready",
      layers: [],
    },
    children: [
      {
        type: "paragraph",
        id: "paragraph:seg_1",
        children: [
          {
            text: "Institutional memory shapes policy choices.",
            owner: "stable",
            lockSource: true,
            sourceRole: "segment_text",
            baseRange: { startUtf16: 0, endUtf16: 42 },
            anchorSegmentId: "seg_1",
            segmentRange: { startUtf16: 0, endUtf16: 42 },
            marks: [vocabularyMark, grammarMark],
          },
        ],
        data: {
          anchorSegmentId: "seg_1",
          coveredAnchorSegmentIds: ["seg_1"],
          sentenceId: "sent_1",
          unitId: "unit_1",
          baseId: "base_1",
          baseRange: { startUtf16: 0, endUtf16: 42 },
          unitRange: { startUtf16: 0, endUtf16: 42 },
          textHash: "segment_hash",
          hashAlgorithm: "fnv1a32-utf16",
          segmentType: "sentence",
          boundaryQuality: "normal",
        },
      },
    ],
  };

  return {
    ...document,
    ...overrides,
  };
}

function firstVocabularyMark(
  document: ReaderRecordPlateDocument,
): ReaderRecordPlateVocabularyMark {
  const paragraph = document.children.find((block) => block.type === "paragraph");
  if (!paragraph || paragraph.type !== "paragraph") {
    throw new Error("expected paragraph block");
  }
  const leaf = paragraph.children[0];
  const mark = leaf.marks.find((m) => m.kind !== "grammar_note" && m.kind !== "user_highlight");
  if (!mark) {
    throw new Error("expected vocabulary mark");
  }
  expect(mark.kind).toBe("phrase_gloss");
  return mark as ReaderRecordPlateVocabularyMark;
}

function firstGrammarMark(
  document: ReaderRecordPlateDocument,
): ReaderRecordPlateGrammarMark {
  const paragraph = document.children.find((block) => block.type === "paragraph");
  if (!paragraph || paragraph.type !== "paragraph") {
    throw new Error("expected paragraph block");
  }
  const leaf = paragraph.children[0];
  const mark = leaf.marks.find((m) => m.kind === "grammar_note");
  if (!mark) {
    throw new Error("expected grammar mark");
  }
  return mark as ReaderRecordPlateGrammarMark;
}

function expectCommonRoot(anchor: UserEditorialAssetAnchorDraft): void {
  expect(anchor).toMatchObject({
    record_id: "record_1",
    base_id: "base_1",
    generation: 2,
    offset_unit: "utf16",
    hash_algorithm: "fnv1a32-utf16",
  });
}

describe("userEditorialAssetAnchorDraftForActiveAnchor", () => {
  it("combines document root metadata with a selection anchor draft", () => {
    const document = makeDocument();
    const selection: ReaderRecordAnchorDraft = {
      record_id: "record_1",
      base_id: "base_1",
      generation: 2,
      unit_id: "unit_1",
      anchor_segment_id: "seg_1",
      start_offset: 0,
      end_offset: 13,
      offset_unit: "utf16",
      selected_text: "Institutional",
      text_hash: computeUtf16FNV1a("Institutional"),
      hash_algorithm: "fnv1a32-utf16",
      scope: "stable_source",
    };

    const anchor = userEditorialAssetAnchorDraftForActiveAnchor(document, {
      source: "selection",
      anchor: selection,
    });

    expect(anchor).not.toBeNull();
    expectCommonRoot(anchor!);
    expect(anchor).toMatchObject({
      unit_id: "unit_1",
      anchor_segment_id: "seg_1",
      scope: "stable_source",
      start_offset: 0,
      end_offset: 13,
      selected_text: "Institutional",
      text_hash: computeUtf16FNV1a("Institutional"),
    });
  });

  it("returns null when a selection anchor draft belongs to a stale root", () => {
    const document = makeDocument();
    const selection: ReaderRecordAnchorDraft = {
      record_id: "stale_record",
      base_id: "stale_base",
      generation: 1,
      unit_id: "unit_1",
      anchor_segment_id: "seg_1",
      start_offset: 0,
      end_offset: 13,
      offset_unit: "utf16",
      selected_text: "Institutional",
      text_hash: computeUtf16FNV1a("Institutional"),
      hash_algorithm: "fnv1a32-utf16",
      scope: "stable_source",
    };

    expect(
      userEditorialAssetAnchorDraftForActiveAnchor(document, {
        source: "selection",
        anchor: selection,
      }),
    ).toBeNull();
  });

  it("combines document root metadata with a vocab system mark anchor", () => {
    const document = makeDocument();
    const mark = firstVocabularyMark(document);

    const anchor = userEditorialAssetAnchorDraftForActiveAnchor(document, {
      source: "system_mark",
      anchor: mark.anchor,
    });

    expect(anchor).not.toBeNull();
    expectCommonRoot(anchor!);
    expect(anchor).toMatchObject({
      unit_id: "unit_1",
      anchor_segment_id: "seg_1",
      scope: "system_ai_layer",
      start_offset: 14,
      end_offset: 20,
      selected_text: "memory",
      text_hash: computeUtf16FNV1a("memory"),
    });
  });

  it("combines document root metadata with a grammar system mark anchor", () => {
    const document = makeDocument();
    const mark = firstGrammarMark(document);

    const anchor = userEditorialAssetAnchorDraftForActiveAnchor(document, {
      source: "system_cue",
      anchor: mark.anchor,
    });

    expect(anchor).not.toBeNull();
    expectCommonRoot(anchor!);
    expect(anchor).toMatchObject({
      unit_id: "unit_1",
      anchor_segment_id: "seg_1",
      scope: "system_ai_layer",
      start_offset: 21,
      end_offset: 27,
      selected_text: "shapes",
      text_hash: computeUtf16FNV1a("shapes"),
    });
  });

  it("finds grouped paragraphs for non-primary seg_2 system marks and cues", () => {
    const secondVocabularyAnchor = textAnchor({
      anchorSegmentId: "seg_2",
      sentenceId: "sent_2",
      unitStartOffset: 23,
      unitEndOffset: 28,
      segmentStartOffset: 0,
      segmentEndOffset: 5,
      selectedText: "Those",
      textHash: computeUtf16FNV1a("Those"),
    });
    const secondGrammarAnchor = textAnchor({
      anchorSegmentId: "seg_2",
      sentenceId: "sent_2",
      unitStartOffset: 37,
      unitEndOffset: 44,
      segmentStartOffset: 14,
      segmentEndOffset: 21,
      selectedText: "persist",
      textHash: computeUtf16FNV1a("persist"),
    });
    const secondVocabularyMark: ReaderRecordPlateVocabularyMark = {
      id: "vocab_mark_seg_2",
      layerId: "layer_vocab_2",
      kind: "phrase_gloss",
      owner: "system_ai",
      anchor: secondVocabularyAnchor,
      startsHere: true,
      endsHere: true,
      vocabulary: {
        itemType: "phrase_gloss",
        phrase: "Those",
        phraseType: "collocation",
        gloss: "那些",
        example: null,
      },
    };
    const secondGrammarMark: ReaderRecordPlateGrammarMark = {
      id: "grammar_mark_seg_2",
      layerId: "layer_grammar_2",
      kind: "grammar_note",
      owner: "system_ai",
      anchor: secondGrammarAnchor,
      startsHere: true,
      endsHere: true,
      itemId: "grammar_item_2",
      spanIndex: 0,
      spanCount: 1,
      showCue: true,
      grammarPoint: "predicate verb",
      pattern: "subject + predicate",
      note: "persist acts as the predicate verb.",
    };
    const document = makeDocument({
      children: [
        {
          type: "paragraph",
          id: "paragraph:seg_1",
          children: [
            {
              text: "Institutional memory.",
              owner: "stable",
              lockSource: true,
              sourceRole: "segment_text",
              baseRange: { startUtf16: 0, endUtf16: 21 },
              anchorSegmentId: "seg_1",
              segmentRange: { startUtf16: 0, endUtf16: 21 },
              marks: [],
            },
            {
              text: "\n\n",
              owner: "stable",
              lockSource: true,
              sourceRole: "separator",
              baseRange: { startUtf16: 21, endUtf16: 23 },
              marks: [],
            },
            {
              text: "Those choices persist.",
              owner: "stable",
              lockSource: true,
              sourceRole: "segment_text",
              baseRange: { startUtf16: 23, endUtf16: 45 },
              anchorSegmentId: "seg_2",
              segmentRange: { startUtf16: 0, endUtf16: 22 },
              marks: [secondVocabularyMark, secondGrammarMark],
            },
          ],
          data: {
            anchorSegmentId: "seg_1",
            coveredAnchorSegmentIds: ["seg_1", "seg_2"],
            sentenceId: "sent_1",
            unitId: "unit_1",
            baseId: "base_1",
            baseRange: { startUtf16: 0, endUtf16: 45 },
            unitRange: { startUtf16: 0, endUtf16: 45 },
            textHash: "seg_1_hash",
            hashAlgorithm: "fnv1a32-utf16",
            segmentType: "sentence",
            boundaryQuality: "normal",
          },
        },
      ],
    });

    const markAnchor = userEditorialAssetAnchorDraftForActiveAnchor(document, {
      source: "system_mark",
      anchor: secondVocabularyAnchor,
    });
    const cueAnchor = userEditorialAssetAnchorDraftForActiveAnchor(document, {
      source: "system_cue",
      anchor: secondGrammarAnchor,
    });

    expect(markAnchor).not.toBeNull();
    expect(cueAnchor).not.toBeNull();
    expect(markAnchor).toMatchObject({
      anchor_segment_id: "seg_2",
      selected_text: "Those",
      start_offset: 23,
      end_offset: 28,
    });
    expect(cueAnchor).toMatchObject({
      anchor_segment_id: "seg_2",
      selected_text: "persist",
      start_offset: 37,
      end_offset: 44,
    });
  });

  it("returns null when document root record or generation is missing", () => {
    const missingRecord = makeDocument({
      record: {
        ...makeDocument().record,
        recordId: "",
      },
    });
    const missingGeneration = makeDocument({
      record: {
        ...makeDocument().record,
        generation: 0,
      },
    });
    const mark = firstVocabularyMark(makeDocument());

    expect(
      userEditorialAssetAnchorDraftForActiveAnchor(missingRecord, {
        source: "system_mark",
        anchor: mark.anchor,
      }),
    ).toBeNull();
    expect(
      userEditorialAssetAnchorDraftForActiveAnchor(missingGeneration, {
        source: "system_mark",
        anchor: mark.anchor,
      }),
    ).toBeNull();
  });

  it("returns null when active source hash does not match selected text", () => {
    const document = makeDocument();
    const mark = firstVocabularyMark(document);

    expect(
      userEditorialAssetAnchorDraftForActiveAnchor(document, {
        source: "system_mark",
        anchor: {
          ...mark.anchor,
          textHash: "segment_hash",
        },
      }),
    ).toBeNull();
  });

  it("returns null when a system anchor belongs to another base", () => {
    const document = makeDocument();
    const mark = firstVocabularyMark(document);

    expect(
      userEditorialAssetAnchorDraftForActiveAnchor(document, {
        source: "system_mark",
        anchor: {
          ...mark.anchor,
          baseId: "base_other",
        },
      }),
    ).toBeNull();
  });

  it("returns null when the active anchor segment is not in the document", () => {
    const document = makeDocument();
    const mark = firstVocabularyMark(document);

    expect(
      userEditorialAssetAnchorDraftForActiveAnchor(document, {
        source: "system_mark",
        anchor: {
          ...mark.anchor,
          anchorSegmentId: "seg_missing",
        },
      }),
    ).toBeNull();
    expect(
      userEditorialAssetAnchorDraftForActiveAnchor(document, {
        source: "system_mark",
        anchor: {
          ...mark.anchor,
          unitId: "unit_missing",
        },
      }),
    ).toBeNull();
  });

  it("returns null when active anchor offsets fall outside the document segment range", () => {
    const document = makeDocument();
    const mark = firstVocabularyMark(document);

    expect(
      userEditorialAssetAnchorDraftForActiveAnchor(document, {
        source: "system_mark",
        anchor: {
          ...mark.anchor,
          unitStartOffset: 40,
          unitEndOffset: 46,
          selectedText: "memory",
          textHash: computeUtf16FNV1a("memory"),
        },
      }),
    ).toBeNull();
  });
});
