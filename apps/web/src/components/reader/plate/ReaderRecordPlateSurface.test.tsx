/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { computeUtf16FNV1a } from "@claread/contracts";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  READER_PLATE_SNAPSHOT_SCHEMA_KIND,
  READER_TEXT_RANGE_HASH_ALGORITHM,
  READER_TEXT_RANGE_OFFSET_UNIT,
  type ReaderAnchorSegmentNodeDto,
  type ReaderEnhancementProgressDto,
  type ReaderGrammarNoteMarkDto,
  type ReaderPlateSnapshotDto,
  type ReaderSourceBlockNodeDto,
  type ReaderSnapshotUserAssetDto,
  type ReaderTitleGenerationStatus,
  type ReaderUnitNodeDto,
  type ReaderVocabularyMarkDto,
} from "@/types/api/reader-plate";
import type { WebDictResult } from "@/types/api/dict";
import {
  ReaderAskToolbarButton,
  ReaderCopyToolbarButton,
  ReaderFloatingToolbarButtons,
  ReaderHighlightToolbarButton,
  ReaderLookupToolbarButton,
  ReaderNoteToolbarButton,
  ReaderToolbarActionsProvider,
  type ReaderToolbarActions,
  type ReaderToolbarActionId,
} from "@/components/editor/plugins/reader-floating-toolbar-buttons";
import { Toolbar } from "@/components/ui/toolbar";
import { TooltipProvider } from "@/components/ui/tooltip";

vi.mock("@/components/editor/plugins/floating-toolbar-kit", async () => {
  const { createPlatePlugin } = await import("platejs/react");
  const { ReaderFloatingToolbarButtons } = await import(
    "@/components/editor/plugins/reader-floating-toolbar-buttons"
  );
  const { Toolbar } = await import("@/components/ui/toolbar");
  const { TooltipProvider } = await import("@/components/ui/tooltip");

  return {
    FloatingToolbarKit: [
      createPlatePlugin({
        key: "reader-floating-toolbar-test-harness",
        render: {
          afterEditable: () => (
            <div data-testid="reader-record-toolbar-test-harness">
              <TooltipProvider>
                <Toolbar>
                  <ReaderFloatingToolbarButtons />
                </Toolbar>
              </TooltipProvider>
            </div>
          ),
        },
      }),
    ],
  };
});

import { ReaderRecordPlateSurface } from "./ReaderRecordPlateSurface";

const SOURCE_TEXT = "Institutional memory shapes policy choices.";
const TRANSLATION_TEXT = "制度记忆会塑造政策选择。";

beforeEach(() => {
  // jsdom does not implement Range.getBoundingClientRect
  if (!Range.prototype.getBoundingClientRect) {
    Range.prototype.getBoundingClientRect = vi.fn(() => ({
      x: 0,
      y: 0,
      top: 0,
      left: 0,
      bottom: 20,
      right: 100,
      width: 100,
      height: 20,
      toJSON() {
        return { x: 0, y: 0, top: 0, left: 0, bottom: 20, right: 100, width: 100, height: 20 };
      },
    })) as unknown as Range["getBoundingClientRect"];
  }
  if (!HTMLElement.prototype.scrollIntoView) {
    HTMLElement.prototype.scrollIntoView = vi.fn();
  }
  vi.stubGlobal(
    "ResizeObserver",
    class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  // 默认 mock 收藏接口，避免 TopBar 中的 FavoriteButton 触发未处理请求。
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.startsWith("/api/web/favorites")) {
        return Promise.resolve(
          new Response(JSON.stringify({ ok: true, favorited: false }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      return Promise.resolve(new Response("Not Found", { status: 404 }));
    }),
  );
  window.getSelection()?.removeAllRanges();
});

afterEach(() => {
  window.getSelection()?.removeAllRanges();
  try {
    window.localStorage?.removeItem?.("claread.reader.settings.v4");
    window.localStorage?.removeItem?.("claread.reader.themeName");
  } catch {
    // Ignore jsdom localStorage variants that do not expose the full Storage API.
  }
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

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
      start_offset: 14,
      end_offset: 20,
      selected_text: "memory",
      text_hash: computeUtf16FNV1a("memory"),
      hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
    },
    created_at: "2026-06-24T01:00:00Z",
    updated_at: "2026-06-24T01:00:00Z",
    ...overrides,
  };
}

function makeUnit({
  vocabularyMarks = [makeVocabularyMark()],
  grammarMarks = [makeGrammarMark()],
  analysis = "Institutional memory is the subject.",
  analysisChunks = [
    { order: 1, label: "subject", text: "Institutional memory" },
  ],
}: {
  vocabularyMarks?: ReaderVocabularyMarkDto[];
  grammarMarks?: ReaderGrammarNoteMarkDto[];
  analysis?: string;
  analysisChunks?: Array<{ order: number; label: string; text: string }>;
} = {}): ReaderUnitNodeDto {
  return {
    type: "reader_unit",
    owner: "stable",
    base_id: "base_1",
    unit_id: "unit_1",
    order_index: 1,
    unit_type: "body",
    boundary_quality: "normal",
    base_start_utf16: 0,
    base_end_utf16: SOURCE_TEXT.length,
    text_hash: "unit_hash",
    hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
    children: [
      {
        type: "reader_source_block",
        owner: "stable",
        base_id: "base_1",
        unit_id: "unit_1",
        base_start_utf16: 0,
        base_end_utf16: SOURCE_TEXT.length,
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
            base_end_utf16: SOURCE_TEXT.length,
            unit_start_utf16: 0,
            unit_end_utf16: SOURCE_TEXT.length,
            text_hash: "seg_hash",
            hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
            children: [
              {
                text: SOURCE_TEXT,
                owner: "stable",
                lock_source: true,
                source_role: "segment_text",
                base_start_utf16: 0,
                base_end_utf16: SOURCE_TEXT.length,
                anchor_segment_id: "seg_1",
                segment_start_utf16: 0,
                segment_end_utf16: SOURCE_TEXT.length,
                reader_vocabulary_marks: vocabularyMarks,
                reader_grammar_note_marks: grammarMarks,
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
        source_text_hash: "unit_hash_1",
        children: [{ text: TRANSLATION_TEXT }],
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
        selected_text: SOURCE_TEXT,
        label: "subject and predicate",
        analysis,
        chunks: analysisChunks,
        children: [{ text: analysis }],
      },
    ],
  };
}

function makeProgress(): ReaderEnhancementProgressDto {
  return {
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
  };
}

function makeSnapshot(
  userAssets: ReaderSnapshotUserAssetDto[] = [],
): ReaderPlateSnapshotDto {
  return {
    schema_kind: READER_PLATE_SNAPSHOT_SCHEMA_KIND,
    snapshot_id: "snapshot_1",
    snapshot_taken_at: "2026-06-24T00:00:00Z",
    last_event_sequence: 8,
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
      text_length_utf16: SOURCE_TEXT.length,
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
          base_end_utf16: SOURCE_TEXT.length,
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
        base_end_utf16: SOURCE_TEXT.length,
        unit_start_utf16: 0,
        unit_end_utf16: SOURCE_TEXT.length,
        text_hash: "seg_hash",
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      },
    ],
    enhancement_layers: [],
    enhancement_progress: makeProgress(),
    ask_supplements: [],
    user_assets: userAssets,
    parsed_decisions: [],
    value: [makeUnit()],
  };
}

function makeOverlappingMarkSnapshot(): ReaderPlateSnapshotDto {
  return {
    ...makeSnapshot(),
    value: [
      makeUnit({
        vocabularyMarks: [
          makeVocabularyMark({
            mark_id: "vocab_split_mark",
            start_offset: 0,
            end_offset: 20,
            segment_start_utf16: 0,
            segment_end_utf16: 20,
            selected_text: "Institutional memory",
            phrase: "Institutional memory",
            gloss: "制度记忆",
            starts_here: true,
            ends_here: true,
          }),
        ],
        grammarMarks: [
          makeGrammarMark({
            mark_id: "grammar_split_mark",
            start_offset: 14,
            end_offset: 27,
            segment_start_utf16: 14,
            segment_end_utf16: 27,
            selected_text: "memory shapes",
            grammar_point: "noun phrase plus predicate",
            starts_here: true,
            ends_here: true,
          }),
        ],
      }),
    ],
  };
}

function makeMultiGrammarSnapshot(): ReaderPlateSnapshotDto {
  return {
    ...makeSnapshot(),
    value: [
      makeUnit({
        grammarMarks: [
          makeGrammarMark({
            mark_id: "grammar_mark_subject",
            item_id: "grammar_item_subject",
            start_offset: 0,
            end_offset: 20,
            selected_text: "Institutional memory",
            segment_start_utf16: 0,
            segment_end_utf16: 20,
            grammar_point: "fronted subject",
            pattern: "noun phrase",
            note: "Institutional memory names the sentence topic.",
          }),
          makeGrammarMark({
            mark_id: "grammar_mark_predicate",
            item_id: "grammar_item_predicate",
            start_offset: 21,
            end_offset: 27,
            selected_text: "shapes",
            segment_start_utf16: 21,
            segment_end_utf16: 27,
            grammar_point: "predicate verb",
            pattern: "subject + verb",
            note: "shapes carries the main action.",
          }),
          makeGrammarMark({
            mark_id: "grammar_mark_object",
            item_id: "grammar_item_object",
            start_offset: 28,
            end_offset: 42,
            selected_text: "policy choices",
            segment_start_utf16: 28,
            segment_end_utf16: 42,
            grammar_point: "direct object",
            pattern: "verb + object",
            note: "policy choices receives the action.",
          }),
        ],
      }),
    ],
  };
}

function makeAnnotationMatrixSnapshot(): ReaderPlateSnapshotDto {
  const noteText = SOURCE_TEXT.slice(0, 34);
  const wideNote = makeUserAsset({
    asset_id: "asset_note_matrix",
    asset_type: "note",
    note_text: "Matrix note for the sentence opening.",
    anchor: {
      anchor_type: "text_range",
      base_id: "base_1",
      unit_id: "unit_1",
      anchor_segment_id: "seg_1",
      sentence_id: "sent_1",
      segment_type: "sentence",
      offset_unit: READER_TEXT_RANGE_OFFSET_UNIT,
      start_offset: 0,
      end_offset: noteText.length,
      selected_text: noteText,
      text_hash: computeUtf16FNV1a(noteText),
      hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
    },
  });

  return {
    ...makeSnapshot([wideNote]),
    value: [
      makeUnit({
        vocabularyMarks: [
          makeVocabularyMark({
            mark_id: "vocab_matrix_mark",
            start_offset: 14,
            end_offset: 20,
            segment_start_utf16: 14,
            segment_end_utf16: 20,
            selected_text: "memory",
            starts_here: true,
            ends_here: true,
          }),
        ],
        grammarMarks: [
          makeGrammarMark({
            mark_id: "grammar_matrix_mark",
            item_id: "grammar_matrix_item",
            start_offset: 14,
            end_offset: 27,
            segment_start_utf16: 14,
            segment_end_utf16: 27,
            selected_text: "memory shapes",
            grammar_point: "noun phrase plus predicate",
            starts_here: true,
            ends_here: true,
          }),
        ],
      }),
    ],
  };
}

function closestMarkStack(element: HTMLElement | null): HTMLElement | null {
  return element?.closest<HTMLElement>("[data-reader-record-mark-stack-kinds]") ?? null;
}

function headerSourceTitleElement(container: HTMLElement): HTMLElement | null {
  return container.querySelector<HTMLElement>(
    "[data-reader-record-source-title='true']",
  );
}

function makeAnchorSegmentNode(
  overrides: Partial<ReaderAnchorSegmentNodeDto> & {
    anchor_segment_id: string;
    sentence_id: string;
    unit_start_utf16: number;
    unit_end_utf16: number;
    text: string;
  },
): ReaderAnchorSegmentNodeDto {
  const {
    text,
    anchor_segment_id,
    sentence_id,
    unit_start_utf16,
    unit_end_utf16,
    ...rest
  } = overrides;

  return {
    type: "reader_anchor_segment",
    owner: "stable",
    base_id: "base_1",
    unit_id: "unit_1",
    anchor_segment_id,
    sentence_id,
    segment_type: "sentence",
    boundary_quality: "normal",
    base_start_utf16: unit_start_utf16,
    base_end_utf16: unit_end_utf16,
    unit_start_utf16,
    unit_end_utf16,
    text_hash: computeUtf16FNV1a(text),
    hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
    children: [
      {
        text,
        owner: "stable",
        lock_source: true,
        source_role: "segment_text",
        base_start_utf16: unit_start_utf16,
        base_end_utf16: unit_end_utf16,
        anchor_segment_id,
        segment_start_utf16: 0,
        segment_end_utf16: text.length,
      },
    ],
    ...rest,
  };
}

function makeSplitSegmentSnapshot(): ReaderPlateSnapshotDto {
  const firstText = "Institutional memory ";
  const secondText = "shapes policy choices.";
  const firstSegment = makeAnchorSegmentNode({
    anchor_segment_id: "seg_1",
    sentence_id: "sent_1",
    unit_start_utf16: 0,
    unit_end_utf16: firstText.length,
    text: firstText,
  });
  const secondSegment = makeAnchorSegmentNode({
    anchor_segment_id: "seg_2",
    sentence_id: "sent_2",
    unit_start_utf16: firstText.length,
    unit_end_utf16: firstText.length + secondText.length,
    text: secondText,
  });
  const sourceBlock: ReaderSourceBlockNodeDto = {
    type: "reader_source_block",
    owner: "stable",
    base_id: "base_1",
    unit_id: "unit_1",
    base_start_utf16: 0,
    base_end_utf16: SOURCE_TEXT.length,
    children: [firstSegment, secondSegment],
  };
  const unit: ReaderUnitNodeDto = {
    ...makeUnit(),
    children: [sourceBlock],
  };

  return {
    ...makeSnapshot(),
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
        base_end_utf16: firstText.length,
        unit_start_utf16: 0,
        unit_end_utf16: firstText.length,
        text_hash: computeUtf16FNV1a(firstText),
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
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
        base_start_utf16: firstText.length,
        base_end_utf16: firstText.length + secondText.length,
        unit_start_utf16: firstText.length,
        unit_end_utf16: firstText.length + secondText.length,
        text_hash: computeUtf16FNV1a(secondText),
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      },
    ],
    value: [unit],
  };
}

function makeSplitSegmentTranslationSnapshot(): ReaderPlateSnapshotDto {
  const snapshot = makeSplitSegmentSnapshot();
  const unit = snapshot.value[0];
  const translation = makeUnit().children.find(
    (child) => child.type === "reader_translation_group",
  );
  if (!translation) {
    throw new Error("Expected translation fixture");
  }

  return {
    ...snapshot,
    value: [
      {
        ...unit,
        children: [
          ...unit.children,
          {
            ...translation,
            covered_anchor_segment_ids: ["seg_1", "seg_2"],
            source_text_hash: "split_unit_hash_1",
          },
        ],
      },
    ],
  };
}

function makeSequentialTranslationGroupSnapshot(): ReaderPlateSnapshotDto {
  const snapshot = makeSplitSegmentSnapshot();
  const unit = snapshot.value[0];

  return {
    ...snapshot,
    value: [
      {
        ...unit,
        children: [
          ...unit.children,
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
            children: [{ text: "制度记忆" }],
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
            source_text_hash: "group_hash_2",
            children: [{ text: "塑造政策选择" }],
          },
        ],
      },
    ],
  };
}

function makeGroupedSeg2PhraseGlossSnapshot(): ReaderPlateSnapshotDto {
  const snapshot = makeSplitSegmentTranslationSnapshot();
  const unit = snapshot.value[0];
  const sourceBlock = unit.children.find(
    (child): child is ReaderSourceBlockNodeDto =>
      child.type === "reader_source_block",
  );

  if (!sourceBlock) {
    throw new Error("Expected source block fixture");
  }

  const secondSegment = sourceBlock.children.find(
    (child): child is ReaderAnchorSegmentNodeDto =>
      "type" in child &&
      child.type === "reader_anchor_segment" &&
      child.anchor_segment_id === "seg_2",
  );
  if (!secondSegment) {
    throw new Error("Expected seg_2 fixture");
  }

  const seg2PhraseMark = makeVocabularyMark({
    mark_id: "vocab_mark_seg_2",
    anchor_segment_id: "seg_2",
    item_type: "phrase_gloss",
    start_offset: 7,
    end_offset: 21,
    segment_start_utf16: 7,
    segment_end_utf16: 21,
    selected_text: "policy choices",
    phrase: "policy choices",
    phrase_type: "collocation",
    gloss: "政策选择",
    example: "Policy choices shape institutions.",
  });

  secondSegment.children = [
    {
      ...secondSegment.children[0],
      reader_vocabulary_marks: [seg2PhraseMark],
    },
  ];

  return snapshot;
}

function makeDictionaryEntryResult(query = "memory"): WebDictResult {
  return {
    kind: "entry",
    query,
    provider: "test",
    cached: true,
    entry: {
      id: 1,
      word: query,
      baseWord: query,
      phonetic: "/memory/",
      meanings: [
        {
          partOfSpeech: "noun",
          definitions: [
            {
              meaning: "the ability to remember information",
              example: "Institutional memory shapes choices.",
            },
          ],
        },
      ],
      examples: [],
      phrases: [],
      entryKind: "entry",
      exchange: [],
      tags: [],
    },
  };
}

function installClipboardMock() {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
  return writeText;
}

function firstTextNode(element: HTMLElement): Text {
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  const node = walker.nextNode();
  if (!node) {
    throw new Error("Expected text node");
  }
  return node as Text;
}

function focusNearestEditor(element: HTMLElement) {
  const editor = element.closest<HTMLElement>(
    '[contenteditable="true"], [data-slate-editor="true"]',
  );
  if (!editor) {
    return;
  }
  editor.focus();
  fireEvent.focus(editor);
}

function selectTextInElement(element: HTMLElement, startOffset: number, endOffset: number) {
  focusNearestEditor(element);
  const textNode = firstTextNode(element);
  const range = document.createRange();
  range.setStart(textNode, startOffset);
  range.setEnd(textNode, endOffset);
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
  document.dispatchEvent(new Event("selectionchange"));
}

function selectAcrossElements(
  startElement: HTMLElement,
  startOffset: number,
  endElement: HTMLElement,
  endOffset: number,
) {
  focusNearestEditor(startElement);
  const range = document.createRange();
  range.setStart(firstTextNode(startElement), startOffset);
  range.setEnd(firstTextNode(endElement), endOffset);
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
  document.dispatchEvent(new Event("selectionchange"));
}

function selectionActionButton(
  container: HTMLElement,
  action: "lookup" | "copy" | "ask" | "highlight" | "note",
): HTMLButtonElement | null {
  return container.querySelector<HTMLButtonElement>(
    `[data-reader-record-toolbar-action="${action}"]`,
  );
}

async function waitForSelectionAction(
  container: HTMLElement,
  action: "lookup" | "copy" | "ask" | "highlight" | "note",
) {
  return waitFor(() => {
    const button = selectionActionButton(container, action);
    if (!button) {
      throw new Error(`Selection action not found: ${action}`);
    }
    return button;
  });
}

async function openAskPanelFromToolbar(askButton: HTMLButtonElement) {
  fireEvent.click(askButton);
  const attachContext = await screen.findByText("加入 Ask 上下文");
  fireEvent.click(attachContext);
}

async function submitAskPromptFromToolbar(
  askButton: HTMLButtonElement,
  prompt: string,
) {
  fireEvent.click(askButton);
  const input = await screen.findByPlaceholderText("Ask Claread anything...");
  fireEvent.change(input, { target: { value: prompt } });
  fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
}

function makeToolbarActions(
  state: Partial<ReaderToolbarActions["state"]> = {},
): ReaderToolbarActions {
  const enabled = { disabled: false };
  return {
    onAsk: vi.fn(),
    onCopy: vi.fn(),
    onHighlight: vi.fn(),
    onNote: vi.fn(),
    onLookup: vi.fn(),
    state: {
      lookup: enabled,
      copy: enabled,
      ask: enabled,
      highlight: enabled,
      note: enabled,
      ...state,
    },
  };
}

function renderToolbarHarness(
  actions = makeToolbarActions(),
  toolbarChildren: ReactNode = <ReaderFloatingToolbarButtons />,
) {
  return {
    actions,
    ...render(
      <ReaderToolbarActionsProvider value={actions}>
        <TooltipProvider>
          <Toolbar>
            {toolbarChildren}
          </Toolbar>
        </TooltipProvider>
      </ReaderToolbarActionsProvider>,
    ),
  };
}

function expectedMemoryAnchor() {
  return {
    record_id: "record_1",
    base_id: "base_1",
    generation: 1,
    unit_id: "unit_1",
    anchor_segment_id: "seg_1",
    start_offset: 14,
    end_offset: 20,
    offset_unit: READER_TEXT_RANGE_OFFSET_UNIT,
    selected_text: "memory",
    text_hash: computeUtf16FNV1a("memory"),
    hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
    scope: "stable_source",
  };
}

function policyNoteAnchor(selectedText = "policy") {
  const start = SOURCE_TEXT.indexOf(selectedText);
  if (start < 0) {
    throw new Error(`Missing policy note fixture text: ${selectedText}`);
  }
  return {
    anchor_type: "text_range" as const,
    base_id: "base_1",
    unit_id: "unit_1",
    anchor_segment_id: "seg_1",
    sentence_id: "sent_1",
    segment_type: "sentence" as const,
    offset_unit: READER_TEXT_RANGE_OFFSET_UNIT,
    start_offset: start,
    end_offset: start + selectedText.length,
    selected_text: selectedText,
    text_hash: computeUtf16FNV1a(selectedText),
    hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
  };
}

function makePolicyNoteAsset(
  overrides: Partial<ReaderSnapshotUserAssetDto> = {},
): ReaderSnapshotUserAssetDto {
  return makeUserAsset({
    asset_id: "asset_note_policy",
    asset_type: "note",
    note_text: "Policy note.",
    anchor: policyNoteAnchor(),
    ...overrides,
  });
}

function makePolicyHighlightAsset(
  overrides: Partial<ReaderSnapshotUserAssetDto> = {},
): ReaderSnapshotUserAssetDto {
  return makeUserAsset({
    asset_id: "asset_highlight_policy",
    asset_type: "user_highlight",
    color: "soft_mint",
    anchor: policyNoteAnchor(),
    ...overrides,
  });
}

