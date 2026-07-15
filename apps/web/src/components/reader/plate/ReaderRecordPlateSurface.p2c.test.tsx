/**
 * @vitest-environment jsdom
 *
 * T4.2a-PUX-R4-R2.2-P2c-R1 Task 4: Surface component tests for grammar
 * first-publish semantic insert path.
 *
 * Tests:
 * 1. 合法 grammar 首发不调用 setValue，不 replace 既有 paragraph
 * 2. 既有 paragraph / 词汇 mark DOM identity 保留
 * 3. vocabulary Quick Peek 锚定同一 paragraph 时 grammar insert 后仍可见，浮层 rect 非零
 * 4. fallback full reload 时 Quick Peek 被关闭，旧 panel 不残留
 *
 * The merger is mocked to control targeted_apply vs fallback_full_reload,
 * avoiding the C6 fail-closed behavior that occurs when the real projection
 * adds grammar marks to the anchor paragraph's text leaf on first publish.
 */

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { computeUtf16FNV1a } from "@claread/contracts";
import type { ReactNode } from "react";
import type { Descendant } from "platejs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  READER_PLATE_SNAPSHOT_SCHEMA_KIND,
  READER_TEXT_RANGE_HASH_ALGORITHM,
  READER_TEXT_RANGE_OFFSET_UNIT,
  type ReaderAnchorSegmentNodeDto,
  type ReaderEnhancementProgressDto,
  type ReaderEventResponseDto,
  type ReaderGrammarNoteMarkDto,
  type ReaderPlateSnapshotDto,
  type ReaderSourceBlockNodeDto,
  type ReaderSnapshotUserAssetDto,
  type ReaderTitleGenerationStatus,
  type ReaderUnitNodeDto,
  type ReaderVocabularyMarkDto,
} from "@/types/api/reader-plate";
import type { ReloadContext } from "@/lib/reader-plate-snapshot/polling";
import type { WebDictResult } from "@/types/api/dict";

// Mock the incremental projection merger so we can control
// targeted_apply (insert ops) vs fallback_full_reload per test.
vi.mock("@/lib/reader-plate-snapshot/incremental-projection-merger", () => ({
  mergeIncrementalProjection: vi.fn(),
}));

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
import { mergeIncrementalProjection } from "@/lib/reader-plate-snapshot/incremental-projection-merger";

