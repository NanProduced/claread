/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { computeUtf16FNV1a } from "@claread/contracts";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

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

function makeOverlappingMarkSnapshot(): ReaderPlateSnapshotDto {
  const snapshot = makeSnapshot();
  const unit = snapshot.value[0] as ReaderUnitNodeDto;
  const sourceBlock = unit.children[0] as ReaderSourceBlockNodeDto;
  const segment = sourceBlock.children[0] as ReaderAnchorSegmentNodeDto;
  const sourceLeaf = segment.children[0] as {
    reader_grammar_note_marks?: ReaderGrammarNoteMarkDto[];
  };

  sourceLeaf.reader_grammar_note_marks = [
    makeGrammarMark({
      start_offset: 14,
      end_offset: 20,
      selected_text: "memory",
      segment_start_utf16: 14,
      segment_end_utf16: 20,
      grammar_point: "nominal object",
      pattern: "adjective + noun",
      note: "memory is part of the noun phrase.",
    }),
  ];

  return snapshot;
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

describe("ReaderRecordPlateSurface", () => {
  it("projects and renders stable source text", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const source = container.querySelector<HTMLElement>(
      '[data-reader-record-node="source-block"]',
    );
    expect(source?.textContent).toContain(SOURCE_TEXT);
    expect(screen.getByTestId("reader-record-plate-surface")).toBeTruthy();
  });

  it("renders unit translation as a unit-level block outside the first segment", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const translation = container.querySelector<HTMLElement>(
      '[data-reader-record-node="unit-translation"]',
    );
    const segment = container.querySelector<HTMLElement>(
      '[data-reader-record-node="anchor-segment"]',
    );

    expect(translation).not.toBeNull();
    expect(translation?.textContent).toContain(TRANSLATION_TEXT);
    expect(translation?.dataset.readerRecordTranslationDisplay).toBe(
      "supporting-paragraph",
    );
    expect(segment).not.toBeNull();
    expect(segment?.textContent).toContain(SOURCE_TEXT);
    expect(segment?.textContent).not.toContain(TRANSLATION_TEXT);
  });

  it("renders vocab and grammar marks with locatable data attributes", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const vocab = container.querySelector<HTMLElement>(
      '[data-reader-record-mark-id="vocab_mark_1"]',
    );
    const grammar = container.querySelector<HTMLElement>(
      '[data-reader-record-mark-id="grammar_mark_1"]',
    );
    const grammarCue = container.querySelector<HTMLElement>(
      '[data-reader-record-cue-id="grammar_note:grammar_item_1"]',
    );
    const sentenceCue = container.querySelector<HTMLElement>(
      '[data-reader-record-cue-id="sentence_analysis:analysis_1"]',
    );
    const cueGroup = container.querySelector<HTMLElement>(
      '[data-reader-record-cues="inline"]',
    );

    expect(vocab?.dataset.readerRecordMarkKind).toBe("phrase_gloss");
    expect(grammar?.dataset.readerRecordMarkKind).toBe("grammar_note");
    expect(cueGroup?.dataset.readerRecordCueDisplay).toBe("marker");
    expect(grammarCue?.dataset.readerRecordCueType).toBe("reader_record_grammar_cue");
    expect(grammarCue?.textContent).toBe("G");
    expect(grammarCue?.tagName).toBe("BUTTON");
    expect(grammarCue?.getAttribute("aria-controls")).toBe(
      "reader-record-plate-active-anchor-inspector",
    );
    expect(grammarCue?.getAttribute("aria-expanded")).toBe("false");
    expect(sentenceCue?.dataset.readerRecordCueType).toBe(
      "reader_record_sentence_analysis_cue",
    );
    expect(sentenceCue?.textContent).toBe("S");
    expect(sentenceCue?.tagName).toBe("BUTTON");
    expect(sentenceCue?.getAttribute("aria-controls")).toBe(
      "reader-record-plate-active-anchor-inspector",
    );
  });

  it("clicking a vocabulary mark shows vocabulary details in the active anchor inspector", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const vocab = container.querySelector<HTMLElement>(
      '[data-reader-record-mark-id="vocab_mark_1"]',
    );
    expect(vocab).not.toBeNull();
    if (!vocab) {
      throw new Error("Expected vocabulary mark");
    }

    fireEvent.click(vocab);

    const inspector = screen.getByTestId("reader-record-active-anchor-inspector");
    expect(inspector.dataset.readerRecordActiveSource).toBe("mark");
    expect(inspector.dataset.readerRecordActiveAnchorSegmentId).toBe("seg_1");
    expect(inspector.dataset.readerRecordActiveSelectedText).toBe("memory");
    expect(inspector.textContent).toContain("Vocabulary");
    expect(inspector.textContent).toContain("memory");
    expect(inspector.textContent).toContain("记忆");
    expect(inspector.textContent).toContain(
      "Institutional memory shapes choices.",
    );
  });

  it("clears the active anchor inspector when a refreshed snapshot replaces the document", async () => {
    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot()} />,
    );
    const vocab = container.querySelector<HTMLElement>(
      '[data-reader-record-mark-id="vocab_mark_1"]',
    );
    expect(vocab).not.toBeNull();
    if (!vocab) {
      throw new Error("Expected vocabulary mark");
    }

    fireEvent.click(vocab);
    expect(screen.getByTestId("reader-record-active-anchor-inspector")).toBeTruthy();

    rerender(
      <ReaderRecordPlateSurface
        snapshot={{
          ...makeSnapshot(),
          snapshot_id: "snapshot_2",
          last_event_sequence: 9,
        }}
      />,
    );

    await waitFor(() => {
      expect(screen.queryByTestId("reader-record-active-anchor-inspector")).toBeNull();
    });
  });

  it("renders overlapping marks as one focusable mark stack", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeOverlappingMarkSnapshot()} />,
    );

    const vocabStack = container.querySelector<HTMLElement>(
      '[data-reader-record-mark-ids~="vocab_mark_1"]',
    );
    const grammarStack = container.querySelector<HTMLElement>(
      '[data-reader-record-mark-ids~="grammar_mark_1"]',
    );
    expect(vocabStack).not.toBeNull();
    expect(grammarStack).not.toBeNull();
    expect(vocabStack).toBe(grammarStack);
    expect(vocabStack?.dataset.readerRecordMarkEntry).toBe("stack");
    expect(vocabStack?.dataset.readerRecordMarkStackSize).toBe("2");
    expect(vocabStack?.querySelector('[role="button"]')).toBeNull();
    expect(
      container.querySelectorAll('[data-reader-record-mark-entry="stack"]'),
    ).toHaveLength(1);

    if (!vocabStack) {
      throw new Error("Expected overlapping mark stack");
    }
    fireEvent.click(vocabStack);

    const inspector = screen.getByTestId("reader-record-active-anchor-inspector");
    expect(inspector.dataset.readerRecordActiveSource).toBe("mark");
    expect(
      inspector
        .querySelector('[data-reader-record-active-mark-stack-size]')
        ?.getAttribute("data-reader-record-active-mark-stack-size"),
    ).toBe("2");
    expect(inspector.textContent).toContain("overlapping annotations");
    expect(inspector.textContent).toContain("Vocabulary");
    expect(inspector.textContent).toContain("Grammar");
    expect(inspector.textContent).toContain("nominal object");
  });

  it("activates grammar details from both grammar mark and G marker", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const grammar = container.querySelector<HTMLElement>(
      '[data-reader-record-mark-id="grammar_mark_1"]',
    );
    expect(grammar).not.toBeNull();
    if (!grammar) {
      throw new Error("Expected grammar mark");
    }

    fireEvent.click(grammar);

    let inspector = screen.getByTestId("reader-record-active-anchor-inspector");
    expect(inspector.dataset.readerRecordActiveSource).toBe("mark");
    expect(inspector.textContent).toContain("Grammar");
    expect(inspector.textContent).toContain("predicate verb");
    expect(inspector.textContent).toContain("subject + verb");
    expect(inspector.textContent).toContain("shapes is the predicate verb.");

    fireEvent.click(
      screen.getByRole("button", { name: "Close active anchor details" }),
    );
    expect(screen.queryByTestId("reader-record-active-anchor-inspector")).toBeNull();

    const grammarCue = container.querySelector<HTMLButtonElement>(
      '[data-reader-record-cue-id="grammar_note:grammar_item_1"]',
    );
    expect(grammarCue).not.toBeNull();
    if (!grammarCue) {
      throw new Error("Expected grammar cue");
    }
    fireEvent.click(grammarCue);

    inspector = screen.getByTestId("reader-record-active-anchor-inspector");
    expect(inspector.dataset.readerRecordActiveSource).toBe("cue");
    expect(inspector.textContent).toContain("predicate verb");
    expect(
      container.querySelector<HTMLButtonElement>(
        '[data-reader-record-cue-id="grammar_note:grammar_item_1"]',
      )?.getAttribute("aria-expanded"),
    ).toBe("true");
  });

  it("clicking the S marker shows sentence-analysis details", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const sentenceCue = container.querySelector<HTMLButtonElement>(
      '[data-reader-record-cue-id="sentence_analysis:analysis_1"]',
    );
    expect(sentenceCue).not.toBeNull();
    if (!sentenceCue) {
      throw new Error("Expected sentence analysis cue");
    }

    fireEvent.click(sentenceCue);

    const inspector = screen.getByTestId("reader-record-active-anchor-inspector");
    expect(inspector.dataset.readerRecordActiveSource).toBe("cue");
    expect(inspector.dataset.readerRecordActiveAnchorSegmentId).toBe("seg_1");
    expect(inspector.textContent).toContain("Sentence Structure");
    expect(inspector.textContent).toContain("subject and predicate");
    expect(inspector.textContent).toContain(
      "Institutional memory is the subject.",
    );
    expect(inspector.textContent).toContain("subject");
    expect(inspector.textContent).toContain("Institutional memory");
  });

  it("focusing a cue marker opens the active anchor inspector", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const grammarCue = container.querySelector<HTMLButtonElement>(
      '[data-reader-record-cue-id="grammar_note:grammar_item_1"]',
    );
    expect(grammarCue).not.toBeNull();
    if (!grammarCue) {
      throw new Error("Expected grammar cue");
    }

    fireEvent.focus(grammarCue);

    const inspector = screen.getByTestId("reader-record-active-anchor-inspector");
    expect(inspector.dataset.readerRecordActiveSource).toBe("cue");
    expect(inspector.textContent).toContain("predicate verb");
  });

  it("renders user highlight marks with stable asset attributes", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot([makeUserAsset()])} />,
    );

    const highlight = container.querySelector<HTMLElement>(
      '[data-reader-record-user-asset-id="asset_highlight_1"]',
    );

    expect(highlight?.dataset.readerRecordMarkEntry).toBe("stack");
    expect(highlight?.dataset.readerRecordMarkKinds).toContain("user_highlight");
    expect(highlight?.dataset.readerRecordMarkOwner).toBe("mixed");
    expect(highlight?.dataset.readerRecordUserAssetIds).toBe("asset_highlight_1");
    expect(highlight?.dataset.selectedText).toBe("memory");
    expect(highlight?.textContent).toBe("memory");
  });

  it("clicking a user highlight mark shows user-owned anchor details", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot([makeUserAsset()])} />,
    );

    const highlight = container.querySelector<HTMLElement>(
      '[data-reader-record-user-asset-id="asset_highlight_1"]',
    );
    expect(highlight).not.toBeNull();
    if (!highlight) {
      throw new Error("Expected user highlight");
    }

    fireEvent.click(highlight);

    const inspector = screen.getByTestId("reader-record-active-anchor-inspector");
    expect(inspector.dataset.readerRecordActiveSource).toBe("mark");
    expect(inspector.textContent).toContain("用户高亮");
    expect(inspector.textContent).toContain("Asset asset_highlight_1");
  });

  it("renders note/comment indicators with stable asset attributes", () => {
    const { container } = render(
      <ReaderRecordPlateSurface
        snapshot={makeSnapshot([
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
            note_text: "Remember shapes as predicate.",
          }),
        ])}
      />,
    );

    const noteIndicator = container.querySelector<HTMLElement>(
      '[data-reader-record-user-asset-id="asset_note_1"]',
    );

    expect(noteIndicator?.dataset.readerRecordCueType).toBe(
      "reader_record_user_comment_cue",
    );
    expect(noteIndicator?.dataset.anchorSegmentId).toBe("seg_1");
    expect(noteIndicator?.textContent).toContain("笔记");
  });

  it("clicking a note/comment cue shows comment details and closes with Escape", () => {
    const { container } = render(
      <ReaderRecordPlateSurface
        snapshot={makeSnapshot([
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
            note_text: "Remember shapes as predicate.",
          }),
        ])}
      />,
    );

    const noteIndicator = container.querySelector<HTMLButtonElement>(
      '[data-reader-record-user-asset-id="asset_note_1"]',
    );
    expect(noteIndicator).not.toBeNull();
    if (!noteIndicator) {
      throw new Error("Expected note indicator");
    }

    fireEvent.click(noteIndicator);

    const inspector = screen.getByTestId("reader-record-active-anchor-inspector");
    expect(inspector.dataset.readerRecordActiveSource).toBe("cue");
    expect(inspector.textContent).toContain("笔记/评论");
    expect(inspector.textContent).toContain("Asset asset_note_1");
    expect(inspector.textContent).toContain("Remember shapes as predicate.");

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByTestId("reader-record-active-anchor-inspector")).toBeNull();
  });

  it("keeps system marks and user marks coexisting on the source text", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot([makeUserAsset()])} />,
    );

    const source = container.querySelector<HTMLElement>(
      '[data-reader-record-node="source-block"]',
    );
    const vocab = container.querySelector<HTMLElement>(
      '[data-reader-record-mark-id="vocab_mark_1"]',
    );
    const userHighlight = container.querySelector<HTMLElement>(
      '[data-reader-record-user-asset-id="asset_highlight_1"]',
    );

    expect(source?.textContent).toContain(SOURCE_TEXT);
    expect(vocab?.dataset.readerRecordMarkKind).toBe("phrase_gloss");
    expect(userHighlight?.dataset.readerRecordMarkKinds).toContain("user_highlight");
    expect(vocab?.dataset.anchorSegmentId).toBe("seg_1");
    expect(userHighlight?.dataset.anchorSegmentId).toBe("seg_1");
  });

  it("renders compact progress without replacing the document body", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const progress = screen.getByTestId("reader-record-plate-progress");
    const strip = screen.getByTestId("reader-record-plate-progress-strip");
    const source = container.querySelector<HTMLElement>(
      '[data-reader-record-node="source-block"]',
    );

    expect(progress.dataset.readerRecordReadingHeader).toBe("compact");
    expect(progress.textContent).toContain("解析生成中");
    expect(strip).toBeTruthy();
    expect(source?.textContent).toContain(SOURCE_TEXT);
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
    ).toContain("Selected: memory");
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
      container.querySelector('[data-reader-record-action="ask"]'),
    ).toBeNull();
    expect(
      container.querySelector('[data-reader-record-action="feedback"]'),
    ).toBeNull();
    expect(
      container.querySelector(
        '[data-reader-record-coming-soon-actions="ask-feedback"]',
      )?.textContent,
    ).toContain("Ask / Feedback coming soon");
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

    const copyButton = await screen.findByRole<HTMLButtonElement>("button", {
      name: "Copy",
    });
    await waitFor(() => {
      expect(copyButton.disabled).toBe(false);
    });
    fireEvent.click(copyButton);

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith("memory");
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("runs dictionary lookup only for a valid single anchor draft", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(makeDictionaryEntryResult("memory")), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
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
      name: "Lookup",
    });
    await waitFor(() => {
      expect(lookupButton.disabled).toBe(false);
    });
    fireEvent.click(lookupButton);

    await screen.findByTestId("reader-record-plate-lookup-panel");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const lookupUrl = String(fetchMock.mock.calls[0]?.[0]);
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
      name: "Highlight",
    });
    await waitFor(() => {
      expect(highlightButton.disabled).toBe(false);
    });
    fireEvent.click(highlightButton);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/web/reading-record/highlights",
    );
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit | undefined)?.method).toBe(
      "POST",
    );
    const body = postedJsonBody(fetchMock);
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
      name: "Note",
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
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/web/reading-record/notes");
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit | undefined)?.method).toBe(
      "POST",
    );
    const body = postedJsonBody(fetchMock);
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
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not expose executable actions for selections outside stable source text", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const translation = container.querySelector<HTMLElement>(
      '[data-reader-record-node="unit-translation"]',
    );
    expect(translation).not.toBeNull();
    if (!translation) {
      throw new Error("Expected translation block");
    }

    selectTextInElement(translation, 0, 2);

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
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not import legacy Workbench, ReaderVm, scene adapters, or legacy write routes", () => {
    const sources = [
      "src/components/reader/plate/ReaderRecordPlateSurface.tsx",
      "src/lib/reader-plate/projection/reader-record-plate-document.ts",
      "src/lib/reader-plate/projection/reader-record-anchor-draft.ts",
      "src/lib/reader-plate/projection/reader-record-dom-selection.ts",
      "src/services/bff/reading-record-user-assets.ts",
      "src/app/api/web/reading-record/highlights/route.ts",
      "src/app/api/web/reading-record/notes/route.ts",
    ].map((filePath) => readFileSync(resolve(process.cwd(), filePath), "utf8"));

    for (const source of sources) {
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
      expect(source).not.toMatch(/\/api\/web\/reader-ask/);
      expect(source).not.toMatch(/\/api\/web\/reader-notes/);
      expect(source).not.toMatch(/\/api\/web\/reader-annotations/);
      expect(source).not.toMatch(/\/api\/web\/annotations/);
    }
  });
});