function makeHighlightWriteItem({
  id = "asset_highlight_policy",
  selectedText = "policy",
  color = "soft_mint",
  supersededIds = [],
}: {
  id?: string;
  selectedText?: string;
  color?: "warm_yellow" | "soft_mint" | "soft_rose";
  supersededIds?: string[];
} = {}) {
  const start = SOURCE_TEXT.indexOf(selectedText);
  if (start < 0) {
    throw new Error(`Missing highlight fixture text: ${selectedText}`);
  }
  const end = start + selectedText.length;
  return {
    id,
    analysis_record_id: null,
    anchor_type: "text_range",
    target_key: `reading-record:record_1:unit_1:seg_1:${start}:${end}`,
    paragraph_id: null,
    sentence_id: "sent_1",
    selected_text: selectedText,
    start_offset: null,
    end_offset: null,
    text_hash: computeUtf16FNV1a(selectedText),
    segments: [],
    color,
    payload_json: {},
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:01Z",
    superseded_ids: supersededIds,
    reading_record_id: "record_1",
    base_id: "base_1",
    generation: 1,
    unit_id: "unit_1",
    anchor_segment_id: "seg_1",
    unit_start_utf16: start,
    unit_end_utf16: end,
  };
}

function installReaderAskFetchMock(recordId = "record_1") {
  const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const requestUrl = new URL(String(input), "https://example.test");
    if (requestUrl.pathname.includes("/api/web/favorites")) {
      return Promise.resolve(
        new Response(JSON.stringify({ ok: true, favorited: false }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    }

    if (requestUrl.pathname === "/api/web/reader-ask/model-options") {
      return Promise.resolve(
        new Response(
          JSON.stringify({
            default_key: "ask-clarity",
            items: [
              {
                key: "ask-clarity",
                label: "Qwen 3.7 Max",
                description: "适合带 reasoning 的 Ask 问答。",
                model_name: "qwen3.7-max",
                replan_model_name: "qwen3.7-max",
                price_multiplier: 1,
                is_default: true,
              },
            ],
          }),
          {
            status: 200,
            headers: { "content-type": "application/json" },
          },
        ),
      );
    }

    if (
      requestUrl.pathname === "/api/web/reader-ask/threads" &&
      requestUrl.searchParams.get("record_scope") === "reading_record"
    ) {
      return Promise.resolve(
        new Response(
          JSON.stringify({
            items: [
              {
                id: "thread-rr-1",
                record_id: recordId,
                title: "Ask Claread",
                is_default: true,
                selected_model: null,
                archived_at: null,
                created_at: "2026-06-25T00:00:00Z",
                updated_at: "2026-06-25T00:00:00Z",
                last_message_at: null,
              },
            ],
          }),
          {
            status: 200,
            headers: { "content-type": "application/json" },
          },
        ),
      );
    }

    if (
      requestUrl.pathname === "/api/web/reader-ask/threads/thread-rr-1" &&
      requestUrl.searchParams.get("record_scope") === "reading_record"
    ) {
      return Promise.resolve(
        new Response(
          JSON.stringify({
            id: "thread-rr-1",
            record_id: recordId,
            title: "Ask Claread",
            is_default: true,
            selected_model: null,
            archived_at: null,
            created_at: "2026-06-25T00:00:00Z",
            updated_at: "2026-06-25T00:00:00Z",
            last_message_at: null,
            messages: [],
          }),
          {
            status: 200,
            headers: { "content-type": "application/json" },
          },
        ),
      );
    }

    if (
      requestUrl.pathname === "/api/web/reader-ask/threads/thread-rr-1/messages/stream" &&
      requestUrl.searchParams.get("record_scope") === "reading_record"
    ) {
      return Promise.resolve(
        new Response("", {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        }),
      );
    }

    return Promise.resolve(
      new Response(
        JSON.stringify({
          ok: false,
          message: `Unexpected fetch: ${requestUrl.pathname}`,
        }),
        {
          status: 404,
          headers: { "content-type": "application/json" },
        },
      ),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function installReaderRecordWriteFetchMock() {
  const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const requestUrl = new URL(String(input), "https://example.test");
    if (requestUrl.pathname.includes("/api/web/favorites")) {
      return Promise.resolve(
        new Response(JSON.stringify({ ok: true, favorited: false }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    }

    if (
      requestUrl.pathname === "/api/web/reading-record/highlights" ||
      requestUrl.pathname === "/api/web/reading-record/notes"
    ) {
      return Promise.resolve(
        new Response(
          JSON.stringify({
            ok: true,
            status: "created",
            item: {},
            session: { state: "signed_in" },
          }),
          {
            status: 201,
            headers: { "content-type": "application/json" },
          },
        ),
      );
    }

    return Promise.resolve(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function sendAskComposerMessageAndReadFirstAttachment(
  fetchMock: ReturnType<typeof vi.fn>,
  content = "解释这个选区",
) {
  await waitFor(() => {
    expect(screen.getByPlaceholderText("继续问这篇文章…")).toBeTruthy();
  });
  fireEvent.change(screen.getByPlaceholderText("继续问这篇文章…"), {
    target: { value: content },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));

  await waitFor(() => {
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/messages/stream"),
      ),
    ).toBe(true);
  });

  const streamCall = fetchMock.mock.calls.findLast(([input]) =>
    String(input).includes("/messages/stream"),
  );
  const body = JSON.parse(String(streamCall?.[1]?.body)) as {
    attachments: Array<{
      subtype?: string | null;
      anchor_payload?: {
        anchor_type?: string | null;
        target_key?: string | null;
        record_id?: string | null;
        paragraph_id?: string | null;
        sentence_id?: string | null;
        selected_text?: string | null;
        start_offset?: number | null;
        end_offset?: number | null;
        text_hash?: string | null;
        segments?: Array<{
          paragraph_id?: string | null;
          sentence_id?: string | null;
          selected_text?: string | null;
          start_offset?: number | null;
          end_offset?: number | null;
          text_hash?: string | null;
        }>;
      } | null;
      selected_text?: string | null;
      segments?: Array<{
        paragraph_id?: string | null;
        sentence_id?: string | null;
        selected_text?: string | null;
        start_offset?: number | null;
        end_offset?: number | null;
        text_hash?: string | null;
      }>;
      target_key?: string | null;
      metadata: Record<string, unknown>;
    }>;
  };
  return body.attachments[0];
}

describe("ReaderRecordPlateSurface", () => {
  it("projects and renders stable source text as paragraph blocks", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const paragraph = container.querySelector<HTMLElement>(
      '[data-reader-record-node="paragraph"]',
    );
    expect(paragraph?.textContent).toContain(SOURCE_TEXT);
    expect(screen.getByTestId("reader-record-plate-surface")).toBeTruthy();
  });

  it("renders unit translation as a blockquote block", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const blockquote = container.querySelector<HTMLElement>(
      '[data-reader-record-node="blockquote"]',
    );
    const paragraph = container.querySelector<HTMLElement>(
      '[data-reader-record-node="paragraph"]',
    );

    expect(blockquote).not.toBeNull();
    expect(blockquote?.textContent).toContain(TRANSLATION_TEXT);
    expect(paragraph).not.toBeNull();
    expect(paragraph?.textContent).toContain(SOURCE_TEXT);
    expect(paragraph?.textContent).not.toContain(TRANSLATION_TEXT);
  });

  it("keeps translation groups interleaved with their covered source span", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSequentialTranslationGroupSnapshot()} />,
    );
    const orderedBlocks = [
      ...container.querySelectorAll<HTMLElement>(
        '[data-reader-record-node="paragraph"], [data-reader-record-node="blockquote"]',
      ),
    ];

    expect(
      orderedBlocks.map((block) => block.dataset.readerRecordBlockId),
    ).toEqual([
      "paragraph:seg_1",
      "blockquote:layer_translation_1:group_translation_1",
      "paragraph:seg_2",
      "blockquote:layer_translation_1:group_translation_2",
    ]);
  });

  it("groups consecutive grammar callouts and keeps sentence analysis separate", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeMultiGrammarSnapshot()} />,
    );

    const grammarGroup = container.querySelector<HTMLElement>(
      '[data-reader-record-node="callout-group"][data-reader-record-callout-group="grammar"]',
    );
    const grammarRows = Array.from(
      container.querySelectorAll<HTMLElement>(
        '[data-reader-record-node="callout"][data-callout-variant="grammar"]',
      ),
    );
    const analysisBlock = container.querySelector<HTMLElement>(
      '[data-reader-record-node="sentence-analysis"][data-reader-record-sentence-analysis-block="true"]',
    );

    expect(grammarGroup).not.toBeNull();
    expect(grammarGroup?.dataset.readerRecordCalloutGroupCount).toBe("3");
    expect(grammarGroup?.textContent).toContain("语法解析 · 3 条");
    expect(container.querySelectorAll('[data-reader-record-node="callout-group"]')).toHaveLength(1);
    expect(grammarGroup?.nextElementSibling).toBe(analysisBlock);
    expect(grammarRows).toHaveLength(3);
    for (const row of grammarRows) {
      expect(row.closest('[data-reader-record-callout-group="grammar"]')).toBe(grammarGroup);
      expect(row.dataset.readerRecordCallout).toBe("true");
      expect(row.dataset.readerRecordCalloutRow).toBe("grammar");
      expect(row.dataset.readerRecordCalloutLabel).toBe("语法解析");
      expect(row.classList.contains("reader-record-plate-callout--grammar-row")).toBe(true);
      expect(row.className).toContain("font-sans");
      expect(row.className).not.toContain("bg-ink/[0.035]");
    }
    expect(grammarRows[0]?.textContent).toContain("fronted subject");
    expect(grammarRows[1]?.textContent).toContain("predicate verb");
    expect(grammarRows[2]?.textContent).toContain("direct object");

    expect(analysisBlock).not.toBeNull();
    expect(analysisBlock?.dataset.readerRecordCallout).toBeUndefined();
    expect(analysisBlock?.dataset.readerRecordSentenceAnalysisElement).toBe(
      "reader_sentence_analysis",
    );
    expect(analysisBlock?.dataset.readerRecordSentenceAnalysisLabel).toBe(
      "长句拆析",
    );
    expect(analysisBlock?.textContent).toContain("subject and predicate");
    expect(analysisBlock?.textContent).toContain("长句拆析");
    expect(analysisBlock?.textContent).toContain(
      "Institutional memory is the subject.",
    );
    expect(analysisBlock?.classList.contains("reader-record-plate-sentence-analysis")).toBe(true);
    expect(analysisBlock?.className).toContain("font-sans");
    expect(analysisBlock?.className).toContain("bg-context-blue/[0.04]");
    expect(
      analysisBlock?.dataset.readerRecordSentenceAnalysisBlock,
    ).toBe("true");
    expect(
      analysisBlock?.querySelector(
        '[data-reader-record-sentence-analysis-chunks="plate"]',
      )?.textContent,
    ).toContain("subject");
  });

  it("renders grammar callouts collapsed by default and toggles full content", async () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const grammarCallout = container.querySelector<HTMLElement>(
      '[data-reader-record-node="callout"][data-callout-variant="grammar"]',
    );
    expect(grammarCallout).not.toBeNull();
    if (!grammarCallout) {
      throw new Error("Expected grammar callout");
    }

    const content = grammarCallout.querySelector<HTMLElement>(
      '[data-reader-record-markdown-content="plate"]',
    );
    const preview = grammarCallout.querySelector<HTMLElement>(
      '[data-reader-record-callout-preview="grammar"]',
    );
    const toggle = grammarCallout.querySelector<HTMLButtonElement>(
      '[data-reader-record-callout-toggle="grammar"]',
    );
    expect(content).not.toBeNull();
    expect(preview).toBeNull();
    expect(toggle).not.toBeNull();
    if (!content || !toggle) {
      throw new Error("Expected grammar callout compact controls");
    }

    expect(grammarCallout.dataset.readerRecordCalloutCollapsed).toBe("true");
    expect(content.hidden).toBe(true);
    expect(toggle.textContent?.trim()).toBe("");
    expect(toggle.getAttribute("aria-label")).toBe("展开语法解析");
    expect(toggle.title).toBe("展开解析");
    const pointerDownResult = fireEvent.pointerDown(toggle);
    const mouseDownResult = fireEvent.mouseDown(toggle);
    expect(pointerDownResult).toBe(false);
    expect(mouseDownResult).toBe(false);
    expect(grammarCallout.dataset.readerRecordCalloutCollapsed).toBe("true");
    expect(
      grammarCallout.querySelector(
        '[data-reader-record-callout-pattern="grammar"]',
      )?.textContent,
    ).toBe("subject + verb");
    const title = grammarCallout.querySelector<HTMLElement>(
      '[data-reader-record-callout-title="grammar"]',
    );
    const pattern = grammarCallout.querySelector<HTMLElement>(
      '[data-reader-record-callout-pattern="grammar"]',
    );
    expect(title).not.toBeNull();
    expect(pattern).not.toBeNull();
    if (!title || !pattern) {
      throw new Error("Expected grammar title and pattern");
    }
    expect(title.compareDocumentPosition(pattern)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );

    fireEvent.click(toggle);
    await waitFor(() => {
      expect(grammarCallout.dataset.readerRecordCalloutCollapsed).toBe("false");
      expect(content.hidden).toBe(false);
    });
    expect(toggle.textContent?.trim()).toBe("");
    expect(toggle.getAttribute("aria-label")).toBe("收起语法解析");
    expect(toggle.title).toBe("收起解析");

    fireEvent.click(toggle);
    await waitFor(() => {
      expect(grammarCallout.dataset.readerRecordCalloutCollapsed).toBe("true");
      expect(content.hidden).toBe(true);
    });
    expect(toggle.textContent?.trim()).toBe("");
    expect(toggle.getAttribute("aria-label")).toBe("展开语法解析");
  });

  it("supports expanding multiple grammar rows inside an aggregated group", async () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeMultiGrammarSnapshot()} />,
    );
    const subjectRow = container.querySelector<HTMLElement>(
      '[data-reader-record-grammar-item-id="grammar_item_subject"][data-callout-variant="grammar"]',
    );
    const predicateRow = container.querySelector<HTMLElement>(
      '[data-reader-record-grammar-item-id="grammar_item_predicate"][data-callout-variant="grammar"]',
    );
    const objectRow = container.querySelector<HTMLElement>(
      '[data-reader-record-grammar-item-id="grammar_item_object"][data-callout-variant="grammar"]',
    );
    expect(subjectRow).not.toBeNull();
    expect(predicateRow).not.toBeNull();
    expect(objectRow).not.toBeNull();
    if (!subjectRow || !predicateRow || !objectRow) {
      throw new Error("Expected grouped grammar rows");
    }

    const subjectContent = subjectRow.querySelector<HTMLElement>(
      '[data-reader-record-markdown-content="plate"]',
    );
    const predicateContent = predicateRow.querySelector<HTMLElement>(
      '[data-reader-record-markdown-content="plate"]',
    );
    const objectContent = objectRow.querySelector<HTMLElement>(
      '[data-reader-record-markdown-content="plate"]',
    );
    const predicateToggle = predicateRow.querySelector<HTMLButtonElement>(
      '[data-reader-record-callout-toggle="grammar"]',
    );
    const objectToggle = objectRow.querySelector<HTMLButtonElement>(
      '[data-reader-record-callout-toggle="grammar"]',
    );
    expect(subjectContent?.hidden).toBe(true);
    expect(predicateContent?.hidden).toBe(true);
    expect(objectContent?.hidden).toBe(true);
    expect(predicateToggle).not.toBeNull();
    expect(objectToggle).not.toBeNull();
    if (!predicateToggle || !objectToggle) {
      throw new Error("Expected grouped grammar toggles");
    }

    fireEvent.click(predicateToggle);
    await waitFor(() => {
      expect(predicateRow.dataset.readerRecordCalloutCollapsed).toBe("false");
      expect(predicateContent?.hidden).toBe(false);
    });
    expect(subjectContent?.hidden).toBe(true);
    expect(objectContent?.hidden).toBe(true);

    fireEvent.click(objectToggle);
    await waitFor(() => {
      expect(objectRow.dataset.readerRecordCalloutCollapsed).toBe("false");
      expect(objectContent?.hidden).toBe(false);
    });
    expect(predicateContent?.hidden).toBe(false);
    expect(subjectContent?.hidden).toBe(true);

    fireEvent.click(predicateToggle);
    await waitFor(() => {
      expect(predicateRow.dataset.readerRecordCalloutCollapsed).toBe("true");
      expect(predicateContent?.hidden).toBe(true);
    });
    expect(objectContent?.hidden).toBe(false);
  });

  it("source grammar mark expands its row without closing other expanded rows", async () => {
    const scrollSpy = vi
      .spyOn(HTMLElement.prototype, "scrollIntoView")
      .mockImplementation(() => undefined);
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeMultiGrammarSnapshot()} />,
    );
    const predicateSource = container.querySelector<HTMLElement>(
      '[data-reader-record-leaf="segment_text"][data-reader-record-grammar-item-id="grammar_item_predicate"]',
    );
    const predicateRow = container.querySelector<HTMLElement>(
      '[data-reader-record-grammar-item-id="grammar_item_predicate"][data-callout-variant="grammar"]',
    );
    const objectRow = container.querySelector<HTMLElement>(
      '[data-reader-record-grammar-item-id="grammar_item_object"][data-callout-variant="grammar"]',
    );
    const objectToggle = objectRow?.querySelector<HTMLButtonElement>(
      '[data-reader-record-callout-toggle="grammar"]',
    );
    const predicateToggle = predicateRow?.querySelector<HTMLButtonElement>(
      '[data-reader-record-callout-toggle="grammar"]',
    );
    const predicateContent = predicateRow?.querySelector<HTMLElement>(
      '[data-reader-record-markdown-content="plate"]',
    );
    const objectContent = objectRow?.querySelector<HTMLElement>(
      '[data-reader-record-markdown-content="plate"]',
    );
    expect(predicateSource).not.toBeNull();
    expect(predicateRow).not.toBeNull();
    expect(objectRow).not.toBeNull();
    expect(objectToggle).not.toBeNull();
    expect(predicateToggle).not.toBeNull();
    if (
      !predicateSource ||
      !predicateRow ||
      !objectRow ||
      !objectToggle ||
      !predicateToggle
    ) {
      throw new Error("Expected source mark and grouped grammar rows");
    }

    fireEvent.click(objectToggle);
    await waitFor(() => {
      expect(objectContent?.hidden).toBe(false);
    });
    scrollSpy.mockClear();

    fireEvent.click(predicateSource);
    await waitFor(() => {
      expect(predicateRow.dataset.readerRecordCalloutCollapsed).toBe("false");
      expect(predicateContent?.hidden).toBe(false);
      expect(objectContent?.hidden).toBe(false);
      expect(scrollSpy).toHaveBeenCalled();
    });

    fireEvent.click(predicateToggle);
    await waitFor(() => {
      expect(predicateRow.dataset.readerRecordCalloutCollapsed).toBe("true");
      expect(predicateContent?.hidden).toBe(true);
      expect(objectContent?.hidden).toBe(false);
    });
  });

  it("renders sentence analysis collapsed without a chunk badge and toggles chunk rows", async () => {
    const snapshot = {
      ...makeSnapshot(),
      value: [
        makeUnit({
          analysis: "Four-part sentence structure.",
          analysisChunks: [
            { order: 1, label: "subject", text: "Institutional memory" },
            { order: 2, label: "predicate", text: "shapes" },
            { order: 3, label: "object", text: "policy choices" },
            { order: 4, label: "modifier", text: "during review" },
          ],
        }),
      ],
    };
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    const analysisBlock = container.querySelector<HTMLElement>(
      '[data-reader-record-node="sentence-analysis"][data-reader-record-sentence-analysis-block="true"]',
    );
    expect(analysisBlock).not.toBeNull();
    if (!analysisBlock) {
      throw new Error("Expected sentence analysis block");
    }

    const content = analysisBlock.querySelector<HTMLElement>(
      '[data-reader-record-markdown-content="plate"]',
    );
    const summary = analysisBlock.querySelector<HTMLElement>(
      '[data-reader-record-callout-preview="sentence-analysis"]',
    );
    const eyebrow = analysisBlock.querySelector<HTMLElement>(
      ".reader-record-plate-sentence-analysis-eyebrow",
    );
    const title = analysisBlock.querySelector<HTMLElement>(
      '[data-reader-record-callout-title="sentence-analysis"]',
    );
    const chunkRows = analysisBlock.querySelector<HTMLElement>(
      '[data-reader-record-sentence-analysis-chunks="plate"]',
    );
    const toggle = analysisBlock.querySelector<HTMLButtonElement>(
      '[data-reader-record-callout-toggle="sentence-analysis"]',
    );
    expect(content).not.toBeNull();
    expect(summary).toBeNull();
    expect(eyebrow).not.toBeNull();
    expect(title).not.toBeNull();
    expect(chunkRows).not.toBeNull();
    expect(toggle).not.toBeNull();
    if (!content || !eyebrow || !title || !chunkRows || !toggle) {
      throw new Error("Expected sentence analysis compact controls");
    }

    expect(analysisBlock.dataset.readerRecordSentenceAnalysisCollapsed).toBe("true");
    expect(eyebrow.textContent).toContain("长句拆析");
    expect(title.textContent).toBe("subject and predicate");
    expect(title.closest(".reader-record-plate-sentence-analysis-title-row")).not.toBeNull();
    expect(content.hidden).toBe(true);
    expect(toggle.textContent?.trim()).toBe("");
    expect(toggle.getAttribute("aria-label")).toBe("展开长句拆析");
    expect(toggle.title).toBe("展开解析");

    fireEvent.click(toggle);
    await waitFor(() => {
      expect(analysisBlock.dataset.readerRecordSentenceAnalysisCollapsed).toBe("false");
      expect(content.hidden).toBe(false);
    });
    expect(chunkRows.textContent).toContain("subject");
    expect(chunkRows.textContent).toContain("modifier");
    expect(toggle.textContent?.trim()).toBe("");
    expect(toggle.getAttribute("aria-label")).toBe("收起长句拆析");
  });

  it("disables Ask from grammar callout actions and disables unsupported grammar feedback", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const grammarCallout = container.querySelector<HTMLElement>(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );
    expect(grammarCallout).not.toBeNull();
    if (!grammarCallout) {
      throw new Error("Expected grammar callout");
    }

    const askButton = grammarCallout.querySelector<HTMLButtonElement>(
      '[data-reader-record-callout-action="ask"]',
    );
    const feedbackButton = grammarCallout.querySelector<HTMLButtonElement>(
      '[data-reader-record-callout-action="feedback"]',
    );
    expect(askButton).not.toBeNull();
    expect(feedbackButton).not.toBeNull();
    if (!askButton || !feedbackButton) {
      throw new Error("Expected grammar callout actions");
    }
    expect(askButton.disabled).toBe(true);
    expect(askButton.getAttribute("aria-label")).toBe("带这条解析提问");
    expect(askButton.title).toBe("当前解析暂不能加入 Ask Claread");
    expect(feedbackButton.disabled).toBe(true);
    expect(feedbackButton.title).toBe("当前解析缺少可反馈的记录 ID");
    expect(
      askButton.querySelector("svg")?.classList.contains(
        "lucide-message-circle-question",
      ),
    ).toBe(true);

    fireEvent.click(feedbackButton);
    expect(screen.queryByRole("dialog", { name: "反馈选项" })).toBeNull();
    fireEvent.click(askButton);
    expect(screen.queryByRole("button", { name: "发送" })).toBeNull();
  });

  it("submits sentence analysis callout feedback only when analysis id is available", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const requestUrl = new URL(String(input), "https://example.test");
      if (requestUrl.pathname === "/api/web/feedback") {
        return Promise.resolve(
          new Response(JSON.stringify({ ok: true, message: "ok" }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify({ ok: true, favorited: false }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const analysisBlock = container.querySelector<HTMLElement>(
      '[data-reader-record-node="sentence-analysis"]',
    );
    const feedbackButton = analysisBlock?.querySelector<HTMLButtonElement>(
      '[data-reader-record-callout-action="feedback"]',
    );
    expect(analysisBlock).not.toBeNull();
    expect(feedbackButton).not.toBeNull();
    if (!analysisBlock || !feedbackButton) {
      throw new Error("Expected sentence analysis feedback action");
    }
    expect(feedbackButton.disabled).toBe(false);

    fireEvent.click(feedbackButton);
    const menu = await screen.findByRole("dialog", { name: "反馈选项" });
    fireEvent.click(within(menu).getByText("有问题"));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url]) => url === "/api/web/feedback"),
      ).toBe(true);
    });
    const feedbackCall = fetchMock.mock.calls.find(
      ([url]) => url === "/api/web/feedback",
    );
    const body = JSON.parse(
      String((feedbackCall?.[1] as RequestInit | undefined)?.body),
    ) as Record<string, unknown>;
    expect(body).toMatchObject({
      feedbackScope: "annotation",
      targetId: "sentence_analysis:analysis_1",
      sentiment: "negative",
      feedbackType: "inaccurate",
      analysisRecordId: "analysis_1",
      annotationType: "sentence_analysis",
      entryPoint: "reader_record_callout",
      clientSurface: "reader_record",
    });
    expect(body.contextJson).toMatchObject({
      readingRecordId: "record_1",
      annotationType: "sentence_analysis",
      targetVariant: "sentence_analysis",
    });
  });

  it("marks callout chrome controls as excluded from copied content", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const chromeControls = [
      ...container.querySelectorAll<HTMLElement>(
        '[data-reader-record-callout-controls], [data-reader-record-callout-toggle], [data-reader-record-callout-action]',
      ),
    ];
    expect(chromeControls.length).toBeGreaterThan(0);
    for (const control of chromeControls) {
      expect(control.dataset.readerRecordCopyExclude).toBe("true");
      expect(control.getAttribute("contenteditable")).toBe("false");
      expect(control.getAttribute("draggable")).toBe("false");
    }

    const grammarTitle = container.querySelector<HTMLElement>(
      '[data-reader-record-callout-title="grammar"]',
    );
    const grammarPreview = container.querySelector<HTMLElement>(
      '[data-reader-record-callout-preview="grammar"]',
    );
    expect(grammarTitle?.dataset.readerRecordCopyExclude).toBeUndefined();
    expect(grammarPreview).toBeNull();

    const globalsSource = readFileSync(
      resolve(process.cwd(), "src/app/globals.css"),
      "utf8",
    );
    expect(globalsSource).toMatch(
      /\.reader-record-plate-callout-row-controls\s*\{[\s\S]*?opacity:\s*0/,
    );
    expect(globalsSource).toMatch(
      /\.reader-record-plate-callout:hover \.reader-record-plate-callout-row-controls/,
    );
    expect(globalsSource).toMatch(
      /\.reader-record-plate-callout-group-rows\s*\{[\s\S]*?counter-reset:\s*reader-record-grammar-row/,
    );
    expect(globalsSource).toMatch(
      /\.reader-record-plate-callout--grammar-row::before\s*\{[\s\S]*?content:\s*counter\(reader-record-grammar-row\)/,
    );
    expect(globalsSource).toMatch(
      /\.reader-record-plate-callout-group\s*[\s\S]*?\.reader-record-plate-callout--grammar-row:hover\s*\{[\s\S]*?background-color:\s*color-mix\(in srgb, var\(--grammar-violet\) 1\.6%, transparent\)/,
    );
    expect(globalsSource).toMatch(
      /\.reader-record-plate-callout-group:has\(\+ \.reader-record-plate-sentence-analysis\)\s*\{[\s\S]*?border-bottom-color:\s*transparent/,
    );
    expect(globalsSource).toMatch(
      /\.reader-record-plate-callout-group\s*\+\s*\.reader-record-plate-sentence-analysis\[data-reader-record-sentence-analysis-collapsed="true"\]\s*\{[\s\S]*?border-top-color:\s*transparent/,
    );
    expect(globalsSource).toMatch(
      /\.reader-record-plate-sentence-analysis\[data-reader-record-sentence-analysis-collapsed="true"\]\s*\{[\s\S]*?border-right-color:\s*transparent[\s\S]*?background-color:\s*transparent/,
    );
    expect(globalsSource).toMatch(
      /\.reader-record-plate-sentence-analysis\[data-reader-record-sentence-analysis-collapsed="false"\]\s*\{[\s\S]*?background-color:\s*color-mix\(in srgb, var\(--context-blue\) 2\.4%, transparent\)/,
    );
  });

  it("sanitizes copied grammar content without callout chrome text", async () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const grammarCallout = container.querySelector<HTMLElement>(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );
    const toggle = grammarCallout?.querySelector<HTMLButtonElement>(
      '[data-reader-record-callout-toggle="grammar"]',
    );
    expect(grammarCallout).not.toBeNull();
    expect(toggle).not.toBeNull();
    if (!grammarCallout || !toggle) {
      throw new Error("Expected grammar callout");
    }

    fireEvent.click(toggle);
    await waitFor(() => {
      expect(grammarCallout.dataset.readerRecordCalloutCollapsed).toBe("false");
    });

    const title = grammarCallout.querySelector<HTMLElement>(
      '[data-reader-record-callout-title="grammar"]',
    );
    const content = grammarCallout.querySelector<HTMLElement>(
      '[data-reader-record-markdown-content="plate"] p',
    );
    expect(title).not.toBeNull();
    expect(content).not.toBeNull();
    if (!title || !content) {
      throw new Error("Expected expanded grammar content");
    }

    const range = document.createRange();
    range.setStart(firstTextNode(title), 0);
    range.setEnd(firstTextNode(content), content.textContent?.length ?? 0);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);

    const clipboardData = {
      setData: vi.fn(),
    };
    fireEvent.copy(grammarCallout, { clipboardData });

    const plainTextCall = clipboardData.setData.mock.calls.find(
      ([type]) => type === "text/plain",
    );
    expect(plainTextCall).toBeDefined();
    const copiedText = String(plainTextCall?.[1] ?? "");
    expect(copiedText).toContain("predicate verb");
    expect(copiedText).toContain("shapes is the predicate verb.");
    expect(copiedText).not.toContain("收起");
    expect(copiedText).not.toContain("展开");
    expect(copiedText).not.toContain("加入 Ask");
    expect(copiedText).not.toContain("反馈");
  });

  it("renders expanded enhancement markdown through Slate-managed Plate children", async () => {
    const markdownSnapshot = {
      ...makeSnapshot(),
      value: [
        makeUnit({
          grammarMarks: [
            makeGrammarMark({
              note:
                "### Pattern\n\n**shapes** uses `subject + verb`.\n\n- Keeps policy choices active.\n\n```txt\nsubject -> verb\n```\n\n---\n\n> Read the verb.",
            }),
          ],
          analysis:
            "**Institutional memory** anchors the sentence.\n\n1. Find the subject\n2. Read the predicate",
          analysisChunks: [
            { order: 1, label: "subject", text: "Institutional memory" },
            { order: 2, label: "predicate", text: "shapes policy choices" },
          ],
        }),
      ],
    };
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={markdownSnapshot} />,
    );

    const grammarCallout = container.querySelector<HTMLElement>(
      '[data-reader-record-node="callout"][data-callout-variant="grammar"]',
    );
    const analysisBlock = container.querySelector<HTMLElement>(
      '[data-reader-record-node="sentence-analysis"][data-reader-record-sentence-analysis-block="true"]',
    );
    const grammarPreview = grammarCallout?.querySelector<HTMLElement>(
      '[data-reader-record-callout-preview="grammar"]',
    );

    expect(grammarPreview).toBeNull();
    expect(
      grammarCallout?.querySelector(
        '[data-reader-record-markdown-content="plate"] [data-slate-node="element"]',
      ),
    ).not.toBeNull();
    expect(
      analysisBlock?.querySelector(
        '[data-reader-record-markdown-content="plate"] [data-slate-node="element"]',
      ),
    ).not.toBeNull();
    const grammarToggle = grammarCallout?.querySelector<HTMLButtonElement>(
      '[data-reader-record-callout-toggle="grammar"]',
    );
    const analysisToggle = analysisBlock?.querySelector<HTMLButtonElement>(
      '[data-reader-record-callout-toggle="sentence-analysis"]',
    );
    expect(grammarToggle).not.toBeNull();
    expect(analysisToggle).not.toBeNull();
    if (!grammarToggle || !analysisToggle || !grammarCallout || !analysisBlock) {
      throw new Error("Expected callout toggles");
    }
    fireEvent.click(grammarToggle);
    fireEvent.click(analysisToggle);
    await waitFor(() => {
      expect(grammarCallout.dataset.readerRecordCalloutCollapsed).toBe("false");
      expect(analysisBlock.dataset.readerRecordSentenceAnalysisCollapsed).toBe("false");
    });
    expect(grammarCallout?.querySelector("strong")?.textContent).toBe("shapes");
    const inlineCode = grammarCallout?.querySelector("code");
    expect(inlineCode?.textContent).toBe("subject + verb");
    expect(inlineCode?.className).toContain("reader-record-plate-inline-code");
    expect(inlineCode?.className).not.toContain("bg-muted/50");
    expect(inlineCode?.className).not.toContain("font-mono");
    expect(grammarCallout?.querySelector("li")?.textContent).toContain(
      "Keeps policy choices active.",
    );
    expect(grammarCallout?.querySelector("h3")?.textContent).toContain("Pattern");
    expect(grammarCallout?.querySelector("pre")?.textContent).toContain(
      "subject -> verb",
    );
    expect(grammarCallout?.querySelector("hr")).not.toBeNull();
    expect(grammarCallout?.querySelector("blockquote")?.textContent).toContain(
      "Read the verb",
    );
    expect(analysisBlock?.querySelector("strong")?.textContent).toContain(
      "Institutional memory",
    );
    expect(analysisBlock?.querySelector("ol")?.textContent).toContain(
      "Read the predicate",
    );
    expect(analysisBlock?.innerHTML).not.toContain("<script");

    const blockKitSource = readFileSync(
      resolve(process.cwd(), "src/components/editor/plugins/reader-blocks-kit.tsx"),
      "utf8",
    );
    expect(blockKitSource).not.toMatch(/CalloutMarkdownRenderer/);
    expect(blockKitSource).not.toMatch(/dangerouslySetInnerHTML/);
    expect(blockKitSource).not.toMatch(/Grammar X-Ray/);
    expect(blockKitSource).not.toMatch(
      /reader-record-plate-inline-code[^`]*bg-muted\/50[^`]*font-mono/,
    );
  });

  it("switches between intensive and immersive document visibility", async () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot([makeUserAsset()])} />,
    );

    expect(
      container.querySelector('[data-reader-record-node="blockquote"]'),
    ).not.toBeNull();
    expect(
      container.querySelector('[data-callout-variant="grammar"]'),
    ).not.toBeNull();
    expect(
      container.querySelector('[data-reader-record-node="sentence-analysis"]'),
    ).not.toBeNull();
    expect(
      container.querySelector('[data-reader-record-grammar-mark-id="grammar_mark_1"]'),
    ).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "切换到沉浸模式" }));

    await waitFor(() => {
      expect(
        container.querySelector('[data-reader-record-node="blockquote"]'),
      ).toBeNull();
    });
    expect(
      container.querySelector('[data-callout-variant="grammar"]'),
    ).toBeNull();
    expect(
      container.querySelector('[data-reader-record-node="sentence-analysis"]'),
    ).toBeNull();
    expect(
      container.querySelector('[data-reader-record-grammar-mark-id="grammar_mark_1"]'),
    ).toBeNull();
    expect(
      container.querySelector('[data-reader-record-vocabulary-mark-id="vocab_mark_1"]'),
    ).not.toBeNull();
    expect(
      container.querySelector(
        '[data-reader-record-user-highlight-asset-id="asset_highlight_1"]',
      ),
    ).not.toBeNull();
  });

  it("does not keep hidden enhancement selection context after switching to immersive mode", async () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const blockquote = container.querySelector<HTMLElement>(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquote).not.toBeNull();
    if (!blockquote) {
      throw new Error("Expected blockquote block");
    }

    selectTextInElement(blockquote, 0, 4);
    const actions = screen.getByTestId("reader-record-plate-selection-state");
    await waitFor(() => {
      expect(actions.dataset.readerRecordSelectionSurfaceKind).toBe("translation");
    });

    fireEvent.click(screen.getByRole("button", { name: "切换到沉浸模式" }));
    await waitFor(() => {
      expect(
        container.querySelector('[data-reader-record-node="blockquote"]'),
      ).toBeNull();
    });
    document.dispatchEvent(new Event("selectionchange"));

    await waitFor(() => {
      expect(actions.dataset.readerRecordSelectionSurfaceKind).not.toBe("translation");
    });
    expect(
      container.querySelector('[data-callout-variant="grammar"]'),
    ).toBeNull();
    expect(
      container.querySelector('[data-reader-record-node="sentence-analysis"]'),
    ).toBeNull();
  });

  it("renders vocab and grammar marks with locatable data attributes", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const vocab = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    const grammar = container.querySelector<HTMLElement>(
      '[data-reader-record-grammar-mark-id="grammar_mark_1"]',
    );

    expect(vocab?.dataset.readerRecordVocabularyKind).toBe("phrase_gloss");
    expect(grammar?.dataset.readerRecordGrammarMarkId).toBe("grammar_mark_1");
  });

  it("keeps vocabulary and grammar visuals on continuation leaves split by overlapping marks", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeOverlappingMarkSnapshot()} />,
    );

    const vocabFragments = Array.from(
      container.querySelectorAll<HTMLElement>(
        '[data-reader-record-vocabulary-mark-id="vocab_split_mark"]',
      ),
    );
    const grammarFragments = Array.from(
      container.querySelectorAll<HTMLElement>(
        '[data-reader-record-grammar-mark-id="grammar_split_mark"]',
      ),
    );

    expect(vocabFragments).toHaveLength(2);
    expect(grammarFragments).toHaveLength(2);
    expect(vocabFragments.map((fragment) => fragment.textContent)).toEqual([
      "Institutional ",
      "memory",
    ]);
    expect(grammarFragments.map((fragment) => fragment.textContent)).toEqual([
      "memory",
      " shapes",
    ]);

    for (const fragment of vocabFragments) {
      const stack = closestMarkStack(fragment);
      expect(stack?.className).toContain("reader-record-mark-stack");
      expect(stack?.className).toContain("reader-record-mark-stack--vocabulary");
      expect(stack?.dataset.readerRecordMarkStackKinds).toContain("phrase_gloss");
      expect(stack?.getAttribute("aria-label")).toContain("短语");
      expect(fragment.dataset.readerRecordVocabularyKind).toBe("phrase_gloss");
    }
    for (const fragment of grammarFragments) {
      const stack = closestMarkStack(fragment);
      expect(stack?.className).toContain("reader-record-mark-stack");
      expect(stack?.className).toContain("reader-record-mark-stack--grammar");
      expect(stack?.dataset.readerRecordMarkStackKinds).toContain("grammar_note");
      expect(stack?.getAttribute("aria-label")).toContain("语法");
      expect(fragment.dataset.readerRecordGrammarMarkId).toBe("grammar_split_mark");
    }
    const overlapStack = closestMarkStack(vocabFragments[1] ?? null);
    expect(overlapStack?.className).toContain("reader-record-mark-stack--vocabulary");
    expect(overlapStack?.className).toContain("reader-record-mark-stack--grammar");
    expect(overlapStack?.dataset.readerRecordMarkStackKinds).toContain("phrase_gloss");
    expect(overlapStack?.dataset.readerRecordMarkStackKinds).toContain("grammar_note");
    expect(vocabFragments[0]?.dataset.readerRecordVocabularyStartsHere).toBe("true");
    expect(vocabFragments[1]?.dataset.readerRecordVocabularyStartsHere).toBe("false");
    expect(grammarFragments[0]?.dataset.readerRecordGrammarStartsHere).toBe("true");
    expect(grammarFragments[1]?.dataset.readerRecordGrammarStartsHere).toBe("false");
  });

  it("renders user highlight marks with stable asset attributes", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot([makeUserAsset()])} />,
    );

    const highlight = container.querySelector<HTMLElement>(
      '[data-reader-record-user-highlight-asset-id="asset_highlight_1"]',
    );

    expect(highlight?.dataset.readerRecordUserHighlightAssetId).toBe("asset_highlight_1");
    expect(highlight?.dataset.readerRecordMarkStackKinds).toContain("user_highlight");
    expect(highlight?.textContent).toBe("memory");
  });

  it("targets actual renderLeaf stack classes for user highlight hover", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot([makeUserAsset()])} />,
    );
    const highlight = container.querySelector<HTMLElement>(
      '[data-reader-record-user-highlight-asset-id="asset_highlight_1"]',
    );
    const stack = closestMarkStack(highlight);
    const cssSource = readFileSync(
      resolve(process.cwd(), "src/app/globals.css"),
      "utf8",
    );

    expect(stack).not.toBeNull();
    expect(stack).toBe(highlight);
    expect(stack?.className).toContain("reader-record-mark-stack--user-highlight");
    expect(cssSource).toMatch(/\.reader-record-mark-stack--user-highlight:hover/);
    expect(cssSource).toMatch(
      /--reader-mark-grammar-line-soft:\s*rgba\(95,\s*78,\s*138,\s*0\.7\)/,
    );
    expect(cssSource).toMatch(/text-decoration-thickness:\s*0\.1em/);
    expect(cssSource).not.toMatch(
      /\.reader-record-mark-stack--grammar-active\s+\.reader-record-mark-hit--grammar/,
    );
    expect(cssSource).not.toMatch(/\.reader-record-mark-hit--/);
  });

  it("renders source text leaves as a single stack span without inner mark-hit wrappers", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeAnnotationMatrixSnapshot()} />,
    );

    const sourceLeaves = container.querySelectorAll<HTMLElement>(
      '[data-reader-record-leaf="segment_text"]',
    );
    expect(sourceLeaves.length).toBeGreaterThan(0);

    for (const leaf of sourceLeaves) {
      expect(leaf.querySelector(".reader-record-mark-hit--grammar")).toBeNull();
      expect(leaf.querySelector(".reader-record-mark-hit--vocabulary")).toBeNull();
      expect(leaf.querySelector(".reader-record-mark-hit--user-highlight")).toBeNull();
      expect(leaf.querySelector(".reader-record-mark-hit--user-note")).toBeNull();
      expect(leaf.querySelector("[data-reader-record-mark-entry]")).toBeNull();
    }
  });

  it("keeps overlap leaf as a single stack span with vocabulary+grammar kinds and data attrs", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeAnnotationMatrixSnapshot()} />,
    );

    const overlap = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_matrix_mark"]',
    );
    expect(overlap).not.toBeNull();
    if (!overlap) {
      throw new Error("Expected overlap leaf");
    }

    // 同一个 span 同时承载 vocabulary 和 grammar 的 stack class 与 data attrs
    expect(overlap.className).toContain("reader-record-mark-stack--vocabulary");
    expect(overlap.className).toContain("reader-record-mark-stack--grammar");
    expect(overlap.dataset.readerRecordVocabularyMarkId).toBe("vocab_matrix_mark");
    expect(overlap.dataset.readerRecordGrammarMarkId).toBe("grammar_matrix_mark");
    expect(overlap.dataset.readerRecordGrammarItemId).toBe("grammar_matrix_item");
    expect(overlap.dataset.readerRecordMarkStackKinds).toContain("phrase_gloss");
    expect(overlap.dataset.readerRecordMarkStackKinds).toContain("grammar_note");

    // 不应存在内层 mark-hit wrapper
    expect(overlap.querySelector(".reader-record-mark-hit--vocabulary")).toBeNull();
    expect(overlap.querySelector(".reader-record-mark-hit--grammar")).toBeNull();
  });

  it("does not enlarge a partial selection that crosses from plain text into the grammar+vocabulary overlap", () => {
    // SOURCE_TEXT = "Institutional memory shapes policy choices."
    // vocabulary "memory" 位于 [14, 20)，grammar "memory shapes" 位于 [14, 27)。
    // 源文本会被切分成多个 leaf：plain "Institutional " + overlap "memory shapes" + ...
    // 从普通文本末尾空格拖入 overlap 首字符 "m"，native selection 应保持精确文本，不扩大到 vocabulary 边界。
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeAnnotationMatrixSnapshot()} />,
    );

    const sourceLeaves = Array.from(
      container.querySelectorAll<HTMLElement>(
        '[data-reader-record-leaf="segment_text"]',
      ),
    );
    expect(sourceLeaves.length).toBeGreaterThanOrEqual(2);

    const plainLeaf = sourceLeaves[0]!;
    const overlapLeaf = sourceLeaves.find((leaf) =>
      leaf.querySelector('[data-reader-record-vocabulary-mark-id]') ||
      leaf.matches("[data-reader-record-vocabulary-mark-id]"),
    );
    expect(overlapLeaf).toBeDefined();

    const plainText = firstTextNode(plainLeaf);
    const overlapText = firstTextNode(overlapLeaf!);
    expect(plainText).toBeTruthy();
    expect(overlapText).toBeTruthy();

    // 选取 plain leaf 最后 1 字符 " " + overlap leaf 前 1 字符 "m"
    selectAcrossElements(plainLeaf, plainText!.length - 1, overlapLeaf!, 1);

    expect(window.getSelection()?.isCollapsed).toBe(false);
    expect(window.getSelection()?.toString()).toBe(" m");

    // overlap leaf 的 startsHere 仍应保留为 true，未被 selection 改写
    const overlap = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_matrix_mark"]',
    );
    expect(overlap?.dataset.readerRecordVocabularyStartsHere).toBe("true");
  });

  it("keeps system marks and user marks coexisting on the source text", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot([makeUserAsset()])} />,
    );

    const paragraph = container.querySelector<HTMLElement>(
      '[data-reader-record-node="paragraph"]',
    );
    const vocab = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    const userHighlight = container.querySelector<HTMLElement>(
      '[data-reader-record-user-highlight-asset-id="asset_highlight_1"]',
    );

    expect(paragraph?.textContent).toContain(SOURCE_TEXT);
    expect(vocab?.dataset.readerRecordVocabularyKind).toBe("phrase_gloss");
    expect(userHighlight?.dataset.readerRecordUserHighlightAssetId).toBe("asset_highlight_1");
  });

  it("resolves overlapping mark clicks by vocabulary, grammar, user note, then user highlight priority", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const scrollSpy = vi
      .spyOn(HTMLElement.prototype, "scrollIntoView")
      .mockImplementation(() => undefined);
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeAnnotationMatrixSnapshot()} />,
    );

    const vocab = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_matrix_mark"]',
    );
    expect(vocab).not.toBeNull();
    if (!vocab) {
      throw new Error("Expected vocabulary mark");
    }
    fireEvent.click(vocab);

    expect(await screen.findByTestId("reader-record-plate-lookup-panel")).toBeTruthy();
    expect(screen.queryByTestId("reader-record-inline-comment-panel")).toBeNull();

    const grammarOnly = Array.from(
      container.querySelectorAll<HTMLElement>(
        '[data-reader-record-grammar-mark-id="grammar_matrix_mark"]',
      ),
    ).find((element) => element.textContent?.includes("shapes"));
    expect(grammarOnly).toBeDefined();
    if (!grammarOnly) {
      throw new Error("Expected grammar-only fragment");
    }
    fireEvent.click(grammarOnly);

    const grammarCallout = container.querySelector<HTMLElement>(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_matrix_item"]',
    );
    expect(grammarCallout).not.toBeNull();
    await waitFor(() => {
      expect(grammarCallout?.dataset.readerRecordGrammarActive).toBe("true");
      expect(scrollSpy).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.queryByTestId("reader-record-plate-lookup-panel")).toBeNull();
    });
    expect(screen.queryByTestId("reader-record-inline-comment-panel")).toBeNull();

    const noteOnly = Array.from(
      container.querySelectorAll<HTMLElement>(
        '[data-reader-record-user-note-asset-ids~="asset_note_matrix"]',
      ),
    ).find((element) => element.textContent?.includes("Institutional"));
    expect(noteOnly).toBeDefined();
    if (!noteOnly) {
      throw new Error("Expected note-only fragment");
    }
    fireEvent.click(noteOnly);

    const notePanel = await screen.findByTestId("reader-record-inline-comment-panel");
    expect(notePanel.textContent).toContain("Matrix note for the sentence opening.");
  });

  it("does not trigger mark actions while a non-collapsed native selection exists", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const scrollSpy = vi
      .spyOn(HTMLElement.prototype, "scrollIntoView")
      .mockImplementation(() => undefined);
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeAnnotationMatrixSnapshot()} />,
    );

    const vocab = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_matrix_mark"]',
    );
    expect(vocab).not.toBeNull();
    if (!vocab) {
      throw new Error("Expected vocabulary mark");
    }

    selectTextInElement(vocab, 0, "memory".length);
    expect(window.getSelection()?.isCollapsed).toBe(false);
    scrollSpy.mockClear();

    fireEvent.click(vocab);

    expect(screen.queryByTestId("reader-record-plate-lookup-panel")).toBeNull();
    expect(screen.queryByTestId("reader-record-inline-comment-panel")).toBeNull();
    expect(scrollSpy).not.toHaveBeenCalled();
    expect(
      fetchMock.mock.calls.some(
        ([url]) => typeof url === "string" && url.includes("/api/web/dict"),
      ),
    ).toBe(false);
  });

  it("suppresses the following mark click after pointer drag", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot([makePolicyHighlightAsset()])} />,
    );
    const highlight = container.querySelector<HTMLElement>(
      '[data-reader-record-user-highlight-asset-id="asset_highlight_policy"]',
    );
    expect(highlight).not.toBeNull();
    if (!highlight) {
      throw new Error("Expected policy highlight mark");
    }

    fireEvent.mouseDown(highlight, { clientX: 10, clientY: 10 });
    fireEvent.mouseMove(highlight, { clientX: 18, clientY: 10 });
    fireEvent.mouseUp(highlight, { clientX: 18, clientY: 10 });
    fireEvent.click(highlight);

    expect(screen.queryByRole("button", { name: "删除高亮" })).toBeNull();
  });

  it("keeps every grammar leaf active across vocabulary overlap and same-item hover transitions", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeAnnotationMatrixSnapshot()} />,
    );
    const grammarLeaves = () =>
      Array.from(
        container.querySelectorAll<HTMLElement>(
          '[data-reader-record-leaf="segment_text"][data-reader-record-grammar-item-id="grammar_matrix_item"]',
        ),
      );
    const overlapLeaf = grammarLeaves().find((element) =>
      element.textContent?.includes("memory"),
    );
    const grammarOnlyLeaf = grammarLeaves().find((element) =>
      element.textContent?.includes("shapes"),
    );
    expect(overlapLeaf).toBeDefined();
    expect(grammarOnlyLeaf).toBeDefined();
    if (!overlapLeaf || !grammarOnlyLeaf) {
      throw new Error("Expected overlapped and grammar-only leaves");
    }

    fireEvent.mouseEnter(overlapLeaf);
    await waitFor(() => {
      for (const leaf of grammarLeaves()) {
        expect(leaf.dataset.readerRecordGrammarActive).toBe("true");
        expect(leaf.className).toContain("reader-record-mark-stack--grammar-active");
      }
    });

    fireEvent.mouseLeave(overlapLeaf, { relatedTarget: grammarOnlyLeaf });
    await waitFor(() => {
      for (const leaf of grammarLeaves()) {
        expect(leaf.dataset.readerRecordGrammarActive).toBe("true");
      }
    });

    fireEvent.mouseLeave(grammarOnlyLeaf, { relatedTarget: document.body });
    await waitFor(() => {
      for (const leaf of grammarLeaves()) {
        expect(leaf.dataset.readerRecordGrammarActive).toBeUndefined();
      }
    });
  });

  it("keeps grammar hover active when a stacked vocabulary leaf opens vocabulary inspect", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeAnnotationMatrixSnapshot()} />,
    );
    const grammarLeaves = () =>
      Array.from(
        container.querySelectorAll<HTMLElement>(
          '[data-reader-record-leaf="segment_text"][data-reader-record-grammar-item-id="grammar_matrix_item"]',
        ),
      );
    const overlapLeaf = grammarLeaves().find((element) =>
      element.textContent?.includes("memory"),
    );
    expect(overlapLeaf).toBeDefined();
    if (!overlapLeaf) {
      throw new Error("Expected overlapped vocabulary/grammar leaf");
    }

    fireEvent.mouseEnter(overlapLeaf);
    await waitFor(() => {
      expect(overlapLeaf.dataset.readerRecordGrammarActive).toBe("true");
    });
    fireEvent.click(overlapLeaf);

    expect(await screen.findByTestId("reader-record-plate-lookup-panel")).toBeTruthy();
    await waitFor(() => {
      for (const leaf of grammarLeaves()) {
        expect(leaf.dataset.readerRecordGrammarActive).toBe("true");
      }
    });
  });

  it("opens vocabulary inspect from a continuation fragment, not only startsHere", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeOverlappingMarkSnapshot()} />,
    );

    const continuation = Array.from(
      container.querySelectorAll<HTMLElement>(
        '[data-reader-record-vocabulary-mark-id="vocab_split_mark"]',
      ),
    ).find((element) => element.dataset.readerRecordVocabularyStartsHere === "false");
    expect(continuation).toBeDefined();
    if (!continuation) {
      throw new Error("Expected vocabulary continuation fragment");
    }

    fireEvent.click(continuation);

    expect(await screen.findByTestId("reader-record-plate-lookup-panel")).toBeTruthy();
    expect(
      fetchMock.mock.calls.filter(
        ([url]) => typeof url === "string" && url.includes("/api/web/dict/lookup"),
      ),
    ).toHaveLength(0);
  });

  it("coordinates grammar source and callout active state by itemId without opening Quick Peek", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const scrollSpy = vi
      .spyOn(HTMLElement.prototype, "scrollIntoView")
      .mockImplementation(() => undefined);
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const sourceGrammar = container.querySelector<HTMLElement>(
      '[data-reader-record-leaf="segment_text"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );
    const callout = container.querySelector<HTMLElement>(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );
    expect(sourceGrammar).not.toBeNull();
    expect(callout).not.toBeNull();
    if (!sourceGrammar || !callout) {
      throw new Error("Expected grammar source and callout");
    }
    const calloutContent = callout.querySelector<HTMLElement>(
      '[data-reader-record-markdown-content="plate"]',
    );
    expect(calloutContent).not.toBeNull();
    if (!calloutContent) {
      throw new Error("Expected grammar callout content");
    }
    expect(callout.dataset.readerRecordCalloutCollapsed).toBe("true");
    expect(calloutContent.hidden).toBe(true);

    fireEvent.mouseEnter(callout);
    await waitFor(() => {
      expect(sourceGrammar.dataset.readerRecordGrammarActive).toBe("true");
    });
    fireEvent.mouseLeave(callout);
    await waitFor(() => {
      expect(sourceGrammar.dataset.readerRecordGrammarActive).toBeUndefined();
    });

    scrollSpy.mockClear();
    fireEvent.mouseEnter(sourceGrammar);
    await waitFor(() => {
      expect(callout.dataset.readerRecordGrammarActive).toBe("true");
    });
    expect(calloutContent.hidden).toBe(true);
    expect(scrollSpy).not.toHaveBeenCalled();
    fireEvent.mouseLeave(sourceGrammar);

    fireEvent.click(sourceGrammar);
    await waitFor(() => {
      expect(callout.dataset.readerRecordGrammarActive).toBe("true");
      expect(callout.dataset.readerRecordCalloutCollapsed).toBe("false");
      expect(calloutContent.hidden).toBe(false);
      expect(scrollSpy).toHaveBeenCalled();
    });
    expect(screen.queryByTestId("reader-record-plate-lookup-panel")).toBeNull();
  });

  it("clicking a grammar callout briefly activates the source grammar span without scrolling", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );
    const scrollSpy = vi
      .spyOn(HTMLElement.prototype, "scrollIntoView")
      .mockImplementation(() => undefined);
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const sourceGrammar = container.querySelector<HTMLElement>(
      '[data-reader-record-leaf="segment_text"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );
    const callout = container.querySelector<HTMLElement>(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );
    expect(sourceGrammar).not.toBeNull();
    expect(callout).not.toBeNull();
    if (!sourceGrammar || !callout) {
      throw new Error("Expected grammar source and callout");
    }

    scrollSpy.mockClear();
    fireEvent.click(callout);

    await waitFor(() => {
      expect(sourceGrammar.dataset.readerRecordGrammarActive).toBe("true");
    });
    expect(scrollSpy).not.toHaveBeenCalled();
  });

  it("keeps user note quote classes out of legacy blue dashed and border/ring visuals", () => {
    const noteAsset = makeUserAsset({
      asset_id: "asset_note_visual",
      asset_type: "note",
      note_text: "Visual note.",
    });
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot([noteAsset])} />,
    );
    const noteMark = container.querySelector<HTMLElement>(
      '[data-reader-record-user-note-asset-ids~="asset_note_visual"]',
    );
    expect(noteMark).not.toBeNull();
    expect(noteMark?.className).toContain("reader-record-mark-stack--user-note");
    expect(noteMark?.className).not.toMatch(/blue|decoration-blue|dashed/);
    expect(noteMark?.className).not.toMatch(/border|ring|inset/);
  });

  it("downgrades AI vocabulary background when it overlaps a user asset", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot([makeUserAsset()])} />,
    );
    const vocab = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    const stack = closestMarkStack(vocab);
    const userHighlight = container.querySelector<HTMLElement>(
      '[data-reader-record-user-highlight-asset-id="asset_highlight_1"]',
    );

    expect(vocab).not.toBeNull();
    expect(userHighlight).not.toBeNull();
    expect(stack?.className).toContain("reader-record-mark-stack--user-highlight");
    expect(vocab?.className).toContain("reader-record-mark-stack--vocabulary");
    expect(vocab?.className).toContain(
      "reader-record-mark-stack--vocabulary-downgraded",
    );
    expect(userHighlight?.className).toContain(
      "reader-record-mark-stack--user-highlight",
    );
  });

  it("renders the Chinese title from snapshot.record.display_title_zh in the header", () => {
    const snapshot = makeSnapshot();
    snapshot.record.display_title_zh = "阅读记录 Plate 测试标题";
    snapshot.record.title_generation_status = "succeeded";
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);

    const titleEl = container.querySelector<HTMLElement>(
      "[data-reader-record-reading-title]",
    );
    expect(titleEl).not.toBeNull();
    expect(titleEl?.tagName).toBe("H1");
    expect(titleEl?.textContent).toBe("阅读记录 Plate 测试标题");
    expect(titleEl?.dataset.readerRecordTitleState).toBe("succeeded");
  });

  it("does not promote record.title to the succeeded masthead when display_title_zh is missing", () => {
    const snapshot = makeSnapshot();
    snapshot.record.display_title_zh = null;
    snapshot.record.title_generation_status = "succeeded";
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={snapshot} />,
    );

    const titleEl = container.querySelector<HTMLElement>(
      "[data-reader-record-reading-title]",
    );
    expect(titleEl?.textContent).not.toBe(
      "Reader Record Plate Surface Fixture",
    );
    expect(titleEl?.dataset.readerRecordTitleState).not.toBe("succeeded");
  });

  it("renders pending title state as a skeleton and does not promote record.title to the Chinese masthead", () => {
    const snapshot = makeSnapshot();
    snapshot.record.display_title_zh = null;
    snapshot.record.title_generation_status = "pending";
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);

    const titleEl = container.querySelector<HTMLElement>(
      "[data-reader-record-reading-title]",
    );
    expect(titleEl).not.toBeNull();
    expect(titleEl?.tagName).toBe("H1");
    expect(titleEl?.textContent).not.toContain("标题生成中");
    expect(titleEl?.querySelector(".reader-skeleton")).not.toBeNull();
    expect(titleEl?.dataset.readerRecordTitleState).toBe("pending");
    expect(headerSourceTitleElement(container)).toBeNull();
  });

  it("renders failed_retryable title state with record.title as secondary source title", () => {
    const snapshot = makeSnapshot();
    snapshot.record.display_title_zh = null;
    snapshot.record.title_generation_status = "failed_retryable";
    snapshot.record.title_generation_error_code = "llm_timeout";
    snapshot.record.title_generation_error_message = "LLM 调用超时";
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);

    const titleEl = container.querySelector<HTMLElement>(
      "[data-reader-record-reading-title]",
    );
    expect(titleEl).not.toBeNull();
    expect(titleEl?.textContent).toBe("标题生成失败");
    expect(titleEl?.dataset.readerRecordTitleState).toBe("failed_retryable");

    const sourceTitleEl = headerSourceTitleElement(container);
    expect(sourceTitleEl).not.toBeNull();
    expect(sourceTitleEl?.textContent).toContain(
      "Reader Record Plate Surface Fixture",
    );
  });

  it("omits the title element when succeeded status has no display_title_zh (fail-closed)", () => {
    // 契约保证 succeeded 必有 display_title_zh；前端对违反契约的数据 fail-closed，
    // 不渲染任何标题元素，而不是用源标题冒充成功中文标题。
    const snapshot = makeSnapshot();
    snapshot.record.display_title_zh = null;
    snapshot.record.title_generation_status = "succeeded";
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);

    const titleEl = container.querySelector<HTMLElement>(
      "[data-reader-record-reading-title]",
    );
    expect(titleEl).toBeNull();
  });

  it("does not render a header eyebrow with mode label and date", () => {
    render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const header = screen.getByTestId("reader-record-plate-header");
    expect(header).toBeTruthy();
    // Hero eyebrow 已在 R1 中移除；模式标签只出现在 More Menu 与 action bar tab。
    expect(header.textContent).not.toContain("精读模式 · 2026年6月24日");
    expect(header.textContent).not.toContain("2026年6月24日");
  });

  it("renders header with progress status and metadata", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const header = screen.getByTestId("reader-record-plate-header");
    expect(header).toBeTruthy();
    expect(header.textContent).toContain("解析生成中");

    const progressStatus = container.querySelector<HTMLElement>(
      "[data-reader-record-progress-status]",
    );
    expect(progressStatus?.dataset.readerRecordProgressStatus).toBe(
      "readable_enhancing",
    );
  });

  it("does not show estimated reading minutes in the header", () => {
    render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const header = screen.getByTestId("reader-record-plate-header");
    expect(header.textContent).not.toContain("分钟阅读");
    expect(header.textContent).not.toMatch(/约\s*\d+\s*分钟/);
  });

  it("does not use sentence count as the primary reading metric in the header", () => {
    render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const header = screen.getByTestId("reader-record-plate-header");
    // 旧实现会把 anchor_segments.length 渲染成 "1 句"；新版 action bar 不应再展示该 metric。
    expect(header.textContent).not.toMatch(/^\s*1\s*句$/);
    expect(header.textContent).not.toContain("1 句");
    expect(header.textContent).not.toMatch(/\d+\s*句/);
  });

  it("centers the main document column independently and attaches the outline to its right", () => {
    render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const surface = screen.getByTestId("reader-record-plate-surface");
    // Surface-level sticky top bar is a sibling of the canvas section, not inside it.
    const topBar = surface.querySelector<HTMLElement>('[data-testid="reader-record-top-bar"]');
    expect(topBar).not.toBeNull();
    expect(topBar?.parentElement).toBe(surface);
    expect(topBar?.dataset.readerRecordTopBarLayer).toBe("surface");

    // Top bar 不再使用内容列居中约束。
    expect(topBar?.className).not.toContain("mx-auto");
    expect(topBar?.className).not.toContain("max-w-[var(--reader-record-main-width)]");

    const contentSection = surface.querySelector("section");
    expect(contentSection).not.toBeNull();

    // Hero / header 与正文主列共享同一个独立居中的主列。
    const canvas = contentSection?.querySelector(".reader-record-canvas");
    expect(canvas).not.toBeNull();
    const body = canvas?.querySelector(".reader-record-canvas__body");
    expect(body).not.toBeNull();

    const mainColumn = body?.querySelector(".reader-record-main");
    expect(mainColumn).not.toBeNull();
    expect(mainColumn?.className).toContain("reader-record-main");

    const headerColumn = mainColumn?.querySelector(".reader-header-band-inner");
    expect(headerColumn).not.toBeNull();
    expect(headerColumn?.className).toContain("max-w-[var(--reader-record-main-width)]");
    expect(headerColumn?.querySelector('[data-testid="reader-record-plate-header"]')).not.toBeNull();

    const plateDocument = mainColumn?.querySelector(".reader-record-plate-document");
    expect(plateDocument).not.toBeNull();
    const contentColumn = plateDocument?.parentElement;
    expect(contentColumn?.className).toContain("max-w-[var(--reader-record-main-width)]");

    // Outline slot is a sibling of the main column, absolutely positioned to the right.
    const outlineSlot = body?.querySelector(".reader-record-outline-slot");
    expect(outlineSlot).not.toBeNull();
    expect(outlineSlot?.parentElement).toBe(body);

    const globalsSource = readFileSync(
      resolve(process.cwd(), "src/app/globals.css"),
      "utf-8",
    );
    expect(globalsSource).toContain(".reader-record-outline-slot {");
    expect(globalsSource).toContain("position: absolute;");
    expect(globalsSource).toContain(
      "right: var(--reader-record-outline-right-offset);",
    );
  });

  it("anchors the navigation rail inside the canvas outline slot", () => {
    render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const outlineSlot = document.querySelector(".reader-record-outline-slot");
    expect(outlineSlot).not.toBeNull();

    const rail = outlineSlot?.querySelector('[data-testid="reader-record-navigation-rail"]');
    expect(rail).not.toBeNull();
    expect(rail?.getAttribute("data-layout")).toBe("canvas");
  });

  it("opens Ask Claread and adds the ask-open modifier to the canvas without overlapping the outline slot", async () => {
    installReaderAskFetchMock();
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const canvas = container.querySelector(".reader-record-canvas");
    expect(canvas).not.toBeNull();
    // Ask closed by default: modifier not applied yet.
    expect(canvas?.className).not.toContain("reader-record-canvas--ask-open");

    // Rail lives inside the canvas outline slot.
    const outlineSlot = canvas?.querySelector(".reader-record-outline-slot");
    expect(outlineSlot).not.toBeNull();

    const memoryMark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) {
      throw new Error("Expected memory mark");
    }

    selectTextInElement(memoryMark, 0, "memory".length);
    const askButton = await waitForSelectionAction(container, "ask");
    await openAskPanelFromToolbar(askButton);

    await waitFor(() => {
      expect(canvas?.className).toContain("reader-record-canvas--ask-open");
    });

    // Ask panel is rendered as a sibling of the canvas, not nested inside it.
    const askPanel = container.querySelector(".ai-workspace-panel");
    expect(askPanel).not.toBeNull();
    expect(askPanel?.parentElement).toBe(canvas?.parentElement);
  });

  it("renders the action bar as a single horizontal control strip on desktop", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot()} />,
    );

    const actionBar = container.querySelector<HTMLElement>(
      '[data-reader-record-action-bar="true"]',
    );
    expect(actionBar).not.toBeNull();
    expect(actionBar?.className).toContain("sm:flex-row");
    expect(actionBar?.className).not.toContain("flex-wrap");
    expect(actionBar?.className).toContain("border-t");
    expect(actionBar?.className).toContain("border-b");
    expect(actionBar?.className).toContain("border-hairline");

    const rightButtons = actionBar?.querySelector(
      ".flex.items-stretch.divide-x.divide-hairline",
    );
    expect(rightButtons).not.toBeNull();
    expect(rightButtons?.className).toContain("divide-x");
    expect(rightButtons?.className).toContain("divide-hairline");
  });

  it("renders the progress status as a chip with Sparkles icon instead of a blue dot", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot()} />,
    );

    const progressStatus = container.querySelector<HTMLElement>(
      "[data-reader-record-progress-status]",
    );
    expect(progressStatus).not.toBeNull();
    expect(progressStatus?.className).toContain("rounded-[0.5rem]");
    expect(progressStatus?.className).toContain("bg-surface-warm");
    expect(progressStatus?.textContent).toContain("解析生成中");

    const blueDot = container.querySelector(
      ".rounded-full.bg-lens-blue",
    );
    expect(blueDot).toBeNull();
  });

  it("maps raw source_type 'text' to user-readable '粘贴导入' in bottom metadata", () => {
    const snapshot = makeSnapshot();
    snapshot.record.source_type = "text";
    render(<ReaderRecordPlateSurface snapshot={snapshot} />);

    const header = screen.getByTestId("reader-record-plate-header");
    expect(header.textContent).toContain("来源 粘贴导入");
    expect(header.textContent).not.toContain("来源 text");
    expect(header.textContent).not.toContain("数据来源 text");
    // 无 sourceUrl 时底部来源标签只出现一次，不在右侧重复显示。
    expect((header.textContent?.match(/来源 粘贴导入/g) ?? []).length).toBe(1);
  });

  it("renders the right action buttons with icon + label + sublabel", () => {
    render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const header = screen.getByTestId("reader-record-plate-header");
    // Hero action bar 右侧只保留精读/沉浸两个阅读状态 tab。
    expect(header.textContent).toContain("精读");
    expect(header.textContent).toContain("逐句解析");
    expect(header.textContent).toContain("沉浸");
    expect(header.textContent).toContain("专注阅读");
    // 收藏与阅读设置已迁移到 sticky top bar。
    expect(header.textContent).not.toContain("收藏");
    expect(header.textContent).not.toContain("阅读设置");
    expect(header.textContent).not.toContain("版式与偏好");
  });

  it("shows source-only word count from stable source text in the header", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot()} />,
    );

    // SOURCE_TEXT = "Institutional memory shapes policy choices." → 5 词
    const wordCountEl = container.querySelector<HTMLElement>(
      "[data-reader-record-source-word-count]",
    );
    expect(wordCountEl).not.toBeNull();
    expect(wordCountEl?.textContent).toBe("5 词");
  });

  it("counts source words correctly across multiple segments with separator leaves", () => {
    const snapshot = makeSnapshot();
    const unit = snapshot.value[0];
    const sourceBlock = unit.children.find(
      (child) => child.type === "reader_source_block",
    );
    if (sourceBlock && sourceBlock.type === "reader_source_block") {
      sourceBlock.children = [
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
          base_end_utf16: 11,
          unit_start_utf16: 0,
          unit_end_utf16: 11,
          text_hash: "seg_hash_1",
          hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
          children: [
            {
              text: "Hello world",
              owner: "stable",
              lock_source: true,
              source_role: "segment_text",
              base_start_utf16: 0,
              base_end_utf16: 11,
              anchor_segment_id: "seg_1",
              segment_start_utf16: 0,
              segment_end_utf16: 11,
            },
          ],
        },
        {
          text: " ",
          owner: "stable",
          lock_source: true,
          source_role: "separator",
          base_start_utf16: 11,
          base_end_utf16: 12,
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
          base_start_utf16: 12,
          base_end_utf16: 27,
          unit_start_utf16: 12,
          unit_end_utf16: 27,
          text_hash: "seg_hash_2",
          hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
          children: [
            {
              text: "next sentence",
              owner: "stable",
              lock_source: true,
              source_role: "segment_text",
              base_start_utf16: 12,
              base_end_utf16: 27,
              anchor_segment_id: "seg_2",
              segment_start_utf16: 0,
              segment_end_utf16: 15,
            },
          ],
        },
      ];
    }

    const { container } = render(
      <ReaderRecordPlateSurface snapshot={snapshot} />,
    );

    const wordCountEl = container.querySelector<HTMLElement>(
      "[data-reader-record-source-word-count]",
    );
    expect(wordCountEl).not.toBeNull();
    expect(wordCountEl?.textContent).toBe("4 词");
  });

  it("omits source word count when stable source text is empty", () => {
    const snapshot = makeSnapshot();
    // 移除 unit 的 source_block children，模拟无法可靠获取原文的场景。
    const unit = snapshot.value[0];
    const sourceBlock = unit.children.find(
      (child) => child.type === "reader_source_block",
    );
    if (sourceBlock && sourceBlock.type === "reader_source_block") {
      sourceBlock.children = [];
    }
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={snapshot} />,
    );

    const wordCountEl = container.querySelector<HTMLElement>(
      "[data-reader-record-source-word-count]",
    );
    expect(wordCountEl).toBeNull();
  });

  it("does not render a leading separator dot in bottom metadata when source info is absent", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot()} />,
    );

    const header = container.querySelector<HTMLElement>(
      '[data-testid="reader-record-plate-header"]',
    );
    const bottomMetadata = header?.querySelector(
      ".mt-3.flex.flex-col.sm\\:flex-row.sm\\:items-center.justify-between",
    );
    expect(bottomMetadata?.textContent).toBeTruthy();
    expect(bottomMetadata?.textContent?.trim() ?? "").not.toMatch(/^·/);
    expect(bottomMetadata?.textContent).toContain("来源 粘贴导入");
  });

  it("shows reading goal and variant label when both fields are present and mappable", () => {
    const snapshot = makeSnapshot();
    snapshot.record.reading_goal = "exam";
    snapshot.record.reading_variant = "kaoyan";
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={snapshot} />,
    );

    const labelEl = container.querySelector<HTMLElement>(
      "[data-reader-record-reading-goal-variant]",
    );
    expect(labelEl).not.toBeNull();
    expect(labelEl?.textContent).toBe("备考精读 · 考研");
  });

  it("omits reading goal and variant label when variant cannot be mapped", () => {
    const snapshot = makeSnapshot();
    snapshot.record.reading_goal = "exam";
    snapshot.record.reading_variant =
      "this_variant_does_not_exist" as typeof snapshot.record.reading_variant;
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={snapshot} />,
    );

    const labelEl = container.querySelector<HTMLElement>(
      "[data-reader-record-reading-goal-variant]",
    );
    expect(labelEl).toBeNull();
  });

  it("keeps only intensive and immersive mode tabs on the right side of the action bar", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot()} />,
    );

    const actionBar = container.querySelector<HTMLElement>(
      "[data-reader-record-action-bar]",
    );
    expect(actionBar).not.toBeNull();

    expect(
      container.querySelector('[data-reader-record-mode-option="intensive"]'),
    ).not.toBeNull();
    expect(
      container.querySelector('[data-reader-record-mode-option="immersive"]'),
    ).not.toBeNull();
    // 阅读设置入口已移除，收藏已迁移到 sticky top bar。
    expect(
      container.querySelector('[data-reader-record-action="open-settings"]'),
    ).toBeNull();
    expect(
      actionBar?.querySelector('button[title="收藏"]'),
    ).toBeNull();
    // 旧版 pill segmented control 已被移除，新版使用 hairline action bar
    expect(
      container.querySelector('[data-reader-record-mode-switch="intensive"][role="group"]'),
    ).toBeNull();
    // Hero action bar 只保留精读、沉浸两个状态 tab
    expect(
      screen.getByRole("button", { name: "切换到精读模式" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "切换到沉浸模式" }),
    ).toBeTruthy();
  });

  it("renders a sticky top bar with title, favorite, and more menu trigger", () => {
    const snapshot = makeSnapshot();
    snapshot.record.display_title_zh = "顶部标题测试";
    snapshot.record.title_generation_status = "succeeded";
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);

    const topBar = screen.getByTestId("reader-record-top-bar");
    expect(topBar).toBeTruthy();
    expect(topBar.className).toContain("sticky");
    expect(topBar.className).toContain("z-20");
    // Top bar 必须是页面级 operation layer，不能再被内容列居中约束。
    expect(topBar.className).not.toContain("-mx-5");
    expect(topBar.className).not.toContain("mx-auto");
    expect(topBar.className).not.toContain("max-w-[82ch]");

    const titleEl = topBar.querySelector<HTMLElement>(
      '[data-reader-record-top-bar-title-state="succeeded"]',
    );
    expect(titleEl).not.toBeNull();
    expect(titleEl?.textContent).toBe("顶部标题测试");

    // 收藏按钮只在 top bar（ FavoriteButton icon-only 默认 title="收藏"）
    expect(topBar.querySelector('button[title="收藏"]')).not.toBeNull();
    expect(
      screen.getByTestId("reader-record-plate-header").querySelector('button[title="收藏"]'),
    ).toBeNull();

    const moreTrigger = screen.getByTestId("reader-record-more-menu-trigger");
    expect(moreTrigger).toBeTruthy();

    // 标题 cluster 居左，操作 cluster 居右，不跟随 82ch 内容列
    const titleCluster = topBar.querySelector<HTMLElement>(
      '[data-reader-record-top-bar-title-state]',
    )?.parentElement;
    expect(titleCluster?.className).toContain("truncate");
    expect(titleCluster?.className).toContain("text-left");
    expect(titleCluster?.className).toContain("max-w-[min(46vw,36rem)]");

    const actionCluster = moreTrigger.parentElement;
    expect(actionCluster?.className).toContain("ml-auto");
    expect(actionCluster?.className).toContain("shrink-0");
  });

  it("renders top bar title skeleton when title generation is pending", () => {
    const snapshot = makeSnapshot();
    snapshot.record.display_title_zh = null;
    snapshot.record.title_generation_status = "pending";
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);

    const topBar = screen.getByTestId("reader-record-top-bar");
    const titleEl = topBar.querySelector<HTMLElement>(
      '[data-reader-record-top-bar-title-state="pending"]',
    );
    expect(titleEl).not.toBeNull();
    expect(titleEl?.textContent).not.toContain("标题生成中");
    expect(titleEl?.className).toContain("reader-skeleton");
  });

  it("renders top bar fallback labels for failed_retryable, migration, and empty states", () => {
    const retrySnapshot = makeSnapshot();
    retrySnapshot.record.display_title_zh = null;
    retrySnapshot.record.title_generation_status = "failed_retryable";
    const { container: retryContainer } = render(
      <ReaderRecordPlateSurface snapshot={retrySnapshot} />,
    );
    expect(
      retryContainer.querySelector(
        '[data-reader-record-top-bar-title-state="failed_retryable"]',
      )?.textContent,
    ).toContain("标题生成失败");

    cleanup();

    const migrationSnapshot = makeSnapshot();
    migrationSnapshot.record.display_title_zh = null;
    migrationSnapshot.record.title_generation_status = null as unknown as ReaderTitleGenerationStatus;
    const { container: migrationContainer } = render(
      <ReaderRecordPlateSurface snapshot={migrationSnapshot} />,
    );
    // migration fallback 使用 record.title，此处 fixture 为 "Reader Record Plate Surface Fixture"
    expect(
      migrationContainer.querySelector(
        '[data-reader-record-top-bar-title-state="migration_fallback"]',
      )?.textContent,
    ).toContain("Reader Record Plate Surface Fixture");

    cleanup();

    const emptySnapshot = makeSnapshot();
    emptySnapshot.record.title = "";
    emptySnapshot.record.display_title_zh = null;
    emptySnapshot.record.title_generation_status = null as unknown as ReaderTitleGenerationStatus;
    const { container: emptyContainer } = render(
      <ReaderRecordPlateSurface snapshot={emptySnapshot} />,
    );
    expect(
      emptyContainer.querySelector(
        '[data-reader-record-top-bar-title-state="empty"]',
      )?.textContent,
    ).toContain("阅读记录");
  });

  it("opens the more menu and exposes reading mode, typography, and article actions", async () => {
    const user = userEvent.setup();
    render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    await user.click(screen.getByTestId("reader-record-more-menu-trigger"));

    const menu = screen.getByTestId("reader-record-more-menu-content");
    expect(menu).toBeTruthy();
    expect(menu.dataset.readerRecordMoreMenuPanel).toBe("true");
    expect(menu.className).toContain("w-[340px]");
    expect(menu.textContent).toContain("阅读体验");
    expect(menu.querySelector('[data-reader-record-more-mode="intensive"]')).not.toBeNull();
    expect(menu.querySelector('[data-reader-record-more-mode="immersive"]')).not.toBeNull();
    expect(menu.querySelector('[data-reader-record-more-theme="paper"]')).not.toBeNull();
    expect(menu.querySelector('[data-reader-record-more-font-scale="md"]')).not.toBeNull();
    expect(menu.querySelector('[data-reader-record-more-font-family="sans"]')).not.toBeNull();
    expect(menu.querySelector('[data-reader-record-more-action="copy-link"]')).not.toBeNull();
    expect(menu.querySelector('[data-reader-record-more-metadata="true"]')).not.toBeNull();
    // Font preview cards render an "Ag" sample in each font family option.
    expect(menu.textContent).toContain("Ag");
  });

  it("switches reading mode from the more menu and keeps it in sync with the hero tabs", async () => {
    const user = userEvent.setup();
    const storage = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      removeItem: (key: string) => storage.delete(key),
      clear: () => storage.clear(),
    });
    render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const heroTab = screen.getByRole("button", { name: "切换到沉浸模式" });
    expect(heroTab).toBeTruthy();
    expect(heroTab.getAttribute("aria-pressed")).toBe("false");

    await user.click(screen.getByTestId("reader-record-more-menu-trigger"));
    const menu = await waitFor(() =>
      screen.getByTestId("reader-record-more-menu-content"),
    );
    const immersiveItem = menu.querySelector<HTMLElement>(
      '[data-reader-record-more-mode="immersive"]',
    );
    expect(immersiveItem).not.toBeNull();
    await user.click(immersiveItem!);

    // 关闭菜单后再验证 Hero tab，避免 Radix portal 的 aria-hidden 阻塞查询
    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "切换到沉浸模式" }).getAttribute("aria-pressed"),
      ).toBe("true");
      expect(
        screen.getByRole("button", { name: "切换到精读模式" }).getAttribute("aria-pressed"),
      ).toBe("false");
    });

    // 模式变化应持久化到 localStorage
    const stored = window.localStorage.getItem("claread.reader.settings.v4");
    expect(stored).toBeTruthy();
    expect(JSON.parse(stored!).mode).toBe("immersive");
  });

  it("updates theme, font scale, and font family from the more menu", async () => {
    const user = userEvent.setup();
    const storage = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      removeItem: (key: string) => storage.delete(key),
      clear: () => storage.clear(),
    });
    render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    await user.click(screen.getByTestId("reader-record-more-menu-trigger"));
    const menu = await waitFor(() => screen.getByTestId("reader-record-more-menu-content"));

    await user.click(menu.querySelector('[data-reader-record-more-theme="dark"]')!);
    await waitFor(() => {
      expect(window.localStorage.getItem("claread.reader.themeName")).toBe("dark");
    });

    await user.click(menu.querySelector('[data-reader-record-more-font-scale="lg"]')!);
    await waitFor(() => {
      const stored = JSON.parse(window.localStorage.getItem("claread.reader.settings.v4")!);
      expect(stored.fontScale).toBe("lg");
    });

    await user.click(menu.querySelector('[data-reader-record-more-font-family="editorial"]')!);
    await waitFor(() => {
      const stored = JSON.parse(window.localStorage.getItem("claread.reader.settings.v4")!);
      expect(stored.fontFamily).toBe("editorial");
    });
  });

  it("shows the source url action in the more menu only when sourceUrl exists", async () => {
    const user = userEvent.setup();
    const withUrl = makeSnapshot();
    withUrl.record.source_metadata = {
      source_url: "https://example.com/article",
      source_name: "Example Source",
    };
    const { unmount } = render(
      <ReaderRecordPlateSurface snapshot={withUrl} />,
    );

    await user.click(screen.getByTestId("reader-record-more-menu-trigger"));
    const menu = await waitFor(() =>
      screen.getByTestId("reader-record-more-menu-content"),
    );
    const link = menu.querySelector<HTMLAnchorElement>(
      '[data-reader-record-more-action="open-source-url"]',
    );
    expect(link).not.toBeNull();
    expect(link?.href).toBe("https://example.com/article");

    unmount();

    const withoutUrl = makeSnapshot();
    render(<ReaderRecordPlateSurface snapshot={withoutUrl} />);
    await user.click(screen.getByTestId("reader-record-more-menu-trigger"));
    const menu2 = await waitFor(() =>
      screen.getByTestId("reader-record-more-menu-content"),
    );
    expect(
      menu2.querySelector('[data-reader-record-more-action="open-source-url"]'),
    ).toBeNull();
  });

  it("renders low-weight metadata in the more menu footer", async () => {
    const user = userEvent.setup();
    render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    await user.click(screen.getByTestId("reader-record-more-menu-trigger"));
    const menu = await waitFor(() =>
      screen.getByTestId("reader-record-more-menu-content"),
    );
    const metadata = menu.querySelector<HTMLElement>(
      '[data-reader-record-more-metadata="true"]',
    );
    expect(metadata).not.toBeNull();
    expect(metadata?.textContent).toContain("5 词");
    expect(metadata?.textContent).toContain("粘贴导入");
  });

  it("keeps Plate toolbar as the only selection action surface and disables it when idle", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const actions = screen.getByTestId("reader-record-plate-selection-state");

    expect(actions.dataset.readerRecordActions).toBe("selection-state");
    expect(actions.dataset.readerRecordActionMode).toBe("idle");
    expect(
      actions.querySelector('[data-reader-record-action-hint]')?.textContent,
    ).toContain("划取原文后");

    const toolbarButtons = container.querySelectorAll<HTMLButtonElement>(
      "[data-reader-record-toolbar-action]",
    );
    expect(toolbarButtons).toHaveLength(5);
    for (const button of toolbarButtons) {
      expect(button.disabled).toBe(true);
      expect(button.getAttribute("title")).toBeNull();
      expect(button.dataset.readerRecordDisabledReason).toBe("请选择稳定原文后再操作");
    }
    expect(container.querySelector('[data-reader-record-action="feedback"]')).toBeNull();
    expect(container.querySelector('[data-reader-record-actions="selection-context"]')).toBeNull();
    expect(container.querySelector("[data-reader-record-test-action]")).toBeNull();
  });

  it("renders the real Plate toolbar button set with disabled semantics in the toolbar harness", () => {
    const { container, actions } = renderToolbarHarness(
      makeToolbarActions({
        lookup: { disabled: true, reason: "暂不支持跨段或非稳定原文选区" },
      }),
    );

    const toolbarButtons = container.querySelectorAll<HTMLButtonElement>(
      "[data-reader-record-toolbar-action]",
    );
    expect(toolbarButtons).toHaveLength(5);
    expect(container.querySelector("[data-reader-record-test-action]")).toBeNull();
    for (const button of toolbarButtons) {
      expect(button.getAttribute("title")).toBeNull();
    }

    const lookup = container.querySelector<HTMLButtonElement>(
      '[data-reader-record-toolbar-action="lookup"]',
    );
    const copy = container.querySelector<HTMLButtonElement>(
      '[data-reader-record-toolbar-action="copy"]',
    );
    expect(lookup?.disabled).toBe(true);
    expect(lookup?.dataset.readerRecordDisabledReason).toBe(
      "暂不支持跨段或非稳定原文选区",
    );
    expect(copy?.disabled).toBe(false);

    if (!lookup) {
      throw new Error("Expected lookup toolbar button");
    }
    if (!copy) {
      throw new Error("Expected copy toolbar button");
    }
    fireEvent.click(copy);
    expect(actions.onCopy).toHaveBeenCalledTimes(1);
    fireEvent.click(lookup);
    expect(actions.onLookup).not.toHaveBeenCalled();
  });

  it("keeps each reader toolbar button as a Plate-style primitive with disabled reason and click forwarding", () => {
    const cases: Array<{
      action: ReaderToolbarActionId;
      component: ReactNode;
      handler: keyof Pick<
        ReaderToolbarActions,
        "onLookup" | "onCopy" | "onAsk" | "onHighlight" | "onNote"
      >;
    }> = [
      { action: "lookup", component: <ReaderLookupToolbarButton />, handler: "onLookup" },
      { action: "copy", component: <ReaderCopyToolbarButton />, handler: "onCopy" },
      { action: "ask", component: <ReaderAskToolbarButton />, handler: "onAsk" },
      {
        action: "highlight",
        component: <ReaderHighlightToolbarButton />,
        handler: "onHighlight",
      },
      { action: "note", component: <ReaderNoteToolbarButton />, handler: "onNote" },
    ];

    for (const item of cases) {
      const enabledActions = makeToolbarActions();
      const enabledHarness = renderToolbarHarness(
        enabledActions,
        item.component,
      );
      const enabledButton = enabledHarness.container.querySelector<HTMLButtonElement>(
        `[data-reader-record-toolbar-action="${item.action}"]`,
      );
      expect(enabledButton).not.toBeNull();
      expect(enabledButton?.disabled).toBe(false);
      expect(enabledButton?.getAttribute("title")).toBeNull();
      if (!enabledButton) {
        throw new Error(`Expected enabled toolbar button: ${item.action}`);
      }
      fireEvent.click(enabledButton);
      if (item.action === "ask") {
        expect(document.querySelector('[data-reader-record-ask-menu="open"]')).not.toBeNull();
        expect(enabledActions.onAsk).not.toHaveBeenCalled();
      } else {
        expect(enabledActions[item.handler]).toHaveBeenCalledTimes(1);
      }
      enabledHarness.unmount();

      const disabledActions = makeToolbarActions({
        [item.action]: {
          disabled: true,
          reason: "暂不支持跨段或非稳定原文选区",
        },
      });
      const disabledHarness = renderToolbarHarness(
        disabledActions,
        item.component,
      );
      const disabledButton = disabledHarness.container.querySelector<HTMLButtonElement>(
        `[data-reader-record-toolbar-action="${item.action}"]`,
      );
      expect(disabledButton).not.toBeNull();
      expect(disabledButton?.disabled).toBe(true);
      expect(disabledButton?.getAttribute("title")).toBeNull();
      expect(disabledButton?.dataset.readerRecordDisabledReason).toBe(
        "暂不支持跨段或非稳定原文选区",
      );
      if (!disabledButton) {
        throw new Error(`Expected disabled toolbar button: ${item.action}`);
      }
      fireEvent.click(disabledButton);
      expect(disabledActions[item.handler]).not.toHaveBeenCalled();
      disabledHarness.unmount();
    }
  });

  it("opens the Ask menu with the Plate AI shortcut while keeping native title tooltips out", () => {
    const { container } = renderToolbarHarness();
    const askButton = container.querySelector<HTMLButtonElement>(
      '[data-reader-record-toolbar-action="ask"]',
    );

    expect(askButton).not.toBeNull();
    expect(askButton?.getAttribute("title")).toBeNull();
    expect(document.querySelector('[data-reader-record-ask-menu="open"]')).toBeNull();

    fireEvent.keyDown(window, {
      code: "KeyJ",
      ctrlKey: true,
      key: "j",
    });

    expect(document.querySelector('[data-reader-record-ask-menu="open"]')).not.toBeNull();
  });

  it("maps a stable source selection to an anchor draft with unit-local UTF-16 offsets", async () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const memoryMark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) {
      throw new Error("Expected memory mark");
    }

    selectTextInElement(memoryMark, 0, "memory".length);

    const actions = screen.getByTestId("reader-record-plate-selection-state");
    await waitFor(() => {
      expect(actions.dataset.readerRecordSelectionSupported).toBe("true");
    });
    expect(actions.dataset.readerRecordSelectionDraftCount).toBe("1");
    expect(actions.dataset.readerRecordSelectionAnchorSegmentId).toBe("seg_1");
    expect(actions.dataset.readerRecordSelectionSurfaceKind).toBe("source");
    expect(actions.dataset.readerRecordSelectionBlockType).toBe("reader_paragraph");
    expect(actions.dataset.readerRecordSelectionBlockId).toBe("paragraph:seg_1");
    expect(actions.dataset.readerRecordSelectionUnitId).toBe("unit_1");
    expect(actions.dataset.readerRecordSelectionStartOffset).toBe("14");
    expect(actions.dataset.readerRecordSelectionEndOffset).toBe("20");
    expect(actions.dataset.readerRecordActionMode).toBe("selection");
    expect(
      actions.querySelector('[data-reader-record-action-hint]')?.textContent,
    ).toContain("已选：memory");
    await waitForSelectionAction(container, "lookup");
    for (const action of ["lookup", "copy", "highlight", "note", "ask"]) {
      expect(
        selectionActionButton(
          container,
          action as "lookup" | "copy" | "highlight" | "note" | "ask",
        )?.disabled,
      ).toBe(false);
    }
    expect(container.querySelector('[data-reader-record-action="feedback"]')).toBeNull();
    expect(container.querySelector('[data-reader-record-actions="selection-context"]')).toBeNull();
  });

  it("maps selection in the second anchor segment of the same unit using the segment baseline", async () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSplitSegmentSnapshot()} />,
    );
    const secondSegmentLeaf = container.querySelector<HTMLElement>(
      '[data-anchor-segment-id="seg_2"] [data-reader-record-leaf="segment_text"]',
    );
    expect(secondSegmentLeaf).not.toBeNull();
    if (!secondSegmentLeaf) {
      throw new Error("Expected second segment leaf");
    }

    selectTextInElement(secondSegmentLeaf, 7, 13);

    const actions = screen.getByTestId("reader-record-plate-selection-state");
    await waitFor(() => {
      expect(actions.dataset.readerRecordSelectionSupported).toBe("true");
    });
    expect(actions.dataset.readerRecordSelectionAnchorSegmentId).toBe("seg_2");
    expect(actions.dataset.readerRecordSelectionStartOffset).toBe("28");
    expect(actions.dataset.readerRecordSelectionEndOffset).toBe("34");
  });

  it("copies selected text through the Clipboard API without calling a backend", async () => {
    const writeText = installClipboardMock();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const memoryMark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) {
      throw new Error("Expected memory mark");
    }

    selectTextInElement(memoryMark, 0, "memory".length);

    const copyButton = await waitForSelectionAction(container, "copy");
    await waitFor(() => {
      expect(copyButton.disabled).toBe(false);
    });
    fireEvent.click(copyButton);

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith("memory");
    });
    expect(
      fetchMock.mock.calls.filter(
        ([url]) => !(typeof url === "string" && url.includes("/api/web/favorites")),
      ),
    ).toHaveLength(0);
  });

  it.each([
    {
      label: "phrase_gloss",
      mark: makeVocabularyMark({
        item_type: "phrase_gloss",
        phrase: "memory",
        phrase_type: "collocation",
        gloss: "记忆",
        example: "Institutional memory shapes choices.",
      }),
      expectedText: "记忆",
    },
    {
      label: "context_gloss",
      mark: makeVocabularyMark({
        item_type: "context_gloss",
        display: "memory",
        gloss: "此处指制度延续下来的经验",
        reason: "这里强调制度在时间中的延续性。",
      }),
      expectedText: "此处指制度延续下来的经验",
    },
  ])(
    "opens structured inspect for $label vocabulary marks without dictionary lookup",
    async ({ mark, expectedText }) => {
      const fetchMock = vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
      vi.stubGlobal("fetch", fetchMock);
      const snapshot = {
        ...makeSnapshot(),
        value: [makeUnit({ vocabularyMarks: [mark] })],
      };
      const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
      const memoryMark = container.querySelector<HTMLElement>(
        `[data-reader-record-vocabulary-mark-id="${mark.mark_id}"]`,
      );
      expect(memoryMark).not.toBeNull();
      if (!memoryMark) {
        throw new Error("Expected vocabulary mark");
      }

      fireEvent.click(memoryMark);

      const panel = await screen.findByTestId("reader-record-plate-lookup-panel");
      const inspectPanel = within(panel);
      expect(inspectPanel.getByText(expectedText)).toBeTruthy();
      expect(panel.textContent).not.toContain("当前词典暂未收录");
      expect(inspectPanel.getByLabelText("查短语")).toBeTruthy();
      expect(inspectPanel.getByLabelText("带入 Ask")).toBeTruthy();
      expect(inspectPanel.getByLabelText("反馈")).toBeTruthy();
      expect(
        fetchMock.mock.calls.filter(
          ([url]) => typeof url === "string" && url.includes("/api/web/dict/lookup"),
        ),
      ).toHaveLength(0);
    },
  );

  it("uses the seg_2 source sentence for grouped phrase_gloss lookup context", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (typeof url === "string" && url.includes("/api/web/favorites")) {
        return Promise.resolve(
          new Response(JSON.stringify({ ok: true, favorited: false }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify(makeDictionaryEntryResult("policy choices")), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeGroupedSeg2PhraseGlossSnapshot()} />,
    );
    const seg2Mark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_seg_2"]',
    );
    expect(seg2Mark).not.toBeNull();
    if (!seg2Mark) {
      throw new Error("Expected seg_2 vocabulary mark");
    }

    fireEvent.click(seg2Mark);

    const panel = await screen.findByTestId("reader-record-plate-lookup-panel");
    fireEvent.click(within(panel).getByLabelText("查短语"));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([url]) =>
            typeof url === "string" && url.includes("/api/web/dict/lookup"),
        ),
      ).toBe(true);
    });

    const lookupCall = fetchMock.mock.calls.find(
      ([url]) => typeof url === "string" && url.includes("/api/web/dict/lookup"),
    );
    const lookupUrl = String(lookupCall?.[0]);
    const lookupParams = new URL(lookupUrl, "http://claread.test").searchParams;
    expect(lookupParams.get("word")).toBe("policy choices");
    expect(lookupParams.get("context")).toBe("shapes policy choices.");
  });

  it("submits vocabulary inspect feedback through the dictionary feedback scope", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url === "/api/web/feedback") {
        return Promise.resolve(
          new Response(JSON.stringify({ ok: true, message: "ok" }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify({ ok: true, favorited: false }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const memoryMark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) {
      throw new Error("Expected vocabulary mark");
    }

    fireEvent.click(memoryMark);

    const panel = await screen.findByTestId("reader-record-plate-lookup-panel");
    fireEvent.click(within(panel).getByLabelText("反馈"));
    const menu = await screen.findByRole("dialog", { name: "反馈选项" });
    const feedbackMenu = within(menu);
    expect(feedbackMenu.queryByText("有帮助")).toBeNull();
    fireEvent.click(feedbackMenu.getByText("释义有问题"));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url]) => url === "/api/web/feedback"),
      ).toBe(true);
    });
    const feedbackCall = fetchMock.mock.calls.find(
      ([url]) => url === "/api/web/feedback",
    );
    const body = JSON.parse(
      String((feedbackCall?.[1] as RequestInit | undefined)?.body),
    ) as Record<string, unknown>;
    expect(body).toMatchObject({
      feedbackScope: "dictionary",
      targetId: "vocab_mark_1",
      sentiment: "negative",
      feedbackType: "wrong_definition",
      entryPoint: "reader_record_vocabulary_mark",
      clientSurface: "reader_record",
    });
    expect(body).not.toHaveProperty("analysisRecordId");
    expect(body.contextJson).toMatchObject({
      readingRecordId: "record_1",
      annotationType: "phrase_gloss",
      targetVariant: "vocabulary",
    });
  });

  it("runs dictionary lookup when a vocab_highlight mark is clicked", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (typeof url === "string" && url.includes("/api/web/favorites")) {
        return Promise.resolve(
          new Response(JSON.stringify({ ok: true, favorited: false }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify(makeDictionaryEntryResult("memory")), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const vocabMark = makeVocabularyMark({
      item_type: "vocab_highlight",
      headword: "memory",
      brief_explanation: "the ability to remember information",
      reason: "Useful for this source sentence.",
    });
    const snapshot = {
      ...makeSnapshot(),
      value: [makeUnit({ vocabularyMarks: [vocabMark] })],
    };
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    const memoryMark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) {
      throw new Error("Expected vocabulary mark");
    }

    fireEvent.click(memoryMark);

    await screen.findByTestId("reader-record-plate-lookup-panel");
    const lookupCalls = fetchMock.mock.calls.filter(
      ([url]) => typeof url === "string" && url.includes("/api/web/dict/lookup"),
    );
    expect(lookupCalls).toHaveLength(1);
    const lookupUrl = String(lookupCalls[0]?.[0]);
    const lookupParams = new URL(lookupUrl, "http://claread.test").searchParams;
    expect(lookupParams.get("word")).toBe("memory");
    expect(lookupParams.get("type")).toBe("word");
    expect(lookupParams.get("context")).toBe(SOURCE_TEXT);
    expect(await screen.findByText("the ability to remember information")).toBeTruthy();
  });

  it("runs dictionary lookup only for a valid single anchor draft", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (typeof url === "string" && url.includes("/api/web/favorites")) {
        return Promise.resolve(
          new Response(JSON.stringify({ ok: true, favorited: false }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify(makeDictionaryEntryResult("memory")), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const memoryMark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) {
      throw new Error("Expected memory mark");
    }

    selectTextInElement(memoryMark, 0, "memory".length);

    const lookupButton = await waitForSelectionAction(container, "lookup");
    await waitFor(() => {
      expect(lookupButton.disabled).toBe(false);
    });
    fireEvent.click(lookupButton);

    await screen.findByTestId("reader-record-plate-lookup-panel");
    expect(selectionActionButton(container, "lookup")).toBeNull();
    const nonFavoritesCalls = fetchMock.mock.calls.filter(
      ([url]) => !(typeof url === "string" && url.includes("/api/web/favorites")),
    );
    expect(nonFavoritesCalls).toHaveLength(1);
    const lookupUrl = String(nonFavoritesCalls[0]?.[0]);
    expect(lookupUrl).toContain("/api/web/dict/lookup?");
    expect(lookupUrl).toContain("word=memory");
    expect(screen.getByText("the ability to remember information")).toBeTruthy();
  });

  it("saves highlight through the Reading Record write endpoint with nested anchor", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          ok: true,
          status: "created",
          item: {},
          session: { state: "signed_in" },
        }),
        {
          status: 201,
          headers: { "content-type": "application/json" },
        },
      ),
    );
    const onRequestSnapshotReload = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(
      <ReaderRecordPlateSurface
        snapshot={makeSnapshot()}
        onRequestSnapshotReload={onRequestSnapshotReload}
      />,
    );
    const memoryMark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) {
      throw new Error("Expected memory mark");
    }

    selectTextInElement(memoryMark, 0, "memory".length);

    const highlightButton = await waitForSelectionAction(container, "highlight");
    await waitFor(() => {
      expect(highlightButton.disabled).toBe(false);
    });
    fireEvent.click(highlightButton);

    await waitFor(() => {
      const nonFavoritesCalls = fetchMock.mock.calls.filter(
        ([url]) => !(typeof url === "string" && url.includes("/api/web/favorites")),
      );
      expect(nonFavoritesCalls).toHaveLength(1);
    });
    const highlightCall = fetchMock.mock.calls.find(
      ([url]) => typeof url === "string" && url === "/api/web/reading-record/highlights",
    );
    expect(highlightCall?.[0]).toBe("/api/web/reading-record/highlights");
    expect((highlightCall?.[1] as RequestInit | undefined)?.method).toBe("POST");
    const body = JSON.parse(
      String((highlightCall?.[1] as RequestInit | undefined)?.body),
    ) as Record<string, unknown>;
    expect(body.anchor).toEqual(expectedMemoryAnchor());
    expect(body.selectedText).toBe("memory");
    expect(body.color).toBe("warm_yellow");
    await waitFor(() => {
      expect(onRequestSnapshotReload).toHaveBeenCalledTimes(1);
    });
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/api/web/annotations"),
      ),
    ).toBe(false);
  });

  it("opens an existing user highlight menu and updates color through the Reading Record PATCH endpoint", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/web/favorites")) {
        return Promise.resolve(
          new Response(JSON.stringify({ ok: true, favorited: false }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      if (url === "/api/web/reading-record/highlights/asset_highlight_policy") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              ok: true,
              status: "updated",
              item: makeHighlightWriteItem({ color: "soft_rose" }),
              session: { state: "signed_in" },
            }),
            {
              status: 200,
              headers: { "content-type": "application/json" },
            },
          ),
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    });
    const onRequestSnapshotReload = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("fetch", fetchMock);

    const { container } = render(
      <ReaderRecordPlateSurface
        snapshot={makeSnapshot([makePolicyHighlightAsset()])}
        onRequestSnapshotReload={onRequestSnapshotReload}
      />,
    );
    const highlight = container.querySelector<HTMLElement>(
      '[data-reader-record-user-highlight-asset-id="asset_highlight_policy"]',
    );
    expect(highlight).not.toBeNull();
    if (!highlight) {
      throw new Error("Expected policy highlight mark");
    }

    fireEvent.click(highlight);
    fireEvent.click(await screen.findByLabelText("切换为难点"));

    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(
        ([url]) =>
          typeof url === "string" &&
          url === "/api/web/reading-record/highlights/asset_highlight_policy",
      );
      expect(patchCall).toBeDefined();
      expect((patchCall?.[1] as RequestInit | undefined)?.method).toBe("PATCH");
      const body = JSON.parse(
        String((patchCall?.[1] as RequestInit | undefined)?.body),
      ) as Record<string, unknown>;
      expect(body.color).toBe("soft_rose");
    });
    expect(screen.queryByTestId("reader-record-plate-lookup-panel")).toBeNull();
    expect(onRequestSnapshotReload).not.toHaveBeenCalled();
  });

  it("deletes an existing user highlight from the highlight menu", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/web/favorites")) {
        return Promise.resolve(
          new Response(JSON.stringify({ ok: true, favorited: false }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify({ ok: true, status: "deleted" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const onRequestSnapshotReload = vi.fn().mockResolvedValue(undefined);

    const { container } = render(
      <ReaderRecordPlateSurface
        snapshot={makeSnapshot([makePolicyHighlightAsset()])}
        onRequestSnapshotReload={onRequestSnapshotReload}
      />,
    );
    const highlight = container.querySelector<HTMLElement>(
      '[data-reader-record-user-highlight-asset-id="asset_highlight_policy"]',
    );
    expect(highlight).not.toBeNull();
    if (!highlight) {
      throw new Error("Expected policy highlight mark");
    }

    fireEvent.click(highlight);
    fireEvent.click(await screen.findByRole("button", { name: "删除高亮" }));

    await waitFor(() => {
      const deleteCall = fetchMock.mock.calls.find(
        ([url]) =>
          typeof url === "string" &&
          url === "/api/web/reading-record/highlights/asset_highlight_policy",
      );
      expect(deleteCall).toBeDefined();
      expect((deleteCall?.[1] as RequestInit | undefined)?.method).toBe("DELETE");
    });
    await waitFor(() => {
      expect(
        container.querySelector(
          '[data-reader-record-user-highlight-asset-id="asset_highlight_policy"]',
        ),
      ).toBeNull();
    });
  });

  it("updates an exact saved highlight range instead of creating a duplicate highlight", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/web/favorites")) {
        return Promise.resolve(
          new Response(JSON.stringify({ ok: true, favorited: false }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            ok: true,
            status: "updated",
            item: makeHighlightWriteItem({ color: "warm_yellow" }),
            session: { state: "signed_in" },
          }),
          {
            status: 200,
            headers: { "content-type": "application/json" },
          },
        ),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const onRequestSnapshotReload = vi.fn().mockResolvedValue(undefined);

    const { container } = render(
      <ReaderRecordPlateSurface
        snapshot={makeSnapshot([makePolicyHighlightAsset()])}
        onRequestSnapshotReload={onRequestSnapshotReload}
      />,
    );
    const highlight = container.querySelector<HTMLElement>(
      '[data-reader-record-user-highlight-asset-id="asset_highlight_policy"]',
    );
    expect(highlight).not.toBeNull();
    if (!highlight) {
      throw new Error("Expected policy highlight mark");
    }

    selectTextInElement(highlight, 0, "policy".length);
    const highlightButton = await waitForSelectionAction(container, "highlight");
    await waitFor(() => {
      expect(highlightButton.disabled).toBe(false);
    });
    fireEvent.click(highlightButton);

    await waitFor(() => {
      const patchCalls = fetchMock.mock.calls.filter(
        ([url]) =>
          typeof url === "string" &&
          url === "/api/web/reading-record/highlights/asset_highlight_policy",
      );
      expect(patchCalls).toHaveLength(1);
      expect((patchCalls[0]?.[1] as RequestInit | undefined)?.method).toBe("PATCH");
    });
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) =>
          typeof url === "string" &&
          url === "/api/web/reading-record/highlights" &&
          (init as RequestInit | undefined)?.method === "POST",
      ),
    ).toBe(false);
  });

  it("removes superseded highlight assets after a canonical merge response", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/web/favorites")) {
        return Promise.resolve(
          new Response(JSON.stringify({ ok: true, favorited: false }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      if (url === "/api/web/reading-record/highlights") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              ok: true,
              status: "created",
              item: makeHighlightWriteItem({
                id: "asset_highlight_canonical",
                selectedText: "policy choices",
                color: "soft_rose",
                supersededIds: ["asset_highlight_policy"],
              }),
              session: { state: "signed_in" },
            }),
            {
              status: 201,
              headers: { "content-type": "application/json" },
            },
          ),
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const onRequestSnapshotReload = vi.fn().mockResolvedValue(undefined);

    const { container } = render(
      <ReaderRecordPlateSurface
        snapshot={makeSnapshot([makePolicyHighlightAsset()])}
        onRequestSnapshotReload={onRequestSnapshotReload}
      />,
    );
    const policyMark = container.querySelector<HTMLElement>(
      '[data-reader-record-user-highlight-asset-id="asset_highlight_policy"]',
    );
    const choicesLeaf = Array.from(
      container.querySelectorAll<HTMLElement>('[data-reader-record-leaf="segment_text"]'),
    ).find((element) => element.textContent?.includes(" choices"));
    expect(policyMark).not.toBeNull();
    expect(choicesLeaf).not.toBeNull();
    if (!policyMark || !choicesLeaf) {
      throw new Error("Expected policy highlight and choices leaf");
    }

    selectAcrossElements(policyMark, 0, choicesLeaf, " choices".length);
    const highlightButton = await waitForSelectionAction(container, "highlight");
    await waitFor(() => {
      expect(highlightButton.disabled).toBe(false);
    });
    fireEvent.click(highlightButton);

    await waitFor(() => {
      expect(
        container.querySelector(
          '[data-reader-record-user-highlight-asset-id="asset_highlight_policy"]',
        ),
      ).toBeNull();
      const canonical = container.querySelector<HTMLElement>(
        '[data-reader-record-user-highlight-asset-id="asset_highlight_canonical"]',
      );
      expect(canonical).not.toBeNull();
      expect(canonical?.textContent).toContain("policy");
    });
    expect(onRequestSnapshotReload).not.toHaveBeenCalled();
  });

  it("saves note through the Reading Record write endpoint with nested anchor", async () => {
    const fetchMock = installReaderRecordWriteFetchMock();
    const onRequestSnapshotReload = vi.fn().mockResolvedValue(undefined);
    const { container } = render(
      <ReaderRecordPlateSurface
        snapshot={makeSnapshot()}
        onRequestSnapshotReload={onRequestSnapshotReload}
      />,
    );
    const memoryMark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) {
      throw new Error("Expected memory mark");
    }

    selectTextInElement(memoryMark, 0, "memory".length);

    const noteButton = await waitForSelectionAction(container, "note");
    await waitFor(() => {
      expect(noteButton.disabled).toBe(false);
    });
    fireEvent.click(noteButton);

    const panel = await screen.findByTestId("reader-record-inline-comment-panel");
    expect(panel.dataset.readerRecordCommentMode).toBe("draft");
    expect(
      panel.querySelector('[data-reader-record-note-quote="true"]')?.textContent,
    ).toContain("memory");
    const noteInput = await screen.findByTestId<HTMLTextAreaElement>(
      "reader-record-plate-note-input",
    );
    fireEvent.change(noteInput, {
      target: { value: "Keep this policy concept for review." },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => {
      const nonFavoritesCalls = fetchMock.mock.calls.filter(
        ([url]) => !(typeof url === "string" && url.includes("/api/web/favorites")),
      );
      expect(nonFavoritesCalls).toHaveLength(1);
    });
    const noteCall = fetchMock.mock.calls.find(
      ([url]) => typeof url === "string" && url === "/api/web/reading-record/notes",
    );
    expect(noteCall?.[0]).toBe("/api/web/reading-record/notes");
    expect((noteCall?.[1] as RequestInit | undefined)?.method).toBe("POST");
    const body = JSON.parse(
      String((noteCall?.[1] as RequestInit | undefined)?.body),
    ) as Record<string, unknown>;
    expect(body.anchor).toEqual(expectedMemoryAnchor());
    expect(body.selectedText).toBe("memory");
    expect(body.noteText).toBe("Keep this policy concept for review.");
    await waitFor(() => {
      expect(onRequestSnapshotReload).toHaveBeenCalledTimes(1);
    });
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/api/web/reader-notes"),
      ),
    ).toBe(false);
  });

  it("shows a duplicate warning before creating another note on the same normalized anchor", async () => {
    const fetchMock = installReaderRecordWriteFetchMock();
    const existingNote = makeUserAsset({
      asset_id: "asset_note_existing",
      asset_type: "note",
      note_text: "Existing note for memory.",
    });
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot([existingNote])} />,
    );
    const memoryMark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) {
      throw new Error("Expected memory mark");
    }

    selectTextInElement(memoryMark, 0, "memory".length);
    const noteButton = await waitForSelectionAction(container, "note");
    fireEvent.click(noteButton);

    const duplicateWarning = await screen.findByTestId(
      "reader-record-note-duplicate-warning",
    );
    expect(duplicateWarning.textContent).toContain("这个选区已有笔记");
    expect(duplicateWarning.textContent).toContain("Existing note for memory.");
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "保存" }).disabled,
    ).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "仍新增一条" }));
    await waitFor(() => {
      expect(
        screen.getByTestId("reader-record-inline-comment-panel").querySelector(
          '[data-reader-record-note-duplicate="acknowledged"]',
        ),
      ).not.toBeNull();
    });
    const noteInput = screen.getByTestId<HTMLTextAreaElement>(
      "reader-record-plate-note-input",
    );
    fireEvent.change(noteInput, {
      target: { value: "Second note on the same quote." },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => {
      const noteCall = fetchMock.mock.calls.find(
        ([url]) => typeof url === "string" && url === "/api/web/reading-record/notes",
      );
      expect(noteCall).toBeDefined();
      const body = JSON.parse(
        String((noteCall?.[1] as RequestInit | undefined)?.body),
      ) as Record<string, unknown>;
      expect(body.anchor).toEqual(expectedMemoryAnchor());
      expect(body.noteText).toBe("Second note on the same quote.");
    });
  });

  it("can jump from the duplicate warning to the existing note view panel", async () => {
    const existingNote = makeUserAsset({
      asset_id: "asset_note_existing",
      asset_type: "note",
      note_text: "Existing note for memory.",
    });
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot([existingNote])} />,
    );
    const memoryMark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) {
      throw new Error("Expected memory mark");
    }

    selectTextInElement(memoryMark, 0, "memory".length);
    const noteButton = await waitForSelectionAction(container, "note");
    fireEvent.click(noteButton);

    await screen.findByTestId("reader-record-note-duplicate-warning");
    fireEvent.click(screen.getByRole("button", { name: "查看/编辑已有笔记" }));

    const panel = await screen.findByTestId("reader-record-inline-comment-panel");
    await waitFor(() => {
      expect(panel.dataset.readerRecordCommentMode).toBe("view");
    });
    expect(panel.textContent).toContain("Existing note for memory.");
    expect(
      panel.querySelector('[data-reader-record-note-quote="true"]')?.textContent,
    ).toContain("memory");
  });

  it("opens the RR Ask panel from a stable source selection and loads RR-scoped ask threads", async () => {
    const fetchMock = installReaderAskFetchMock();
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const memoryMark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) {
      throw new Error("Expected memory mark");
    }

    selectTextInElement(memoryMark, 0, "memory".length);

    const askButton = await waitForSelectionAction(container, "ask");
    await openAskPanelFromToolbar(askButton);

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "收起 AI 工作区" }),
      ).toBeTruthy();
    });
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes(
          "/api/web/reader-ask/threads?record_id=record_1&record_scope=reading_record",
        ),
      ),
    ).toBe(true);
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes(
          "/api/web/reader-ask/threads/thread-rr-1?record_id=record_1&record_scope=reading_record",
        ),
      ),
    ).toBe(true);
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/api/web/reader-ask/context-records"),
      ),
    ).toBe(false);

    const attachment = await sendAskComposerMessageAndReadFirstAttachment(fetchMock);
    expect(attachment?.selected_text).toBe("memory");
    expect(attachment?.metadata).toMatchObject({
      reading_record_anchor: expectedMemoryAnchor(),
    });
    expect(attachment?.metadata).not.toHaveProperty("surface_kind");
    expect(attachment?.metadata).not.toHaveProperty("block_type");
    expect(attachment?.metadata).not.toHaveProperty("anchor_segment_id");
  });

  it("submits a toolbar Ask prompt with the current selection as context", async () => {
    const fetchMock = installReaderAskFetchMock();
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const memoryMark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) {
      throw new Error("Expected memory mark");
    }

    selectTextInElement(memoryMark, 0, "memory".length);

    const askButton = await waitForSelectionAction(container, "ask");
    await submitAskPromptFromToolbar(askButton, "这句话为什么这样表达？");

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).includes("/messages/stream"),
        ),
      ).toBe(true);
    });
    const streamCall = fetchMock.mock.calls.findLast(([input]) =>
      String(input).includes("/messages/stream"),
    );
    const body = JSON.parse(String(streamCall?.[1]?.body)) as {
      content: string;
      entry_action: string;
      attachments: Array<{ selected_text?: string | null }>;
    };
    expect(body.content).toBe("这句话为什么这样表达？");
    expect(body.entry_action).toBe("ask_about_this");
    expect(body.attachments[0]?.selected_text).toBe("memory");
  });

  it("opens the RR Ask panel from a saved note in Reading Record scope", async () => {
    const fetchMock = installReaderAskFetchMock();
    const noteAsset = makePolicyNoteAsset({
      asset_id: "asset_note_1",
      note_text: "Keep this policy concept for review.",
    });
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot([noteAsset])} />,
    );
    const noteMark = container.querySelector<HTMLElement>(
      '[data-reader-record-user-note-asset-ids]',
    );
    expect(noteMark).not.toBeNull();
    if (!noteMark) {
      throw new Error("Expected note mark");
    }

    fireEvent.click(noteMark);

    const panel = await screen.findByTestId("reader-record-inline-comment-panel");
    await waitFor(() => {
      expect(panel.dataset.readerRecordCommentMode).toBe("view");
    });
    expect(panel.textContent).toContain("Keep this policy concept for review.");
    expect(
      panel.querySelector('[data-reader-record-note-quote="true"]')?.textContent,
    ).toContain("policy");
    await waitFor(() => {
      expect(noteMark.dataset.readerRecordNoteActive).toBe("true");
    });

    const askButton = await screen.findByRole("button", {
      name: "Ask 关于这条笔记",
    });
    fireEvent.click(askButton);

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "收起 AI 工作区" }),
      ).toBeTruthy();
    });
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes(
          "/api/web/reader-ask/threads?record_id=record_1&record_scope=reading_record",
        ),
      ),
    ).toBe(true);
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes(
          "/api/web/reader-ask/threads/thread-rr-1?record_id=record_1&record_scope=reading_record",
        ),
      ),
    ).toBe(true);
  });

  it("keeps overlapping note marks locatable and opens the clicked note", async () => {
    const shortNote = makePolicyNoteAsset({
      asset_id: "asset_note_short",
      note_text: "Short policy note.",
    });
    const wideNote = makePolicyNoteAsset({
      asset_id: "asset_note_wide",
      note_text: "Wider policy-choices note.",
      anchor: policyNoteAnchor("policy choices"),
    });
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot([shortNote, wideNote])} />,
    );

    const shortNoteMarks = Array.from(
      container.querySelectorAll<HTMLElement>(
        '[data-reader-record-user-note-asset-ids~="asset_note_short"]',
      ),
    );
    const wideNoteMarks = Array.from(
      container.querySelectorAll<HTMLElement>(
        '[data-reader-record-user-note-asset-ids~="asset_note_wide"]',
      ),
    );
    expect(shortNoteMarks.length).toBeGreaterThan(0);
    expect(wideNoteMarks.length).toBeGreaterThan(0);

    fireEvent.click(shortNoteMarks[0] as HTMLElement);
    let panel = await screen.findByTestId("reader-record-inline-comment-panel");
    expect(panel.textContent).toContain("Short policy note.");
    expect(panel.textContent).toContain("policy");

    const wideOnlyMark = wideNoteMarks.find((element) =>
      element.textContent?.includes("choices"),
    );
    expect(wideOnlyMark).toBeDefined();
    if (!wideOnlyMark) {
      throw new Error("Expected wide-only note fragment");
    }

    fireEvent.click(wideOnlyMark);
    panel = await screen.findByTestId("reader-record-inline-comment-panel");
    await waitFor(() => {
      expect(panel.textContent).toContain("Wider policy-choices note.");
    });
    expect(panel.textContent).toContain("policy choices");
  });

  it("edits an existing note through the Reading Record PATCH endpoint", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/web/favorites")) {
        return Promise.resolve(
          new Response(JSON.stringify({ ok: true, favorited: false }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify({ ok: true, status: "updated" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    });
    const onRequestSnapshotReload = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("fetch", fetchMock);

    const noteAsset = makePolicyNoteAsset({
      asset_id: "asset_note_1",
      note_text: "Original note text for editing.",
    });
    const { container } = render(
      <ReaderRecordPlateSurface
        snapshot={makeSnapshot([noteAsset])}
        onRequestSnapshotReload={onRequestSnapshotReload}
      />,
    );

    const noteMark = container.querySelector<HTMLElement>(
      '[data-reader-record-user-note-asset-ids]',
    );
    expect(noteMark).not.toBeNull();
    if (!noteMark) {
      throw new Error("Expected note mark");
    }

    fireEvent.click(noteMark);

    const editButton = await screen.findByRole("button", {
      name: "编辑笔记",
    });
    fireEvent.click(editButton);

    const editInput = await screen.findByLabelText<HTMLTextAreaElement>(
      "编辑笔记内容",
    );
    fireEvent.change(editInput, {
      target: { value: "Updated note text after editing." },
    });

    const saveButton = await screen.findByRole("button", {
      name: "保存笔记",
    });
    fireEvent.click(saveButton);

    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(
        ([url]) =>
          typeof url === "string" &&
          url === "/api/web/reading-record/notes/asset_note_1",
      );
      expect(patchCall).toBeDefined();
      expect(
        (patchCall?.[1] as RequestInit | undefined)?.method,
      ).toBe("PATCH");
      const body = JSON.parse(
        String((patchCall?.[1] as RequestInit | undefined)?.body),
      ) as Record<string, unknown>;
      expect(body.noteText).toBe("Updated note text after editing.");
    });

    await waitFor(() => {
      expect(onRequestSnapshotReload).toHaveBeenCalledTimes(1);
    });
  });

  it("deletes an existing note through the Reading Record DELETE endpoint", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/web/favorites")) {
        return Promise.resolve(
          new Response(JSON.stringify({ ok: true, favorited: false }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify({ ok: true, status: "deleted" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    });
    const onRequestSnapshotReload = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("fetch", fetchMock);

    const noteAsset = makePolicyNoteAsset({
      asset_id: "asset_note_1",
      note_text: "Note to be deleted.",
    });
    const { container } = render(
      <ReaderRecordPlateSurface
        snapshot={makeSnapshot([noteAsset])}
        onRequestSnapshotReload={onRequestSnapshotReload}
      />,
    );

    const noteMark = container.querySelector<HTMLElement>(
      '[data-reader-record-user-note-asset-ids]',
    );
    expect(noteMark).not.toBeNull();
    if (!noteMark) {
      throw new Error("Expected note mark");
    }

    fireEvent.click(noteMark);

    const deleteButton = await screen.findByRole("button", {
      name: "删除笔记",
    });
    fireEvent.click(deleteButton);

    await waitFor(() => {
      const deleteCall = fetchMock.mock.calls.find(
        ([url]) =>
          typeof url === "string" &&
          url === "/api/web/reading-record/notes/asset_note_1",
      );
      expect(deleteCall).toBeDefined();
      expect(
        (deleteCall?.[1] as RequestInit | undefined)?.method,
      ).toBe("DELETE");
    });

    await waitFor(() => {
      expect(onRequestSnapshotReload).toHaveBeenCalledTimes(1);
    });
  });

  it("cancels a draft note without calling any write endpoint", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/web/favorites")) {
        return Promise.resolve(
          new Response(JSON.stringify({ ok: true, favorited: false }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot()} />,
    );

    const memoryMark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) {
      throw new Error("Expected memory mark");
    }

    selectTextInElement(memoryMark, 0, "memory".length);

    const noteButton = await waitForSelectionAction(container, "note");
    await waitFor(() => {
      expect(noteButton.disabled).toBe(false);
    });
    fireEvent.click(noteButton);

    const noteInput = await screen.findByTestId<HTMLTextAreaElement>(
      "reader-record-plate-note-input",
    );
    expect(noteInput).not.toBeNull();

    const cancelButton = screen.getByRole("button", { name: "取消" });
    fireEvent.click(cancelButton);

    await waitFor(() => {
      expect(
        screen.queryByTestId("reader-record-plate-note-input"),
      ).toBeNull();
    });

    const nonFavoritesCalls = fetchMock.mock.calls.filter(
      ([url]) => !(typeof url === "string" && url.includes("/api/web/favorites")),
    );
    expect(nonFavoritesCalls).toHaveLength(0);
  });

  it("cancels note editing and returns to view mode", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/web/favorites")) {
        return Promise.resolve(
          new Response(JSON.stringify({ ok: true, favorited: false }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const noteAsset = makePolicyNoteAsset({
      asset_id: "asset_note_1",
      note_text: "Original note for cancel-edit test.",
    });
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot([noteAsset])} />,
    );

    const noteMark = container.querySelector<HTMLElement>(
      '[data-reader-record-user-note-asset-ids]',
    );
    expect(noteMark).not.toBeNull();
    if (!noteMark) {
      throw new Error("Expected note mark");
    }

    fireEvent.click(noteMark);

    const editButton = await screen.findByRole("button", {
      name: "编辑笔记",
    });
    fireEvent.click(editButton);

    const editInput = await screen.findByLabelText<HTMLTextAreaElement>(
      "编辑笔记内容",
    );
    fireEvent.change(editInput, {
      target: { value: "Modified text that should not be saved." },
    });

    const cancelEditButton = screen.getByRole("button", {
      name: "取消编辑笔记",
    });
    fireEvent.click(cancelEditButton);

    await waitFor(() => {
      expect(
        screen.queryByLabelText("编辑笔记内容"),
      ).toBeNull();
    });

    expect(
      screen.getByText("Original note for cancel-edit test."),
    ).toBeTruthy();

    const nonFavoritesCalls = fetchMock.mock.calls.filter(
      ([url]) => !(typeof url === "string" && url.includes("/api/web/favorites")),
    );
    expect(nonFavoritesCalls).toHaveLength(0);
  });

  it("keeps source multi_text limited to Copy and blocks Ask plus write actions", async () => {
    const writeText = installClipboardMock();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSplitSegmentSnapshot()} />,
    );
    const firstSegmentLeaf = container.querySelector<HTMLElement>(
      '[data-anchor-segment-id="seg_1"] [data-reader-record-leaf="segment_text"]',
    );
    const secondSegmentLeaf = container.querySelector<HTMLElement>(
      '[data-anchor-segment-id="seg_2"] [data-reader-record-leaf="segment_text"]',
    );
    expect(firstSegmentLeaf).not.toBeNull();
    expect(secondSegmentLeaf).not.toBeNull();
    if (!firstSegmentLeaf || !secondSegmentLeaf) {
      throw new Error("Expected split segment leaves");
    }

    selectAcrossElements(firstSegmentLeaf, 14, secondSegmentLeaf, 13);

    const actions = screen.getByTestId("reader-record-plate-selection-state");
    await waitFor(() => {
      expect(actions.dataset.readerRecordSelectionDraftCount).toBe("2");
    });
    expect(actions.dataset.readerRecordSelectionSupported).toBe("true");

    const lookupButton = await waitForSelectionAction(container, "lookup");
    const copyButton = await waitForSelectionAction(container, "copy");
    const askButton = await waitForSelectionAction(container, "ask");
    const highlightButton = await waitForSelectionAction(container, "highlight");
    const noteButton = await waitForSelectionAction(container, "note");

    expect(copyButton.disabled).toBe(false);
    expect(askButton.disabled).toBe(true);
    expect(askButton.dataset.readerRecordDisabledReason).toBe("跨句选区暂不支持 Ask");
    expect(lookupButton.disabled).toBe(true);
    expect(lookupButton.dataset.readerRecordDisabledReason).toBe("跨句选区暂不支持查词");
    expect(highlightButton.disabled).toBe(true);
    expect(highlightButton.dataset.readerRecordDisabledReason).toBe("跨句选区暂不支持高亮/笔记");
    expect(noteButton.disabled).toBe(true);
    expect(noteButton.dataset.readerRecordDisabledReason).toBe("跨句选区暂不支持高亮/笔记");

    fireEvent.click(copyButton);
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith("memory shapes policy");
    });

    fireEvent.click(askButton);
    expect(container.querySelector('[data-reader-record-action="feedback"]')).toBeNull();
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).includes("/api/web/reading-record/highlights"),
      ),
    ).toBe(false);
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).includes("/api/web/reading-record/notes"),
      ),
    ).toBe(false);
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).includes("/messages/stream"),
      ),
    ).toBe(false);
  });

  it("enables Copy and disables Ask for translation selections without a stable anchor", async () => {
    const writeText = installClipboardMock();
    const fetchMock = installReaderAskFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const blockquote = container.querySelector<HTMLElement>(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquote).not.toBeNull();
    if (!blockquote) {
      throw new Error("Expected blockquote block");
    }

    selectTextInElement(blockquote, 0, 4);

    const actions = screen.getByTestId("reader-record-plate-selection-state");
    await waitFor(() => {
      expect(actions.dataset.readerRecordSelectionSupported).toBe("true");
    });
    expect(actions.dataset.readerRecordSelectionDraftCount).toBe("0");
    expect(actions.dataset.readerRecordSelectionSurfaceKind).toBe("translation");
    expect(actions.dataset.readerRecordSelectionBlockType).toBe("reader_blockquote");
    expect(actions.dataset.readerRecordSelectionBlockId).toBe(
      "blockquote:layer_translation_1:group_translation_1",
    );
    expect(actions.dataset.readerRecordSelectionUnitId).toBe("unit_1");
    expect(actions.dataset.readerRecordSelectionLayerId).toBe("layer_translation_1");

    const lookupButton = await waitForSelectionAction(container, "lookup");
    const copyButton = await waitForSelectionAction(container, "copy");
    const askButton = await waitForSelectionAction(container, "ask");
    const highlightButton = await waitForSelectionAction(container, "highlight");
    const noteButton = await waitForSelectionAction(container, "note");

    expect(copyButton.disabled).toBe(false);
    expect(askButton.disabled).toBe(true);
    expect(askButton.dataset.readerRecordDisabledReason).toBe("当前仅支持原文 Ask");
    expect(lookupButton.disabled).toBe(true);
    expect(lookupButton.dataset.readerRecordDisabledReason).toBe("当前仅支持原文查词");
    expect(highlightButton.disabled).toBe(true);
    expect(highlightButton.dataset.readerRecordDisabledReason).toBe("当前仅支持原文高亮/笔记");
    expect(noteButton.disabled).toBe(true);
    expect(noteButton.dataset.readerRecordDisabledReason).toBe("当前仅支持原文高亮/笔记");

    fireEvent.click(copyButton);
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith("制度记忆");
    });

    fireEvent.click(askButton);
    expect(screen.queryByRole("button", { name: "发送" })).toBeNull();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/messages/stream"),
      ),
    ).toBe(false);
    expect(container.querySelector('[data-reader-record-action="feedback"]')).toBeNull();
  });

  it("keeps multi-segment translation selections Copy-only without source anchor fallback", async () => {
    const fetchMock = installReaderAskFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSplitSegmentTranslationSnapshot()} />,
    );
    const blockquotes = container.querySelectorAll<HTMLElement>(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquotes).toHaveLength(1);
    const blockquote = blockquotes[0];
    if (!blockquote) {
      throw new Error("Expected blockquote block");
    }
    expect(blockquote.textContent).toContain(TRANSLATION_TEXT);

    selectTextInElement(blockquote, 0, 4);

    const actions = screen.getByTestId("reader-record-plate-selection-state");
    await waitFor(() => {
      expect(actions.dataset.readerRecordSelectionSurfaceKind).toBe("translation");
    });
    expect(actions.dataset.readerRecordSelectionAnchorSegmentId).toBeUndefined();

    const askButton = await waitForSelectionAction(container, "ask");
    expect(askButton.disabled).toBe(true);
    expect(askButton.dataset.readerRecordDisabledReason).toBe("当前仅支持原文 Ask");
    fireEvent.click(askButton);
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/messages/stream"),
      ),
    ).toBe(false);
  });

  it("does not silently fallback to a source anchor for mixed source and enhancement selections", async () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const sourceParagraph = container.querySelector<HTMLElement>(
      '[data-reader-record-node="paragraph"]',
    );
    const blockquote = container.querySelector<HTMLElement>(
      '[data-reader-record-node="blockquote"]',
    );
    expect(sourceParagraph).not.toBeNull();
    expect(blockquote).not.toBeNull();
    if (!sourceParagraph || !blockquote) {
      throw new Error("Expected source paragraph and translation blockquote");
    }

    selectTextInElement(sourceParagraph, 0, "Institutional".length);
    const actions = screen.getByTestId("reader-record-plate-selection-state");
    const copyButton = await waitForSelectionAction(container, "copy");
    const askButton = await waitForSelectionAction(container, "ask");
    const highlightButton = await waitForSelectionAction(container, "highlight");
    await waitFor(() => {
      expect(actions.dataset.readerRecordSelectionSurfaceKind).toBe("source");
      expect(copyButton.disabled).toBe(false);
      expect(askButton.disabled).toBe(false);
      expect(highlightButton.disabled).toBe(false);
    });

    selectAcrossElements(sourceParagraph, 0, blockquote, 2);
    await waitFor(() => {
      expect(copyButton.disabled).toBe(true);
      expect(askButton.disabled).toBe(true);
      expect(highlightButton.disabled).toBe(true);
    });
    expect(actions.dataset.readerRecordSelectionSupported).toBe("false");
    expect(actions.dataset.readerRecordSelectionSurfaceKind).not.toBe("source");
  });

  it("disables Ask for grammar callout selections without a stable source anchor", async () => {
    const fetchMock = installReaderAskFetchMock();
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const grammarCallout = container.querySelector<HTMLElement>(
      '[data-callout-variant="grammar"]',
    );
    const grammarToggle = grammarCallout?.querySelector<HTMLButtonElement>(
      '[data-reader-record-callout-toggle="grammar"]',
    );
    expect(grammarCallout).not.toBeNull();
    expect(grammarToggle).not.toBeNull();
    if (!grammarCallout || !grammarToggle) {
      throw new Error("Expected grammar callout controls");
    }

    fireEvent.click(grammarToggle);
    await waitFor(() => {
      expect(grammarCallout.dataset.readerRecordCalloutCollapsed).toBe("false");
    });
    const grammarContent = grammarCallout.querySelector<HTMLElement>(
      '[data-reader-record-markdown-content="plate"] p',
    );
    expect(grammarContent).not.toBeNull();
    if (!grammarContent) {
      throw new Error("Expected grammar callout content");
    }

    selectTextInElement(grammarContent, 0, "shapes".length);

    const actions = screen.getByTestId("reader-record-plate-selection-state");
    await waitFor(() => {
      expect(actions.dataset.readerRecordSelectionSurfaceKind).toBe("grammar_callout");
    });
    expect(actions.dataset.readerRecordSelectionSupported).toBe("true");
    expect(actions.dataset.readerRecordSelectionBlockType).toBe("reader_callout");
    expect(actions.dataset.readerRecordSelectionBlockId).toBe("callout:grammar:grammar_item_1");
    expect(actions.dataset.readerRecordSelectionAnchorSegmentId).toBe("seg_1");
    expect(actions.dataset.readerRecordSelectionUnitId).toBe("unit_1");
    expect(actions.dataset.readerRecordSelectionLayerId).toBe("layer_grammar_1");

    const askButton = await waitForSelectionAction(container, "ask");
    const lookupButton = await waitForSelectionAction(container, "lookup");
    const copyButton = await waitForSelectionAction(container, "copy");
    const highlightButton = await waitForSelectionAction(container, "highlight");
    const noteButton = await waitForSelectionAction(container, "note");
    expect(copyButton.disabled).toBe(false);
    expect(askButton.disabled).toBe(true);
    expect(askButton.dataset.readerRecordDisabledReason).toBe("当前仅支持原文 Ask");
    expect(lookupButton.disabled).toBe(true);
    expect(lookupButton.dataset.readerRecordDisabledReason).toBe("当前仅支持原文查词");
    expect(highlightButton.disabled).toBe(true);
    expect(highlightButton.dataset.readerRecordDisabledReason).toBe("当前仅支持原文高亮/笔记");
    expect(noteButton.disabled).toBe(true);
    expect(noteButton.dataset.readerRecordDisabledReason).toBe("当前仅支持原文高亮/笔记");

    fireEvent.click(askButton);
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/messages/stream"),
      ),
    ).toBe(false);
  });

  it("disables Ask for sentence analysis selections without a stable source anchor", async () => {
    const fetchMock = installReaderAskFetchMock();
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const analysisBlock = container.querySelector<HTMLElement>(
      '[data-reader-record-node="sentence-analysis"]',
    );
    const analysisToggle = analysisBlock?.querySelector<HTMLButtonElement>(
      '[data-reader-record-callout-toggle="sentence-analysis"]',
    );
    expect(analysisBlock).not.toBeNull();
    expect(analysisToggle).not.toBeNull();
    if (!analysisBlock || !analysisToggle) {
      throw new Error("Expected sentence analysis controls");
    }

    fireEvent.click(analysisToggle);
    await waitFor(() => {
      expect(analysisBlock.dataset.readerRecordSentenceAnalysisCollapsed).toBe("false");
    });
    const analysisContent = analysisBlock.querySelector<HTMLElement>(
      '[data-reader-record-markdown-content="plate"] p',
    );
    expect(analysisContent).not.toBeNull();
    if (!analysisContent) {
      throw new Error("Expected sentence analysis content");
    }

    selectTextInElement(analysisContent, 0, "Institutional".length);

    const actions = screen.getByTestId("reader-record-plate-selection-state");
    await waitFor(() => {
      expect(actions.dataset.readerRecordSelectionSurfaceKind).toBe("sentence_analysis");
    });
    expect(actions.dataset.readerRecordSelectionSupported).toBe("true");
    expect(actions.dataset.readerRecordSelectionBlockType).toBe("reader_sentence_analysis");
    expect(actions.dataset.readerRecordSelectionBlockId).toBe("sentence_analysis:analysis_1");
    expect(actions.dataset.readerRecordSelectionAnchorSegmentId).toBe("seg_1");
    expect(actions.dataset.readerRecordSelectionUnitId).toBe("unit_1");
    expect(actions.dataset.readerRecordSelectionLayerId).toBe("layer_sentence_analysis_1");
    expect(actions.dataset.readerRecordSelectionAnalysisId).toBe("analysis_1");

    const askButton = await waitForSelectionAction(container, "ask");
    const copyButton = await waitForSelectionAction(container, "copy");
    expect(copyButton.disabled).toBe(false);
    expect(askButton.disabled).toBe(true);
    expect(askButton.dataset.readerRecordDisabledReason).toBe("当前仅支持原文 Ask");
    fireEvent.click(askButton);
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/messages/stream"),
      ),
    ).toBe(false);
  });

  it("keeps sentence analysis chunk selections inside the Plate-managed analysis block", async () => {
    const fetchMock = installReaderAskFetchMock();
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const analysisBlock = container.querySelector<HTMLElement>(
      '[data-reader-record-node="sentence-analysis"]',
    );
    const analysisToggle = analysisBlock?.querySelector<HTMLButtonElement>(
      '[data-reader-record-callout-toggle="sentence-analysis"]',
    );
    expect(analysisBlock).not.toBeNull();
    expect(analysisToggle).not.toBeNull();
    if (!analysisBlock || !analysisToggle) {
      throw new Error("Expected sentence analysis controls");
    }
    fireEvent.click(analysisToggle);
    await waitFor(() => {
      expect(analysisBlock.dataset.readerRecordSentenceAnalysisCollapsed).toBe("false");
    });
    const chunk = container.querySelector<HTMLElement>(
      '[data-reader-record-sentence-analysis-chunk="subject"]',
    );
    const chunkText = chunk?.querySelector<HTMLElement>("dd");
    expect(chunk).not.toBeNull();
    expect(chunk?.getAttribute("data-slate-node")).toBe("element");
    expect(chunkText).not.toBeNull();
    if (!chunkText) {
      throw new Error("Expected Plate-managed sentence analysis chunk text");
    }

    selectTextInElement(chunkText, 0, "Institutional".length);

    const actions = screen.getByTestId("reader-record-plate-selection-state");
    await waitFor(() => {
      expect(actions.dataset.readerRecordSelectionSurfaceKind).toBe("sentence_analysis");
    });
    expect(actions.dataset.readerRecordSelectionSupported).toBe("true");
    expect(actions.dataset.readerRecordSelectionBlockId).toBe("sentence_analysis:analysis_1");

    const askButton = await waitForSelectionAction(container, "ask");
    const copyButton = await waitForSelectionAction(container, "copy");
    expect(copyButton.disabled).toBe(false);
    expect(askButton.disabled).toBe(true);
    expect(askButton.dataset.readerRecordDisabledReason).toBe("当前仅支持原文 Ask");
    fireEvent.click(askButton);
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/messages/stream"),
      ),
    ).toBe(false);
  });

  it("activates sentence analysis source overlay only from chunk row hover, focus, or tap", async () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const analysisBlock = container.querySelector<HTMLElement>(
      '[data-reader-record-node="sentence-analysis"]',
    );
    const analysisToggle = analysisBlock?.querySelector<HTMLButtonElement>(
      '[data-reader-record-callout-toggle="sentence-analysis"]',
    );
    expect(analysisBlock).not.toBeNull();
    expect(analysisToggle).not.toBeNull();
    if (!analysisBlock || !analysisToggle) {
      throw new Error("Expected sentence analysis controls");
    }
    fireEvent.click(analysisToggle);
    await waitFor(() => {
      expect(analysisBlock.dataset.readerRecordSentenceAnalysisCollapsed).toBe("false");
    });
    const chunk = container.querySelector<HTMLElement>(
      '[data-reader-record-sentence-analysis-chunk="subject"]',
    );
    expect(chunk).not.toBeNull();
    if (!chunk) {
      throw new Error("Expected sentence analysis chunk row");
    }

    expect(chunk.dataset.readerRecordSentenceAnalysisChunkMatch).toBe("true");
    expect(chunk.dataset.readerRecordSentenceAnalysisChunkSourceMarkId).toBe(
      "sentence_chunk:analysis_1:1:subject",
    );
    expect(chunk.dataset.readerRecordSentenceAnalysisChunkSourceStart).toBe("0");
    expect(chunk.dataset.readerRecordSentenceAnalysisChunkSourceEnd).toBe("20");

    const sourceLeaves = Array.from(
      container.querySelectorAll<HTMLElement>(
        '[data-reader-record-sentence-analysis-chunk-source="sentence_chunk:analysis_1:1:subject"]',
      ),
    );
    expect(sourceLeaves).toHaveLength(2);
    expect(sourceLeaves.map((leaf) => leaf.textContent).join("")).toBe(
      "Institutional memory",
    );
    for (const leaf of sourceLeaves) {
      expect(leaf.dataset.readerRecordMarkStackKinds).toContain(
        "sentence_analysis_chunk",
      );
      expect(leaf.tabIndex).toBe(-1);
    }

    fireEvent.mouseEnter(sourceLeaves[0]!);
    fireEvent.click(sourceLeaves[0]!);
    await waitFor(() => {
      expect(chunk.dataset.readerRecordSentenceAnalysisChunkActive).toBe("false");
      for (const leaf of sourceLeaves) {
        expect(leaf.dataset.readerRecordSentenceAnalysisChunkActive).toBeUndefined();
      }
    });

    fireEvent.mouseEnter(chunk);
    await waitFor(() => {
      expect(chunk.dataset.readerRecordSentenceAnalysisChunkActive).toBe("true");
      for (const leaf of sourceLeaves) {
        expect(leaf.dataset.readerRecordSentenceAnalysisChunkActive).toBe("true");
      }
    });

    fireEvent.mouseLeave(chunk);
    await waitFor(() => {
      expect(chunk.dataset.readerRecordSentenceAnalysisChunkActive).toBe("false");
      for (const leaf of sourceLeaves) {
        expect(leaf.dataset.readerRecordSentenceAnalysisChunkActive).toBeUndefined();
      }
    });

    fireEvent.focus(chunk);
    await waitFor(() => {
      expect(chunk.dataset.readerRecordSentenceAnalysisChunkActive).toBe("true");
      for (const leaf of sourceLeaves) {
        expect(leaf.dataset.readerRecordSentenceAnalysisChunkActive).toBe("true");
      }
    });

    fireEvent.blur(chunk);
    await waitFor(() => {
      expect(chunk.dataset.readerRecordSentenceAnalysisChunkActive).toBe("false");
    });

    fireEvent.click(chunk);
    await waitFor(() => {
      expect(chunk.dataset.readerRecordSentenceAnalysisChunkActive).toBe("true");
      for (const leaf of sourceLeaves) {
        expect(leaf.dataset.readerRecordSentenceAnalysisChunkActive).toBe("true");
      }
    });
  });

  it("freezes Quick Peek reference rect and does not resolve mark intent from DOM JSON payloads", () => {
    const surfaceSource = readFileSync(
      resolve(process.cwd(), "src/components/reader/plate/ReaderRecordPlateSurface.tsx"),
      "utf8",
    );
    const leafSource = readFileSync(
      resolve(process.cwd(), "src/components/editor/plugins/reader-leaf-kit.tsx"),
      "utf8",
    );

    expect(surfaceSource).toContain("const rect = anchor.getBoundingClientRect();");
    expect(surfaceSource).toContain(
      'quickPeekAnchorRef.current = { kind: "range", getRect: () => rect };',
    );
    expect(surfaceSource).toContain("handleLeafClickIntent");
    expect(surfaceSource).not.toMatch(/onClickCapture/);
    expect(surfaceSource).not.toMatch(/handleSurfaceClick/);
    expect(surfaceSource).not.toMatch(/readerRecordMarkPayload/);
    expect(surfaceSource).not.toMatch(/JSON\.parse\(.*payload/i);
    expect(leafSource).not.toMatch(/data-reader-record-mark-payload/);
  });

  it("keeps Plate write paths on RR APIs and avoids legacy adapters or legacy note/annotation routes", () => {
    const surfaceSource = readFileSync(
      resolve(process.cwd(), "src/components/reader/plate/ReaderRecordPlateSurface.tsx"),
      "utf8",
    );
    const otherSources = [
      "src/lib/reader-plate/projection/reader-record-plate-document.ts",
      "src/lib/reader-plate/projection/reader-record-anchor-draft.ts",
      "src/lib/reader-plate/projection/reader-record-dom-selection.ts",
      "src/services/bff/reading-record-user-assets.ts",
      "src/app/api/web/reading-record/highlights/route.ts",
      "src/app/api/web/reading-record/notes/route.ts",
    ].map((filePath) => readFileSync(resolve(process.cwd(), filePath), "utf8"));

    expect(surfaceSource).toMatch(/AiWorkspacePanel/);
    expect(surfaceSource).toMatch(/recordScope="reading_record"/);
    expect(surfaceSource).not.toMatch(/\/api\/web\/reader-notes/);
    expect(surfaceSource).not.toMatch(/\/api\/web\/reader-annotations/);
    expect(surfaceSource).not.toMatch(/\/api\/web\/annotations/);

    for (const source of [surfaceSource, ...otherSources]) {
      expect(source).not.toMatch(/ReaderRecordWorkbenchSurface/);
      expect(source).not.toMatch(/ReaderVm/);
      expect(source).not.toMatch(/ReaderMockVm/);
      expect(source).not.toMatch(/readPlateReaderSelection/);
      expect(source).not.toMatch(/adaptReaderPlateSnapshotToReaderVm/);
      expect(source).not.toMatch(/renderSceneToPlateDocument/);
      expect(source).not.toMatch(/render_scene_json/);
      expect(source).not.toMatch(/analysis-tasks/);
      expect(source).not.toMatch(/\/scene/);
      expect(source).not.toMatch(/platePath|slatePath|plate_path|slate_path/);
      expect(source).not.toMatch(/\/api\/web\/writer/);
    }

    for (const source of otherSources) {
      expect(source).not.toMatch(/\/api\/web\/reader-notes/);
      expect(source).not.toMatch(/\/api\/web\/reader-annotations/);
      expect(source).not.toMatch(/\/api\/web\/annotations/);
    }
  });

  it("keeps production selection UI on the Plate FloatingToolbar path without hidden test controls", () => {
    const surfaceSource = readFileSync(
      resolve(process.cwd(), "src/components/reader/plate/ReaderRecordPlateSurface.tsx"),
      "utf8",
    );
    const floatingToolbarKitSource = readFileSync(
      resolve(process.cwd(), "src/components/editor/plugins/floating-toolbar-kit.tsx"),
      "utf8",
    );
    const toolbarSource = readFileSync(
      resolve(
        process.cwd(),
        "src/components/editor/plugins/reader-floating-toolbar-buttons.tsx",
      ),
      "utf8",
    );

    expect(floatingToolbarKitSource).toContain("FloatingToolbar");
    expect(floatingToolbarKitSource).toContain(
      'data-reader-record-floating-toolbar="plate"',
    );
    expect(floatingToolbarKitSource).toContain("!shadow-[0_8px_20px");
    expect(floatingToolbarKitSource).toContain(
      "[&_[data-slot=separator][data-orientation=vertical]]:h-6",
    );
    expect(toolbarSource).toContain("rounded-[8px]");
    expect(toolbarSource).toContain("hover:bg-lens-blue-soft/35");
    expect(toolbarSource).not.toMatch(/hover:bg-ink/);
    expect(surfaceSource).toContain("SelectionActionState");
    expect(surfaceSource).not.toMatch(/SelectionActionStrip/);
    expect(surfaceSource).not.toMatch(/data-reader-record-test-action/);
    expect(surfaceSource).not.toMatch(/removeAllRanges/);
  });

  it("routes the Note toolbar action through the Plate CommentKit draft path", () => {
    const surfaceSource = readFileSync(
      resolve(process.cwd(), "src/components/reader/plate/ReaderRecordPlateSurface.tsx"),
      "utf8",
    );
    const toolbarSource = readFileSync(
      resolve(
        process.cwd(),
        "src/components/editor/plugins/reader-floating-toolbar-buttons.tsx",
      ),
      "utf8",
    );

    expect(toolbarSource).toContain("export function ReaderNoteToolbarButton");
    expect(toolbarSource).toContain("ToolbarButton");
    expect(toolbarSource).toContain("ToolbarGroup");
    expect(toolbarSource).toContain("AIMenu");
    expect(surfaceSource).toContain("commentApiRef.current?.setDraft()");
    expect(surfaceSource).toContain("commentApiRef.current?.setActiveId");
    expect(surfaceSource).not.toMatch(/ReaderRecordNoteComposer/);
  });

  it("keeps new user highlight choices to yellow, mint, and rose", () => {
    const surfaceSource = readFileSync(
      resolve(process.cwd(), "src/components/reader/plate/ReaderRecordPlateSurface.tsx"),
      "utf8",
    );
    const userHighlightSources = [
      "src/components/reader/SelectionToolbar.tsx",
      "src/components/reader/ReaderContextPanel.tsx",
      "src/components/editor/plugins/reader-leaf-kit.tsx",
      "src/components/reader/plate/ReaderMarkLeaf.tsx",
      "src/app/globals.css",
    ].map((filePath) => readFileSync(resolve(process.cwd(), filePath), "utf8"));

    expect(
      Array.from(
        surfaceSource.matchAll(/value: "(warm_yellow|soft_mint|soft_rose)"/g),
      ).map((match) => match[1]),
    ).toEqual(["warm_yellow", "soft_mint", "soft_rose"]);
    for (const source of [surfaceSource, ...userHighlightSources]) {
      expect(source).not.toMatch(/soft_blue/);
      expect(source).not.toMatch(/soft_green/);
      expect(source).not.toMatch(/soft_purple/);
      expect(source).not.toMatch(/sage_green/);
    }
  });

  it("selection state bridge exposes write state without rendering the legacy action strip", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const state = container.querySelector<HTMLElement>(
      '[data-reader-record-actions="selection-state"]',
    );
    expect(state).not.toBeNull();
    expect(state?.dataset.readerRecordWriteState).toBe("idle");
    expect(
      container.querySelector('[data-reader-record-actions="selection-context"]'),
    ).toBeNull();
  });

  it("blockquote translation renders as a low-weight document lane", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const blockquote = container.querySelector<HTMLElement>(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquote).not.toBeNull();
    expect(blockquote?.className).toContain("reader-record-plate-translation");
    expect(blockquote?.dataset.readerRecordTranslationLane).toBe("true");
    expect(blockquote?.className).toContain("border-l");
    expect(blockquote?.className).toContain("bg-transparent");
    expect(blockquote?.className).toContain("reader-font-sans");
    expect(blockquote?.className).not.toContain("reader-serif");
    expect(blockquote?.className).toContain("reader-record-plate-translation-copy");
    expect(blockquote?.getAttribute("aria-label")).toBe("译文");
    expect(blockquote?.textContent).toContain(TRANSLATION_TEXT);
    expect(blockquote?.textContent).not.toContain("本段译文");

    const visibleLabel = blockquote?.querySelector("span");
    expect(visibleLabel?.textContent).not.toBe("本段译文");
  });

  it("uses the Reader Record Plate typography ramp on the document surface", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const surface = screen.getByTestId("reader-record-plate-surface");
    const headerColumn = surface.querySelector<HTMLElement>(".reader-header-band-inner");
    const contentColumn = surface.querySelector<HTMLElement>("[class*='max-w-[var(--reader-record-main-width)]']");
    const documentSurface = container.querySelector<HTMLElement>(
      ".reader-record-plate-document",
    );
    const paragraph = container.querySelector<HTMLElement>(
      '[data-reader-record-node="paragraph"]',
    );
    const analysisBlock = container.querySelector<HTMLElement>(
      '[data-reader-record-node="sentence-analysis"]',
    );

    expect(headerColumn?.className).toContain("max-w-[var(--reader-record-main-width)]");
    expect(contentColumn?.className).toContain("max-w-[var(--reader-record-main-width)]");
    expect(documentSurface?.className).toContain("reader-record-plate-font-sans");
    expect(documentSurface?.className).toContain("reader-record-plate-type-md");
    expect(documentSurface?.className).toContain(
      "reader-record-plate-density-intensive",
    );
    expect(paragraph?.className).toContain("reader-record-plate-paragraph");
    expect(analysisBlock?.className).toContain("reader-record-plate-sentence-analysis");
  });

  it("surface source code includes auto-dismiss timer for UI polish", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/components/reader/plate/ReaderRecordPlateSurface.tsx"),
      "utf-8",
    );
    expect(source).toContain('window.setTimeout(() => {\n      setWriteState({ kind: "idle" });\n    }, 4000)');
  });

  it("paragraph block carries anchor segment metadata as data attributes", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const paragraph = container.querySelector<HTMLElement>(
      '[data-reader-record-node="paragraph"]',
    );
    expect(paragraph?.dataset.anchorSegmentId).toBe("seg_1");
    expect(paragraph?.dataset.sentenceId).toBe("sent_1");
    expect(paragraph?.dataset.unitId).toBe("unit_1");
    expect(paragraph?.dataset.readerRecordUnitStart).toBe("true");
  });

  it("segment text leaf carries anchor metadata as data attributes", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const leaf = container.querySelector<HTMLElement>(
      '[data-reader-record-leaf="segment_text"]',
    );
    expect(leaf?.dataset.anchorSegmentId).toBe("seg_1");
    expect(leaf?.dataset.segmentStartUtf16).toBe("0");
    // splitTextLeafByMarks 为每个子 leaf 设置局部 segmentRange，
    // 第一个子 leaf 覆盖 "Institutional "（0-14），而非完整 segment（0-43）。
    expect(leaf?.dataset.segmentEndUtf16).toBe("14");
  });

  it("enhancement blocks carry anchor segment metadata", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const grammarCallout = container.querySelector<HTMLElement>(
      '[data-callout-variant="grammar"]',
    );
    const analysisBlock = container.querySelector<HTMLElement>(
      '[data-reader-record-node="sentence-analysis"]',
    );

    expect(grammarCallout?.dataset.anchorSegmentId).toBe("seg_1");
    expect(analysisBlock?.dataset.anchorSegmentId).toBe("seg_1");
    expect(analysisBlock?.dataset.analysisId).toBe("analysis_1");
  });
});
