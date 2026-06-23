/** @vitest-environment jsdom */

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PlateReaderSurface } from "@/components/reader/plate/PlateReaderSurface";
import type {
  ReaderGrammarNoteMarkDto,
  ReaderPlateSnapshotDto,
  ReaderStableSeparatorLeafDto,
  ReaderUnitNodeDto,
  ReaderVocabularyMarkDto,
} from "@/types/api/reader-plate";

import {
  adaptReaderPlateSnapshotToPlateDocument,
  adaptReaderPlateSnapshotToReaderVm,
} from "./snapshot-to-reader-workbench";

const FIRST_SENTENCE = "Institutional memory shapes policy choices.";
const SECOND_SENTENCE = "These choices persist across administrations.";

function makeVocabularyMark(
  overrides: Partial<ReaderVocabularyMarkDto> = {},
): ReaderVocabularyMarkDto {
  return {
    mark_id: "mark_vocab_memory",
    layer_id: "layer_vocab_1",
    item_type: "vocab_highlight",
    anchor_segment_id: "seg_1",
    start_offset: 14,
    end_offset: 20,
    selected_text: "memory",
    segment_start_utf16: 14,
    segment_end_utf16: 20,
    starts_here: true,
    ends_here: true,
    headword: "memory",
    brief_explanation: "记忆；既有经验",
    reason: "key concept in context",
    ...overrides,
  } as ReaderVocabularyMarkDto;
}

function makeGrammarNoteMark(
  overrides: Partial<ReaderGrammarNoteMarkDto> = {},
): ReaderGrammarNoteMarkDto {
  return {
    mark_id: "mark_grammar_shapes",
    item_id: "grammar_entry_shapes",
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
    grammar_point: "谓语动词",
    pattern: "subject + verb + object",
    note: "shapes 在这里作谓语，说明主语产生的影响。",
    ...overrides,
  };
}