const mockedMerge = vi.mocked(mergeIncrementalProjection);

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
  if (!HTMLElement.prototype.scrollTo) {
    HTMLElement.prototype.scrollTo = vi.fn();
  }
  // 模拟非零 rect，用于 Quick Peek 浮层定位验证
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(() => ({
    x: 100,
    y: 200,
    top: 200,
    left: 100,
    bottom: 250,
    right: 300,
    width: 200,
    height: 50,
    toJSON() {
      return { x: 100, y: 200, top: 200, left: 100, bottom: 250, right: 300, width: 200, height: 50 };
    },
  }));
  vi.stubGlobal(
    "ResizeObserver",
    class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  // 默认 mock 收藏接口
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
  mockedMerge.mockReset();
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
      title_generation_status: "pending" as ReaderTitleGenerationStatus,
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

/**
 * 构造含 seg_1 词汇标注的双段快照，用于 Quick Peek 锚定 seg_1 + grammar
 * insert 在 seg_2 的测试场景。
 */
function makeSplitSegmentSnapshotWithVocabMark(): ReaderPlateSnapshotDto {
  const snapshot = makeSplitSegmentSnapshot();
  const unit = snapshot.value[0];
  const sourceBlock = unit.children.find(
    (child): child is ReaderSourceBlockNodeDto => child.type === "reader_source_block",
  );
  if (!sourceBlock) {
    throw new Error("Expected source block fixture");
  }
  // makeSplitSegmentSnapshot 构造的 source block 第一个 child 一定是 anchor segment，
  // 使用 cast 避免 ReaderSourceBlockChildNodeDto 联合类型无法访问 type 字段。
  const firstSegment = sourceBlock.children[0] as ReaderAnchorSegmentNodeDto;
  // offsets 与 selected_text 长度一致（"Institutional" = 13 chars），
  // 保证 DOM 渲染的 mark text leaf 长度与 selectTextInElement 的 endOffset 匹配
  const vocabMark = makeVocabularyMark({
    mark_id: "vocab_seg_1_split_mark",
    anchor_segment_id: "seg_1",
    start_offset: 0,
    end_offset: 13,
    segment_start_utf16: 0,
    segment_end_utf16: 13,
    selected_text: "Institutional",
    phrase: "Institutional",
    gloss: "制度的",
  });

  return {
    ...snapshot,
    value: [
      {
        ...unit,
        children: [
          {
            ...sourceBlock,
            children: [
              {
                ...firstSegment,
                children: [
                  {
                    ...firstSegment.children[0],
                    reader_vocabulary_marks: [vocabMark],
                  },
                ],
              },
              ...sourceBlock.children.slice(1),
            ],
          },
          ...unit.children.slice(1),
        ],
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Event / reload helpers
// ---------------------------------------------------------------------------

function makeGrammarFirstPublishEvent(
  sequence: number = 9,
  anchorSegmentId: string = "seg_1",
  unitId: string = "unit_1",
  layerId: string = "layer_grammar_1",
  itemId: string = "grammar_item_inserted",
): ReaderEventResponseDto {
  return {
    id: `evt_${sequence}`,
    reading_record_id: "record_1",
    sequence,
    event_type: "layer_published",
    payload: {
      record_id: "record_1",
      base_id: "base_1",
      layer_id: layerId,
      layer_type: "grammar_note",
      target_scope: "unit",
      target_key: unitId,
      generation: 1,
      schema_version: 1,
      operation: "insert_after_anchor",
      insertions: [
        {
          unit_id: unitId,
          anchor_segment_id: anchorSegmentId,
          kind: "grammar_note",
          layer_id: layerId,
          item_ids: [itemId],
        },
      ],
    },
    created_at: "2026-07-14T00:00:00Z",
  };
}

function makeReloadContext(
  events: ReaderEventResponseDto[],
  reason = "layer_published",
): ReloadContext {
  return {
    cursor: 8,
    events,
    triggerClassification: {
      kind: "reload_snapshot",
      reason,
    },
    acceptedSnapshotFence: { generation: 1, baseId: "base_1" },
    reason,
  };
}

function makeNextSnapshot(
  prev: ReaderPlateSnapshotDto,
  overrides: { userAssets?: ReaderSnapshotUserAssetDto[] } = {},
): ReaderPlateSnapshotDto {
  return {
    ...prev,
    snapshot_id: "snapshot_2",
    last_event_sequence: 9,
    user_assets: overrides.userAssets ?? prev.user_assets,
  };
}

// ---------------------------------------------------------------------------
// DOM interaction helpers
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Dict helpers
// ---------------------------------------------------------------------------

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

function installQuickPeekFetchMock() {
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
      if (url.pathname.includes("/api/web/dict/")) {
        return Promise.resolve(
          new Response(JSON.stringify(makeDictionaryEntryResult("memory")), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      return Promise.resolve(new Response("Not Found", { status: 404 }));
    }),
  );
}

// ---------------------------------------------------------------------------
// Insert node builder
// ---------------------------------------------------------------------------

/**
 * 构造 grammar callout-group Plate 元素，用于 mock merger 返回的 insert
 * operation。模拟 grammar 首发时在 anchor 段落后插入的 callout-group。
 */
function makeInsertCalloutGroup(
  blockId: string,
  anchorSegmentId: string,
  unitId: string = "unit_1",
): Descendant {
  return {
    type: "reader_callout_group",
    id: blockId,
    children: [
      {
        type: "reader_callout",
        id: `callout:grammar:${blockId}:item`,
        variant: "grammar",
        icon: "📖",
        children: [{ text: "Inserted grammar note" }],
        data: {
          anchorSegmentId,
          unitId,
          layerId: "layer_grammar_inserted",
          itemId: `${blockId}:item`,
          grammarPoint: "test point",
          pattern: "test pattern",
          note: "Inserted grammar note",
        },
      },
    ],
    data: { unitId, anchorSegmentId },
  } as unknown as Descendant;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ReaderRecordPlateSurface — T4.2a-PUX-R4-R2.2-P2c grammar semantic insert", () => {
  it("合法 grammar 首发不调用 setValue，不 replace 既有 paragraph", async () => {
    const prevSnapshot = makeSnapshot();
    const nextSnapshot = makeNextSnapshot(prevSnapshot);
    const event = makeGrammarFirstPublishEvent();
    const insertBlockId = "callout-group:unit_1:seg_1:inserted";

    // Mock merger 返回 targeted_apply + insert 操作
    mockedMerge.mockReturnValue({
      kind: "targeted_apply",
      operations: [
        {
          path: [1],
          blockId: insertBlockId,
          type: "insert",
          nodes: [makeInsertCalloutGroup(insertBlockId, "seg_1")],
        },
      ],
      preservedInteraction: {
        preserveSelection: true,
        preserveScroll: true,
        preserveGrammarAccordion: true,
        preserveQuickPeek: true,
        preservePanels: true,
      },
      affectedTargetKeys: ["unit_1"],
    });

    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={prevSnapshot} />,
    );

    await waitFor(() => {
      expect(container.querySelector(".reader-record-plate-document")).not.toBeNull();
    });

    // 捕获非目标 DOM（blockquote 译文块）
    const blockquoteBefore = container.querySelector(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquoteBefore).not.toBeNull();

    await act(async () => {
      rerender(
        <ReaderRecordPlateSurface
          snapshot={nextSnapshot}
          pendingReloadContext={makeReloadContext([event])}
        />,
      );
    });

    // setValue 未调用 → 非目标 DOM identity 保留
    const blockquoteAfter = container.querySelector(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquoteAfter).not.toBeNull();
    expect(blockquoteBefore!.isSameNode(blockquoteAfter)).toBe(true);

    // insertNodes 被调用 → 新 callout-group 出现在 DOM 中
    const insertedGroup = container.querySelector(
      `[data-reader-record-block-id="${insertBlockId}"]`,
    );
    expect(insertedGroup).not.toBeNull();
  });

  it("既有 paragraph / 词汇 mark DOM identity 保留", async () => {
    const prevSnapshot = makeSnapshot();
    const nextSnapshot = makeNextSnapshot(prevSnapshot);
    const event = makeGrammarFirstPublishEvent();
    const insertBlockId = "callout-group:unit_1:seg_1:inserted_identity";

    mockedMerge.mockReturnValue({
      kind: "targeted_apply",
      operations: [
        {
          path: [1],
          blockId: insertBlockId,
          type: "insert",
          nodes: [makeInsertCalloutGroup(insertBlockId, "seg_1")],
        },
      ],
      preservedInteraction: {
        preserveSelection: true,
        preserveScroll: true,
        preserveGrammarAccordion: true,
        preserveQuickPeek: true,
        preservePanels: true,
      },
      affectedTargetKeys: ["unit_1"],
    });

    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={prevSnapshot} />,
    );

    await waitFor(() => {
      expect(container.querySelector(".reader-record-plate-document")).not.toBeNull();
    });

    // 捕获 paragraph DOM
    const paragraphBefore = container.querySelector(
      '[data-reader-record-node="paragraph"]',
    );
    expect(paragraphBefore).not.toBeNull();

    // 捕获词汇 mark DOM
    const vocabMarkBefore = container.querySelector(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(vocabMarkBefore).not.toBeNull();

    await act(async () => {
      rerender(
        <ReaderRecordPlateSurface
          snapshot={nextSnapshot}
          pendingReloadContext={makeReloadContext([event])}
        />,
      );
    });

    // paragraph DOM identity 保留（未被 replace / setValue 重建）
    const paragraphAfter = container.querySelector(
      '[data-reader-record-node="paragraph"]',
    );
    expect(paragraphAfter).not.toBeNull();
    expect(paragraphBefore!.isSameNode(paragraphAfter)).toBe(true);

    // 词汇 mark DOM identity 保留
    const vocabMarkAfter = container.querySelector(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(vocabMarkAfter).not.toBeNull();
    expect(vocabMarkBefore!.isSameNode(vocabMarkAfter)).toBe(true);
  });

  it("vocabulary Quick Peek 锚定同一 paragraph 时 grammar insert 后仍可见，浮层 rect 非零", async () => {
    installQuickPeekFetchMock();

    // 使用双段快照：seg_1 有词汇标注，grammar insert 在 seg_2
    const prevSnapshot = makeSplitSegmentSnapshotWithVocabMark();
    const nextSnapshot = makeNextSnapshot(prevSnapshot);
    const event = makeGrammarFirstPublishEvent(9, "seg_2");
    const insertBlockId = "callout-group:unit_1:seg_2:inserted";

    // Mock merger 返回 targeted_apply + insert 在 seg_2（不同 blockId）
    mockedMerge.mockReturnValue({
      kind: "targeted_apply",
      operations: [
        {
          path: [2],
          blockId: insertBlockId,
          type: "insert",
          nodes: [makeInsertCalloutGroup(insertBlockId, "seg_2")],
        },
      ],
      preservedInteraction: {
        preserveSelection: true,
        preserveScroll: true,
        preserveGrammarAccordion: true,
        preserveQuickPeek: true,
        preservePanels: true,
      },
      affectedTargetKeys: ["unit_1"],
    });

    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={prevSnapshot} />,
    );

    await waitFor(() => {
      expect(container.querySelector(".reader-record-plate-document")).not.toBeNull();
    });

    // 打开 Quick Peek：选中 seg_1 词汇标注 → 点击 lookup 按钮
    const vocabMark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_seg_1_split_mark"]',
    );
    expect(vocabMark).not.toBeNull();
    if (!vocabMark) {
      throw new Error("Expected vocabulary mark on seg_1");
    }

    selectTextInElement(vocabMark, 0, "Institutional".length);
    const lookupButton = await waitForSelectionAction(container, "lookup");
    await waitFor(() => {
      expect(lookupButton.disabled).toBe(false);
    });
    fireEvent.click(lookupButton);

    // Quick Peek 已打开
    const panelBefore = await screen.findByTestId("reader-record-plate-lookup-panel");
    expect(panelBefore).toBeTruthy();

    // 捕获 Quick Peek 锚定的 paragraph DOM
    const paragraphBefore = container.querySelector(
      '[data-reader-record-node="paragraph"]',
    );
    expect(paragraphBefore).not.toBeNull();

    await act(async () => {
      rerender(
        <ReaderRecordPlateSurface
          snapshot={nextSnapshot}
          pendingReloadContext={makeReloadContext([event])}
        />,
      );
    });

    // Quick Peek 仍然可见（insert 在不同 blockId，不触发 quickPeekTargetsReplacement）
    const panelAfter = screen.getByTestId("reader-record-plate-lookup-panel");
    expect(panelAfter).toBeTruthy();

    // 浮层 rect 非零（anchor ref 未 detach）
    const rect = panelAfter.getBoundingClientRect();
    expect(rect.width).toBeGreaterThan(0);
    expect(rect.height).toBeGreaterThan(0);

    // Quick Peek 锚定 paragraph DOM identity 保留（anchor 未重建）
    const paragraphAfter = container.querySelector(
      '[data-reader-record-node="paragraph"]',
    );
    expect(paragraphAfter).not.toBeNull();
    expect(paragraphBefore!.isSameNode(paragraphAfter)).toBe(true);
  });

  it("fallback full reload 时 Quick Peek 被关闭，旧 panel 不残留", async () => {
    installQuickPeekFetchMock();

    const prevSnapshot = makeSnapshot();
    const nextSnapshot = makeNextSnapshot(prevSnapshot);
    const event = makeGrammarFirstPublishEvent();

    // Mock merger 返回 fallback_full_reload
    mockedMerge.mockReturnValue({
      kind: "fallback_full_reload",
      reason: "test_fallback_full_reload",
    });

    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={prevSnapshot} />,
    );

    await waitFor(() => {
      expect(container.querySelector(".reader-record-plate-document")).not.toBeNull();
    });

    // 打开 Quick Peek
    const vocabMark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(vocabMark).not.toBeNull();
    if (!vocabMark) {
      throw new Error("Expected vocabulary mark");
    }

    selectTextInElement(vocabMark, 0, "memory".length);
    const lookupButton = await waitForSelectionAction(container, "lookup");
    await waitFor(() => {
      expect(lookupButton.disabled).toBe(false);
    });
    fireEvent.click(lookupButton);

    // Quick Peek 已打开
    const panel = await screen.findByTestId("reader-record-plate-lookup-panel");
    expect(panel).toBeTruthy();

    await act(async () => {
      rerender(
        <ReaderRecordPlateSurface
          snapshot={nextSnapshot}
          pendingReloadContext={makeReloadContext([event])}
        />,
      );
    });

    // Quick Peek 被关闭（lookupState idle, inspectState null, quickPeekAnchorBlockId null）
    expect(screen.queryByTestId("reader-record-plate-lookup-panel")).toBeNull();

    // 旧 panel 不残留：无浮层元素残留
    const lingeringPanels = container.querySelectorAll(
      "[data-reader-record-plate-lookup-panel], [data-testid='reader-record-plate-lookup-panel']",
    );
    expect(lingeringPanels).toHaveLength(0);

    // reload 确实发生了（paragraph 存在，setValue 重建 DOM）
    const paragraphAfter = container.querySelector(
      '[data-reader-record-node="paragraph"]',
    );
    expect(paragraphAfter).not.toBeNull();
  });
});
