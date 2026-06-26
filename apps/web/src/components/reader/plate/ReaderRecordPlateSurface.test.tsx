/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { computeUtf16FNV1a } from "@claread/contracts";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
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
  type ReaderUnitNodeDto,
  type ReaderVocabularyMarkDto,
} from "@/types/api/reader-plate";
import type { WebDictResult } from "@/types/api/dict";

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
});

afterEach(() => {
  window.getSelection()?.removeAllRanges();
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
                reader_vocabulary_marks: [makeVocabularyMark()],
                reader_grammar_note_marks: [makeGrammarMark()],
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
        analysis: "Institutional memory is the subject.",
        chunks: [{ order: 1, label: "subject", text: "Institutional memory" }],
        children: [{ text: "Institutional memory is the subject." }],
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

function selectTextInElement(element: HTMLElement, startOffset: number, endOffset: number) {
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
  const range = document.createRange();
  range.setStart(firstTextNode(startElement), startOffset);
  range.setEnd(firstTextNode(endElement), endOffset);
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
  document.dispatchEvent(new Event("selectionchange"));
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

function postedJsonBody(fetchMock: ReturnType<typeof vi.fn>) {
  const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
  return JSON.parse(String(init?.body)) as Record<string, unknown>;
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

  it("renders grammar and sentence analysis as callout blocks with variant attributes", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const grammarCallout = container.querySelector<HTMLElement>(
      '[data-reader-record-node="callout"][data-callout-variant="grammar"]',
    );
    const analysisCallout = container.querySelector<HTMLElement>(
      '[data-reader-record-node="callout"][data-callout-variant="analysis"]',
    );

    expect(grammarCallout).not.toBeNull();
    expect(grammarCallout?.textContent).toContain("predicate verb");
    expect(grammarCallout?.textContent).toContain(
      "shapes is the predicate verb.",
    );
    expect(grammarCallout?.classList.contains("reader-record-plate-callout--grammar")).toBe(true);

    expect(analysisCallout).not.toBeNull();
    expect(analysisCallout?.textContent).toContain("subject and predicate");
    expect(analysisCallout?.textContent).toContain(
      "Institutional memory is the subject.",
    );
    expect(analysisCallout?.classList.contains("reader-record-plate-callout--analysis")).toBe(true);
  });

  it("renders vocab and grammar marks with locatable data attributes", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const vocab = container.querySelector<HTMLElement>(
      '[data-reader-record-mark-id="vocab_mark_1"]',
    );
    const grammar = container.querySelector<HTMLElement>(
      '[data-reader-record-mark-id="grammar_mark_1"]',
    );

    expect(vocab?.dataset.readerRecordMarkKind).toBe("phrase_gloss");
    expect(grammar?.dataset.readerRecordMarkKind).toBe("grammar_note");
  });

  it("renders user highlight marks with stable asset attributes", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot([makeUserAsset()])} />,
    );

    const highlight = container.querySelector<HTMLElement>(
      '[data-reader-record-mark-id="user_highlight:asset_highlight_1"]',
    );

    expect(highlight?.dataset.readerRecordMarkEntry).toBe("stack");
    expect(highlight?.dataset.readerRecordMarkKind).toBe("user_highlight");
    expect(highlight?.textContent).toBe("memory");
  });

  it("keeps system marks and user marks coexisting on the source text", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot([makeUserAsset()])} />,
    );

    const paragraph = container.querySelector<HTMLElement>(
      '[data-reader-record-node="paragraph"]',
    );
    const vocab = container.querySelector<HTMLElement>(
      '[data-reader-record-mark-id="vocab_mark_1"]',
    );
    const userHighlight = container.querySelector<HTMLElement>(
      '[data-reader-record-mark-id="user_highlight:asset_highlight_1"]',
    );

    expect(paragraph?.textContent).toContain(SOURCE_TEXT);
    expect(vocab?.dataset.readerRecordMarkKind).toBe("phrase_gloss");
    expect(userHighlight?.dataset.readerRecordMarkKind).toBe("user_highlight");
  });

  it("renders the article title in the header", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const titleEl = container.querySelector<HTMLElement>(
      "[data-reader-record-reading-title]",
    );
    expect(titleEl).not.toBeNull();
    expect(titleEl?.tagName).toBe("H1");
    expect(titleEl?.textContent).toBe("Reader Record Plate Surface Fixture");
  });

  it("omits the title element when snapshot.record.title is empty", () => {
    const snapshot = makeSnapshot();
    snapshot.record.title = "";
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);

    const titleEl = container.querySelector<HTMLElement>(
      "[data-reader-record-reading-title]",
    );
    expect(titleEl).toBeNull();
  });

  it("renders header with progress status and metadata", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const header = screen.getByTestId("reader-record-plate-header");
    expect(header).toBeTruthy();
    expect(header.textContent).toContain("精读模式");
    expect(header.textContent).toContain("解析生成中");

    const progressStatus = container.querySelector<HTMLElement>(
      "[data-reader-record-progress-status]",
    );
    expect(progressStatus?.dataset.readerRecordProgressStatus).toBe(
      "readable_enhancing",
    );
  });

  it("keeps context actions disabled until there is a valid selection", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const actions = screen.getByTestId("reader-record-plate-disabled-actions");

    expect(actions.dataset.readerRecordActions).toBe("selection-context");
    expect(actions.dataset.readerRecordActionMode).toBe("idle");
    expect(
      actions.querySelector('[data-reader-record-action-hint]')?.textContent,
    ).toContain("划取原文后");

    for (const action of [
      "lookup",
      "copy",
      "ask",
      "highlight",
      "note",
      "feedback",
    ]) {
      expect(
        container.querySelector(`[data-reader-record-action="${action}"]`),
      ).toBeNull();
    }
  });

  it("maps a stable source selection to an anchor draft with unit-local UTF-16 offsets", async () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const memoryMark = container.querySelector<HTMLElement>(
      '[data-reader-record-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) {
      throw new Error("Expected memory mark");
    }

    selectTextInElement(memoryMark, 0, "memory".length);

    const actions = screen.getByTestId("reader-record-plate-disabled-actions");
    await waitFor(() => {
      expect(actions.dataset.readerRecordSelectionSupported).toBe("true");
    });
    expect(actions.dataset.readerRecordSelectionDraftCount).toBe("1");
    expect(actions.dataset.readerRecordSelectionAnchorSegmentId).toBe("seg_1");
    expect(actions.dataset.readerRecordSelectionStartOffset).toBe("14");
    expect(actions.dataset.readerRecordSelectionEndOffset).toBe("20");
    expect(actions.dataset.readerRecordActionMode).toBe("selection");
    expect(
      actions.querySelector('[data-reader-record-action-hint]')?.textContent,
    ).toContain("已选：memory");
    expect(
      container.querySelector<HTMLButtonElement>(
        '[data-reader-record-action="highlight"]',
      )?.disabled,
    ).toBe(false);
    expect(
      container.querySelector<HTMLButtonElement>('[data-reader-record-action="note"]')
        ?.disabled,
    ).toBe(false);
    expect(
      container.querySelector<HTMLButtonElement>('[data-reader-record-action="ask"]')
        ?.disabled,
    ).toBe(false);
    expect(container.querySelector('[data-reader-record-action="feedback"]')).toBeNull();
    expect(
      container.querySelector('[data-reader-record-coming-soon-actions="feedback"]')
        ?.textContent,
    ).toContain("反馈稍后开放");
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

    const actions = screen.getByTestId("reader-record-plate-disabled-actions");
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
      '[data-reader-record-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) {
      throw new Error("Expected memory mark");
    }

    selectTextInElement(memoryMark, 0, "memory".length);

    const copyButton = await waitFor(() => {
      const button = container.querySelector<HTMLButtonElement>(
        '[data-reader-record-action="copy"]',
      );
      if (!button) {
        throw new Error("Copy button not found");
      }
      return button;
    });
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
      '[data-reader-record-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) {
      throw new Error("Expected memory mark");
    }

    selectTextInElement(memoryMark, 0, "memory".length);

    const lookupButton = await screen.findByRole<HTMLButtonElement>("button", {
      name: "查词",
    });
    await waitFor(() => {
      expect(lookupButton.disabled).toBe(false);
    });
    fireEvent.click(lookupButton);

    await screen.findByTestId("reader-record-plate-lookup-panel");
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
      '[data-reader-record-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) {
      throw new Error("Expected memory mark");
    }

    selectTextInElement(memoryMark, 0, "memory".length);

    const highlightButton = await screen.findByRole<HTMLButtonElement>("button", {
      name: "高亮",
    });
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
    expect(body.color).toBe("soft_green");
    await waitFor(() => {
      expect(onRequestSnapshotReload).toHaveBeenCalledTimes(1);
    });
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/api/web/annotations"),
      ),
    ).toBe(false);
  });

  it("saves note through the Reading Record write endpoint with nested anchor", async () => {
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
      '[data-reader-record-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) {
      throw new Error("Expected memory mark");
    }

    selectTextInElement(memoryMark, 0, "memory".length);

    const noteButton = await screen.findByRole<HTMLButtonElement>("button", {
      name: "新建笔记",
    });
    await waitFor(() => {
      expect(noteButton.disabled).toBe(false);
    });
    fireEvent.click(noteButton);

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

  it("opens the RR Ask panel from a stable source selection and loads RR-scoped ask threads", async () => {
    const fetchMock = installReaderAskFetchMock();
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const memoryMark = container.querySelector<HTMLElement>(
      '[data-reader-record-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) {
      throw new Error("Expected memory mark");
    }

    selectTextInElement(memoryMark, 0, "memory".length);

    const askButton = await screen.findByRole("button", {
      name: "Ask",
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
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/api/web/reader-ask/context-records"),
      ),
    ).toBe(false);
  });

  it("opens the RR Ask panel from a saved note in Reading Record scope", async () => {
    const fetchMock = installReaderAskFetchMock();
    const noteAsset = makeUserAsset({
      asset_id: "asset_note_1",
      asset_type: "note",
      note_text: "Keep this policy concept for review.",
    });
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot([noteAsset])} />,
    );
    const noteMark = container.querySelector<HTMLElement>(
      '[data-reader-record-mark-kind="user_note"]',
    );
    expect(noteMark).not.toBeNull();
    if (!noteMark) {
      throw new Error("Expected note mark");
    }

    fireEvent.click(noteMark);

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

    const noteAsset = makeUserAsset({
      asset_id: "asset_note_1",
      asset_type: "note",
      note_text: "Original note text for editing.",
    });
    const { container } = render(
      <ReaderRecordPlateSurface
        snapshot={makeSnapshot([noteAsset])}
        onRequestSnapshotReload={onRequestSnapshotReload}
      />,
    );

    const noteMark = container.querySelector<HTMLElement>(
      '[data-reader-record-mark-kind="user_note"]',
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

    const noteAsset = makeUserAsset({
      asset_id: "asset_note_1",
      asset_type: "note",
      note_text: "Note to be deleted.",
    });
    const { container } = render(
      <ReaderRecordPlateSurface
        snapshot={makeSnapshot([noteAsset])}
        onRequestSnapshotReload={onRequestSnapshotReload}
      />,
    );

    const noteMark = container.querySelector<HTMLElement>(
      '[data-reader-record-mark-kind="user_note"]',
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
      '[data-reader-record-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) {
      throw new Error("Expected memory mark");
    }

    selectTextInElement(memoryMark, 0, "memory".length);

    const noteButton = await screen.findByRole<HTMLButtonElement>("button", {
      name: "新建笔记",
    });
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

    const noteAsset = makeUserAsset({
      asset_id: "asset_note_1",
      asset_type: "note",
      note_text: "Original note for cancel-edit test.",
    });
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot([noteAsset])} />,
    );

    const noteMark = container.querySelector<HTMLElement>(
      '[data-reader-record-mark-kind="user_note"]',
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

  it("keeps lookup, copy, and write actions disabled for unsupported cross-segment selections", async () => {
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

    const actions = screen.getByTestId("reader-record-plate-disabled-actions");
    await waitFor(() => {
      expect(actions.dataset.readerRecordSelectionDraftCount).toBe("2");
    });
    expect(actions.dataset.readerRecordSelectionSupported).toBe("false");

    for (const action of ["lookup", "copy", "ask", "highlight", "note", "feedback"]) {
      expect(
        container.querySelector(`[data-reader-record-action="${action}"]`),
      ).toBeNull();
    }
    expect(
      fetchMock.mock.calls.filter(
        ([url]) => !(typeof url === "string" && url.includes("/api/web/favorites")),
      ),
    ).toHaveLength(0);
  });

  it("does not expose executable actions for selections outside stable source text", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const blockquote = container.querySelector<HTMLElement>(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquote).not.toBeNull();
    if (!blockquote) {
      throw new Error("Expected blockquote block");
    }

    selectTextInElement(blockquote, 0, 2);

    const actions = screen.getByTestId("reader-record-plate-disabled-actions");
    await waitFor(() => {
      expect(actions.dataset.readerRecordSelectionDraftCount).toBe("0");
    });
    expect(actions.dataset.readerRecordSelectionSupported).toBe("false");
    for (const action of ["lookup", "copy", "highlight", "note", "ask", "feedback"]) {
      expect(
        container.querySelector(`[data-reader-record-action="${action}"]`),
      ).toBeNull();
    }
    expect(
      fetchMock.mock.calls.filter(
        ([url]) => !(typeof url === "string" && url.includes("/api/web/favorites")),
      ),
    ).toHaveLength(0);
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

  it("selection action strip exposes write state data attribute", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const strip = container.querySelector<HTMLElement>(
      '[data-reader-record-actions="selection-context"]',
    );
    expect(strip).not.toBeNull();
    expect(strip?.dataset.readerRecordWriteState).toBe("idle");
  });

  it("blockquote translation uses improved spacing and label", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const blockquote = container.querySelector<HTMLElement>(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquote).not.toBeNull();
    expect(blockquote?.className).toContain("border-l-2");
    expect(blockquote?.className).toContain("mt-3");

    const label = blockquote?.querySelector("span");
    expect(label?.textContent).toBe("译文");
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

  it("callout blocks carry variant and anchor segment metadata", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const grammarCallout = container.querySelector<HTMLElement>(
      '[data-callout-variant="grammar"]',
    );
    const analysisCallout = container.querySelector<HTMLElement>(
      '[data-callout-variant="analysis"]',
    );

    expect(grammarCallout?.dataset.anchorSegmentId).toBe("seg_1");
    expect(analysisCallout?.dataset.anchorSegmentId).toBe("seg_1");
  });
});