function makeSnapshot(): ReaderPlateSnapshotDto {
  const separator: ReaderStableSeparatorLeafDto = {
    text: "\n\n",
    owner: "stable",
    lock_source: true,
    source_role: "separator",
    base_start_utf16: FIRST_SENTENCE.length,
    base_end_utf16: FIRST_SENTENCE.length + 2,
  };
  const secondStart = FIRST_SENTENCE.length + separator.text.length;

  const unit: ReaderUnitNodeDto = {
    type: "reader_unit",
    owner: "stable",
    base_id: "base_1",
    unit_id: "unit_1",
    order_index: 1,
    unit_type: "body",
    boundary_quality: "normal",
    base_start_utf16: 0,
    base_end_utf16: secondStart + SECOND_SENTENCE.length,
    text_hash: "unit_hash",
    hash_algorithm: "fnv1a32-utf16",
    children: [
      {
        type: "reader_source_block",
        owner: "stable",
        base_id: "base_1",
        unit_id: "unit_1",
        base_start_utf16: 0,
        base_end_utf16: secondStart + SECOND_SENTENCE.length,
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
            base_end_utf16: FIRST_SENTENCE.length,
            unit_start_utf16: 0,
            unit_end_utf16: FIRST_SENTENCE.length,
            text_hash: "seg_1_hash",
            hash_algorithm: "fnv1a32-utf16",
            children: [
              {
                text: FIRST_SENTENCE,
                owner: "stable",
                lock_source: true,
                source_role: "segment_text",
                base_start_utf16: 0,
                base_end_utf16: FIRST_SENTENCE.length,
                anchor_segment_id: "seg_1",
                segment_start_utf16: 0,
                segment_end_utf16: FIRST_SENTENCE.length,
                reader_vocabulary_marks: [makeVocabularyMark()],
                reader_grammar_note_marks: [makeGrammarNoteMark()],
              },
            ],
          },
          separator,
          {
            type: "reader_anchor_segment",
            owner: "stable",
            base_id: "base_1",
            unit_id: "unit_1",
            anchor_segment_id: "seg_2",
            sentence_id: "sent_2",
            segment_type: "sentence",
            boundary_quality: "normal",
            base_start_utf16: secondStart,
            base_end_utf16: secondStart + SECOND_SENTENCE.length,
            unit_start_utf16: secondStart,
            unit_end_utf16: secondStart + SECOND_SENTENCE.length,
            text_hash: "seg_2_hash",
            hash_algorithm: "fnv1a32-utf16",
            children: [
              {
                text: SECOND_SENTENCE,
                owner: "stable",
                lock_source: true,
                source_role: "segment_text",
                base_start_utf16: secondStart,
                base_end_utf16: secondStart + SECOND_SENTENCE.length,
                anchor_segment_id: "seg_2",
                segment_start_utf16: 0,
                segment_end_utf16: SECOND_SENTENCE.length,
                reader_vocabulary_marks: [
                  makeVocabularyMark({
                    mark_id: "mark_vocab_choices",
                    anchor_segment_id: "seg_2",
                    start_offset: secondStart + 6,
                    end_offset: secondStart + 13,
                    selected_text: "choices",
                    segment_start_utf16: 6,
                    segment_end_utf16: 13,
                    headword: "choices",
                    brief_explanation: "选择",
                  }),
                ],
                reader_grammar_note_marks: [
                  makeGrammarNoteMark({
                    mark_id: "mark_grammar_persist",
                    item_id: "grammar_entry_persist",
                    anchor_segment_id: "seg_2",
                    start_offset: secondStart + 14,
                    end_offset: secondStart + 21,
                    selected_text: "persist",
                    segment_start_utf16: 14,
                    segment_end_utf16: 21,
                    grammar_point: "谓语动词",
                    pattern: "subject + persist",
                    note: "persist 说明动作或状态延续。",
                  }),
                ],
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
        children: [{ text: "制度记忆会塑造政策选择。" }],
      },
      {
        type: "reader_sentence_analysis",
        owner: "system_ai",
        analysis_id: "analysis_seg_1",
        layer_id: "layer_analysis_1",
        layer_version: 1,
        base_id: "base_1",
        unit_id: "unit_1",
        target_scope: "unit",
        target_key: "unit_1",
        anchor_segment_id: "seg_1",
        selected_text: FIRST_SENTENCE,
        label: "nominal subject driving predicate",
        analysis: "Institutional memory 是主语，shapes 是谓语。",
        chunks: [
          { order: 1, label: "subject", text: "Institutional memory" },
          { order: 2, label: "predicate", text: "shapes policy choices" },
        ],
        children: [{ text: "Institutional memory 是主语，shapes 是谓语。" }],
      },
    ],
  };

  return {
    schema_kind: "reader_plate_snapshot",
    snapshot_id: "snapshot_1",
    snapshot_taken_at: "2026-06-22T00:00:00Z",
    last_event_sequence: 4,
    record_id: "record_1",
    record: {
      title: "Snapshot Adapter Fixture",
      created_at: "2026-06-22T00:00:00Z",
      source_type: "plain_text",
      source_metadata: {},
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
    },
    base: {
      base_id: "base_1",
      content_sha256: "sha256_1",
      canonicalizer_version: "canonicalizer_test",
      builder_version: "builder_test",
      segmenter_version: "segmenter_test",
      hash_algorithm: "fnv1a32-utf16",
      text_length_utf16: secondStart + SECOND_SENTENCE.length,
    },
    navigation: {
      units: [
        {
          unit_id: "unit_1",
          order_index: 1,
          unit_type: "body",
          boundary_quality: "normal",
          base_start_utf16: 0,
          base_end_utf16: secondStart + SECOND_SENTENCE.length,
          text_hash: "unit_hash",
          hash_algorithm: "fnv1a32-utf16",
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
        base_end_utf16: FIRST_SENTENCE.length,
        unit_start_utf16: 0,
        unit_end_utf16: FIRST_SENTENCE.length,
        text_hash: "seg_1_hash",
        hash_algorithm: "fnv1a32-utf16",
      },
      {
        anchor_segment_id: "seg_2",
        sentence_id: "sent_2",
        paragraph_id: "unit_1",
        unit_id: "unit_1",
        order_index: 2,
        unit_order_index: 2,
        segment_type: "sentence",
        boundary_quality: "normal",
        base_start_utf16: secondStart,
        base_end_utf16: secondStart + SECOND_SENTENCE.length,
        unit_start_utf16: secondStart,
        unit_end_utf16: secondStart + SECOND_SENTENCE.length,
        text_hash: "seg_2_hash",
        hash_algorithm: "fnv1a32-utf16",
      },
    ],
    enhancement_layers: [],
    parsed_decisions: [],
    user_assets: [],
    ask_supplements: [],
    value: [unit],
  };
}

describe("snapshot-to-reader-workbench adapter", () => {
  it("projects reader_unit and reader_anchor_segment into paragraph and sentence models", () => {
    const vm = adaptReaderPlateSnapshotToReaderVm(makeSnapshot());

    expect(vm.article.paragraphs).toEqual([
      {
        paragraphId: "unit_1",
        sentenceIds: ["sent_1", "sent_2"],
      },
    ]);
    expect(vm.article.sentences).toEqual([
      {
        paragraphId: "unit_1",
        sentenceId: "sent_1",
        text: FIRST_SENTENCE,
      },
      {
        paragraphId: "unit_1",
        sentenceId: "sent_2",
        text: SECOND_SENTENCE,
      },
    ]);
  });

  it("projects unit-targeted translation onto the first sentence-like anchor", () => {
    const vm = adaptReaderPlateSnapshotToReaderVm(makeSnapshot());

    expect(vm.translations).toEqual([
      {
        sentenceId: "sent_1",
        translationZh: "制度记忆会塑造政策选择。",
      },
    ]);
  });

  it("projects vocabulary and grammar marks into old inline range marks", () => {
    const vm = adaptReaderPlateSnapshotToReaderVm(makeSnapshot());

    expect(vm.inlineMarks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: "mark_vocab_memory",
          annotationType: "vocab_highlight",
          visualTone: "vocab",
          lookupKind: "word",
          lookupText: "memory",
          anchor: {
            kind: "range",
            sentenceId: "sent_1",
            offsetUnit: "utf16",
            start: 14,
            end: 20,
            text: "memory",
          },
        }),
        expect.objectContaining({
          id: "mark_grammar_shapes",
          parentId: "grammar_entry_shapes",
          annotationType: "grammar_note",
          visualTone: "grammar",
          anchor: {
            kind: "range",
            sentenceId: "sent_1",
            offsetUnit: "utf16",
            start: 21,
            end: 27,
            text: "shapes",
          },
        }),
        expect.objectContaining({
          id: "mark_vocab_choices",
          annotationType: "vocab_highlight",
          anchor: {
            kind: "range",
            sentenceId: "sent_2",
            offsetUnit: "utf16",
            start: 6,
            end: 13,
            text: "choices",
          },
        }),
        expect.objectContaining({
          id: "mark_grammar_persist",
          parentId: "grammar_entry_persist",
          annotationType: "grammar_note",
          anchor: {
            kind: "range",
            sentenceId: "sent_2",
            offsetUnit: "utf16",
            start: 14,
            end: 21,
            text: "persist",
          },
        }),
      ]),
    );
  });

  it("projects grammar notes and sentence analysis into old analysis entries", () => {
    const vm = adaptReaderPlateSnapshotToReaderVm(makeSnapshot());

    expect(vm.sentenceEntries).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: "grammar_entry_shapes",
          sentenceId: "sent_1",
          entryType: "grammar_note",
          title: "谓语动词",
          content: "shapes 在这里作谓语，说明主语产生的影响。",
        }),
        expect.objectContaining({
          id: "grammar_entry_persist",
          sentenceId: "sent_2",
          entryType: "grammar_note",
          title: "谓语动词",
          content: "persist 说明动作或状态延续。",
        }),
        expect.objectContaining({
          id: "analysis_seg_1",
          sentenceId: "sent_1",
          entryType: "sentence_analysis",
          label: "nominal subject driving predicate",
          chunks: [
            { order: 1, label: "subject", text: "Institutional memory" },
            { order: 2, label: "predicate", text: "shapes policy choices" },
          ],
        }),
      ]),
    );
  });

  it("renders adapted document through the old Plate surface with Workbench DOM hooks", () => {
    const documentValue = adaptReaderPlateSnapshotToPlateDocument(makeSnapshot());
    const paragraph = documentValue.children[0];
    if (paragraph.type !== "reader_paragraph") {
      throw new Error("Expected reader paragraph");
    }
    const sentence = paragraph.children[0];
    const sentenceText = sentence.children[0];
    if (sentenceText.type !== "reader_sentence_text") {
      throw new Error("Expected reader sentence text");
    }

    expect(sentenceText.children).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          text: "memory",
          readerSentenceId: "sent_1",
          readerTextStartOffset: 14,
          readerTextEndOffset: 20,
          readerMarkId: "mark_vocab_memory",
        }),
        expect.objectContaining({
          text: "shapes",
          readerSentenceId: "sent_1",
          readerTextStartOffset: 21,
          readerTextEndOffset: 27,
          readerMarkId: "mark_grammar_shapes",
          readerMarkParentId: "grammar_entry_shapes",
        }),
      ]),
    );

    const { container } = render(
      <PlateReaderSurface
        document={documentValue}
        showTranslation
        readingClassName="reader-serif text-ink"
      />,
    );

    const sentenceEl = container.querySelector(
      '[data-reader-anchor="sentence"][data-sentence-id="sent_1"]',
    );
    const sentenceTextEl = container.querySelector(
      '[data-reader-anchor="sentence"][data-sentence-id="sent_1"] [data-reader-sentence-text="true"]',
    );
    const vocabularyMarkEl = container.querySelector(
      '[data-reader-mark-id="mark_vocab_memory"]',
    );
    const secondSentenceVocabularyMarkEl = container.querySelector(
      '[data-reader-mark-id="mark_vocab_choices"]',
    );
    const grammarMarkEl = container.querySelector(
      '[data-reader-mark-parent-id="grammar_entry_shapes"]',
    );
    const secondSentenceGrammarMarkEl = container.querySelector(
      '[data-reader-mark-parent-id="grammar_entry_persist"]',
    );
    const translationEl = container.querySelector('[data-reader-node="translation"]');
    const analysisEl = container.querySelector(
      '[data-reader-node="analysis"][data-entry-id="analysis_seg_1"]',
    );

    expect(sentenceEl).not.toBeNull();
    expect(sentenceTextEl?.textContent).toContain(FIRST_SENTENCE);
    expect(vocabularyMarkEl?.getAttribute("data-reader-mark-tone")).toBe("vocab");
    expect(secondSentenceVocabularyMarkEl?.textContent).toBe("choices");
    expect(grammarMarkEl?.getAttribute("data-reader-mark-tone")).toBe("grammar");
    expect(secondSentenceGrammarMarkEl?.textContent).toBe("persist");
    expect(translationEl?.textContent).toContain("制度记忆会塑造政策选择。");
    expect(analysisEl?.textContent).toContain("nominal subject driving predicate");
  });
});
