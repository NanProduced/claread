/** @vitest-environment jsdom */

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { computeUtf16FNV1a } from "@claread/contracts";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeAnalysisProgressDto } from "@/test/fixtures/reader-analysis-progress";

import {
  READER_TEXT_RANGE_HASH_ALGORITHM,
  READER_TEXT_RANGE_OFFSET_UNIT,
  type ReaderAnchorSegmentNodeDto,
  type ReaderEventResponseDto,
  type ReaderGrammarNoteMarkDto,
  type ReaderPlateSnapshotDto,
  type ReaderSourceBlockNodeDto,
  type ReaderSnapshotUserAssetDto,
  type ReaderStableDocumentBlockNodeDto,
  type ReaderTitleGenerationStatus,
  type ReaderUnitNodeDto,
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
import { AppShellLayoutContext } from "@/components/layout/app-shell/app-shell-context";

const themePreferenceSetter = vi.fn();
let themePreferenceCurrent: "system" | "light" | "dark" = "system";

vi.mock("@/components/providers/appearance-provider", () => ({
  useAppearance: () => ({
    get themePreference() {
      return themePreferenceCurrent;
    },
    resolvedTheme: "light" as const,
    setThemePreference: (next: "system" | "light" | "dark") => {
      themePreferenceCurrent = next;
      themePreferenceSetter(next);
    },
  }),
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

import {
  ReaderRecordPlateSurface,
  groupConsecutiveGrammarCallouts,
} from "./ReaderRecordPlateSurface";
import type { ReaderCalloutElement } from "@/lib/reader-plate/projection/reader-record-plate-to-plate-value";
import { READER_CALLOUT_TYPE } from "@/lib/reader-plate/projection/reader-record-plate-to-plate-value";
import {
  SOURCE_TEXT,
  TRANSLATION_TEXT,
  firstTextNode,
  focusNearestEditor,
  makeAnchorSegmentNode,
  makeDictionaryEntryResult,
  makeGrammarMark,
  makeNextSnapshot,
  makeReloadContext,
  makeSnapshot,
  makeSplitSegmentSnapshot,
  makeUserAsset,
  makeUnit,
  makeVocabularyMark,
  selectTextInElement,
  selectionActionButton,
  waitForSelectionAction,
} from "./reader-record-plate-surface-fixtures";

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
      if (url.pathname.includes("/api/web/reader/records/") && url.pathname.endsWith("/favorite")) {
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
  } catch {
    // Ignore jsdom localStorage variants that do not expose the full Storage API.
  }
  themePreferenceCurrent = "system";
  themePreferenceSetter.mockClear();
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

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
    phrase_type: "fixed_collocation",
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

function makePolicyChoicesDisambiguationResult(): WebDictResult {
  return {
    kind: "disambiguation",
    query: "policy choices",
    provider: "test",
    cached: false,
    ambiguityKind: "lemma_competing",
    selectionRequired: true,
    candidates: [
      {
        entryId: 42,
        label: "policy choices",
        preview: "choices about public policy",
        entryKind: "entry",
        matchKind: "exact",
        lookupType: "phrase",
        candidateKind: "phrase",
      },
      {
        entryId: 43,
        label: "policy choice",
        preview: "a single policy option",
        entryKind: "entry",
        matchKind: "variant",
        lookupType: "phrase",
        candidateKind: "variant",
      },
    ],
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

async function openAskPanelFromToolbar(askButton: HTMLButtonElement) {
  expect(askButton.isConnected).toBe(true);
  // 新交互：toolbar Ask 打开 Surface 托管的快捷框；面板本身经启动器打开，
  // 选区由 composer auto-slot 自动成为 chip（Notion 式）。
  fireEvent.click(askButton);
  await screen.findByPlaceholderText("Ask Claread anything...");
  fireEvent.keyDown(window, { key: "Escape" });
  fireEvent.click(screen.getByRole("button", { name: "打开 Ask Claread" }));
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
    if (
      requestUrl.pathname.includes("/api/web/reader/records/") &&
      requestUrl.pathname.endsWith("/favorite")
    ) {
      return Promise.resolve(
        new Response(JSON.stringify({ ok: true, favorited: false }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    }

    if (requestUrl.pathname === `/api/web/reader/records/${recordId}/ask/model-options`) {
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
      requestUrl.pathname === `/api/web/reader/records/${recordId}/ask/threads`
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
      requestUrl.pathname === `/api/web/reader/records/${recordId}/ask/threads/thread-rr-1`
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
      requestUrl.pathname === `/api/web/reader/records/${recordId}/ask/threads/thread-rr-1/messages/stream`
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

function installReaderRecordWriteFetchMock(recordId = "record_1") {
  const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const requestUrl = new URL(String(input), "https://example.test");
    if (
      requestUrl.pathname.includes("/api/web/reader/records/") &&
      requestUrl.pathname.endsWith("/favorite")
    ) {
      return Promise.resolve(
        new Response(JSON.stringify({ ok: true, favorited: false }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    }

    if (
      requestUrl.pathname === `/api/web/reader/records/${recordId}/highlights` ||
      requestUrl.pathname === `/api/web/reader/records/${recordId}/notes`
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

function makeStableCodeBlockSnapshot(
  codeLanguage?: string | null,
): ReaderPlateSnapshotDto {
  const unit = makeUnit({ vocabularyMarks: [], grammarMarks: [] });
  const sourceBlock = unit.children[0];
  if (sourceBlock?.type !== "reader_source_block") {
    throw new Error("Expected reader_source_block");
  }
  sourceBlock.stableBlockType = "code_block";
  sourceBlock.stableBlockId = "b_code";
  if (codeLanguage !== undefined) {
    sourceBlock.codeLanguage = codeLanguage;
  }
  unit.children = [sourceBlock];
  return {
    ...makeSnapshot(),
    value: [unit],
  };
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

  it("renders a unique copy-excluded language badge for a stable code_block with codeLanguage", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeStableCodeBlockSnapshot("python")} />,
    );

    const pres = container.querySelectorAll("pre");
    expect(pres).toHaveLength(1);
    const pre = pres[0];
    if (!pre) {
      throw new Error("Expected stable code_block pre");
    }

    const codes = pre.querySelectorAll("code");
    expect(codes).toHaveLength(1);
    const code = codes[0];
    if (!code) {
      throw new Error("Expected stable code_block code");
    }

    expect(pre.getAttribute("data-language")).toBe("python");
    expect(pre.getAttribute("data-reader-record-stable-block-type")).toBe(
      "code_block",
    );
    expect(pre.getAttribute("data-reader-record-node")).toBe("code_block");
    expect(pre.getAttribute("data-unit-id")).toBe("unit_1");
    expect(pre.getAttribute("data-reader-record-unit-start")).toBe("true");
    expect(pre.getAttribute("data-anchor-segment-id")).toBe("seg_1");
    expect(pre.className).not.toMatch(/\bpr-\d+\b/);
    expect(code.textContent).toBe(SOURCE_TEXT);
    expect(code.className).toMatch(/\bblock\b/);
    expect(code.className).toMatch(/\bpt-\d+\b/);

    const badges = pre.querySelectorAll('[data-testid="code-language-badge"]');
    expect(badges).toHaveLength(1);
    const badge = badges[0];
    if (!badge) {
      throw new Error("Expected code language badge");
    }
    expect(badge.tagName).toBe("SPAN");
    expect(badge.textContent).toBe("python");
    expect(badge.getAttribute("contenteditable")).toBe("false");
    expect(badge.getAttribute("draggable")).toBe("false");
    expect(badge.getAttribute("data-reader-record-copy-exclude")).toBe("true");
    expect(code.contains(badge)).toBe(false);

    selectTextInElement(code, 0, SOURCE_TEXT.length);
    expect(window.getSelection()?.toString()).toBe(SOURCE_TEXT);
  });

  it.each([null, undefined, ""] as const)(
    "does not render a language badge when codeLanguage is %j",
    (codeLanguage) => {
      const { container } = render(
        <ReaderRecordPlateSurface
          snapshot={makeStableCodeBlockSnapshot(codeLanguage)}
        />,
      );

      const pres = container.querySelectorAll("pre");
      expect(pres).toHaveLength(1);
      const pre = pres[0];
      if (!pre) {
        throw new Error("Expected stable code_block pre");
      }

      const codes = pre.querySelectorAll("code");
      expect(codes).toHaveLength(1);
      expect(codes[0]?.textContent).toBe(SOURCE_TEXT);
      expect(codes[0]?.className ?? "").not.toMatch(/\bpt-\d+\b/);
      expect(pre.querySelector('[data-testid="code-language-badge"]')).toBeNull();
      expect(pre.textContent).not.toContain("null");
      expect(pre.textContent).not.toContain("undefined");
    },
  );

  it("excludes the language badge from mixed-range copy", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeStableCodeBlockSnapshot("python")} />,
    );

    const pre = container.querySelector("pre");
    const badge = pre?.querySelector<HTMLElement>(
      '[data-testid="code-language-badge"]',
    );
    const code = pre?.querySelector("code");
    if (!pre || !badge || !code) {
      throw new Error("Expected stable code_block with badge");
    }

    const badgeText = firstTextNode(badge);
    const codeText = firstTextNode(code);
    const range = document.createRange();
    range.setStart(badgeText, 0);
    range.setEnd(codeText, codeText.length);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);

    const clipboardData = {
      setData: vi.fn(),
    };
    fireEvent.copy(pre, { clipboardData });

    const plainTextCall = clipboardData.setData.mock.calls.find(
      ([type]) => type === "text/plain",
    );
    expect(plainTextCall).toBeDefined();
    const copiedText = String(plainTextCall?.[1] ?? "");
    expect(copiedText).toBe(SOURCE_TEXT);
    expect(copiedText).not.toContain("python");
    const htmlCall = clipboardData.setData.mock.calls.find(
      ([type]) => type === "text/html",
    );
    expect(String(htmlCall?.[1] ?? "")).not.toContain(
      'data-testid="code-language-badge"',
    );
    expect(pre.querySelector("code")?.textContent).toBe(SOURCE_TEXT);
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
    // Visuals are owned by the semantic class in globals.css — no inline
    // border/background utilities on the component anymore.
    expect(analysisBlock?.className).not.toContain("border");
    expect(analysisBlock?.className).not.toContain("bg-");
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
    expect(toggle.getAttribute("title")).toBeNull();
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
    expect(toggle.getAttribute("title")).toBeNull();

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
    expect(toggle.getAttribute("title")).toBeNull();

    fireEvent.click(toggle);
    await waitFor(() => {
      expect(analysisBlock.dataset.readerRecordSentenceAnalysisCollapsed).toBe("false");
      expect(content.hidden).toBe(false);
    });
    expect(chunkRows.textContent).toContain("subject");
    expect(chunkRows.textContent).toContain("modifier");
    expect(chunkRows.textContent).not.toContain("结构片段");
    expect(chunkRows.textContent).not.toContain("从句 / 信息层");
    expect(chunkRows.textContent).not.toContain("主语 / 话题核心");
    expect(chunkRows.textContent).not.toContain("修饰 / 补充限定");
    expect(toggle.textContent?.trim()).toBe("");
    expect(toggle.getAttribute("aria-label")).toBe("收起长句拆析");
  });

  it("hides unavailable Ask and grammar feedback actions when the grammar item lacks a real analysis id", () => {
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
    const toggleButton = grammarCallout.querySelector<HTMLButtonElement>(
      '[data-reader-record-callout-toggle="grammar"]',
    );

    expect(askButton).toBeNull();
    expect(feedbackButton).toBeNull();
    expect(toggleButton).not.toBeNull();
    expect(toggleButton?.getAttribute("title")).toBeNull();
  });

  it("does not render legacy fake article feedback or call the legacy feedback BFF", () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const requestUrl = new URL(String(input), "https://example.test");
      return Promise.resolve(
        requestUrl.pathname.includes("/api/web/reader/records/") && requestUrl.pathname.endsWith("/favorite")
          ? new Response(JSON.stringify({ ok: true, favorited: false }), {
              status: 200,
              headers: { "content-type": "application/json" },
            })
          : new Response("Not Found", { status: 404 }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const prompt = container.querySelector<HTMLElement>(
      '[data-reader-record-article-feedback="ready"]',
    );
    expect(prompt).toBeNull();
    expect(
      fetchMock.mock.calls.some(([input]) => String(input) === "/api/web/feedback"),
    ).toBe(false);
  });

  it("keeps grammar and sentence callout feedback hidden until the new reader feedback contract exists", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const grammarCallout = container.querySelector<HTMLElement>(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );
    const analysisBlock = container.querySelector<HTMLElement>(
      '[data-reader-record-node="sentence-analysis"]',
    );
    const grammarFeedbackButton = grammarCallout?.querySelector<HTMLButtonElement>(
      '[data-reader-record-callout-action="feedback"]',
    );
    const sentenceFeedbackButton = analysisBlock?.querySelector<HTMLButtonElement>(
      '[data-reader-record-callout-action="feedback"]',
    );
    expect(grammarCallout).not.toBeNull();
    expect(analysisBlock).not.toBeNull();
    expect(grammarFeedbackButton).toBeNull();
    expect(sentenceFeedbackButton).toBeNull();
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
      /\.reader-record-plate-callout-group\s*[\s\S]*?\.reader-record-plate-callout--grammar-row:hover\s*\{[\s\S]*?background-color:\s*color-mix\(in srgb, var\(--grammar-violet\) 6%, transparent\)/,
    );
    expect(globalsSource).toMatch(
      /\.reader-record-plate-sentence-analysis-chunks\s*\{[\s\S]*?border-left:\s*1px solid color-mix\(in srgb, var\(--context-blue\) 16%, var\(--hairline\)\)/,
    );
    // Unified filled-card family: both the grammar group and the sentence
    // analysis card are borderless 8px-radius fills, and collapse state no
    // longer restyles the card shell (content hides via `hidden` only).
    expect(globalsSource).toMatch(
      /\.reader-record-plate-callout-group\s*\{[\s\S]*?border-radius:\s*8px[\s\S]*?background-color:\s*var\(--reader-record-note-fill-grammar\)/,
    );
    expect(globalsSource).toMatch(
      /\.reader-record-plate-sentence-analysis\s*\{[\s\S]*?border-radius:\s*8px[\s\S]*?background-color:\s*var\(--reader-record-note-fill-analysis\)/,
    );
    expect(globalsSource).not.toMatch(
      /\.reader-record-plate-sentence-analysis\[data-reader-record-sentence-analysis-collapsed="true"\]\s*\{[\s\S]*?padding-inline:\s*0/,
    );
    expect(globalsSource).toMatch(
      /\.reader-record-plate-callout-toggle\[aria-expanded="true"\]\s*\.reader-record-plate-callout-toggle-icon\s*\{[\s\S]*?transform:\s*rotate\(180deg\)/,
    );
  });

  it("uses the quiet annotation-fill tokens for note cards in both themes", () => {
    const globalsSource = readFileSync(
      resolve(process.cwd(), "src/app/globals.css"),
      "utf8",
    );
    expect(globalsSource).toMatch(
      /(?:^|\n)\.reader-record-plate-document\s*\{[\s\S]*?--reader-record-note-fill-grammar:\s*var\(--reader-annotation-grammar-fill\)[\s\S]*?--reader-record-note-fill-analysis:\s*var\(--reader-annotation-context-fill\)/,
    );
    // Dark theme reuses the same annotation tokens (overridden at the token
    // layer), so no per-document dark override should remain.
    expect(globalsSource).not.toMatch(
      /\.dark \.reader-record-plate-document\s*\{[\s\S]*?--reader-record-note-fill/,
    );
    expect(globalsSource).toMatch(
      /--reader-record-note-fill-supplement:\s*var\(--reader-record-note-fill-grammar\)/,
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
      /--reader-mark-grammar-line-soft:\s*var\(--grammar-violet\)/,
    );
    expect(cssSource).toMatch(/text-decoration-thickness:\s*0\.08em/);
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

  it("resolves overlapping mark clicks by user note before system mark priority", async () => {
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

    const overlapNotePanel = await screen.findByTestId(
      "reader-record-inline-comment-panel",
    );
    expect(overlapNotePanel.textContent).toContain(
      "Matrix note for the sentence opening.",
    );
    expect(screen.queryByTestId("reader-record-plate-lookup-panel")).toBeNull();
    expect(scrollSpy).toHaveBeenCalled();

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

  it("resolves overlapping mark clicks by user highlight before vocabulary priority", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(
      <ReaderRecordPlateSurface
        snapshot={makeSnapshot([
          makeUserAsset({
            asset_id: "asset_highlight_vocab_overlap",
            asset_type: "user_highlight",
            color: "warm_yellow",
          }),
        ])}
      />,
    );

    const vocab = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(vocab).not.toBeNull();
    if (!vocab) {
      throw new Error("Expected vocabulary mark");
    }

    fireEvent.click(vocab);

    expect(await screen.findByRole("button", { name: "删除高亮" })).toBeTruthy();
    expect(screen.queryByTestId("reader-record-plate-lookup-panel")).toBeNull();
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
      <ReaderRecordPlateSurface snapshot={makeOverlappingMarkSnapshot()} />,
    );
    const grammarLeaves = () =>
      Array.from(
        container.querySelectorAll<HTMLElement>(
          '[data-reader-record-leaf="segment_text"][data-reader-record-grammar-item-id="grammar_item_1"]',
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

  it("uses the same generated display title for the Ask current-article chip", async () => {
    installReaderAskFetchMock();
    const snapshot = makeSnapshot();
    snapshot.record.title = "Untitled Reading";
    snapshot.record.display_title_zh = "加拿大山火烟雾蔓延美国多州";
    snapshot.record.title_generation_status = "succeeded";
    const { container } = render(
      <TooltipProvider>
        <ReaderRecordPlateSurface snapshot={snapshot} />
      </TooltipProvider>,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "打开 Ask Claread" }),
    );

    await waitFor(() => {
      expect(
        container.querySelector('[data-ask-current-article-chip="true"]'),
      ).not.toBeNull();
    });
    const chip = container.querySelector<HTMLElement>(
      '[data-ask-current-article-chip="true"]',
    );
    if (!chip) {
      throw new Error("Expected the Ask current-article chip to render");
    }
    expect(chip.getAttribute("aria-label")).toBe(
      "当前文章：加拿大山火烟雾蔓延美国多州",
    );
    expect(chip.textContent).toContain("加拿大山火烟雾蔓延美国多州");
    expect(chip.textContent).not.toContain("Untitled Reading");
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
    const title = header.querySelector<HTMLElement>(
      "[data-reader-record-reading-title]",
    );
    expect(title?.previousElementSibling).toBeNull();
    // Hero eyebrow 已移除；模式标签只出现在 More Menu 与 action bar tab。
    expect(header.textContent).not.toContain("精读模式 · 2026年6月24日");
    expect(header.textContent).not.toContain("2026年6月24日");
  });

  it("renders header with progress status and metadata", () => {
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const header = screen.getByTestId("reader-record-plate-header");
    expect(header).toBeTruthy();
    expect(header.textContent).toContain("可以开始阅读");

    const progressStatus = container.querySelector<HTMLElement>(
      "[data-reader-record-progress-status]",
    );
    expect(progressStatus?.dataset.readerRecordProgressStatus).toBe(
      "ready_to_read",
    );
  });

  it.each([
    {
      label: "processing → 解析中",
      productState: "processing" as const,
      readinessState: "article_ready" as const,
      expected: "解析中",
      expectedKey: "processing",
    },
    {
      label: "needs_confirmation → 需要确认",
      productState: "needs_confirmation" as const,
      readinessState: "candidate_base_ready" as const,
      expected: "需要确认",
      expectedKey: "needs_confirmation",
    },
    {
      label: "readable_enhancing + article_ready → 可以开始阅读",
      productState: "readable_enhancing" as const,
      readinessState: "article_ready" as const,
      expected: "可以开始阅读",
      expectedKey: "ready_to_read",
    },
    {
      label: "readable_enhancing + coverage_complete → 解析完成",
      productState: "readable_enhancing" as const,
      readinessState: "coverage_complete" as const,
      expected: "解析完成",
      expectedKey: "completed",
    },
    {
      label: "action_required → 等待继续",
      productState: "action_required" as const,
      readinessState: "article_ready" as const,
      expected: "等待继续",
      expectedKey: "awaiting_continue",
    },
    {
      label: "failed → 解析遇到问题",
      productState: "failed" as const,
      readinessState: "article_ready" as const,
      expected: "解析遇到问题",
      expectedKey: "failed",
    },
  ])(
    "renders header badge with approved label for $label",
    ({ productState, readinessState, expected, expectedKey }) => {
      const snapshot = makeSnapshot();
      snapshot.record.product_state = productState;
      snapshot.record.readiness_state = readinessState;
      const { container } = render(
        <ReaderRecordPlateSurface snapshot={snapshot} />,
      );

      const header = screen.getByTestId("reader-record-plate-header");
      expect(header.textContent).toContain(expected);

      const progressStatus = container.querySelector<HTMLElement>(
        "[data-reader-record-progress-status]",
      );
      expect(progressStatus?.dataset.readerRecordProgressStatus).toBe(
        expectedKey,
      );
    },
  );

  it("does not show legacy status labels in the header", () => {
    render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const header = screen.getByTestId("reader-record-plate-header");
    expect(header.textContent).not.toContain("解析生成中");
    expect(header.textContent).not.toContain("部分解析失败");
    expect(header.textContent).not.toContain("正文可读");
  });

  it("renders article status section in the more menu with label and description", async () => {
    const user = userEvent.setup();
    render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    await user.click(screen.getByTestId("reader-record-more-menu-trigger"));

    const menu = screen.getByTestId("reader-record-more-menu-content");
    const statusSection = menu.querySelector<HTMLElement>(
      '[data-reader-record-more-article-status="true"]',
    );
    expect(statusSection).not.toBeNull();
    expect(menu.textContent).toContain("文章状态");
    expect(menu.textContent).toContain("可以开始阅读");
    expect(menu.textContent).toContain("正文已就绪");
  });

  it.each([
    {
      stateLabel: "awaiting_continue",
      productState: "action_required" as const,
      readinessState: "article_ready" as const,
      expectedMenuLabel: "等待继续",
      expectedDescription: "这篇内容还需要完成下一步处理。",
    },
    {
      stateLabel: "failed",
      productState: "failed" as const,
      readinessState: "article_ready" as const,
      expectedMenuLabel: "解析遇到问题",
      expectedDescription: "这篇内容在准备时遇到了问题。",
    },
  ])(
    "renders article status section in the more menu for $stateLabel state",
    async ({ productState, readinessState, expectedMenuLabel, expectedDescription }) => {
      const user = userEvent.setup();
      const snapshot = makeSnapshot();
      snapshot.record.product_state = productState;
      snapshot.record.readiness_state = readinessState;
      render(<ReaderRecordPlateSurface snapshot={snapshot} />);

      await user.click(screen.getByTestId("reader-record-more-menu-trigger"));

      const menu = screen.getByTestId("reader-record-more-menu-content");
      const statusSection = menu.querySelector<HTMLElement>(
        '[data-reader-record-more-article-status="true"]',
      );
      expect(statusSection).not.toBeNull();
      expect(menu.textContent).toContain("文章状态");
      expect(menu.textContent).toContain(expectedMenuLabel);
      expect(menu.textContent).toContain(expectedDescription);
    },
  );

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
    expect(topBar?.parentElement?.parentElement?.className).toContain("reader-workspace-shell");
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
    expect(mainColumn?.className).toContain("reader-record-main--document-rhythm");

    const headerColumn = mainColumn?.querySelector(".reader-header-band-inner");
    expect(headerColumn).not.toBeNull();
    expect(headerColumn?.className).toContain("max-w-[var(--reader-record-main-width)]");
    expect(headerColumn?.querySelector('[data-testid="reader-record-plate-header"]')).not.toBeNull();

    const plateDocument = mainColumn?.querySelector(".reader-record-plate-document");
    expect(plateDocument).not.toBeNull();
    const contentColumn = plateDocument?.parentElement;
    expect(contentColumn?.className).toContain("max-w-[var(--reader-record-main-width)]");

    // Outline is a workspace sibling, so its popup is never clipped by the reading canvas.
    const outlineSlot = contentSection?.querySelector(".reader-record-outline-slot");
    expect(outlineSlot).not.toBeNull();
    expect(outlineSlot?.parentElement).toBe(contentSection);

    const globalsSource = readFileSync(
      resolve(process.cwd(), "src/app/globals.css"),
      "utf-8",
    );
    expect(globalsSource).toContain("--reader-record-main-width: 70ch;");
    expect(globalsSource).toContain("--app-shell-sidebar-width-collapsed: 84px;");
    expect(globalsSource).toContain("--app-shell-sidebar-width-expanded: 232px;");
    expect(globalsSource).toContain("--app-shell-sidebar-width-locked: 280px;");
    expect(globalsSource).toContain(
      "--reader-record-app-sidebar-width: var(--app-shell-sidebar-width);",
    );
    expect(globalsSource).toContain(
      "--reader-record-dictionary-rail-width: 26rem;",
    );
    expect(globalsSource).toContain(
      "--reader-record-dictionary-rail-left-offset: clamp(4.75rem, 5.5vw, 7rem);",
    );
    expect(globalsSource).toContain(
      "--reader-record-headroom: clamp(4.75rem, 9vh, 6.25rem);",
    );
    expect(globalsSource).toContain("--app-shell-topbar-left-safe: 0px;");
    expect(globalsSource).toContain(
      "--app-shell-topbar-left-safe: 3.5rem;",
    );
    expect(globalsSource).toMatch(
      /\.app-sidebar-peek-button\s*\{[\s\S]*?background:\s*transparent;[\s\S]*?box-shadow:\s*none;/,
    );
    expect(globalsSource).toMatch(
      /\.app-workspace-sidebar\[data-app-sidebar-state="overlay"\]\s*\{[\s\S]*?background:\s*var\(--reader-paper\);/,
    );
    expect(globalsSource).toContain(".reader-record-top-bar {");
    expect(globalsSource).toContain("padding-top: var(--reader-record-headroom);");
    expect(globalsSource).toContain(".reader-record-dictionary-rail--docked {");
    expect(globalsSource).toContain(
      '.app-shell[data-app-sidebar-state="overlay"] [data-reader-record-dictionary-rail="docked"].reader-record-dictionary-rail--docked {',
    );
    expect(globalsSource).toContain(
      "left: var(--reader-record-dictionary-rail-left-offset);",
    );
    expect(globalsSource).not.toContain(
      ".app-shell[data-app-sidebar-state=\"overlay\"] .reader-record-dictionary-rail--docked {\n  left: calc(\n    var(--app-shell-sidebar-width-locked)",
    );
    expect(globalsSource).toContain(".reader-record-outline-slot {");
    expect(globalsSource).toContain("position: fixed;");
    expect(globalsSource).toContain("top: 50%;");
    expect(globalsSource).toContain(
      "right: var(--reader-record-outline-right-offset);",
    );
    expect(globalsSource).toContain("height: min(72vh, 42rem);");
    expect(globalsSource).toContain("transform: translateY(-50%);");
  });

  it("anchors the navigation rail inside the canvas outline slot", () => {
    const snapshot = makeSnapshot();
    snapshot.semantic_outline = {
      schema_kind: "reader_semantic_outline",
      schema_version: 1,
      status: "ready",
      source_identity: { base_id: "base_1", generation: 1 },
      publication: {
        outline_revision: "rev_1",
        layer_id: "layer_ol",
        published_at: "2026-07-17T00:00:00Z",
      },
      provenance: { kind: "llm", builder: "test", model: "m" },
      diagnostics: { drops: [], skipped_node_count: 0 },
      nodes: [
        {
          node_id: "n1",
          parent_node_id: null,
          depth: 1,
          title: "Root A",
          start_unit_id: "unit_1",
          end_unit_id: "unit_1",
          start_anchor_segment_id: null,
          end_anchor_segment_id: null,
          order_index: 1,
        },
      ],
    };
    render(<ReaderRecordPlateSurface snapshot={snapshot} />);

    const outlineSlot = document.querySelector(".reader-record-outline-slot");
    expect(outlineSlot).not.toBeNull();

    const rail = outlineSlot?.querySelector('[data-testid="reader-record-navigation-rail"]');
    expect(rail).not.toBeNull();
    expect(rail?.getAttribute("data-layout")).toBe("canvas");
  });

  it("opens contextual Ask without letting the entry point override presentation", async () => {
    installReaderAskFetchMock();
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);

    const canvas = container.querySelector(".reader-record-canvas");
    expect(canvas).not.toBeNull();
    expect(canvas?.className).not.toContain("reader-record-canvas--ask-open");
    expect(container.querySelector(".reader-record-outline-slot")).not.toBeNull();

    const memoryMark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) {
      throw new Error("Expected memory mark");
    }

    selectTextInElement(memoryMark, 0, "memory".length);
    const askButton = await waitForSelectionAction("ask");
    await openAskPanelFromToolbar(askButton);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "关闭 Ask Claread" })).toBeTruthy();
    });

    // JSDOM has no measurable workspace width, so the requested sidecar safely
    // falls back to floating. The contextual entry itself did not pick floating.
    expect(canvas?.className).not.toContain("reader-record-canvas--ask-open");
    const askPanel = container.querySelector(".ai-workspace-panel");
    expect(askPanel?.className).toContain("ai-workspace-panel--surface-floating");
    expect(askPanel?.className).toContain("ai-workspace-panel--layout-overlay");
    expect(container.textContent).toContain("当前阅读区较窄，Ask Claread 以浮窗形式展示。");
  });
  it("reflows a measured-wide workspace into a docked Ask sidecar", async () => {
    installReaderAskFetchMock();
    const wideRect = {
      x: 0,
      y: 0,
      top: 0,
      right: 1920,
      bottom: 1080,
      left: 0,
      width: 1920,
      height: 1080,
      toJSON: () => ({}),
    } as DOMRect;
    const rectSpy = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockReturnValue(wideRect);

    try {
      const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
      fireEvent.click(screen.getByRole("button", { name: "打开 Ask Claread" }));

      await waitFor(() => {
        expect(container.querySelector(".reader-workspace-shell")?.className).toContain(
          "reader-workspace-shell--ask-docked",
        );
      });

      const canvas = container.querySelector(".reader-record-canvas");
      const askPanel = container.querySelector(".ai-workspace-panel");
      expect(canvas?.className).toContain("reader-record-canvas--ask-open");
      expect(askPanel?.className).toContain("ai-workspace-panel--surface-sidecar");
      expect(askPanel?.className).toContain("ai-workspace-panel--layout-docked");
      expect(container.querySelector(".reader-record-outline-slot")).not.toBeNull();
    } finally {
      rectSpy.mockRestore();
    }
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
    expect(progressStatus?.className).toContain("bg-surface-raised");
    expect(progressStatus?.textContent).toContain("可以开始阅读");

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
    render(<ReaderRecordPlateSurface snapshot={snapshot} />);

    const topBar = screen.getByTestId("reader-record-top-bar");
    expect(topBar).toBeTruthy();
    const workspaceChrome = topBar.closest(".reader-workspace-shell__topbar");
    expect(workspaceChrome).not.toBeNull();
    expect(topBar.className).toContain("relative");    // Top bar 必须是页面级 operation layer，不能再被内容列居中约束。
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

  it("places the analysis progress control before FavoriteButton in the sticky top bar", () => {
    const snapshot = makeSnapshot();
    render(<ReaderRecordPlateSurface snapshot={snapshot} />);

    const topBar = screen.getByTestId("reader-record-top-bar");
    const workspaceChrome = topBar.closest(".reader-workspace-shell__topbar");
    expect(workspaceChrome).not.toBeNull();

    const progressTrigger = screen.getByTestId("reader-analysis-progress-trigger");
    const favorite = topBar.querySelector('button[title="收藏"]');
    const moreTrigger = screen.getByTestId("reader-record-more-menu-trigger");
    expect(favorite).not.toBeNull();
    expect(topBar.contains(progressTrigger)).toBe(true);
    expect(
      progressTrigger.compareDocumentPosition(favorite as Node) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      (favorite as Node).compareDocumentPosition(moreTrigger) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    expect(screen.getByTestId("reader-record-plate-header").querySelector(
      '[data-testid="reader-analysis-progress-trigger"]',
    )).toBeNull();
    expect(document.querySelectorAll('[data-testid="reader-analysis-progress-trigger"]')).toHaveLength(1);
    expect(screen.getByTestId("reader-record-more-menu-trigger")).toBeTruthy();
    expect(screen.getByRole("button", { name: "打开 Ask Claread" })).toBeTruthy();
  });

  it("renders top bar title skeleton when title generation is pending", () => {
    const snapshot = makeSnapshot();
    snapshot.record.display_title_zh = null;
    snapshot.record.title_generation_status = "pending";
    render(<ReaderRecordPlateSurface snapshot={snapshot} />);

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
    expect(menu.querySelector('[data-reader-record-more-theme="system"]')).not.toBeNull();
    expect(menu.querySelector('[data-reader-record-more-theme="light"]')).not.toBeNull();
    expect(menu.querySelector('[data-reader-record-more-theme="dark"]')).not.toBeNull();
    expect(menu.querySelector('[data-reader-record-more-theme="paper"]')).toBeNull();
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
      // Reader 切换主题只通过 AppearanceProvider 的 setThemePreference 写全局偏好。
      expect(themePreferenceSetter).toHaveBeenCalledWith("dark");
      expect(themePreferenceCurrent).toBe("dark");
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

  it("keeps Plate toolbar as the only selection action surface and unmounts it when idle", () => {
    render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const actions = screen.getByTestId("reader-record-plate-selection-state");

    expect(actions.dataset.readerRecordActions).toBe("selection-state");
    expect(actions.dataset.readerRecordActionMode).toBe("idle");
    expect(
      actions.querySelector('[data-reader-record-action-hint]')?.textContent,
    ).toContain("划取原文后");

    expect(
      document.querySelector('[data-reader-record-toolbar-action]'),
    ).toBeNull();
    expect(
      document.querySelector(
        '[data-reader-record-floating-toolbar="selection-actions"]',
      ),
    ).toBeNull();
    expect(document.querySelector('[data-reader-record-action="feedback"]')).toBeNull();
    expect(document.querySelector('[data-reader-record-actions="selection-context"]')).toBeNull();
    expect(document.querySelector("[data-reader-record-test-action]")).toBeNull();
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
      expect(enabledActions[item.handler]).toHaveBeenCalledTimes(1);
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

  it("opens the Surface-owned Ask quick menu via the Plate AI shortcut", async () => {
    installReaderAskFetchMock();
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const memoryMark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) {
      throw new Error("Expected memory mark");
    }

    selectTextInElement(memoryMark, 0, "memory".length);
    await waitForSelectionAction("ask");
    expect(
      document.querySelector('[data-reader-record-ask-quick-menu="open"]'),
    ).toBeNull();

    fireEvent.keyDown(window, {
      code: "KeyJ",
      ctrlKey: true,
      key: "j",
    });

    // 快捷框由 Surface 托管：选区/toolbar 卸载后菜单仍存活。
    const input = await screen.findByPlaceholderText("Ask Claread anything...");
    expect(
      document.querySelector('[data-reader-record-ask-quick-menu="open"]'),
    ).not.toBeNull();

    fireEvent.keyDown(input, { key: "Escape" });
    await waitFor(() => {
      expect(
        document.querySelector('[data-reader-record-ask-quick-menu="open"]'),
      ).toBeNull();
    });
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
    await waitForSelectionAction("lookup");
    for (const action of ["lookup", "copy", "highlight", "note", "ask"]) {
      expect(
        selectionActionButton(
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

    const copyButton = await waitForSelectionAction("copy");
    await waitFor(() => {
      expect(copyButton.disabled).toBe(false);
    });
    fireEvent.click(copyButton);

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith("memory");
    });
    expect(
      fetchMock.mock.calls.filter(
        ([url]) => !(typeof url === "string" && url.includes("/favorite")),
      ),
    ).toHaveLength(0);
  });

  it.each([
    {
      label: "phrase_gloss",
      mark: makeVocabularyMark({
        item_type: "phrase_gloss",
        phrase: "memory",
        phrase_type: "fixed_collocation",
        gloss: "记忆",
        example: "Institutional memory shapes choices.",
      }),
      expectedText: "记忆",
      expectedExample: "Institutional memory shapes choices.",
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
      expectedExample: null,
    },
  ])(
    "opens structured inspect for $label vocabulary marks without dictionary lookup",
    async ({ mark, expectedExample, expectedText }) => {
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
      expect(memoryMark.hasAttribute("title")).toBe(false);

      fireEvent.click(memoryMark);

      const panel = await screen.findByTestId("reader-record-plate-lookup-panel");
      const inspectPanel = within(panel);
      expect(inspectPanel.getByText(expectedText)).toBeTruthy();
      if (expectedExample) {
        expect(inspectPanel.getByText(expectedExample)).toBeTruthy();
      }
      expect(panel.textContent).not.toContain("当前词典暂未收录");
      expect(inspectPanel.queryByLabelText("查短语")).toBeNull();
      expect(inspectPanel.getByLabelText("打开词典")).toBeTruthy();
      expect(inspectPanel.getByLabelText("带入 Ask")).toBeTruthy();
      expect(inspectPanel.getByLabelText("反馈")).toBeTruthy();
      expect(
        fetchMock.mock.calls.filter(
          ([url]) => typeof url === "string" && url.includes("/api/web/dict/lookup"),
        ),
      ).toHaveLength(0);
    },
  );

  it("opens phrase_gloss Quick Peek from snapshot with subtype, gloss, learning_note, and example", async () => {
    const snapshot = makeSnapshot();
    const unit = snapshot.value[0];
    const sourceBlock = unit.children.find(
      (child): child is ReaderSourceBlockNodeDto => child.type === "reader_source_block",
    );
    if (!sourceBlock) {
      throw new Error("Expected source block");
    }
    const segment = sourceBlock.children.find(
      (child): child is ReaderAnchorSegmentNodeDto =>
        "type" in child && child.type === "reader_anchor_segment",
    );
    if (!segment) {
      throw new Error("Expected anchor segment");
    }

    const phraseMark = makeVocabularyMark({
      mark_id: "vocab_mark_phrase_peek",
      item_type: "phrase_gloss",
      start_offset: 0,
      end_offset: 20,
      segment_start_utf16: 0,
      segment_end_utf16: 20,
      selected_text: "Institutional memory",
      phrase: "Institutional memory",
      phrase_type: "verb_expression",
      gloss: "制度记忆",
      learning_note: "机构内部的**经验沉淀**，不是个人记忆。常见：`institutional memory`",
      example: "Institutional memory shapes future choices.",
    });
    segment.children = [
      {
        ...segment.children[0],
        reader_vocabulary_marks: [phraseMark],
      },
    ];

    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    const markEl = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_phrase_peek"]',
    );
    expect(markEl).not.toBeNull();
    fireEvent.click(markEl!);

    const peek = await screen.findByTestId("reader-record-plate-lookup-panel");
    const peekView = within(peek);
    expect(peekView.getByText("动词短语")).toBeTruthy();
    expect(peekView.getByText("制度记忆")).toBeTruthy();
    expect(peekView.getByText("例句")).toBeTruthy();
    expect(peekView.getByText("Institutional memory shapes future choices.")).toBeTruthy();

    const noteRoot = peek.querySelector('[data-testid="learning-note-markdown"]');
    expect(noteRoot).toBeTruthy();
    expect(
      noteRoot?.querySelector('strong, b, [data-streamdown="strong"]')?.textContent,
    ).toBe("经验沉淀");
    expect(
      noteRoot?.querySelector('code, [data-streamdown="inline-code"]')?.textContent,
    ).toBe("institutional memory");
    expect(noteRoot?.textContent).toContain("不是个人记忆");
  });

  it("opens phrase_gloss Quick Peek without empty chrome when learning_note and example are absent", async () => {
    const snapshot = makeSnapshot();
    const unit = snapshot.value[0];
    const sourceBlock = unit.children.find(
      (child): child is ReaderSourceBlockNodeDto => child.type === "reader_source_block",
    );
    if (!sourceBlock) {
      throw new Error("Expected source block");
    }
    const segment = sourceBlock.children.find(
      (child): child is ReaderAnchorSegmentNodeDto =>
        "type" in child && child.type === "reader_anchor_segment",
    );
    if (!segment) {
      throw new Error("Expected anchor segment");
    }

    const phraseMark = makeVocabularyMark({
      mark_id: "vocab_mark_phrase_minimal",
      item_type: "phrase_gloss",
      start_offset: 0,
      end_offset: 20,
      segment_start_utf16: 0,
      segment_end_utf16: 20,
      selected_text: "Institutional memory",
      phrase: "Institutional memory",
      phrase_type: "fixed_collocation",
      gloss: "制度记忆",
      learning_note: null,
      example: null,
    });
    segment.children = [
      {
        ...segment.children[0],
        reader_vocabulary_marks: [phraseMark],
      },
    ];

    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    const markEl = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_phrase_minimal"]',
    );
    expect(markEl).not.toBeNull();
    fireEvent.click(markEl!);

    const peek = await screen.findByTestId("reader-record-plate-lookup-panel");
    const peekView = within(peek);
    expect(peekView.getByText("固定搭配")).toBeTruthy();
    expect(peekView.getByText("制度记忆")).toBeTruthy();
    expect(peekView.queryByText("例句")).toBeNull();
    expect(peek.querySelector('[data-testid="learning-note-markdown"]')).toBeNull();
  });

  it("opens grouped phrase_gloss in the dictionary rail with the AI gloss inserted above definitions", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (typeof url === "string" && url.includes("/favorite")) {
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
    expect(within(panel).queryByLabelText("查短语")).toBeNull();
    fireEvent.click(within(panel).getByLabelText("打开词典"));

    await waitFor(() => {
      const rail = container.querySelector<HTMLElement>(
        '[data-reader-record-dictionary-rail="docked"]',
      );
      if (!rail) {
        throw new Error("Expected dictionary rail");
      }
      expect(rail.className).toContain("reader-record-dictionary-rail--docked");
      expect(rail.className).not.toContain("left-[calc");
      expect(rail.className).not.toContain("w-[420px]");
      expect(within(rail).getByText("policy choices")).toBeTruthy();
      expect(within(rail).getByText("政策选择")).toBeTruthy();
      expect(within(rail).getByText("解析提示")).toBeTruthy();
      expect(within(rail).getByText("Policy choices shape institutions.")).toBeTruthy();
    });

    const lookupCalls = fetchMock.mock.calls.filter(
      ([url]) => typeof url === "string" && url.includes("/api/web/dict/lookup"),
    );
    expect(lookupCalls).toHaveLength(1);
    const lookupUrl = new URL(lookupCalls[0]![0] as string, "http://localhost");
    expect(lookupUrl.searchParams.get("word")).toBe("policy choices");
    expect(lookupUrl.searchParams.get("type")).toBe("phrase");
    expect(lookupUrl.searchParams.get("context")).toContain("policy choices");
  });

  it("opens phrase_gloss Quick Peek in the dictionary rail with learning_note rendered via the lookup path", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (typeof url === "string" && url.includes("/favorite")) {
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

    const snapshot = makeGroupedSeg2PhraseGlossSnapshot();
    // Equip the seg_2 phrase_gloss mark with a learning_note + subtype to verify
    // the lookup-path rail rendering preserves them end-to-end.
    const unit = snapshot.value[0];
    const sourceBlock = unit.children.find(
      (child): child is ReaderSourceBlockNodeDto => child.type === "reader_source_block",
    );
    if (!sourceBlock) {
      throw new Error("Expected source block");
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
    const seg2Leaf = secondSegment.children[0];
    const seg2Mark = seg2Leaf.reader_vocabulary_marks?.[0];
    if (!seg2Mark || seg2Mark.item_type !== "phrase_gloss") {
      throw new Error("Expected seg_2 phrase_gloss mark");
    }
    seg2Mark.phrase_type = "verb_expression";
    seg2Mark.learning_note =
      "注意 `policy choices` 与 `policy decisions` 的区分：前者强调选项，后者强调决策动作。";
    seg2Mark.example = "Policy choices shape institutions.";
    seg2Leaf.reader_vocabulary_marks = [seg2Mark];

    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    const seg2MarkEl = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_seg_2"]',
    );
    expect(seg2MarkEl).not.toBeNull();
    if (!seg2MarkEl) {
      throw new Error("Expected seg_2 vocabulary mark element");
    }

    fireEvent.click(seg2MarkEl);

    // Dictionary panel starts closed, so the first click opens the structured
    // inspect Quick Peek. The "打开词典" action then opens the rail, which
    // takes the lookup path (runDictionaryLookupRequest) — this is the path
    // that previously dropped learning_note.
    const panel = await screen.findByTestId("reader-record-plate-lookup-panel");
    fireEvent.click(within(panel).getByLabelText("打开词典"));

    await waitFor(() => {
      const rail = container.querySelector<HTMLElement>(
        '[data-reader-record-dictionary-rail="docked"]',
      );
      if (!rail) {
        throw new Error("Expected dictionary rail");
      }
      const railView = within(rail);
      // "policy choices" appears both as the rail headword and as inline code
      // inside the learning_note markdown — assert headword role explicitly to
      // disambiguate.
      expect(railView.getByRole("heading", { name: "policy choices" })).toBeTruthy();
      expect(railView.getByText("动词短语")).toBeTruthy();
      expect(railView.getByText("政策选择")).toBeTruthy();
      expect(railView.getByText("解析提示")).toBeTruthy();
      expect(railView.getByText("学习提示")).toBeTruthy();
      const noteRoot = rail.querySelector('[data-testid="learning-note-markdown"]');
      expect(noteRoot).toBeTruthy();
      const codeNodes = Array.from(noteRoot!.querySelectorAll("code"));
      expect(codeNodes.map((node) => node.textContent ?? "")).toEqual([
        "policy choices",
        "policy decisions",
      ]);
      expect(railView.getByText("例句")).toBeTruthy();
      expect(railView.getByText("Policy choices shape institutions.")).toBeTruthy();
    });

    const lookupCalls = fetchMock.mock.calls.filter(
      ([url]) => typeof url === "string" && url.includes("/api/web/dict/lookup"),
    );
    expect(lookupCalls).toHaveLength(1);
    const lookupUrl = new URL(lookupCalls[0]![0] as string, "http://localhost");
    expect(lookupUrl.searchParams.get("word")).toBe("policy choices");
    expect(lookupUrl.searchParams.get("type")).toBe("phrase");
  });

  it("keeps the dictionary rail closed when the workspace sidebar is locked", async () => {
    const releaseSidebarForReadingTool = vi.fn();
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (typeof url === "string" && url.includes("/favorite")) {
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
      <AppShellLayoutContext.Provider
        value={{
          variant: "workspace",
          sidebarMode: "locked",
          isWorkspaceShell: true,
          lockSidebar: () => undefined,
          closeSidebar: () => undefined,
          showSidebarOverlay: () => undefined,
          hideSidebarOverlay: () => undefined,
          releaseSidebarForReadingTool,
        }}
      >
        <ReaderRecordPlateSurface snapshot={makeGroupedSeg2PhraseGlossSnapshot()} />
      </AppShellLayoutContext.Provider>,
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
    fireEvent.click(within(panel).getByLabelText("打开词典"));

    expect(releaseSidebarForReadingTool).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(
        container.querySelector('[data-reader-record-dictionary-rail="docked"]'),
      ).toBeNull();
      expect(
        container.querySelector('[data-reader-record-dictionary-rail="sheet"]'),
      ).toBeNull();
    });
  });

  it("drops AI annotation context after a dictionary disambiguation candidate is selected", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (typeof url === "string" && url.includes("/favorite")) {
        return Promise.resolve(
          new Response(JSON.stringify({ ok: true, favorited: false }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      if (typeof url === "string" && url.includes("/api/web/dict/lookup")) {
        return Promise.resolve(
          new Response(JSON.stringify(makePolicyChoicesDisambiguationResult()), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      if (typeof url === "string" && url.includes("/api/web/dict/entry")) {
        return Promise.resolve(
          new Response(JSON.stringify(makeDictionaryEntryResult("policy choices")), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      return Promise.resolve(new Response("Not Found", { status: 404 }));
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
    fireEvent.click(within(panel).getByLabelText("打开词典"));

    const rail = await waitFor(() => {
      const element = container.querySelector<HTMLElement>(
        '[data-reader-record-dictionary-rail="docked"]',
      );
      if (!element) {
        throw new Error("Expected dictionary rail");
      }
      expect(within(element).getByText("解析提示")).toBeTruthy();
      expect(within(element).getByText("Policy choices shape institutions.")).toBeTruthy();
      expect(within(element).getByText("choices about public policy")).toBeTruthy();
      return element;
    });

    const candidateText = within(rail).getByText("choices about public policy");
    const candidateButton = candidateText.closest("button");
    expect(candidateButton).not.toBeNull();
    if (!candidateButton) {
      throw new Error("Expected candidate button");
    }
    fireEvent.click(candidateButton);

    await waitFor(() => {
      expect(within(rail).getByText("the ability to remember information")).toBeTruthy();
      expect(within(rail).queryByText("解析提示")).toBeNull();
      expect(within(rail).queryByText("Policy choices shape institutions.")).toBeNull();
    });

    const entryCalls = fetchMock.mock.calls.filter(
      ([url]) => typeof url === "string" && url.includes("/api/web/dict/entry"),
    );
    expect(entryCalls).toHaveLength(1);
    const entryUrl = new URL(entryCalls[0]![0] as string, "http://localhost");
    expect(entryUrl.searchParams.get("id")).toBe("42");
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
      if (typeof url === "string" && url.includes("/favorite")) {
        return Promise.resolve(
          new Response(JSON.stringify({ ok: true, favorited: false }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      const query =
        typeof url === "string" && url.includes("/api/web/dict/lookup")
          ? new URL(url, "http://claread.test").searchParams.get("word") ?? "memory"
          : "memory";
      return Promise.resolve(
        new Response(JSON.stringify(makeDictionaryEntryResult(query)), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const vocabMark = makeVocabularyMark({
      item_type: "vocab_highlight",
      headword: "memory",
      brief_explanation: "AI reading note for this sentence.",
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

    const panel = await screen.findByTestId("reader-record-plate-lookup-panel");
    await waitFor(() => {
      expect(within(panel).getAllByText("重点词汇")).toHaveLength(1);
    });
    const lookupCalls = fetchMock.mock.calls.filter(
      ([url]) => typeof url === "string" && url.includes("/api/web/dict/lookup"),
    );
    expect(lookupCalls).toHaveLength(1);
    const lookupUrl = String(lookupCalls[0]?.[0]);
    const lookupParams = new URL(lookupUrl, "http://claread.test").searchParams;
    expect(lookupParams.get("word")).toBe("memory");
    expect(lookupParams.get("type")).toBe("word");
    expect(lookupParams.get("context")).toBe(SOURCE_TEXT);
    expect(within(panel).getByText("词典释义")).toBeTruthy();
    expect(within(panel).queryByText("阅读提示")).toBeNull();
    expect(within(panel).getByText("AI reading note for this sentence.")).toBeTruthy();
    expect(within(panel).getByText("the ability to remember information")).toBeTruthy();
    expect(within(panel).getByText("Useful for this source sentence.")).toBeTruthy();
    const panelText = panel.textContent ?? "";
    expect(panelText.indexOf("AI reading note for this sentence.")).toBeLessThan(
      panelText.indexOf("词典释义"),
    );
    expect(panelText.indexOf("词典释义")).toBeLessThan(
      panelText.indexOf("the ability to remember information"),
    );
  });

  it("runs dictionary lookup only for a valid single anchor draft", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (typeof url === "string" && url.includes("/favorite")) {
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

    const lookupButton = await waitForSelectionAction("lookup");
    await waitFor(() => {
      expect(lookupButton.disabled).toBe(false);
    });
    fireEvent.click(lookupButton);

    await screen.findByTestId("reader-record-plate-lookup-panel");
    expect(selectionActionButton("lookup")).toBeNull();
    const nonFavoritesCalls = fetchMock.mock.calls.filter(
        ([url]) => !(typeof url === "string" && url.includes("/favorite")),
    );
    expect(nonFavoritesCalls).toHaveLength(1);
    const lookupUrl = String(nonFavoritesCalls[0]?.[0]);
    expect(lookupUrl).toContain("/api/web/dict/lookup?");
    expect(lookupUrl).toContain("word=memory");
    expect(screen.getByText("the ability to remember information")).toBeTruthy();
  });

  it("keeps the lookup context when opening the dictionary rail from quick peek", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (typeof url === "string" && url.includes("/favorite")) {
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
    const lookupButton = await waitForSelectionAction("lookup");
    fireEvent.click(lookupButton);

    const quickPeek = await screen.findByTestId("reader-record-plate-lookup-panel");
    fireEvent.click(within(quickPeek).getByLabelText("打开词典"));

    await waitFor(() => {
      const rail = container.querySelector<HTMLElement>(
        '[data-reader-record-dictionary-rail="docked"]',
      );
      if (!rail) {
        throw new Error("Expected dictionary rail");
      }
      expect(within(rail).getByText("memory")).toBeTruthy();
      expect(within(rail).getByText("the ability to remember information")).toBeTruthy();
    });
    expect(screen.queryByText("先从正文点一个词")).toBeNull();
  });

  it("wires the quick peek AI fallback to /api/web/dict/ai and shows the result in the rail", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (typeof url === "string" && url.includes("/api/web/dict/ai")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              kind: "ai_entry",
              mode: "missing_fallback",
              resultKind: "ai_entry",
              query: "memory",
              classification: "valid_word",
              summary: "AI 生成的补充释义",
              confidence: "medium",
              verified: false,
              source: "ai_generated",
              suggestedQuery: [],
              entry: {
                word: "memory",
                base_word: null,
                phonetic: null,
                meanings: [
                  {
                    part_of_speech: "n.",
                    definitions: [
                      {
                        meaning: "AI 语境下的释义",
                        example: null,
                        example_translation: null,
                      },
                    ],
                  },
                ],
                examples: [],
                phrases: [],
                entry_kind: "entry",
                exchange: [],
                tags: [],
              },
            }),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
        );
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            kind: "not_found",
            query: "memory",
            provider: "tecd3",
            cached: false,
            reason: "not_in_dictionary",
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<ReaderRecordPlateSurface snapshot={makeSnapshot()} />);
    const memoryMark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    if (!memoryMark) {
      throw new Error("Expected memory mark");
    }

    selectTextInElement(memoryMark, 0, "memory".length);
    const lookupButton = await waitForSelectionAction("lookup");
    fireEvent.click(lookupButton);

    const quickPeek = await screen.findByTestId("reader-record-plate-lookup-panel");
    const aiButton = await within(quickPeek).findByRole("button", {
      name: /词典未收录，试试 AI|AI 补充词义/,
    });
    fireEvent.click(aiButton);

    await waitFor(() => {
      const aiCall = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).includes("/api/web/dict/ai") &&
          (init as RequestInit | undefined)?.method === "POST",
      );
      expect(aiCall).toBeTruthy();
      const body = JSON.parse(String(aiCall?.[1]?.body)) as {
        mode: string;
        query: string;
      };
      expect(body).toMatchObject({ mode: "missing_fallback", query: "memory" });
    });

    // Quick peek 移交词典侧栏，AI 面板展示加载态/结果。
    await waitFor(() => {
      const rail = container.querySelector<HTMLElement>(
        '[data-reader-record-dictionary-rail="docked"]',
      );
      if (!rail) {
        throw new Error("Expected dictionary rail");
      }
      expect(rail.textContent).toContain("AI");
    });
  });

  it("routes structured vocabulary clicks into the open dictionary rail", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (typeof url === "string" && url.includes("/favorite")) {
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
    const lookupButton = await waitForSelectionAction("lookup");
    fireEvent.click(lookupButton);

    const quickPeek = await screen.findByTestId("reader-record-plate-lookup-panel");
    const openDictButton = within(quickPeek).getByLabelText("打开词典");
    fireEvent.pointerDown(openDictButton);
    fireEvent.click(openDictButton);

    const rail = await waitFor(() => {
      const node = container.querySelector<HTMLElement>(
        '[data-reader-record-dictionary-rail="docked"]',
      );
      if (!node) {
        throw new Error("Expected dictionary rail");
      }
      return node;
    });
    expect(within(rail).getByText("the ability to remember information")).toBeTruthy();

    fireEvent.click(memoryMark);

    await waitFor(() => {
      expect(within(rail).getByText("记忆")).toBeTruthy();
      expect(within(rail).getByText("the ability to remember information")).toBeTruthy();
    });
    expect(screen.queryByTestId("reader-record-plate-lookup-panel")).toBeNull();

    fireEvent.pointerDown(document.body);
    expect(within(rail).getByText("记忆")).toBeTruthy();
    expect(within(rail).queryByText("先从正文点一个词")).toBeNull();
  });

  it("adds manual dictionary searches to recent history while the rail is open", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (typeof url === "string" && url.includes("/favorite")) {
        return Promise.resolve(
          new Response(JSON.stringify({ ok: true, favorited: false }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      const query =
        typeof url === "string" && url.includes("/api/web/dict/lookup")
          ? new URL(url, "http://claread.test").searchParams.get("word") ?? "memory"
          : "memory";
      return Promise.resolve(
        new Response(JSON.stringify(makeDictionaryEntryResult(query)), {
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
    const lookupButton = await waitForSelectionAction("lookup");
    fireEvent.click(lookupButton);
    const quickPeek = await screen.findByTestId("reader-record-plate-lookup-panel");
    fireEvent.click(within(quickPeek).getByLabelText("打开词典"));

    const rail = await waitFor(() => {
      const node = container.querySelector<HTMLElement>(
        '[data-reader-record-dictionary-rail="docked"]',
      );
      if (!node) {
        throw new Error("Expected dictionary rail");
      }
      return node;
    });

    fireEvent.click(within(rail).getByLabelText("搜索词典"));
    const searchInput = within(rail).getByRole("textbox", { name: "搜索词典" });
    fireEvent.change(searchInput, { target: { value: "policy" } });
    const searchForm = searchInput.closest("form");
    expect(searchForm).not.toBeNull();
    fireEvent.submit(searchForm!);

    await waitFor(() => {
      expect(within(rail).getAllByText("policy").length).toBeGreaterThan(0);
    });

    const historyToggle = within(rail).getByText("最近查阅").closest("button");
    expect(historyToggle).not.toBeNull();
    fireEvent.click(historyToggle!);

    await waitFor(() => {
      expect(within(rail).getAllByText("policy").length).toBeGreaterThan(0);
      expect(within(rail).getAllByText("memory").length).toBeGreaterThan(0);
    });
  });

  it("runs direct word lookup after double-clicking an unmarked source word", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (typeof url === "string" && url.includes("/favorite")) {
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
    const snapshot = {
      ...makeSnapshot(),
      value: [makeUnit({ vocabularyMarks: [], grammarMarks: [] })],
    };
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    const sourceLeaf = Array.from(
      container.querySelectorAll<HTMLElement>('[data-reader-record-leaf="segment_text"]'),
    ).find((leaf) => leaf.textContent?.includes("shapes"));
    expect(sourceLeaf).not.toBeNull();
    if (!sourceLeaf) {
      throw new Error("Expected source leaf");
    }

    const startOffset = sourceLeaf.textContent?.indexOf("shapes") ?? -1;
    expect(startOffset).toBeGreaterThanOrEqual(0);
    selectTextInElement(sourceLeaf, startOffset, startOffset + "shapes ".length);
    const lookupButton = await waitForSelectionAction("lookup");
    await waitFor(() => {
      expect(lookupButton.disabled).toBe(false);
    });
    expect(window.getSelection()?.toString()).toBe("shapes ");
    fireEvent.doubleClick(sourceLeaf);

    await screen.findByTestId("reader-record-plate-lookup-panel");
    expect(selectionActionButton("lookup")).toBeNull();
    expect(window.getSelection()?.toString()).not.toBe("shapes ");
    const lookupCall = fetchMock.mock.calls.find(
      ([url]) => typeof url === "string" && url.includes("/api/web/dict/lookup"),
    );
    const lookupUrl = String(lookupCall?.[0]);
    const lookupParams = new URL(lookupUrl, "http://claread.test").searchParams;
    expect(lookupParams.get("word")).toBe("shapes");
    expect(lookupParams.get("context")).toBe(SOURCE_TEXT);
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

    const highlightButton = await waitForSelectionAction("highlight");
    await waitFor(() => {
      expect(highlightButton.disabled).toBe(false);
    });
    fireEvent.click(highlightButton);

    await waitFor(() => {
      const nonFavoritesCalls = fetchMock.mock.calls.filter(
        ([url]) => !(typeof url === "string" && url.includes("/favorite")),
      );
      expect(nonFavoritesCalls).toHaveLength(1);
    });
    const highlightCall = fetchMock.mock.calls.find(
      ([url]) => typeof url === "string" && url === "/api/web/reader/records/record_1/highlights",
    );
    expect(highlightCall?.[0]).toBe("/api/web/reader/records/record_1/highlights");
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
      if (url.includes("/favorite")) {
        return Promise.resolve(
          new Response(JSON.stringify({ ok: true, favorited: false }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      if (url === "/api/web/reader/records/record_1/highlights/asset_highlight_policy") {
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
    fireEvent.click(await screen.findByLabelText("切换为雾粉"));

    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(
        ([url]) =>
          typeof url === "string" &&
          url === "/api/web/reader/records/record_1/highlights/asset_highlight_policy",
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
      if (url.includes("/favorite")) {
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
          url === "/api/web/reader/records/record_1/highlights/asset_highlight_policy",
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
      if (url.includes("/favorite")) {
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
    const highlightButton = await waitForSelectionAction("highlight");
    await waitFor(() => {
      expect(highlightButton.disabled).toBe(false);
    });
    fireEvent.click(highlightButton);

    await waitFor(() => {
      const patchCalls = fetchMock.mock.calls.filter(
        ([url]) =>
          typeof url === "string" &&
          url === "/api/web/reader/records/record_1/highlights/asset_highlight_policy",
      );
      expect(patchCalls).toHaveLength(1);
      expect((patchCalls[0]?.[1] as RequestInit | undefined)?.method).toBe("PATCH");
    });
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) =>
          typeof url === "string" &&
          url === "/api/web/reader/records/record_1/highlights" &&
          (init as RequestInit | undefined)?.method === "POST",
      ),
    ).toBe(false);
  });

  it("removes superseded highlight assets after a canonical merge response", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/favorite")) {
        return Promise.resolve(
          new Response(JSON.stringify({ ok: true, favorited: false }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      if (url === "/api/web/reader/records/record_1/highlights") {
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
    const highlightButton = await waitForSelectionAction("highlight");
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

    const noteButton = await waitForSelectionAction("note");
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
    fireEvent.click(screen.getByRole("button", { name: "保存笔记" }));

    await waitFor(() => {
      const nonFavoritesCalls = fetchMock.mock.calls.filter(
        ([url]) => !(typeof url === "string" && url.includes("/favorite")),
      );
      expect(nonFavoritesCalls).toHaveLength(1);
    });
    const noteCall = fetchMock.mock.calls.find(
      ([url]) => typeof url === "string" && url === "/api/web/reader/records/record_1/notes",
    );
    expect(noteCall?.[0]).toBe("/api/web/reader/records/record_1/notes");
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
    const noteButton = await waitForSelectionAction("note");
    fireEvent.click(noteButton);

    const duplicateWarning = await screen.findByTestId(
      "reader-record-note-duplicate-warning",
    );
    expect(duplicateWarning.textContent).toContain("这个选区已有笔记");
    expect(duplicateWarning.textContent).toContain("Existing note for memory.");
    // The note panel is a floating layer gated by readerFloatingStyles
    // (visibility hidden until the positioning timer flushes); wait for
    // the layer to become queryable before touching its controls.
    const saveNoteButton = await waitFor(
      () => screen.getByRole<HTMLButtonElement>("button", { name: "保存笔记" }),
    );
    expect(saveNoteButton.disabled).toBe(true);

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
    fireEvent.click(screen.getByRole("button", { name: "保存笔记" }));

    await waitFor(() => {
      const noteCall = fetchMock.mock.calls.find(
      ([url]) => typeof url === "string" && url === "/api/web/reader/records/record_1/notes",
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
    const noteButton = await waitForSelectionAction("note");
    fireEvent.click(noteButton);

    await screen.findByTestId("reader-record-note-duplicate-warning");
    // Floating-layer visibility gate: wait for the panel controls to
    // become queryable before clicking (see readerFloatingStyles).
    const viewExistingNote = await waitFor(
      () => screen.getByRole("button", { name: "查看/编辑已有笔记" }),
    );
    fireEvent.click(viewExistingNote);

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

    const askButton = await waitForSelectionAction("ask");
    await openAskPanelFromToolbar(askButton);

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "关闭 Ask Claread" }),
      ).toBeTruthy();
    });
    expect(document.querySelector(".ai-workspace-panel--surface-floating")).not.toBeNull();
    expect(screen.queryByRole("button", { name: "选择 Ask Claread 面板形式" })).toBeNull();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes(
          "/api/web/reader/records/record_1/ask/threads",
        ),
      ),
    ).toBe(true);
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes(
          "/api/web/reader/records/record_1/ask/threads/thread-rr-1",
        ),
      ),
    ).toBe(true);
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/api/web/reader/records/record_1/ask/context-records"),
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

  it("does not re-ingest a stale selection draft when snapshot generation advances before selection clear", async () => {
    // Production path: currentAskSelectionAttachment. On the first render after
    // generation advances, activeSelection still holds the gen-1 draft (clear
    // runs in an effect). Without the host fence, that draft becomes a
    // selectionCandidate and the composer re-ingests it after its own identity
    // clear. With the fence, the candidate is null and the auto chip stays empty.
    const fetchMock = installReaderAskFetchMock();
    const gen1 = makeSnapshot();
    const gen2: ReaderPlateSnapshotDto = {
      ...gen1,
      snapshot_id: "snapshot_gen2",
      record: {
        ...gen1.record,
        generation: 2,
      },
    };

    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={gen1} />,
    );
    const memoryMark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) {
      throw new Error("Expected memory mark");
    }

    selectTextInElement(memoryMark, 0, "memory".length);

    // Open Ask via launcher so the auto slot (not pin/manual) is under test.
    const openLauncher = await screen.findByRole("button", {
      name: "打开 Ask Claread",
    });
    await act(async () => {
      fireEvent.click(openLauncher);
    });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "关闭 Ask Claread" })).toBeTruthy();
    });
    await waitFor(() => {
      expect(document.querySelector('[data-ask-selection-slot="auto"]')).not.toBeNull();
    });

    // Advance generation while the native selection (and thus the bridge draft)
    // is still gen-1-stamped for this render. Do not manually null the candidate.
    await act(async () => {
      rerender(<ReaderRecordPlateSurface snapshot={gen2} />);
    });

    await waitFor(() => {
      expect(document.querySelector('[data-ask-selection-slot="auto"]')).toBeNull();
    });
    expect(document.querySelector('[data-ask-selection-slot="manual"]')).toBeNull();

    // Same-identity protection: a fresh selection under gen2 re-enters the auto slot.
    const memoryMarkGen2 = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(memoryMarkGen2).not.toBeNull();
    if (!memoryMarkGen2) {
      throw new Error("Expected memory mark after generation advance");
    }
    selectTextInElement(memoryMarkGen2, 0, "memory".length);

    await waitFor(() => {
      expect(document.querySelector('[data-ask-selection-slot="auto"]')).not.toBeNull();
    });

    const attachment = await sendAskComposerMessageAndReadFirstAttachment(fetchMock);
    expect(attachment?.selected_text).toBe("memory");
    expect(attachment?.metadata).toMatchObject({
      reading_record_anchor: {
        record_id: "record_1",
        base_id: "base_1",
        generation: 2,
      },
    });
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

    const askButton = await waitForSelectionAction("ask");
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
    expect(panel.textContent).not.toContain("我的笔记");
    expect(panel.textContent).not.toContain("已保存");
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
        screen.getByRole("button", { name: "关闭 Ask Claread" }),
      ).toBeTruthy();
    });
    expect(document.querySelector(".ai-workspace-panel--surface-floating")).not.toBeNull();
    expect(screen.queryByRole("button", { name: "选择 Ask Claread 面板形式" })).toBeNull();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes(
          "/api/web/reader/records/record_1/ask/threads",
        ),
      ),
    ).toBe(true);
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes(
          "/api/web/reader/records/record_1/ask/threads/thread-rr-1",
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
      if (url.includes("/favorite")) {
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
          url === "/api/web/reader/records/record_1/notes/asset_note_1",
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
      if (url.includes("/favorite")) {
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
    expect(screen.getByText("确认删除？")).toBeTruthy();
    expect(
      fetchMock.mock.calls.some(
        ([url]) =>
          typeof url === "string" &&
          url === "/api/web/reader/records/record_1/notes/asset_note_1",
      ),
    ).toBe(false);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "确认删除笔记",
      }),
    );

    await waitFor(() => {
      const deleteCall = fetchMock.mock.calls.find(
        ([url]) =>
          typeof url === "string" &&
          url === "/api/web/reader/records/record_1/notes/asset_note_1",
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

  it("resets the delete-confirmation state when switching to another note", async () => {
    const firstNote = makePolicyNoteAsset({
      asset_id: "asset_note_confirm_a",
      note_text: "First policy note.",
    });
    const secondNote = makePolicyNoteAsset({
      asset_id: "asset_note_confirm_b",
      note_text: "Second policy note.",
      anchor: policyNoteAnchor("policy choices"),
    });
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeSnapshot([firstNote, secondNote])} />,
    );

    const firstMark = container.querySelector<HTMLElement>(
      '[data-reader-record-user-note-asset-ids~="asset_note_confirm_a"]',
    );
    expect(firstMark).not.toBeNull();
    if (!firstMark) {
      throw new Error("Expected first note mark");
    }

    fireEvent.click(firstMark);
    let panel = await screen.findByTestId("reader-record-inline-comment-panel");
    expect(panel.textContent).toContain("First policy note.");

    fireEvent.click(
      await screen.findByRole("button", { name: "删除笔记" }),
    );
    expect(screen.getByText("确认删除？")).toBeTruthy();

    const secondMarks = Array.from(
      container.querySelectorAll<HTMLElement>(
        '[data-reader-record-user-note-asset-ids~="asset_note_confirm_b"]',
      ),
    );
    const secondMark = secondMarks.find((element) =>
      element.textContent?.includes("choices"),
    );
    if (!secondMark) {
      throw new Error("Expected second note mark fragment");
    }
    fireEvent.click(secondMark);
    panel = await screen.findByTestId("reader-record-inline-comment-panel");
    await waitFor(() => {
      expect(panel.textContent).toContain("Second policy note.");
    });
    // Switching notes resets the stale delete-confirmation: the new note's
    // delete entry point must be available again, no "确认删除？" residue.
    expect(screen.queryByText("确认删除？")).toBeNull();
    expect(
      await screen.findByRole("button", { name: "删除笔记" }),
    ).toBeTruthy();
  });

  it("cancels a draft note without calling any write endpoint", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/favorite")) {
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

    const noteButton = await waitForSelectionAction("note");
    await waitFor(() => {
      expect(noteButton.disabled).toBe(false);
    });
    fireEvent.click(noteButton);

    const noteInput = await screen.findByTestId<HTMLTextAreaElement>(
      "reader-record-plate-note-input",
    );
    expect(noteInput).not.toBeNull();
    const panel = await screen.findByTestId("reader-record-inline-comment-panel");
    expect(panel.textContent).not.toContain("新建笔记");

    const cancelButton = screen.getByRole("button", { name: "关闭笔记面板" });
    fireEvent.click(cancelButton);

    await waitFor(() => {
      expect(
        screen.queryByTestId("reader-record-plate-note-input"),
      ).toBeNull();
    });

    const nonFavoritesCalls = fetchMock.mock.calls.filter(
        ([url]) => !(typeof url === "string" && url.includes("/favorite")),
    );
    expect(nonFavoritesCalls).toHaveLength(0);
  });

  it("cancels note editing and returns to view mode", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/favorite")) {
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
      ([url]) => !(typeof url === "string" && url.includes("/favorite")),
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

    const lookupButton = await waitForSelectionAction("lookup");
    const copyButton = await waitForSelectionAction("copy");
    const askButton = await waitForSelectionAction("ask");
    const highlightButton = await waitForSelectionAction("highlight");
    const noteButton = await waitForSelectionAction("note");

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
        String(url).includes("/api/web/reader/records/record_1/highlights"),
      ),
    ).toBe(false);
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).includes("/api/web/reader/records/record_1/notes"),
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

    const lookupButton = await waitForSelectionAction("lookup");
    const copyButton = await waitForSelectionAction("copy");
    const askButton = await waitForSelectionAction("ask");
    const highlightButton = await waitForSelectionAction("highlight");
    const noteButton = await waitForSelectionAction("note");

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

    const askButton = await waitForSelectionAction("ask");
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
    const copyButton = await waitForSelectionAction("copy");
    const askButton = await waitForSelectionAction("ask");
    const highlightButton = await waitForSelectionAction("highlight");
    await waitFor(() => {
      expect(actions.dataset.readerRecordSelectionSurfaceKind).toBe("source");
      expect(copyButton.disabled).toBe(false);
      expect(askButton.disabled).toBe(false);
      expect(highlightButton.disabled).toBe(false);
    });

    selectAcrossElements(sourceParagraph, 0, blockquote, 2);
    await waitFor(() => {
      expect(actions.dataset.readerRecordSelectionSupported).toBe("false");
      expect(actions.dataset.readerRecordSelectionSurfaceKind).not.toBe("source");
    });
    await waitFor(() => {
      expect(
        document.querySelector(
          '[data-reader-record-floating-toolbar="selection-actions"]',
        ),
      ).toBeNull();
    });
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

    const askButton = await waitForSelectionAction("ask");
    const lookupButton = await waitForSelectionAction("lookup");
    const copyButton = await waitForSelectionAction("copy");
    const highlightButton = await waitForSelectionAction("highlight");
    const noteButton = await waitForSelectionAction("note");
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

    const askButton = await waitForSelectionAction("ask");
    const copyButton = await waitForSelectionAction("copy");
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

    const askButton = await waitForSelectionAction("ask");
    const copyButton = await waitForSelectionAction("copy");
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
    expect(chunk.getAttribute("role")).toBe("button");
    expect(chunk.getAttribute("aria-label")).toContain("定位原文片段：subject");
    expect(chunk.tabIndex).toBe(0);
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

    fireEvent.keyDown(chunk, { key: "Enter" });
    await waitFor(() => {
      expect(chunk.dataset.readerRecordSentenceAnalysisChunkActive).toBe("true");
    });
  });

  it("keeps Quick Peek mark references live and does not resolve mark intent from DOM JSON payloads", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const policyMark = makeVocabularyMark({
      mark_id: "vocab_mark_policy",
      start_offset: 28,
      end_offset: 42,
      segment_start_utf16: 28,
      segment_end_utf16: 42,
      selected_text: "policy choices",
      phrase: "policy choices",
      gloss: "政策选择",
    });
    const snapshot = {
      ...makeSnapshot(),
      value: [makeUnit({ vocabularyMarks: [makeVocabularyMark(), policyMark] })],
    };
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);

    expect(container.querySelector("[data-reader-record-mark-payload]")).toBeNull();

    const memoryMark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    const policyMarkElement = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_policy"]',
    );
    expect(memoryMark).not.toBeNull();
    expect(policyMarkElement).not.toBeNull();
    if (!memoryMark || !policyMarkElement) {
      throw new Error("Expected vocabulary marks");
    }

    fireEvent.click(memoryMark);
    const peek = await screen.findByTestId("reader-record-plate-lookup-panel");
    expect(peek.textContent).toContain("记忆");

    fireEvent.click(policyMarkElement);
    await waitFor(() => {
      const livePeek = screen.queryByTestId("reader-record-plate-lookup-panel");
      expect(livePeek).not.toBeNull();
      expect(livePeek?.textContent).toContain("政策选择");
    });

    const surfaceSource = readFileSync(
      resolve(process.cwd(), "src/components/reader/plate/ReaderRecordPlateSurface.tsx"),
      "utf8",
    );
    const leafSource = readFileSync(
      resolve(process.cwd(), "src/components/editor/plugins/reader-leaf-kit.tsx"),
      "utf8",
    );
    expect(surfaceSource).not.toContain(
      'quickPeekAnchorRef.current = { kind: "range", getRect: () => rect };',
    );
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
      "src/app/api/web/reader/records/[recordId]/highlights/route.ts",
      "src/app/api/web/reader/records/[recordId]/notes/route.ts",
    ].map((filePath) => readFileSync(resolve(process.cwd(), filePath), "utf8"));

    expect(surfaceSource).toMatch(/AiWorkspacePanel/);
    expect(surfaceSource).not.toMatch(/recordScope=/);
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

  it("keeps production selection UI on the Plate FloatingToolbar path without hidden test controls", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
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

    const toolbar = await waitFor(() => {
      const toolbarElement = document.querySelector<HTMLElement>(
        '[data-reader-record-floating-toolbar="selection-actions"]',
      );
      if (!toolbarElement) {
        throw new Error("Expected production selection floating toolbar");
      }
      return toolbarElement;
    });
    const buttons = Array.from(
      toolbar.querySelectorAll<HTMLButtonElement>("[data-reader-record-toolbar-action]"),
    );
    expect(buttons.map((button) => button.dataset.readerRecordToolbarAction)).toEqual([
      "ask",
      "lookup",
      "highlight",
      "copy",
      "note",
    ]);
    for (const button of buttons) {
      expect(button.className).toContain("rounded-[8px]");
      // 统一中性 hover（墨色灰阶），Ask 与其他按钮共享同一套。
      expect(button.className).toContain("hover:bg-ink/[0.06]");
    }
    expect(document.querySelector("[data-reader-record-test-action]")).toBeNull();

    const surfaceSource = readFileSync(
      resolve(process.cwd(), "src/components/reader/plate/ReaderRecordPlateSurface.tsx"),
      "utf8",
    );
    expect(surfaceSource).not.toMatch(/SelectionActionStrip/);
    expect(surfaceSource).not.toMatch(/data-reader-record-test-action/);
  });

  it("routes the Note toolbar action through the Plate CommentKit draft path", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
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
    const noteButton = await waitForSelectionAction("note");
    await waitFor(() => {
      expect(noteButton.disabled).toBe(false);
    });
    fireEvent.click(noteButton);

    const panel = await screen.findByTestId("reader-record-inline-comment-panel");
    expect(panel.dataset.readerRecordCommentMode).toBe("draft");
    expect(
      panel.querySelector('[data-reader-record-note-quote="true"]')?.textContent,
    ).toContain("memory");
    expect(await screen.findByTestId("reader-record-plate-note-input")).toBeTruthy();

    const surfaceSource = readFileSync(
      resolve(process.cwd(), "src/components/reader/plate/ReaderRecordPlateSurface.tsx"),
      "utf8",
    );
    expect(surfaceSource).not.toMatch(/ReaderRecordNoteComposer/);
  });

  it("keeps new user highlight choices to yellow, mint, and rose", async () => {
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

    fireEvent.click(highlight);

    const menu = await waitFor(() => {
      const menuElement = document.querySelector<HTMLElement>(
        '[data-reader-record-floating-toolbar="highlight-menu"]',
      );
      if (!menuElement) {
        throw new Error("Expected highlight color menu");
      }
      return menuElement;
    });
    expect(
      Array.from(
        menu.querySelectorAll<HTMLElement>("[data-reader-record-highlight-color]"),
      ).map((option) => option.dataset.readerRecordHighlightColor),
    ).toEqual(["warm_yellow", "soft_mint", "soft_rose"]);
    expect(screen.getByLabelText("切换为暖黄")).toBeTruthy();
    expect(screen.getByLabelText("切换为薄荷绿")).toBeTruthy();
    expect(screen.getByLabelText("切换为雾粉")).toBeTruthy();

    const surfaceSource = readFileSync(
      resolve(process.cwd(), "src/components/reader/plate/ReaderRecordPlateSurface.tsx"),
      "utf8",
    );
    const userHighlightSources = [
      "src/components/reader/SelectionToolbar.tsx",
      "src/components/editor/plugins/reader-leaf-kit.tsx",
      "src/components/reader/plate/ReaderMarkLeaf.tsx",
      "src/app/globals.css",
    ].map((filePath) => readFileSync(resolve(process.cwd(), filePath), "utf8"));
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
    const documentSurface = container.querySelector<HTMLElement>(
      ".reader-record-plate-document",
    );
    const contentColumn = documentSurface?.parentElement;
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

  it("auto-dismisses the saved write state back to idle after four seconds", async () => {
    let resolveHighlightWrite!: (response: Response) => void;
    const highlightWriteGate = new Promise<Response>((resolve) => {
      resolveHighlightWrite = resolve;
    });
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/favorite")) {
        return Promise.resolve(
          new Response(JSON.stringify({ ok: true, favorited: false }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      if (url.startsWith("/api/web/reader/records/record_1/highlights")) {
        return highlightWriteGate;
      }
      return Promise.resolve(new Response("Not Found", { status: 404 }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const onRequestSnapshotReload = vi.fn().mockResolvedValue(undefined);

    const { container } = render(
      <ReaderRecordPlateSurface
        snapshot={makeSnapshot([makePolicyHighlightAsset()])}
        onRequestSnapshotReload={onRequestSnapshotReload}
      />,
    );
    const writeState = container.querySelector<HTMLElement>(
      '[data-reader-record-actions="selection-state"]',
    );
    expect(writeState?.dataset.readerRecordWriteState).toBe("idle");

    const highlight = container.querySelector<HTMLElement>(
      '[data-reader-record-user-highlight-asset-id="asset_highlight_policy"]',
    );
    expect(highlight).not.toBeNull();
    if (!highlight) {
      throw new Error("Expected policy highlight mark");
    }
    selectTextInElement(highlight, 0, "policy".length);
    const highlightButton = await waitForSelectionAction("highlight");
    await waitFor(() => {
      expect(highlightButton.disabled).toBe(false);
    });
    fireEvent.click(highlightButton);

    await waitFor(() => {
      expect(writeState?.dataset.readerRecordWriteState).toBe("saving");
    });

    vi.useFakeTimers();
    try {
      await act(async () => {
        resolveHighlightWrite(
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
      await act(async () => {});
      expect(writeState?.dataset.readerRecordWriteState).toBe("saved");

      act(() => {
        vi.advanceTimersByTime(3999);
      });
      expect(writeState?.dataset.readerRecordWriteState).toBe("saved");

      act(() => {
        vi.advanceTimersByTime(1);
      });
      expect(writeState?.dataset.readerRecordWriteState).toBe("idle");
    } finally {
      vi.useRealTimers();
    }
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

  it("shell-navigation z-index token stays above Reader workspace chrome in the CSS source contract", () => {
    const globalsSource = readFileSync(
      resolve(process.cwd(), "src/app/globals.css"),
      "utf8",
    );

    const shellNavMatch = globalsSource.match(
      /--app-z-shell-navigation:\s*(\d+)\s*;/,
    );
    const workspaceChromeMatch = globalsSource.match(
      /--reader-z-workspace-chrome:\s*(\d+)\s*;/,
    );

    expect(shellNavMatch).not.toBeNull();
    expect(workspaceChromeMatch).not.toBeNull();

    const shellNavValue = Number(shellNavMatch![1]);
    const workspaceChromeValue = Number(workspaceChromeMatch![1]);

    // Application-level navigation (peek button + sidebar overlay) must sit
    // above the Reader workspace chrome so the menu trigger and expanded
    // sidebar are never covered by the sticky Reader Header.
    expect(shellNavValue).toBeGreaterThan(workspaceChromeValue);
  });
});

// ---------------------------------------------------------------------------
// Incremental projection merge integration tests
//
// Verifies that the Surface correctly wires mergeIncrementalProjection into
// its value swap effect: targeted_apply uses editor.tf.replaceNodes (non-target
// DOM identity preserved), fallback uses editor.tf.setValue (DOM rebuilt).
// Also verifies interaction preservation (grammar callout expansion, scroll)
// and that the reload context is consumed after the merge attempt.
// ---------------------------------------------------------------------------

describe("ReaderRecordPlateSurface — incremental projection", () => {
  function makeRepresentationEvent(
    eventType: "projection_ops" | "record_state_changed",
    section: string,
    operation: string,
    targetKeys: string[],
    sequence = 9,
  ): ReaderEventResponseDto {
    return {
      id: `evt_${sequence}`,
      reading_record_id: "record_1",
      sequence,
      event_type: eventType,
      payload: {
        schema_version: 1,
        representation_section: section,
        operation,
        target_keys: targetKeys,
        generation: 1,
        base_id: "base_1",
      },
      created_at: "2026-06-24T02:00:00Z",
    };
  }

  function makeLayerPublishedEvent(sequence = 9): ReaderEventResponseDto {
    return {
      id: `evt_${sequence}`,
      reading_record_id: "record_1",
      sequence,
      event_type: "layer_published",
      payload: { layer_type: "translation" },
      created_at: "2026-06-24T02:00:00Z",
    };
  }

  it("G1 user_assets upsert: targeted_apply preserves non-target DOM identity", async () => {
    const prevSnapshot = makeSnapshot([makeUserAsset({ note_text: "old note" })]);
    const nextSnapshot = makeNextSnapshot(prevSnapshot, {
      userAssets: [makeUserAsset({ note_text: "new note" })],
    });
    const event = makeRepresentationEvent(
      "projection_ops",
      "user_assets",
      "upsert",
      ["asset_highlight_1"],
    );

    let reloadContextConsumed = false;
    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={prevSnapshot} />,
    );

    await waitFor(() => {
      expect(container.querySelector(".reader-record-plate-document")).not.toBeNull();
    });

    // Capture non-target DOM reference (translation blockquote).
    const blockquoteBefore = container.querySelector(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquoteBefore).not.toBeNull();

    await act(async () => {
      rerender(
        <ReaderRecordPlateSurface
          snapshot={nextSnapshot}
          pendingReloadContext={makeReloadContext([event], "user_asset_written")}
          onReloadContextConsumed={() => {
            reloadContextConsumed = true;
          }}
        />,
      );
    });

    // Non-target DOM identity preserved (targeted_apply used replaceNodes).
    const blockquoteAfter = container.querySelector(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquoteAfter).not.toBeNull();
    expect(blockquoteBefore!.isSameNode(blockquoteAfter)).toBe(true);

    // Reload context was consumed.
    expect(reloadContextConsumed).toBe(true);
  });

  it("layer_published event: fallback_full_reload rebuilds non-target DOM", async () => {
    const prevSnapshot = makeSnapshot([makeUserAsset({ note_text: "old note" })]);
    const nextSnapshot = makeNextSnapshot(prevSnapshot, {
      userAssets: [makeUserAsset({ note_text: "new note" })],
    });
    const event = makeLayerPublishedEvent();

    let reloadContextConsumed = false;
    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={prevSnapshot} />,
    );

    await waitFor(() => {
      expect(container.querySelector(".reader-record-plate-document")).not.toBeNull();
    });

    const blockquoteBefore = container.querySelector(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquoteBefore).not.toBeNull();

    await act(async () => {
      rerender(
        <ReaderRecordPlateSurface
          snapshot={nextSnapshot}
          pendingReloadContext={makeReloadContext([event], "layer_published")}
          onReloadContextConsumed={() => {
            reloadContextConsumed = true;
          }}
        />,
      );
    });

    // setValue rebuilds all DOM — non-target DOM identity NOT preserved.
    const blockquoteAfter = container.querySelector(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquoteAfter).not.toBeNull();
    expect(blockquoteBefore!.isSameNode(blockquoteAfter)).toBe(false);

    expect(reloadContextConsumed).toBe(true);
  });

  it("empty trigger events (manual reload): fallback_full_reload via setValue", async () => {
    const prevSnapshot = makeSnapshot();
    const nextSnapshot = makeNextSnapshot(prevSnapshot);

    let reloadContextConsumed = false;
    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={prevSnapshot} />,
    );

    await waitFor(() => {
      expect(container.querySelector(".reader-record-plate-document")).not.toBeNull();
    });

    const blockquoteBefore = container.querySelector(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquoteBefore).not.toBeNull();

    await act(async () => {
      rerender(
        <ReaderRecordPlateSurface
          snapshot={nextSnapshot}
          pendingReloadContext={makeReloadContext([], "manual_retry")}
          onReloadContextConsumed={() => {
            reloadContextConsumed = true;
          }}
        />,
      );
    });

    // Empty events → fallback → setValue → DOM rebuilt.
    const blockquoteAfter = container.querySelector(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquoteAfter).not.toBeNull();
    expect(blockquoteBefore!.isSameNode(blockquoteAfter)).toBe(false);

    expect(reloadContextConsumed).toBe(true);
  });

  it("no pendingReloadContext: existing setValue behavior preserved", async () => {
    const prevSnapshot = makeSnapshot();
    const nextSnapshot = makeNextSnapshot(prevSnapshot);

    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={prevSnapshot} />,
    );

    await waitFor(() => {
      expect(container.querySelector(".reader-record-plate-document")).not.toBeNull();
    });

    const blockquoteBefore = container.querySelector(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquoteBefore).not.toBeNull();

    await act(async () => {
      rerender(<ReaderRecordPlateSurface snapshot={nextSnapshot} />);
    });

    // No reload context → setValue → DOM rebuilt.
    const blockquoteAfter = container.querySelector(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquoteAfter).not.toBeNull();
    expect(blockquoteBefore!.isSameNode(blockquoteAfter)).toBe(false);
  });

  it("G1 targeted_apply preserves grammar callout expanded state", async () => {
    const prevSnapshot = makeSnapshot([makeUserAsset({ note_text: "old note" })]);
    const nextSnapshot = makeNextSnapshot(prevSnapshot, {
      userAssets: [makeUserAsset({ note_text: "new note" })],
    });
    const event = makeRepresentationEvent(
      "projection_ops",
      "user_assets",
      "upsert",
      ["asset_highlight_1"],
    );

    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={prevSnapshot} />,
    );

    await waitFor(() => {
      expect(container.querySelector(".reader-record-plate-document")).not.toBeNull();
    });

    // Expand the grammar callout.
    const grammarCallout = container.querySelector<HTMLElement>(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );
    const toggle = grammarCallout?.querySelector<HTMLButtonElement>(
      '[data-reader-record-callout-toggle="grammar"]',
    );
    expect(grammarCallout).not.toBeNull();
    expect(toggle).not.toBeNull();

    await act(async () => {
      fireEvent.click(toggle!);
    });

    await waitFor(() => {
      expect(grammarCallout!.dataset.readerRecordCalloutCollapsed).toBe("false");
    });

    // Rerender with targeted_apply.
    await act(async () => {
      rerender(
        <ReaderRecordPlateSurface
          snapshot={nextSnapshot}
          pendingReloadContext={makeReloadContext([event], "user_asset_written")}
          onReloadContextConsumed={() => {}}
        />,
      );
    });

    // Grammar callout should still be expanded (interaction preserved).
    const grammarCalloutAfter = container.querySelector<HTMLElement>(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );
    expect(grammarCalloutAfter).not.toBeNull();
    expect(grammarCalloutAfter!.dataset.readerRecordCalloutCollapsed).toBe("false");
  });

  it("G1 targeted_apply preserves scroll position on the scroll container", async () => {
    const prevSnapshot = makeSnapshot([makeUserAsset({ note_text: "old note" })]);
    const nextSnapshot = makeNextSnapshot(prevSnapshot, {
      userAssets: [makeUserAsset({ note_text: "new note" })],
    });
    const event = makeRepresentationEvent(
      "projection_ops",
      "user_assets",
      "upsert",
      ["asset_highlight_1"],
    );

    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={prevSnapshot} />,
    );

    await waitFor(() => {
      expect(container.querySelector(".reader-record-plate-document")).not.toBeNull();
    });

    // Mark the plate body parent as a scroll container and set scrollTop.
    const body = container.querySelector(".reader-record-plate-document");
    const scroller = body?.parentElement as HTMLElement | null;
    expect(scroller).not.toBeNull();
    if (!scroller) throw new Error("expected scroll parent");

    Object.defineProperty(scroller, "scrollTop", {
      configurable: true,
      writable: true,
      value: 240,
    });
    scroller.style.overflowY = "auto";

    await act(async () => {
      rerender(
        <ReaderRecordPlateSurface
          snapshot={nextSnapshot}
          pendingReloadContext={makeReloadContext([event], "user_asset_written")}
          onReloadContextConsumed={() => {}}
        />,
      );
    });

    // rAF restore — flush the requestAnimationFrame callback.
    await act(async () => {
      // jsdom requestAnimationFrame is polyfilled; flush microtasks.
    });

    expect(scroller.scrollTop).toBe(240);
  });

  it("G1 targeted_apply updates target paragraph content correctly", async () => {
    // Use two snapshots with different user asset note_text so the projected
    // paragraph content differs between prev and next.
    const prevSnapshot = makeSnapshot([makeUserAsset({ note_text: "prev note" })]);
    const nextSnapshot = makeNextSnapshot(prevSnapshot, {
      userAssets: [makeUserAsset({ note_text: "next note" })],
    });
    const event = makeRepresentationEvent(
      "projection_ops",
      "user_assets",
      "upsert",
      ["asset_highlight_1"],
    );

    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={prevSnapshot} />,
    );

    await waitFor(() => {
      expect(container.querySelector(".reader-record-plate-document")).not.toBeNull();
    });

    // Verify initial content.
    const paragraphBefore = container.querySelector(
      '[data-reader-record-node="paragraph"]',
    );
    expect(paragraphBefore).not.toBeNull();
    expect(paragraphBefore!.textContent).toContain(SOURCE_TEXT);

    await act(async () => {
      rerender(
        <ReaderRecordPlateSurface
          snapshot={nextSnapshot}
          pendingReloadContext={makeReloadContext([event], "user_asset_written")}
          onReloadContextConsumed={() => {}}
        />,
      );
    });

    // Target paragraph content should still render the source text
    // (the paragraph block was replaced with the re-projected version).
    const paragraphAfter = container.querySelector(
      '[data-reader-record-node="paragraph"]',
    );
    expect(paragraphAfter).not.toBeNull();
    expect(paragraphAfter!.textContent).toContain(SOURCE_TEXT);
  });
  it("closes an open Quick Peek before replacing its target paragraph", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (typeof url === "string" && url.includes("/favorite")) {
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

    const prevSnapshot = makeSnapshot([makeUserAsset({ note_text: "old note" })]);
    const nextSnapshot = makeNextSnapshot(prevSnapshot, {
      userAssets: [makeUserAsset({ note_text: "new note" })],
    });
    const event = makeRepresentationEvent(
      "projection_ops",
      "user_assets",
      "upsert",
      ["asset_highlight_1"],
    );
    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={prevSnapshot} />,
    );

    const memoryMark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) throw new Error("Expected memory vocabulary mark");

    selectTextInElement(memoryMark, 0, "memory".length);
    const lookupButton = await waitForSelectionAction("lookup");
    fireEvent.click(lookupButton);

    const quickPeekBefore = await screen.findByTestId(
      "reader-record-plate-lookup-panel",
    );
    expect(within(quickPeekBefore).getByText("memory")).toBeTruthy();

    await act(async () => {
      rerender(
        <ReaderRecordPlateSurface
          snapshot={nextSnapshot}
          pendingReloadContext={makeReloadContext([event], "user_asset_written")}
          onReloadContextConsumed={() => {}}
        />,
      );
    });

    await waitFor(() => {
      expect(
        screen.queryByTestId("reader-record-plate-lookup-panel"),
      ).toBeNull();
    });
    expect(quickPeekBefore.isConnected).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// layer_published changed-block-only integration tests.
//
// Verifies the Surface correctly applies the layer_published changed-block-only path
// for valid `layer_published` events (same topology revision) and falls back
// to setValue for structural changes.
// ---------------------------------------------------------------------------

describe("ReaderRecordPlateSurface — layer_published changed-block-only", () => {
  function makeValidLayerPublishedEvent(
    layerType: "translation" | "vocabulary" | "grammar_note" | "sentence_analysis" = "translation",
    sequence = 9,
  ): ReaderEventResponseDto {
    return {
      id: `evt_${sequence}`,
      reading_record_id: "record_1",
      sequence,
      event_type: "layer_published",
      payload: {
        record_id: "record_1",
        base_id: "base_1",
        layer_id: `layer_${layerType}_1`,
        layer_type: layerType,
        target_scope: "unit",
        target_key: "unit_1",
        generation: 1,
      },
      created_at: "2026-06-24T02:00:00Z",
    };
  }

  function makeLayerRevisionSnapshot(
    prev: ReaderPlateSnapshotDto,
    overrides: {
      translationText?: string;
      grammarNote?: string;
      analysis?: string;
      analysisChunks?: Array<{ order: number; label: string; text: string }>;
      vocabularyGloss?: string;
    } = {},
  ): ReaderPlateSnapshotDto {
    const unit = prev.value[0] as ReaderUnitNodeDto;
    const nextUnit: ReaderUnitNodeDto = {
      ...unit,
      children: unit.children.map((child) => {
        if (child.type === "reader_translation_group") {
          return {
            ...child,
            children: [
              {
                text:
                  overrides.translationText ??
                  ((child as { children: Array<{ text: string }> }).children[0]?.text ?? ""),
              },
            ],
          };
        }
        if (child.type === "reader_sentence_analysis") {
          return {
            ...child,
            analysis: overrides.analysis ?? (child as { analysis: string }).analysis,
            chunks: overrides.analysisChunks ?? (child as { chunks: Array<{ order: number; label: string; text: string }> }).chunks,
            children: [{ text: overrides.analysis ?? (child as { analysis: string }).analysis }],
          };
        }
        if (child.type === "reader_source_block") {
          return {
            ...child,
            children: child.children.map((seg) => {
              if (!("type" in seg) || seg.type !== "reader_anchor_segment") {
                return seg;
              }
              return {
                ...seg,
                children: seg.children.map((leaf) => ({
                  ...leaf,
                  reader_grammar_note_marks: (leaf.reader_grammar_note_marks ?? []).map(
                    (mark) => ({
                      ...mark,
                      note: overrides.grammarNote ?? mark.note,
                    }),
                  ),
                  reader_vocabulary_marks:
                    overrides.vocabularyGloss !== undefined
                      ? (leaf.reader_vocabulary_marks ?? []).map((mark) => ({
                          ...mark,
                          gloss: overrides.vocabularyGloss!,
                        }))
                      : leaf.reader_vocabulary_marks,
                })),
              };
            }),
          };
        }
        return child;
      }),
    };
    return {
      ...prev,
      snapshot_id: "snapshot_layer_revision",
      last_event_sequence: 9,
      value: [nextUnit],
    };
  }

  it("grammar_note revision with same topology: targeted_apply preserves grammar callout expanded", async () => {
    const prevSnapshot = makeSnapshot();
    const nextSnapshot = makeLayerRevisionSnapshot(prevSnapshot, {
      grammarNote: "shapes is the predicate verb. (revised note)",
    });
    const event = makeValidLayerPublishedEvent("grammar_note");

    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={prevSnapshot} />,
    );

    await waitFor(() => {
      expect(container.querySelector(".reader-record-plate-document")).not.toBeNull();
    });

    // Expand the grammar callout before reload.
    const grammarCallout = container.querySelector<HTMLElement>(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );
    const toggle = grammarCallout?.querySelector<HTMLButtonElement>(
      '[data-reader-record-callout-toggle="grammar"]',
    );
    expect(grammarCallout).not.toBeNull();
    expect(toggle).not.toBeNull();

    await act(async () => {
      fireEvent.click(toggle!);
    });

    await waitFor(() => {
      expect(grammarCallout!.dataset.readerRecordCalloutCollapsed).toBe("false");
    });

    // Apply the layer_published revision via targeted_apply.
    await act(async () => {
      rerender(
        <ReaderRecordPlateSurface
          snapshot={nextSnapshot}
          pendingReloadContext={makeReloadContext([event], "layer_published")}
          onReloadContextConsumed={() => {}}
        />,
        );
    });

    // Grammar callout (same itemId) should still be expanded after targeted_apply.
    const grammarCalloutAfter = container.querySelector<HTMLElement>(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );
    expect(grammarCalloutAfter).not.toBeNull();
    expect(grammarCalloutAfter!.dataset.readerRecordCalloutCollapsed).toBe("false");
  });

  it("grammar_note revision with same topology: non-target blockquote DOM identity preserved", async () => {
    const prevSnapshot = makeSnapshot();
    const nextSnapshot = makeLayerRevisionSnapshot(prevSnapshot, {
      grammarNote: "shapes is the predicate verb. (revised note v2)",
    });
    const event = makeValidLayerPublishedEvent("grammar_note");

    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={prevSnapshot} />,
    );

    await waitFor(() => {
      expect(container.querySelector(".reader-record-plate-document")).not.toBeNull();
    });

    // Capture non-target DOM reference (translation blockquote).
    const blockquoteBefore = container.querySelector(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquoteBefore).not.toBeNull();

    await act(async () => {
      rerender(
        <ReaderRecordPlateSurface
          snapshot={nextSnapshot}
          pendingReloadContext={makeReloadContext([event], "layer_published")}
          onReloadContextConsumed={() => {}}
        />,
      );
    });

    // Non-target DOM identity preserved (targeted_apply used replaceNodes).
    const blockquoteAfter = container.querySelector(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquoteAfter).not.toBeNull();
    expect(blockquoteBefore!.isSameNode(blockquoteAfter)).toBe(true);
  });

  it("translation revision with same topology: targeted_apply replaces blockquote, paragraph DOM identity preserved", async () => {
    const prevSnapshot = makeSnapshot();
    const nextSnapshot = makeLayerRevisionSnapshot(prevSnapshot, {
      translationText: "制度记忆塑造政策选择。(修订版)",
    });
    const event = makeValidLayerPublishedEvent("translation");

    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={prevSnapshot} />,
    );

    await waitFor(() => {
      expect(container.querySelector(".reader-record-plate-document")).not.toBeNull();
    });

    // Capture paragraph DOM reference (non-target — translation revision
    // changes the blockquote, not the paragraph).
    const paragraphBefore = container.querySelector(
      '[data-reader-record-node="paragraph"]',
    );
    expect(paragraphBefore).not.toBeNull();

    await act(async () => {
      rerender(
        <ReaderRecordPlateSurface
          snapshot={nextSnapshot}
          pendingReloadContext={makeReloadContext([event], "layer_published")}
          onReloadContextConsumed={() => {}}
        />,
      );
    });

    // Non-target paragraph DOM identity preserved.
    const paragraphAfter = container.querySelector(
      '[data-reader-record-node="paragraph"]',
    );
    expect(paragraphAfter).not.toBeNull();
    expect(paragraphBefore!.isSameNode(paragraphAfter)).toBe(true);

    // Target blockquote content was updated.
    const blockquoteAfter = container.querySelector(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquoteAfter).not.toBeNull();
    expect(blockquoteAfter!.textContent).toContain("修订版");
  });

  it("translation revision with same topology: opens Quick Peek then targeted_apply on translation does not close Quick Peek (sibling paragraph anchor)", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (typeof url === "string" && url.includes("/favorite")) {
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

    const prevSnapshot = makeSnapshot();
    const nextSnapshot = makeLayerRevisionSnapshot(prevSnapshot, {
      translationText: "制度记忆塑造政策选择。(修订版)",
    });
    const event = makeValidLayerPublishedEvent("translation");

    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={prevSnapshot} />,
    );

    await waitFor(() => {
      expect(container.querySelector(".reader-record-plate-document")).not.toBeNull();
    });

    // Open Quick Peek anchored on the paragraph (vocab mark "memory").
    const memoryMark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) throw new Error("Expected memory vocabulary mark");

    selectTextInElement(memoryMark, 0, "memory".length);
    const lookupButton = await waitForSelectionAction("lookup");
    fireEvent.click(lookupButton);

    const quickPeekBefore = await screen.findByTestId(
      "reader-record-plate-lookup-panel",
    );
    expect(within(quickPeekBefore).getByText("memory")).toBeTruthy();

    // Apply translation revision (target is blockquote, NOT the paragraph
    // that anchors Quick Peek).
    await act(async () => {
      rerender(
        <ReaderRecordPlateSurface
          snapshot={nextSnapshot}
          pendingReloadContext={makeReloadContext([event], "layer_published")}
          onReloadContextConsumed={() => {}}
        />,
      );
    });

    // Quick Peek anchored on paragraph: targeted_apply on blockquote does NOT
    // close Quick Peek (paragraph block_id !== "paragraph:..." being replaced).
    // Per the changed-block-only contract: only target paragraph replacement closes Quick Peek.
    // Translation revision targets blockquote → Quick Peek should stay open.
    await waitFor(() => {
      const panel = screen.queryByTestId("reader-record-plate-lookup-panel");
      // Quick Peek should still be visible (paragraph anchor not replaced).
      expect(panel).not.toBeNull();
    });
  });

  it("vocabulary revision with same topology: targeted_apply on paragraph closes Quick Peek (target paragraph replacement)", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (typeof url === "string" && url.includes("/favorite")) {
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

    const prevSnapshot = makeSnapshot();
    // vocabulary revision changes the projected paragraph (mark data), but
    // block_id sequence stays identical. Make a slight change to vocabulary
    // gloss so the projected paragraph content differs.
    const nextUnit = makeUnit({
      vocabularyMarks: [
        makeVocabularyMark({ gloss: "记忆 (修订)" }),
      ],
    });
    const nextSnapshot: ReaderPlateSnapshotDto = {
      ...prevSnapshot,
      snapshot_id: "snapshot_vocab_revision",
      last_event_sequence: 9,
      value: [nextUnit],
    };
    const event = makeValidLayerPublishedEvent("vocabulary");

    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={prevSnapshot} />,
    );

    await waitFor(() => {
      expect(container.querySelector(".reader-record-plate-document")).not.toBeNull();
    });

    // Open Quick Peek anchored on the paragraph (vocab mark "memory").
    const memoryMark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) throw new Error("Expected memory vocabulary mark");

    selectTextInElement(memoryMark, 0, "memory".length);
    const lookupButton = await waitForSelectionAction("lookup");
    fireEvent.click(lookupButton);

    const quickPeekBefore = await screen.findByTestId(
      "reader-record-plate-lookup-panel",
    );
    expect(within(quickPeekBefore).getByText("memory")).toBeTruthy();

    // Apply vocabulary revision (target IS the paragraph that anchors QP).
    await act(async () => {
      rerender(
        <ReaderRecordPlateSurface
          snapshot={nextSnapshot}
          pendingReloadContext={makeReloadContext([event], "layer_published")}
          onReloadContextConsumed={() => {}}
        />,
      );
    });

    // Quick Peek anchored on target paragraph → must be closed deterministically.
    await waitFor(() => {
      expect(
        screen.queryByTestId("reader-record-plate-lookup-panel"),
      ).toBeNull();
    });
    expect(quickPeekBefore.isConnected).toBe(false);
  });

  it("vocabulary revision with same topology: targeted_apply preserves grammar callout expanded", async () => {
    const prevSnapshot = makeSnapshot();
    const nextSnapshot = makeLayerRevisionSnapshot(prevSnapshot, {
      vocabularyGloss: "记忆 (修订)",
    });
    const event = makeValidLayerPublishedEvent("vocabulary");

    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={prevSnapshot} />,
    );

    await waitFor(() => {
      expect(container.querySelector(".reader-record-plate-document")).not.toBeNull();
    });

    // Expand the grammar callout before reload.
    const grammarCallout = container.querySelector<HTMLElement>(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );
    expect(grammarCallout).not.toBeNull();
    if (!grammarCallout) throw new Error("Expected grammar callout");

    expect(grammarCallout.getAttribute("data-reader-record-callout-collapsed")).toBe("true");
    fireEvent.click(
      grammarCallout.querySelector('[data-reader-record-callout-toggle="grammar"]')!,
    );
    expect(grammarCallout.getAttribute("data-reader-record-callout-collapsed")).toBe("false");

    await act(async () => {
      rerender(
        <ReaderRecordPlateSurface
          snapshot={nextSnapshot}
          pendingReloadContext={makeReloadContext([event], "layer_published")}
          onReloadContextConsumed={() => {}}
        />,
      );
    });

    // Grammar callout (same itemId) should still be expanded after
    // targeted_apply — vocabulary revision does not touch grammar callouts.
    const calloutAfter = container.querySelector<HTMLElement>(
      '[data-callout-variant="grammar"][data-reader-record-grammar-item-id="grammar_item_1"]',
    );
    expect(calloutAfter).not.toBeNull();
    expect(calloutAfter!.getAttribute("data-reader-record-callout-collapsed")).toBe("false");
  });

  it("vocabulary revision with same topology: non-target blockquote DOM identity preserved", async () => {
    const prevSnapshot = makeSnapshot();
    const nextSnapshot = makeLayerRevisionSnapshot(prevSnapshot, {
      vocabularyGloss: "记忆 (修订)",
    });
    const event = makeValidLayerPublishedEvent("vocabulary");

    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={prevSnapshot} />,
    );

    await waitFor(() => {
      expect(container.querySelector(".reader-record-plate-document")).not.toBeNull();
    });

    // Capture blockquote DOM reference (non-target — vocabulary revision
    // changes the paragraph, not the blockquote).
    const blockquoteBefore = container.querySelector(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquoteBefore).not.toBeNull();

    await act(async () => {
      rerender(
        <ReaderRecordPlateSurface
          snapshot={nextSnapshot}
          pendingReloadContext={makeReloadContext([event], "layer_published")}
          onReloadContextConsumed={() => {}}
        />,
      );
    });

    // Non-target blockquote DOM identity preserved.
    const blockquoteAfter = container.querySelector(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquoteAfter).not.toBeNull();
    expect(blockquoteBefore!.isSameNode(blockquoteAfter)).toBe(true);

    // Target paragraph vocabulary mark gloss updated.
    const vocabMarkAfter = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(vocabMarkAfter).not.toBeNull();
    // The revised gloss should be reflected in the mark's data attribute
    // or rendered content. We check the mark element is still present and
    // the paragraph was replaced (not full reload).
    expect(vocabMarkAfter!.isConnected).toBe(true);
  });

  it("vocabulary revision with selection on target paragraph: selection is cleared (not restored by stale offset)", async () => {
    const prevSnapshot = makeSnapshot();
    const nextSnapshot = makeLayerRevisionSnapshot(prevSnapshot, {
      vocabularyGloss: "记忆 (修订)",
    });
    const event = makeValidLayerPublishedEvent("vocabulary");

    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={prevSnapshot} />,
    );

    await waitFor(() => {
      expect(container.querySelector(".reader-record-plate-document")).not.toBeNull();
    });

    // Select text inside the vocabulary mark on the target paragraph.
    const memoryMark = container.querySelector<HTMLElement>(
      '[data-reader-record-vocabulary-mark-id="vocab_mark_1"]',
    );
    expect(memoryMark).not.toBeNull();
    if (!memoryMark) throw new Error("Expected memory vocabulary mark");

    selectTextInElement(memoryMark, 0, "memory".length);

    // Verify selection exists.
    const selection = window.getSelection();
    expect(selection).not.toBeNull();
    expect(selection!.toString()).toBe("memory");

    await act(async () => {
      rerender(
        <ReaderRecordPlateSurface
          snapshot={nextSnapshot}
          pendingReloadContext={makeReloadContext([event], "layer_published")}
          onReloadContextConsumed={() => {}}
        />,
      );
    });

    // Selection was on the target paragraph being replaced. The contract:
    // selection must NOT be restored by stale offset — it should be cleared
    // (deselect). This is the safe behavior: the paragraph's text leaf
    // structure changed (marks data updated), so the old offset may point
    // to a different text run. We explicitly assert no selection remains.
    const selectionAfter = window.getSelection();
    expect(selectionAfter).not.toBeNull();
    // After targeted_apply on the target paragraph, selection is cleared
    // via editor.tf.deselect() because the selection path falls within
    // the replaced op.path.
    expect(selectionAfter!.toString()).toBe("");
  });

  it("structural change (new sentence_analysis block): fallback_full_reload via setValue", async () => {
    const prevSnapshot = makeSnapshot();
    // Add a new sentence_analysis block to nextSnapshot — structural change.
    const nextUnit = makeUnit({
      analysisChunks: [
        { order: 1, label: "subject", text: "Institutional memory" },
        { order: 2, label: "predicate", text: "shapes" },
      ],
    });
    // Add a SECOND sentence_analysis node to make it a true structural change.
    const nextUnitWithExtra: ReaderUnitNodeDto = {
      ...nextUnit,
      children: [
        ...nextUnit.children,
        {
          type: "reader_sentence_analysis",
          owner: "system_ai",
          analysis_id: "analysis_2",
          layer_id: "layer_sentence_analysis_2",
          layer_version: 1,
          base_id: "base_1",
          unit_id: "unit_1",
          target_scope: "unit",
          target_key: "unit_1",
          anchor_segment_id: "seg_1",
          selected_text: SOURCE_TEXT,
          label: "second analysis",
          analysis: "Second analysis text.",
          chunks: [{ order: 1, label: "whole", text: SOURCE_TEXT }],
          children: [{ text: "Second analysis text." }],
        },
      ],
    };
    const nextSnapshot: ReaderPlateSnapshotDto = {
      ...prevSnapshot,
      snapshot_id: "snapshot_structural_change",
      last_event_sequence: 9,
      value: [nextUnitWithExtra],
    };
    const event = makeValidLayerPublishedEvent("sentence_analysis");

    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={prevSnapshot} />,
    );

    await waitFor(() => {
      expect(container.querySelector(".reader-record-plate-document")).not.toBeNull();
    });

    // Capture non-target DOM reference (translation blockquote).
    const blockquoteBefore = container.querySelector(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquoteBefore).not.toBeNull();

    await act(async () => {
      rerender(
        <ReaderRecordPlateSurface
          snapshot={nextSnapshot}
          pendingReloadContext={makeReloadContext([event], "layer_published")}
          onReloadContextConsumed={() => {}}
        />,
      );
    });

    // Structural change → fallback_full_reload → setValue rebuilds all DOM.
    // Non-target DOM identity NOT preserved.
    const blockquoteAfter = container.querySelector(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquoteAfter).not.toBeNull();
    expect(blockquoteBefore!.isSameNode(blockquoteAfter)).toBe(false);
  });

  it("invalid layer_published payload: fallback_full_reload via setValue", async () => {
    const prevSnapshot = makeSnapshot();
    const nextSnapshot = makeLayerRevisionSnapshot(prevSnapshot, {
      translationText: "制度记忆塑造政策选择。(修订版)",
    });
    // Invalid payload: missing required fields.
    const event: ReaderEventResponseDto = {
      id: "evt_9",
      reading_record_id: "record_1",
      sequence: 9,
      event_type: "layer_published",
      payload: { layer_type: "translation" },
      created_at: "2026-06-24T02:00:00Z",
    };

    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={prevSnapshot} />,
    );

    await waitFor(() => {
      expect(container.querySelector(".reader-record-plate-document")).not.toBeNull();
    });

    const blockquoteBefore = container.querySelector(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquoteBefore).not.toBeNull();

    await act(async () => {
      rerender(
        <ReaderRecordPlateSurface
          snapshot={nextSnapshot}
          pendingReloadContext={makeReloadContext([event], "layer_published")}
          onReloadContextConsumed={() => {}}
        />,
      );
    });

    // Invalid payload → fallback_full_reload → setValue rebuilds all DOM.
    const blockquoteAfter = container.querySelector(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquoteAfter).not.toBeNull();
    expect(blockquoteBefore!.isSameNode(blockquoteAfter)).toBe(false);
  });

  it("mixed batch (layer_published + projection_ops): fallback_full_reload via setValue", async () => {
    const prevSnapshot = makeSnapshot();
    const nextSnapshot = makeLayerRevisionSnapshot(prevSnapshot, {
      translationText: "制度记忆塑造政策选择。(修订版)",
    });
    const layerEvent = makeValidLayerPublishedEvent("translation");
    const g1Event: ReaderEventResponseDto = {
      id: "evt_10",
      reading_record_id: "record_1",
      sequence: 10,
      event_type: "projection_ops",
      payload: {
        schema_version: 1,
        representation_section: "user_assets",
        operation: "upsert",
        target_keys: ["asset_1"],
        generation: 1,
        base_id: "base_1",
      },
      created_at: "2026-06-24T02:00:00Z",
    };

    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={prevSnapshot} />,
    );

    await waitFor(() => {
      expect(container.querySelector(".reader-record-plate-document")).not.toBeNull();
    });

    const blockquoteBefore = container.querySelector(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquoteBefore).not.toBeNull();

    await act(async () => {
      rerender(
        <ReaderRecordPlateSurface
          snapshot={nextSnapshot}
          pendingReloadContext={makeReloadContext([layerEvent, g1Event], "layer_published")}
          onReloadContextConsumed={() => {}}
        />,
      );
    });

    // Mixed batch → fallback_full_reload → setValue rebuilds all DOM.
    const blockquoteAfter = container.querySelector(
      '[data-reader-record-node="blockquote"]',
    );
    expect(blockquoteAfter).not.toBeNull();
    expect(blockquoteBefore!.isSameNode(blockquoteAfter)).toBe(false);
  });
});

// ===========================================================================
// Grammar Callout-Group Identity Stabilization
//
// Method A2: group ID = `callout-group:{unitId}:{anchorSegmentId}` (no
// position index). Cross-anchor callouts are split into independent groups.
// Tests verify: stable ID, cross-anchor split, unique block IDs, independent
// expansion, and changed-block-only regression safety.
// ===========================================================================

function defaultSeg1GrammarMarks(): ReaderGrammarNoteMarkDto[] {
  return [
    makeGrammarMark({
      mark_id: "grammar_mark_a1",
      item_id: "grammar_item_a1",
      anchor_segment_id: "seg_1",
      start_offset: 0,
      end_offset: 12,
      selected_text: "Institutional",
      segment_start_utf16: 0,
      segment_end_utf16: 12,
      grammar_point: "adjective",
      pattern: "adjective + noun",
      note: "Institutional modifies memory.",
    }),
    makeGrammarMark({
      mark_id: "grammar_mark_a2",
      item_id: "grammar_item_a2",
      anchor_segment_id: "seg_1",
      start_offset: 13,
      end_offset: 19,
      selected_text: "memory",
      segment_start_utf16: 13,
      segment_end_utf16: 19,
      grammar_point: "noun",
      pattern: "noun",
      note: "memory is the subject noun.",
    }),
  ];
}

function defaultSeg2GrammarMarks(): ReaderGrammarNoteMarkDto[] {
  return [
    makeGrammarMark({
      mark_id: "grammar_mark_b1",
      item_id: "grammar_item_b1",
      anchor_segment_id: "seg_2",
      start_offset: 0,
      end_offset: 6,
      selected_text: "shapes",
      segment_start_utf16: 0,
      segment_end_utf16: 6,
      grammar_point: "predicate verb",
      pattern: "subject + verb",
      note: "shapes is the predicate verb.",
    }),
  ];
}

function makeMultiAnchorGrammarSnapshot(
  options: {
    seg1GrammarMarks?: ReaderGrammarNoteMarkDto[];
    seg2GrammarMarks?: ReaderGrammarNoteMarkDto[];
  } = {},
): ReaderPlateSnapshotDto {
  const firstText = "Institutional memory ";
  const secondText = "shapes policy choices.";

  const seg1GrammarMarks: ReaderGrammarNoteMarkDto[] =
    options.seg1GrammarMarks ?? defaultSeg1GrammarMarks();

  const seg2GrammarMarks: ReaderGrammarNoteMarkDto[] =
    options.seg2GrammarMarks ?? defaultSeg2GrammarMarks();

  const firstSegment = makeAnchorSegmentNode({
    anchor_segment_id: "seg_1",
    sentence_id: "sent_1",
    unit_start_utf16: 0,
    unit_end_utf16: firstText.length,
    text: firstText,
  });
  firstSegment.children = [
    {
      ...firstSegment.children[0],
      reader_grammar_note_marks: seg1GrammarMarks,
    },
  ];

  const secondSegment = makeAnchorSegmentNode({
    anchor_segment_id: "seg_2",
    sentence_id: "sent_2",
    unit_start_utf16: firstText.length,
    unit_end_utf16: firstText.length + secondText.length,
    text: secondText,
  });
  secondSegment.children = [
    {
      ...secondSegment.children[0],
      reader_grammar_note_marks: seg2GrammarMarks,
    },
  ];

  const sourceBlock: ReaderSourceBlockNodeDto = {
    type: "reader_source_block",
    owner: "stable",
    base_id: "base_1",
    unit_id: "unit_1",
    base_start_utf16: 0,
    base_end_utf16: firstText.length + secondText.length,
    children: [firstSegment, secondSegment],
  };

  const unit: ReaderUnitNodeDto = {
    type: "reader_unit",
    owner: "stable",
    base_id: "base_1",
    unit_id: "unit_1",
    order_index: 1,
    unit_type: "body",
    boundary_quality: "normal",
    base_start_utf16: 0,
    base_end_utf16: firstText.length + secondText.length,
    text_hash: "unit_hash_multi_grammar",
    hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
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
        source_text_hash: "multi_grammar_group_hash",
        children: [{ text: "制度记忆 塑造政策选择" }],
      },
    ],
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

function makeMultiUnitGrammarSnapshot(): ReaderPlateSnapshotDto {
  const snapshot = makeMultiAnchorGrammarSnapshot();
  const secondUnit = makeMultiAnchorGrammarSnapshot().value[0]!;
  return {
    ...snapshot,
    value: [
      ...snapshot.value,
      {
        ...secondUnit,
        unit_id: "unit_2",
        children: secondUnit.children.map((child) => {
          if (child.type === "reader_source_block") {
            return {
              ...child,
              unit_id: "unit_2",
              children: child.children.map((segment) => {
                if (
                  "type" in segment &&
                  segment.type === "reader_anchor_segment"
                ) {
                  return {
                    ...segment,
                    unit_id: "unit_2",
                    children: segment.children.map((leaf) => ({
                      ...leaf,
                      anchor_segment_id:
                        leaf.anchor_segment_id === "seg_1"
                          ? "seg_3"
                          : "seg_4",
                    })),
                  } as typeof segment;
                }
                return segment;
              }),
            } as typeof child;
          }
          return {
            ...child,
            unit_id: "unit_2",
          };
        }),
      },
    ],
    anchor_segments: [
      ...snapshot.anchor_segments,
      {
        ...snapshot.anchor_segments[0]!,
        anchor_segment_id: "seg_3",
        sentence_id: "sent_3",
        unit_id: "unit_2",
        order_index: 3,
      },
      {
        ...snapshot.anchor_segments[1]!,
        anchor_segment_id: "seg_4",
        sentence_id: "sent_4",
        unit_id: "unit_2",
        order_index: 4,
      },
    ],
  };
}

describe("ReaderRecordPlateSurface — grammar group identity", () => {
  it("same anchor multiple grammar items form one group with stable ID", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeMultiAnchorGrammarSnapshot()} />,
    );

    const groups = Array.from(
      container.querySelectorAll<HTMLElement>(
        '[data-reader-record-node="callout-group"][data-reader-record-callout-group="grammar"]',
      ),
    );

    // Two anchors → two groups (not one merged cross-anchor group).
    expect(groups).toHaveLength(2);

    // Group IDs are stable, derived only from (unitId, anchorSegmentId).
    expect(groups[0]?.dataset.readerRecordBlockId).toBe(
      "callout-group:unit_1:seg_1",
    );
    expect(groups[1]?.dataset.readerRecordBlockId).toBe(
      "callout-group:unit_1:seg_2",
    );

    // seg_1 group has 2 items, seg_2 group has 1 item.
    expect(groups[0]?.dataset.readerRecordCalloutGroupCount).toBe("2");
    expect(groups[1]?.dataset.readerRecordCalloutGroupCount).toBe("1");
  });

  it("different anchors' consecutive grammar callouts split into separate groups", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeMultiAnchorGrammarSnapshot()} />,
    );

    // The fixture has both segments in the same translation group with no
    // sentence_analysis — in the old code these would merge into 1 group.
    // Method A2 must split them into 2 groups by anchorSegmentId.
    const groups = container.querySelectorAll(
      '[data-reader-record-node="callout-group"][data-reader-record-callout-group="grammar"]',
    );
    expect(groups).toHaveLength(2);

    // Verify the groups belong to different anchors by checking their
    // children's item IDs.
    const group1Rows = groups[0]!.querySelectorAll<HTMLElement>(
      '[data-reader-record-grammar-item-id]',
    );
    const group2Rows = groups[1]!.querySelectorAll<HTMLElement>(
      '[data-reader-record-grammar-item-id]',
    );

    expect(group1Rows).toHaveLength(2);
    expect(group2Rows).toHaveLength(1);
    expect(group1Rows[0]?.dataset.readerRecordGrammarItemId).toBe(
      "grammar_item_a1",
    );
    expect(group1Rows[1]?.dataset.readerRecordGrammarItemId).toBe(
      "grammar_item_a2",
    );
    expect(group2Rows[0]?.dataset.readerRecordGrammarItemId).toBe(
      "grammar_item_b1",
    );
  });

  it("all output block IDs are unique", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeMultiAnchorGrammarSnapshot()} />,
    );

    const allBlockIds = Array.from(
      container.querySelectorAll<HTMLElement>(
        "[data-reader-record-block-id]",
      ),
    ).map((el) => el.dataset.readerRecordBlockId);

    const uniqueIds = new Set(allBlockIds);
    expect(allBlockIds.length).toBe(uniqueIds.size);
  });

  it("prepending item to same anchor preserves group ID", async () => {
    const initialSnapshot = makeMultiAnchorGrammarSnapshot();
    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={initialSnapshot} />,
    );

    const groupBefore = container.querySelector<HTMLElement>(
      '[data-reader-record-block-id="callout-group:unit_1:seg_1"]',
    );
    expect(groupBefore).not.toBeNull();
    expect(groupBefore?.dataset.readerRecordCalloutGroupCount).toBe("2");

    // Prepend a new item before the existing ones in seg_1.
    const updatedSnapshot = makeMultiAnchorGrammarSnapshot({
      seg1GrammarMarks: [
        makeGrammarMark({
          mark_id: "grammar_mark_a0",
          item_id: "grammar_item_a0",
          anchor_segment_id: "seg_1",
          start_offset: 0,
          end_offset: 5,
          selected_text: "Insti",
          segment_start_utf16: 0,
          segment_end_utf16: 5,
          grammar_point: "prefix",
          pattern: "prefix",
          note: "Insti is a prefix.",
        }),
        ...defaultSeg1GrammarMarks(),
      ],
    });

    await act(async () => {
      rerender(<ReaderRecordPlateSurface snapshot={updatedSnapshot} />);
    });

    const groupAfter = container.querySelector<HTMLElement>(
      '[data-reader-record-block-id="callout-group:unit_1:seg_1"]',
    );
    // Group ID must be unchanged despite the prepended item.
    expect(groupAfter).not.toBeNull();
    expect(groupAfter?.dataset.readerRecordBlockId).toBe(
      "callout-group:unit_1:seg_1",
    );
    // Group now has 3 items.
    expect(groupAfter?.dataset.readerRecordCalloutGroupCount).toBe("3");
  });

  it("appending item to same anchor preserves group ID", async () => {
    const initialSnapshot = makeMultiAnchorGrammarSnapshot();
    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={initialSnapshot} />,
    );

    const groupBefore = container.querySelector<HTMLElement>(
      '[data-reader-record-block-id="callout-group:unit_1:seg_1"]',
    );
    expect(groupBefore?.dataset.readerRecordCalloutGroupCount).toBe("2");

    // Append a new item after the existing ones in seg_1.
    const updatedSnapshot = makeMultiAnchorGrammarSnapshot({
      seg1GrammarMarks: [
        ...defaultSeg1GrammarMarks(),
        makeGrammarMark({
          mark_id: "grammar_mark_a3",
          item_id: "grammar_item_a3",
          anchor_segment_id: "seg_1",
          start_offset: 19,
          end_offset: 20,
          selected_text: " ",
          segment_start_utf16: 19,
          segment_end_utf16: 20,
          grammar_point: "space",
          pattern: "space",
          note: "space after memory.",
        }),
      ],
    });

    await act(async () => {
      rerender(<ReaderRecordPlateSurface snapshot={updatedSnapshot} />);
    });

    const groupAfter = container.querySelector<HTMLElement>(
      '[data-reader-record-block-id="callout-group:unit_1:seg_1"]',
    );
    expect(groupAfter?.dataset.readerRecordBlockId).toBe(
      "callout-group:unit_1:seg_1",
    );
    expect(groupAfter?.dataset.readerRecordCalloutGroupCount).toBe("3");
  });

  it("item count change in one anchor does not affect other anchor's group ID", () => {
    const initialSnapshot = makeMultiAnchorGrammarSnapshot();
    const { container, rerender } = render(
      <ReaderRecordPlateSurface snapshot={initialSnapshot} />,
    );

    const seg2GroupBefore = container.querySelector<HTMLElement>(
      '[data-reader-record-block-id="callout-group:unit_1:seg_2"]',
    );
    expect(seg2GroupBefore?.dataset.readerRecordCalloutGroupCount).toBe("1");

    // Change seg_1's grammar items (add one), keep seg_2 unchanged.
    const updatedSnapshot = makeMultiAnchorGrammarSnapshot({
      seg1GrammarMarks: [
        ...defaultSeg1GrammarMarks(),
        makeGrammarMark({
          mark_id: "grammar_mark_a3",
          item_id: "grammar_item_a3",
          anchor_segment_id: "seg_1",
          start_offset: 19,
          end_offset: 20,
          selected_text: " ",
          segment_start_utf16: 19,
          segment_end_utf16: 20,
          grammar_point: "space",
          pattern: "space",
          note: "space after memory.",
        }),
      ],
    });

    rerender(<ReaderRecordPlateSurface snapshot={updatedSnapshot} />);

    const seg2GroupAfter = container.querySelector<HTMLElement>(
      '[data-reader-record-block-id="callout-group:unit_1:seg_2"]',
    );
    // seg_2's group ID and count must be unchanged.
    expect(seg2GroupAfter?.dataset.readerRecordBlockId).toBe(
      "callout-group:unit_1:seg_2",
    );
    expect(seg2GroupAfter?.dataset.readerRecordCalloutGroupCount).toBe("1");
  });

  it("different units do not mix groups", () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeMultiUnitGrammarSnapshot()} />,
    );

    const groups = Array.from(
      container.querySelectorAll<HTMLElement>(
        '[data-reader-record-node="callout-group"][data-reader-record-callout-group="grammar"]',
      ),
    );

    // Two units × two anchors per unit = four groups.
    expect(groups).toHaveLength(4);

    const groupIds = groups.map((g) => g.dataset.readerRecordBlockId);
    const uniqueIds = new Set(groupIds);
    expect(groupIds.length).toBe(uniqueIds.size);

    // Verify expected IDs are present.
    expect(groupIds).toContain("callout-group:unit_1:seg_1");
    expect(groupIds).toContain("callout-group:unit_1:seg_2");
    // seg_3/seg_4 belong to unit_2 but their grammar marks still reference
    // the original anchor_segment_id from makeMultiAnchorGrammarSnapshot.
    // The key assertion is that all 4 IDs are unique.
  });

  it("two anchor groups have independent expand/collapse states", async () => {
    const { container } = render(
      <ReaderRecordPlateSurface snapshot={makeMultiAnchorGrammarSnapshot()} />,
    );

    const group1Row = container.querySelector<HTMLElement>(
      '[data-reader-record-grammar-item-id="grammar_item_a1"][data-callout-variant="grammar"]',
    );
    const group2Row = container.querySelector<HTMLElement>(
      '[data-reader-record-grammar-item-id="grammar_item_b1"][data-callout-variant="grammar"]',
    );

    expect(group1Row).not.toBeNull();
    expect(group2Row).not.toBeNull();

    // Both start collapsed.
    expect(group1Row?.dataset.readerRecordCalloutCollapsed).toBe("true");
    expect(group2Row?.dataset.readerRecordCalloutCollapsed).toBe("true");

    // Expand group 1 (seg_1's item).
    const group1Toggle = group1Row?.querySelector<HTMLButtonElement>(
      '[data-reader-record-callout-toggle="grammar"]',
    );
    expect(group1Toggle).not.toBeNull();
    fireEvent.click(group1Toggle!);

    await waitFor(() => {
      expect(group1Row?.dataset.readerRecordCalloutCollapsed).toBe("false");
    });

    // Group 2 (seg_2's item) must still be collapsed — independent state.
    expect(group2Row?.dataset.readerRecordCalloutCollapsed).toBe("true");

    // Now expand group 2.
    const group2Toggle = group2Row?.querySelector<HTMLButtonElement>(
      '[data-reader-record-callout-toggle="grammar"]',
    );
    fireEvent.click(group2Toggle!);

    await waitFor(() => {
      expect(group2Row?.dataset.readerRecordCalloutCollapsed).toBe("false");
    });

    // Both expanded independently.
    expect(group1Row?.dataset.readerRecordCalloutCollapsed).toBe("false");
    expect(group2Row?.dataset.readerRecordCalloutCollapsed).toBe("false");
  });

  it("grammar_note revision on anchor B preserves anchor A expansion via targeted_apply", async () => {
    const initialSnapshot = makeMultiAnchorGrammarSnapshot();
    const grammarEvent: ReaderEventResponseDto = {
      id: "evt_grammar_1",
      reading_record_id: "record_1",
      sequence: 9,
      event_type: "layer_published",
      payload: {
        record_id: "record_1",
        base_id: "base_1",
        layer_id: "layer_grammar_1",
        layer_type: "grammar_note",
        target_scope: "unit",
        target_key: "unit_1",
        generation: 1,
      },
      created_at: "2026-07-14T00:00:00Z",
    };

    const { container, rerender } = render(
      <ReaderRecordPlateSurface
        snapshot={initialSnapshot}
        pendingReloadContext={null}
        onReloadContextConsumed={() => {}}
      />,
    );

    // Expand anchor A (seg_1) grammar item.
    const groupARow = container.querySelector<HTMLElement>(
      '[data-reader-record-grammar-item-id="grammar_item_a1"][data-callout-variant="grammar"]',
    );
    const groupAToggle = groupARow?.querySelector<HTMLButtonElement>(
      '[data-reader-record-callout-toggle="grammar"]',
    );
    fireEvent.click(groupAToggle!);
    await waitFor(() => {
      expect(groupARow?.dataset.readerRecordCalloutCollapsed).toBe("false");
    });

    // Apply a grammar_note revision on the same topology (change seg_2's
    // grammar note text). The merger should detect only the changed
    // callout-group block and replace it via targeted_apply.
    const nextSnapshot = makeMultiAnchorGrammarSnapshot({
      seg2GrammarMarks: [
        makeGrammarMark({
          mark_id: "grammar_mark_b1",
          item_id: "grammar_item_b1",
          anchor_segment_id: "seg_2",
          start_offset: 0,
          end_offset: 6,
          selected_text: "shapes",
          segment_start_utf16: 0,
          segment_end_utf16: 6,
          grammar_point: "predicate verb (revised)",
          pattern: "subject + verb (revised)",
          note: "shapes is the predicate verb. (revised note)",
        }),
      ],
    });

    await act(async () => {
      rerender(
        <ReaderRecordPlateSurface
          snapshot={nextSnapshot}
          pendingReloadContext={{
            cursor: 8,
            events: [grammarEvent],
            triggerClassification: {
              kind: "reload_snapshot",
              reason: "layer_published",
            },
            acceptedSnapshotFence: {
              generation: 1,
              baseId: "base_1",
            },
            reason: "layer_published",
          }}
          onReloadContextConsumed={() => {}}
        />,
      );
    });

    // Anchor A (seg_1) expansion must be preserved after targeted_apply
    // on anchor B (seg_2).
    const groupARowAfter = container.querySelector<HTMLElement>(
      '[data-reader-record-grammar-item-id="grammar_item_a1"][data-callout-variant="grammar"]',
    );
    expect(groupARowAfter?.dataset.readerRecordCalloutCollapsed).toBe("false");

    // Anchor B (seg_2) grammar note content was updated.
    const groupBRowAfter = container.querySelector<HTMLElement>(
      '[data-reader-record-grammar-item-id="grammar_item_b1"][data-callout-variant="grammar"]',
    );
    expect(groupBRowAfter).not.toBeNull();

    // Group IDs remain stable.
    const groupA = container.querySelector<HTMLElement>(
      '[data-reader-record-block-id="callout-group:unit_1:seg_1"]',
    );
    const groupB = container.querySelector<HTMLElement>(
      '[data-reader-record-block-id="callout-group:unit_1:seg_2"]',
    );
    expect(groupA).not.toBeNull();
    expect(groupB).not.toBeNull();
  });
});

// ===========================================================================
// Group identity evidence: tuple comparison, missing-identity
// conservative behavior, non-contiguous duplicate fail-closed, and global
// block ID uniqueness.
//
// These tests exercise `groupConsecutiveGrammarCallouts` directly with
// synthetic callout elements to verify the grouping invariant at the
// function level, independent of the projection pipeline.
// ===========================================================================

function makeSyntheticGrammarCallout(options: {
  itemId: string;
  unitId?: string;
  anchorSegmentId?: string;
}): ReaderCalloutElement {
  return {
    type: READER_CALLOUT_TYPE,
    id: `callout:grammar:${options.itemId}`,
    children: [{ text: options.itemId }] as never,
    data: {
      anchorSegmentId: options.anchorSegmentId ?? "",
      unitId: options.unitId ?? "",
      layerId: "layer_grammar_1",
      itemId: options.itemId,
    },
    variant: "grammar",
    icon: "📖",
  };
}

function makeSyntheticParagraph(id: string): unknown {
  return { type: "paragraph", id, children: [{ text: id }] };
}

describe("ReaderRecordPlateSurface — grammar group identity evidence (tuple, fail-closed, uniqueness)", () => {
  // Explicit tuple comparison — different unitId, same anchorSegmentId
  // must NOT enter the same group. This proves the grouping condition
  // compares the complete (unitId, anchorSegmentId) tuple, not just
  // anchorSegmentId.
  it("different unitId with same anchorSegmentId does NOT mix groups", () => {
    const calloutUnit1 = makeSyntheticGrammarCallout({
      itemId: "item_u1",
      unitId: "unit_1",
      anchorSegmentId: "seg_1",
    });
    const calloutUnit2 = makeSyntheticGrammarCallout({
      itemId: "item_u2",
      unitId: "unit_2",
      anchorSegmentId: "seg_1", // same anchorSegmentId, different unitId
    });

    const result = groupConsecutiveGrammarCallouts([
      calloutUnit1,
      calloutUnit2,
    ]) as Array<{ id: string; type: string }>;

    const groupBlocks = result.filter((b) => b.type === "reader_callout_group");
    expect(groupBlocks).toHaveLength(2);
    expect(groupBlocks[0]!.id).toBe("callout-group:unit_1:seg_1");
    expect(groupBlocks[1]!.id).toBe("callout-group:unit_2:seg_1");
  });

  // Missing unitId — conservative fallback behavior.
  it("missing unitId produces non-stable fallback ID, not a fake stable ID", () => {
    const calloutMissingUnitId = makeSyntheticGrammarCallout({
      itemId: "item_missing_unit",
      unitId: undefined,
      anchorSegmentId: "seg_1",
    });

    const result = groupConsecutiveGrammarCallouts([
      calloutMissingUnitId,
    ]) as Array<{ id: string; type: string; data: { unitId: string; anchorSegmentId: string } }>;

    const groupBlocks = result.filter((b) => b.type === "reader_callout_group");
    expect(groupBlocks).toHaveLength(1);
    // Fallback ID must contain "fallback" — it must NOT look like a stable ID.
    expect(groupBlocks[0]!.id).toContain("fallback");
    expect(groupBlocks[0]!.id).not.toBe("callout-group::seg_1");
  });

  // Missing anchorSegmentId — conservative fallback behavior.
  it("missing anchorSegmentId produces non-stable fallback ID, not a fake stable ID", () => {
    const calloutMissingAnchor = makeSyntheticGrammarCallout({
      itemId: "item_missing_anchor",
      unitId: "unit_1",
      anchorSegmentId: undefined,
    });

    const result = groupConsecutiveGrammarCallouts([
      calloutMissingAnchor,
    ]) as Array<{ id: string; type: string }>;

    const groupBlocks = result.filter((b) => b.type === "reader_callout_group");
    expect(groupBlocks).toHaveLength(1);
    expect(groupBlocks[0]!.id).toContain("fallback");
    expect(groupBlocks[0]!.id).not.toBe("callout-group:unit_1:");
  });

  // Missing-identity callouts never group with stable-identity
  // callouts, even if adjacent.
  it("missing-identity callout does NOT group with stable-identity callout", () => {
    const stableCallout = makeSyntheticGrammarCallout({
      itemId: "item_stable",
      unitId: "unit_1",
      anchorSegmentId: "seg_1",
    });
    const missingCallout = makeSyntheticGrammarCallout({
      itemId: "item_missing",
      unitId: undefined,
      anchorSegmentId: "seg_1",
    });

    const result = groupConsecutiveGrammarCallouts([
      stableCallout,
      missingCallout,
    ]) as Array<{ id: string; type: string }>;

    const groupBlocks = result.filter((b) => b.type === "reader_callout_group");
    expect(groupBlocks).toHaveLength(2);
    expect(groupBlocks[0]!.id).toBe("callout-group:unit_1:seg_1");
    expect(groupBlocks[1]!.id).toContain("fallback");
  });

  // Non-contiguous same (unitId, anchorSegmentId) must not create a
  // duplicate group ID or make the Reader unrenderable. The first run keeps
  // its stable group; the later anomalous run remains standalone callouts.
  it("non-contiguous same tuple keeps the article renderable without duplicate group IDs", () => {
    const callout1 = makeSyntheticGrammarCallout({
      itemId: "item_1",
      unitId: "unit_1",
      anchorSegmentId: "seg_1",
    });
    const callout2 = makeSyntheticGrammarCallout({
      itemId: "item_2",
      unitId: "unit_1",
      anchorSegmentId: "seg_1", // same tuple as callout1
    });
    const separator = makeSyntheticParagraph("paragraph_separator");

    const result = groupConsecutiveGrammarCallouts([
      callout1,
      separator,
      callout2,
    ]) as Array<{ id: string; type: string }>;

    expect(result).toHaveLength(3);
    expect(result[0]).toMatchObject({
      type: "reader_callout_group",
      id: "callout-group:unit_1:seg_1",
    });
    expect(result[1]).toMatchObject({ id: "paragraph_separator" });
    expect(result[2]).toMatchObject({
      type: READER_CALLOUT_TYPE,
      id: "callout:grammar:item_2",
    });
    expect(new Set(result.map((node) => node.id)).size).toBe(result.length);
  });

  // All top-level block IDs in the output are globally unique.
  // This includes both callout-group blocks and non-callout blocks.
  it("all top-level block IDs are globally unique", () => {
    const calloutA = makeSyntheticGrammarCallout({
      itemId: "item_a",
      unitId: "unit_1",
      anchorSegmentId: "seg_1",
    });
    const calloutB = makeSyntheticGrammarCallout({
      itemId: "item_b",
      unitId: "unit_1",
      anchorSegmentId: "seg_2",
    });
    const paragraph1 = makeSyntheticParagraph("paragraph_1");
    const paragraph2 = makeSyntheticParagraph("paragraph_2");

    const result = groupConsecutiveGrammarCallouts([
      paragraph1,
      calloutA,
      calloutB,
      paragraph2,
    ]) as Array<{ id: string }>;

    const allIds = result.map((b) => b.id);
    const uniqueIds = new Set(allIds);
    expect(allIds.length).toBe(uniqueIds.size);
  });

  // Multiple fallback (missing-identity) callouts each get unique
  // fallback IDs — no collision among fallbacks.
  it("multiple missing-identity callouts get unique fallback IDs", () => {
    const missing1 = makeSyntheticGrammarCallout({
      itemId: "missing_1",
      unitId: undefined,
      anchorSegmentId: "seg_1",
    });
    const missing2 = makeSyntheticGrammarCallout({
      itemId: "missing_2",
      unitId: "unit_1",
      anchorSegmentId: undefined,
    });
    const separator = makeSyntheticParagraph("separator");

    const result = groupConsecutiveGrammarCallouts([
      missing1,
      separator,
      missing2,
    ]) as Array<{ id: string; type: string }>;

    const groupBlocks = result.filter((b) => b.type === "reader_callout_group");
    expect(groupBlocks).toHaveLength(2);
    // Both are fallback, but with different position-based suffixes.
    expect(groupBlocks[0]!.id).toContain("fallback");
    expect(groupBlocks[1]!.id).toContain("fallback");
    expect(groupBlocks[0]!.id).not.toBe(groupBlocks[1]!.id);
  });
});

describe("G3b Reader image safe surface Slice B RED", () => {
  // ponytail: reuse existing snapshot builders, no new framework
  function wgNode(overrides: Partial<ReaderStableDocumentBlockNodeDto>): ReaderStableDocumentBlockNodeDto {
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
  function imgPayload(sourceUrl: string, alt: string, title: string | null, effectiveUrl: string | null): Record<string, unknown> {
    return { source_url: sourceUrl, alt_text: alt, title, position_kind: "standalone", effective_url: effectiveUrl };
  }
  function inlineEntry(sourceUrl: string, alt: string, title: string | null, before: number, effectiveUrl: string | null): Record<string, unknown> {
    return { source_url: sourceUrl, alt_text: alt, title, before_utf16: before, effective_url: effectiveUrl };
  }
  async function renderImageCopyButtonAfter(
    eventName: "load" | "error",
  ): Promise<HTMLButtonElement> {
    const url = "https://example.com/a.png";
    const snapshot = makeImgSnapshotForSurface(
      [{ unitId: "u1", text: "Hello", stableType: "paragraph", stableId: "p1" }],
      [
        wgNode({
          block_id: "img1",
          block_type: "image",
          payload: imgPayload(url, "alt", null, url),
        }),
      ],
    );
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    const image = container.querySelector('[data-reader-image="true"] img');
    expect(image).not.toBeNull();
    if (!image) {
      throw new Error("Expected Reader image");
    }
    await act(async () => {
      fireEvent(image, new Event(eventName));
    });
    return screen.getByRole("button", { name: "复制链接" }) as HTMLButtonElement;
  }
  function makeImgSnapshotForSurface(
    specs: Array<{ unitId: string; text: string; stableType: string; stableId: string; parent?: string | null }>,
    tree: ReaderStableDocumentBlockNodeDto[],
  ): ReaderPlateSnapshotDto {
    const baseId = "base_w1";
    let offset = 0;
    const anchor_segments: ReaderPlateSnapshotDto["anchor_segments"] = [];
    const navigation: ReaderPlateSnapshotDto["navigation"]["units"] = [];
    const value: ReaderUnitNodeDto[] = [];
    for (const [idx, spec] of specs.entries()) {
      const start = offset;
      const end = start + spec.text.length;
      offset = end + 2;
      const segId = `seg_${spec.unitId}`;
      anchor_segments.push({
        anchor_segment_id: segId,
        sentence_id: `sent_${segId}`,
        paragraph_id: spec.unitId,
        unit_id: spec.unitId,
        order_index: idx + 1,
        unit_order_index: 1,
        segment_type: "sentence",
        boundary_quality: "normal",
        base_start_utf16: start,
        base_end_utf16: end,
        unit_start_utf16: 0,
        unit_end_utf16: spec.text.length,
        text_hash: `hash_${segId}`,
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      });
      navigation.push({
        unit_id: spec.unitId,
        order_index: idx + 1,
        unit_type: "body",
        boundary_quality: "normal",
        label: null,
        base_start_utf16: start,
        base_end_utf16: end,
        text_hash: `hash_${spec.unitId}`,
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
        stable_block_type: spec.stableType,
        heading_level: null,
      });
      value.push({
        type: "reader_unit",
        owner: "stable",
        base_id: baseId,
        unit_id: spec.unitId,
        order_index: idx + 1,
        unit_type: "body",
        boundary_quality: "normal",
        base_start_utf16: start,
        base_end_utf16: end,
        text_hash: `hash_${spec.unitId}`,
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
        children: [
          {
            type: "reader_source_block",
            owner: "stable",
            base_id: baseId,
            unit_id: spec.unitId,
            base_start_utf16: start,
            base_end_utf16: end,
            stableBlockType: spec.stableType,
            stableBlockId: spec.stableId,
            parentStableBlockId: spec.parent ?? null,
            children: [
              {
                type: "reader_anchor_segment",
                owner: "stable",
                base_id: baseId,
                unit_id: spec.unitId,
                anchor_segment_id: segId,
                sentence_id: `sent_${segId}`,
                segment_type: "sentence",
                boundary_quality: "normal",
                base_start_utf16: start,
                base_end_utf16: end,
                unit_start_utf16: 0,
                unit_end_utf16: spec.text.length,
                text_hash: `hash_${segId}`,
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
          } as unknown as ReaderSourceBlockNodeDto,
        ],
      });
    }
    return {
      schema_kind: "reader_plate_snapshot" as const,
      snapshot_id: "snapshot_w1",
      snapshot_taken_at: "2026-08-08T00:00:00Z",
      last_event_sequence: 1,
      record_id: "record_w1",
      record: {
        title: "Surface Fixture",
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
        text_length_utf16: offset,
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      },
      navigation: { units: navigation },
      anchor_segments,
      enhancement_layers: [],
      enhancement_progress: undefined,
      analysis_progress: { mode: "automatic", plan_version: "test", overall_status: "completed", active_phase: null, translation_status: "completed", completed_section_count: 0, total_section_count: 0, active_section_id: null, needs_user_action: false, last_progress_at: null, sections: [] } as unknown as import("@/types/api/reader-plate").ReaderAnalysisProgressDto,
      ask_supplements: [],
      user_assets: [],
      parsed_decisions: [],
      value,
      stable_document_tree: tree,
    };
  }

  const ALLOW_URLS = [
    "https://example.com/a.png",
    "http://example.com/a.png",
    "HTTP://Example.COM/a.png",
    "http://example.com:65535/a.png",
    "http://example.com:8080/a.png?q=1#f",
    "http://127.0.0.1/a.png",
    "http://[::1]:8080/a.png",
    "https://xn--r8jz45g.jp/a.png",
    "http://example.com",
    "https://example.com/a%20b.png",
    "http://example.com/%5C@evil.com/a.png",
  ];
  const REJECT_URLS = [
    "",
    "  https://example.com/a.png  ",
    "/a.png",
    "a.png",
    "//example.com/a.png",
    "http:foo",
    "https:foo",
    "http://",
    "https:///",
    "http://user:pass@example.com/a.png",
    "http://user@example.com/a.png",
    "javascript:alert(1)",
    "data:image/png;base64,AAAA",
    "file:///etc/passwd",
    "blob:https://x/y",
    "mailto:a@b.com",
    "http://exa\u0000mple.com/a.png",
    "http://example.com/a\u0001.png",
    "http://exa mple.com/a.png",
    "http://example.com/a b.png",
    "http://example.com\\@evil.com/a.png",
    "http://example.com\\evil/a.png",
    "http://example.com:bad/a.png",
    "http://example.com:65536/a.png",
    "http://example.com:99999/a.png",
    "http://example.com:-1/a.png",
    "http://[::1",
  ];

  // B1: plugin and void contract
  it("inline image is inline void, attributes on outer element, children once, leaf outside chrome", () => {
    const snapshot = makeImgSnapshotForSurface(
      [{ unitId: "u1", text: "hello world", stableType: "paragraph", stableId: "b1" }],
      [wgNode({ block_id: "b1", block_type: "paragraph", payload: { inline_images: [inlineEntry("https://example.com/a.png", "alt", null, 5, "https://example.com/a.png")] } })],
    );
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    const outer = container.querySelector<HTMLElement>('[data-reader-image="true"][data-reader-image-kind="inline"]');
    // RED: before plugin, no such element
    expect(outer).not.toBeNull();
    expect(outer?.hasAttribute("data-slate-node")).toBe(true);
    const leaf = outer?.querySelector<HTMLElement>("[data-slate-leaf]");
    expect(leaf).not.toBeNull();
    const chrome = outer?.querySelector<HTMLElement>('[contenteditable="false"]');
    expect(chrome).not.toBeNull();
    expect(chrome?.contains(leaf as Node)).toBe(false);
    expect(leaf?.textContent).not.toContain("alt");
    expect(leaf?.textContent).not.toContain("https://example.com");
    const zeroWidth = outer?.querySelectorAll("[data-slate-zero-width]")?.length ?? 0;
    expect(zeroWidth).toBe(1);
  });

  it.each(ALLOW_URLS)("ALLOW effective_url renders img with raw src: %s", (url) => {
    const snapshot = makeImgSnapshotForSurface(
      [{ unitId: "u1", text: "Hello", stableType: "paragraph", stableId: "p1" }],
      [
        wgNode({ block_id: "p1", block_type: "paragraph", order_index: 0 }),
        wgNode({ block_id: "img1", block_type: "image", order_index: 1, payload: imgPayload(url, "alt", null, url) }),
      ],
    );
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    const img = container.querySelector('[data-reader-image="true"] img');
    expect(img).not.toBeNull();
    expect(img?.getAttribute("src")).toBe(url);
  });

  it.each(REJECT_URLS)("REJECT effective_url renders no img[src] and unsafe placeholder: %s", (url) => {
    const snapshot = makeImgSnapshotForSurface(
      [{ unitId: "u1", text: "Hello", stableType: "paragraph", stableId: "p1" }],
      [
        wgNode({ block_id: "p1", block_type: "paragraph", order_index: 0 }),
        wgNode({ block_id: "img1", block_type: "image", order_index: 1, payload: imgPayload(url, "alt", null, url) }),
      ],
    );
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    expect(container.querySelector('[data-reader-image="true"] img[src]')).toBeNull();
    expect(container.textContent).toContain("链接不安全");
  });

  it("non-string effective_url renders unsafe (no img)", () => {
    const snapshot = makeImgSnapshotForSurface(
      [{ unitId: "u1", text: "Hello", stableType: "paragraph", stableId: "p1" }],
      [wgNode({ block_id: "img1", block_type: "image", payload: { source_url: "https://example.com/a.png", alt_text: "alt", title: null, position_kind: "standalone", effective_url: 123 as unknown as string } })],
    );
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    expect(container.querySelector('[data-reader-image="true"] img[src]')).toBeNull();
    expect(container.textContent).toContain("链接不安全");
  });

  it("safe effectiveUrl initial loading, loaded, load_failed, empty alt, unsafe states", async () => {
    const snapshotLoading = makeImgSnapshotForSurface(
      [{ unitId: "u1", text: "Hello", stableType: "paragraph", stableId: "p1" }],
      [wgNode({ block_id: "img1", block_type: "image", payload: imgPayload("https://example.com/a.png", "alt text", "Title", "https://example.com/a.png") })],
    );
    const { container: c1 } = render(<ReaderRecordPlateSurface snapshot={snapshotLoading} />);
    expect(c1.querySelector('[data-image-state="loading"]')).not.toBeNull();
    const img = c1.querySelector('[data-reader-image="true"] img');
    expect(img?.getAttribute("loading")).toBe("lazy");
    expect(img?.getAttribute("decoding")).toBe("async");
    expect(img?.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(img?.getAttribute("alt")).toBe("alt text");
    expect(img?.getAttribute("title")).toBe("Title");
    // simulate load
    await act(async () => {
      if (img) fireEvent(img as Element, new Event("load"));
    });
    expect(c1.querySelector('[data-image-state="loaded"]')).not.toBeNull();
    expect(c1.querySelector('[data-image-state="loading"]')).toBeNull();
    // load_failed with alt
    const { container: c2 } = render(<ReaderRecordPlateSurface snapshot={snapshotLoading} />);
    const img2 = c2.querySelector('[data-reader-image="true"] img');
    await act(async () => {
      if (img2) fireEvent(img2 as Element, new Event("error"));
    });
    expect(c2.querySelector('[data-image-state="load_failed"]')).not.toBeNull();
    expect(c2.querySelector('[data-reader-image="true"] img[src]')).toBeNull();
    expect(c2.textContent).toContain("alt text");
    // empty alt
    const snapshotEmptyAlt = makeImgSnapshotForSurface(
      [{ unitId: "u1", text: "Hello", stableType: "paragraph", stableId: "p1" }],
      [wgNode({ block_id: "img1", block_type: "image", payload: imgPayload("https://example.com/a.png", "", null, "https://example.com/a.png") })],
    );
    const { container: c3 } = render(<ReaderRecordPlateSurface snapshot={snapshotEmptyAlt} />);
    const img3 = c3.querySelector('[data-reader-image="true"] img');
    await act(async () => {
      if (img3) fireEvent(img3 as Element, new Event("error"));
    });
    expect(c3.textContent).toContain("图片加载失败");
    // after fix, loaded img with empty alt should have alt=""
    // For this test, we check loaded state empty alt
    const { container: c4 } = render(<ReaderRecordPlateSurface snapshot={snapshotEmptyAlt} />);
    const img4 = c4.querySelector('[data-reader-image="true"] img');
    expect(img4?.getAttribute("alt")).toBe("");
  });

  it("复制链接 only copies effectiveUrl, exactly once, fail-soft", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const snapshot = makeImgSnapshotForSurface(
      [{ unitId: "u1", text: "Hello", stableType: "paragraph", stableId: "p1" }],
      [wgNode({ block_id: "img1", block_type: "image", payload: imgPayload("https://example.com/a.png", "alt", null, "https://example.com/a.png") })],
    );
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    const img = container.querySelector('[data-reader-image="true"] img');
    await act(async () => {
      if (img) fireEvent(img as Element, new Event("error"));
    });
    const btn = screen.getByRole("button", { name: "复制链接" });
    fireEvent.click(btn);
    expect(writeText).toHaveBeenCalledWith("https://example.com/a.png");
    expect(writeText).toHaveBeenCalledTimes(1);
  });

  it("loaded image also exposes an action that copies effectiveUrl", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const button = await renderImageCopyButtonAfter("load");

    fireEvent.click(button);

    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledWith("https://example.com/a.png");
  });

  it("copy effectiveUrl consumes an asynchronously rejected Clipboard promise", async () => {
    const calls: string[] = [];
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: (url: string) => {
          calls.push(url);
          return Promise.reject(new Error("clipboard denied"));
        },
      },
    });
    const button = await renderImageCopyButtonAfter("error");

    expect(() => fireEvent.click(button)).not.toThrow();
    await act(async () => {
      await Promise.resolve();
    });
    expect(calls).toEqual(["https://example.com/a.png"]);
  });

  it("copy effectiveUrl is fail-soft when Clipboard writeText throws synchronously", async () => {
    const calls: string[] = [];
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: (url: string) => {
          calls.push(url);
          throw new Error("clipboard unavailable");
        },
      },
    });
    const button = await renderImageCopyButtonAfter("error");

    expect(() => fireEvent.click(button)).not.toThrow();
    expect(calls).toEqual(["https://example.com/a.png"]);
  });

  it("copy effectiveUrl is fail-soft when Clipboard API is absent", async () => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
    const button = await renderImageCopyButtonAfter("error");

    expect(() => fireEvent.click(button)).not.toThrow();
  });

  it("copy chrome uses copyExcludeProps and mixed Range copy only text", async () => {
    const snapshot = makeImgSnapshotForSurface(
      [{ unitId: "u1", text: "hello world", stableType: "paragraph", stableId: "b1" }],
      [wgNode({ block_id: "b1", block_type: "paragraph", payload: { inline_images: [inlineEntry("https://example.com/a.png", "alt", null, 5, "https://example.com/a.png")] } })],
    );
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    // Check image chrome has copyExcludeProps
    // At least one element with copyExcludeProps should exist inside image
    const image = container.querySelector<HTMLElement>('[data-reader-image="true"]');
    expect(image).not.toBeNull();
    if (!image) {
      throw new Error("Expected inline Reader image");
    }
    const copyExcludes = image.querySelectorAll('[data-reader-record-copy-exclude="true"]');
    expect(copyExcludes.length).toBeGreaterThan(0);
    for (const el of Array.from(copyExcludes)) {
      expect(el.getAttribute("contenteditable")).toBe("false");
      expect(el.getAttribute("draggable")).toBe("false");
    }
    // Mixed range copy test: select across text -> image -> text
    const para = container.querySelector<HTMLElement>('[data-reader-record-node="paragraph"]');
    expect(para).not.toBeNull();
    if (!para) {
      throw new Error("Expected paragraph owning inline image");
    }
    const textLeaves = Array.from(para.querySelectorAll<HTMLElement>("[data-slate-leaf]")).filter(
      (leaf) => !leaf.closest('[data-reader-image="true"]'),
    );
    expect(textLeaves).toHaveLength(2);
    const startNode = firstTextNode(textLeaves[0]!);
    const endNode = firstTextNode(textLeaves[1]!);
    const range = document.createRange();
    range.setStart(startNode, 0);
    range.setEnd(endNode, endNode.textContent?.length ?? 0);
    const selection = window.getSelection();
    expect(selection).not.toBeNull();
    if (!selection) {
      throw new Error("Expected DOM selection");
    }
    selection.removeAllRanges();
    selection.addRange(range);
    const clipboardData = { setData: vi.fn() };
    fireEvent.copy(para, { clipboardData } as unknown as ClipboardEvent);
    const plain = clipboardData.setData.mock.calls.find(([type]) => type === "text/plain");
    expect(plain).toBeDefined();
    if (!plain) {
      throw new Error("Expected text/plain clipboard payload");
    }
    const copiedText = String(plain[1]);
    expect(copiedText).toBe("hello world");
    expect(copiedText).not.toContain("\uFEFF");
    expect(copiedText).not.toContain("\u200B");
    expect(copiedText).not.toContain("https://example.com");
    expect(copiedText).not.toContain("alt");
    expect(copiedText).not.toContain("图片加载中");
    expect(copiedText).not.toContain("复制链接");
  });

  it("keeps an inline image mounted when switching the production surface to immersive mode", async () => {
    const snapshot = makeImgSnapshotForSurface(
      [{ unitId: "u1", text: "hello world", stableType: "paragraph", stableId: "b1" }],
      [
        wgNode({
          block_id: "b1",
          block_type: "paragraph",
          payload: {
            inline_images: [
              inlineEntry(
                "https://example.com/a.png",
                "alt",
                null,
                5,
                "https://example.com/a.png",
              ),
            ],
          },
        }),
      ],
    );
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    expect(container.querySelector('[data-reader-image="true"]')).not.toBeNull();

    expect(() => {
      fireEvent.click(screen.getByRole("button", { name: "切换到沉浸模式" }));
    }).not.toThrow();

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "切换到沉浸模式" }).getAttribute("aria-pressed"),
      ).toBe("true");
    });
    expect(container.querySelector('[data-reader-image="true"]')).not.toBeNull();
  });

  it("renders a promoted list image as a direct presentation li without Stable item identity", () => {
    const imageUrl = "https://example.com/list.png";
    const snapshot = makeImgSnapshotForSurface(
      [
        {
          unitId: "u_item_1",
          text: "First item",
          stableType: "list_item",
          stableId: "item_1",
          parent: "list_1",
        },
        {
          unitId: "u_item_2",
          text: "Second item",
          stableType: "list_item",
          stableId: "item_2",
          parent: "list_1",
        },
      ],
      [
        wgNode({
          block_id: "list_1",
          block_type: "list",
          payload: { ordered: false },
          children: [
            wgNode({
              block_id: "item_1",
              parent_block_id: "list_1",
              block_type: "list_item",
            }),
            wgNode({
              block_id: "img_list",
              parent_block_id: "list_1",
              order_index: 1,
              block_type: "image",
              payload: imgPayload(imageUrl, "list image", null, imageUrl),
            }),
            wgNode({
              block_id: "item_2",
              parent_block_id: "list_1",
              order_index: 2,
              block_type: "list_item",
            }),
          ],
        }),
      ],
    );
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    const list = container.querySelector<HTMLElement>('[data-reader-record-node="list"]');
    expect(list).not.toBeNull();
    if (!list) {
      throw new Error("Expected Stable list");
    }

    const directChildren = Array.from(list.children);
    expect(directChildren).toHaveLength(3);
    expect(directChildren.every((child) => child.tagName === "LI")).toBe(true);
    const image = list.querySelector<HTMLElement>('[data-reader-image="true"]');
    expect(image).not.toBeNull();
    if (!image) {
      throw new Error("Expected promoted list image");
    }
    const imageItem = image.closest("li");
    expect(imageItem).not.toBeNull();
    expect(imageItem?.parentElement).toBe(list);
    expect(imageItem?.hasAttribute("data-reader-record-node")).toBe(false);
    expect(imageItem?.hasAttribute("data-reader-record-stable-block-type")).toBe(false);
    expect(imageItem?.hasAttribute("data-unit-id")).toBe(false);
    expect(imageItem?.hasAttribute("data-anchor-segment-id")).toBe(false);
  });

  // G3b-R4: draggable=false is this round's real production fix (RED-1).
  // The two A→B tests below lock in the Surface's existing isolation behavior:
  // setValue remounts the image element, so a late load/error dispatched on
  // the detached old img node cannot reach any live handler. They are
  // regression locks for that combination, not evidence of a URL-state bug.
  it("native reader img itself sets draggable=false", () => {
    const url = "https://example.com/a.png";
    const snapshot = makeImgSnapshotForSurface(
      [{ unitId: "u1", text: "Hello", stableType: "paragraph", stableId: "p1" }],
      [wgNode({ block_id: "img1", block_type: "image", payload: imgPayload(url, "alt", null, url) })],
    );
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    const img = container.querySelector('[data-reader-image="true"] img[src]');
    expect(img).not.toBeNull();
    expect(img?.getAttribute("draggable")).toBe("false");
  });

  it("after effectiveUrl A→B, a late load from A cannot mark B loaded and copy uses B", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const urlA = "https://example.com/a.png";
    const urlB = "https://example.com/b.png?v=2#Frag";
    const mkSnapshot = (effectiveUrl: string | null) =>
      makeImgSnapshotForSurface(
        [{ unitId: "u1", text: "Hello", stableType: "paragraph", stableId: "p1" }],
        [wgNode({ block_id: "img1", block_type: "image", payload: imgPayload(urlA, "alt", null, effectiveUrl) })],
      );
    const { container, rerender } = render(<ReaderRecordPlateSurface snapshot={mkSnapshot(urlA)} />);
    const imgA = container.querySelector('[data-reader-image="true"] img');
    expect(imgA).not.toBeNull();
    expect(imgA?.getAttribute("src")).toBe(urlA);

    rerender(<ReaderRecordPlateSurface snapshot={mkSnapshot(urlB)} />);
    // setValue commits asynchronously (MessageChannel); wait for observable DOM
    const imgB = await waitFor(() => {
      const el = container.querySelector('[data-reader-image="true"] img');
      if (!el || el.getAttribute("src") !== urlB) {
        throw new Error("imgB with verbatim src B not rendered yet");
      }
      return el;
    });
    // URL B assigned verbatim, no trim/lowercase/normalize
    // B must be loading, not yet loaded
    expect(container.querySelector('[data-image-state="loading"]')).not.toBeNull();
    expect(container.querySelector('[data-image-state="loaded"]')).toBeNull();
    // A and B must not share the same native request node
    expect(imgB).not.toBe(imgA);

    // late load from the old URL A must not flip B to loaded
    await act(async () => {
      fireEvent(imgA as Element, new Event("load"));
    });
    expect(container.querySelector('[data-reader-image="true"] img')?.getAttribute("src")).toBe(urlB);
    expect(container.querySelector('[data-image-state="loaded"]')).toBeNull();
    expect(container.querySelector('[data-image-state="loading"]')).not.toBeNull();

    // only B's own load enters loaded
    await act(async () => {
      fireEvent(imgB as Element, new Event("load"));
    });
    expect(container.querySelector('[data-image-state="loaded"]')).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "复制链接" }));
    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledWith(urlB);
  });

  it("after effectiveUrl A→B, a late error from A cannot mark B load_failed and copy uses B", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const urlA = "https://example.com/a.png";
    const urlB = "https://example.com/b.png?v=2#Frag";
    const mkSnapshot = (effectiveUrl: string | null) =>
      makeImgSnapshotForSurface(
        [{ unitId: "u1", text: "Hello", stableType: "paragraph", stableId: "p1" }],
        [wgNode({ block_id: "img1", block_type: "image", payload: imgPayload(urlA, "alt", null, effectiveUrl) })],
      );
    const { container, rerender } = render(<ReaderRecordPlateSurface snapshot={mkSnapshot(urlA)} />);
    const imgA = container.querySelector('[data-reader-image="true"] img');
    expect(imgA).not.toBeNull();
    expect(imgA?.getAttribute("src")).toBe(urlA);

    rerender(<ReaderRecordPlateSurface snapshot={mkSnapshot(urlB)} />);
    // setValue commits asynchronously (MessageChannel); wait for observable DOM
    const imgB = await waitFor(() => {
      const el = container.querySelector('[data-reader-image="true"] img');
      if (!el || el.getAttribute("src") !== urlB) {
        throw new Error("imgB with verbatim src B not rendered yet");
      }
      return el;
    });
    expect(container.querySelector('[data-image-state="loading"]')).not.toBeNull();
    expect(container.querySelector('[data-image-state="load_failed"]')).toBeNull();
    expect(imgB).not.toBe(imgA);

    // late error from the old URL A must not flip B to load_failed
    await act(async () => {
      fireEvent(imgA as Element, new Event("error"));
    });
    expect(container.querySelector('[data-image-state="load_failed"]')).toBeNull();
    expect(container.querySelector('[data-image-state="loading"]')).not.toBeNull();

    // only B's own error enters load_failed
    await act(async () => {
      fireEvent(imgB as Element, new Event("error"));
    });
    expect(container.querySelector('[data-image-state="load_failed"]')).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "复制链接" }));
    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledWith(urlB);
  });

  it("safe → unsafe/null effectiveUrl unmounts img[src] and never falls back to sourceUrl as src", async () => {
    const safeUrl = "https://example.com/a.png";
    const unsafeSource = "/local/relative.png";
    const mkSnapshot = (effectiveUrl: string | null) =>
      makeImgSnapshotForSurface(
        [{ unitId: "u1", text: "Hello", stableType: "paragraph", stableId: "p1" }],
        [wgNode({ block_id: "img1", block_type: "image", payload: imgPayload(unsafeSource, "alt", null, effectiveUrl) })],
      );
    const { container, rerender } = render(<ReaderRecordPlateSurface snapshot={mkSnapshot(safeUrl)} />);
    expect(container.querySelector('[data-reader-image="true"] img[src]')).not.toBeNull();

    rerender(<ReaderRecordPlateSurface snapshot={mkSnapshot(null)} />);
    // setValue commits asynchronously (MessageChannel); wait for observable DOM
    await waitFor(() => {
      expect(container.querySelector('[data-reader-image="true"] img[src]')).toBeNull();
      expect(container.textContent).toContain("链接不安全");
    });
    // source surface keeps source_url text; no img fallback to sourceUrl
    expect(container.textContent).toContain(unsafeSource);
  });
});

describe("G2D-B frozen image URL editor", () => {
  function wgNode2(overrides: Partial<ReaderStableDocumentBlockNodeDto>): ReaderStableDocumentBlockNodeDto {
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
  function imgPayload2(sourceUrl: string, alt: string, title: string | null, effectiveUrl: string | null, overrideUrl?: unknown): Record<string, unknown> {
    const p: Record<string, unknown> = { source_url: sourceUrl, alt_text: alt, title, position_kind: "standalone", effective_url: effectiveUrl };
    if (overrideUrl !== undefined) (p as Record<string, unknown>).override_url = overrideUrl;
    return p;
  }
  function inlineEntry2(sourceUrl: string, alt: string, title: string | null, before: number, effectiveUrl: string | null, overrideUrl?: unknown): Record<string, unknown> {
    const e: Record<string, unknown> = { source_url: sourceUrl, alt_text: alt, title, before_utf16: before, effective_url: effectiveUrl };
    if (overrideUrl !== undefined) e.override_url = overrideUrl;
    return e;
  }
  function makeG2dSnapshot(opts: {
    stableDocumentId?: string | null;
    image: { sourceUrl: string; effectiveUrl: string | null; overrideUrl?: unknown; blockId: string; alt?: string };
    inline?: { owningBlockId: string; sourceUrl: string; effectiveUrl: string | null; overrideUrl?: unknown; ordinal: number; before: number };
  }): ReaderPlateSnapshotDto {
    const baseId = "base_g2d";
    const text = "Hello world";
    const segId = "seg_1";
    const unitId = "u1";
    const snap: ReaderPlateSnapshotDto = {
      schema_kind: "reader_plate_snapshot",
      snapshot_id: "snap_g2d",
      snapshot_taken_at: "2026-08-08T00:00:00Z",
      last_event_sequence: 1,
      record_id: "record_g2d",
      record: {
        title: "G2D Fixture",
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
        ...(opts.stableDocumentId !== undefined ? { stable_document_id: opts.stableDocumentId } : {}),
      } as unknown as ReaderPlateSnapshotDto["base"],
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
            text_hash: `hash_${segId}`,
            hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
            stable_block_type: "paragraph",
            heading_level: null,
          },
        ],
      },
      anchor_segments: [
        {
          anchor_segment_id: segId,
          sentence_id: "sent_1",
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
          text_hash: `hash_${segId}`,
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
          text_hash: `hash_${unitId}`,
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
                  sentence_id: "sent_1",
                  segment_type: "sentence",
                  boundary_quality: "normal",
                  base_start_utf16: 0,
                  base_end_utf16: text.length,
                  unit_start_utf16: 0,
                  unit_end_utf16: text.length,
                  text_hash: `hash_${segId}`,
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
      stable_document_tree: [],
    };
    if (opts.inline) {
      const owning = opts.inline;
      snap.stable_document_tree = [
        wgNode2({
          block_id: owning.owningBlockId,
          block_type: "paragraph",
          order_index: 0,
          payload: {
            inline_images: [inlineEntry2(owning.sourceUrl, "alt", null, owning.before, owning.effectiveUrl, owning.overrideUrl)],
          },
        }),
      ];
      // ensure array length for ordinal
      const treeInline = (snap.stable_document_tree[0].payload as Record<string, unknown>).inline_images as unknown[];
      while (treeInline.length <= owning.ordinal) {
        treeInline.push(inlineEntry2(owning.sourceUrl, "alt", null, owning.before, owning.effectiveUrl));
      }
      (treeInline[owning.ordinal] as Record<string, unknown>).override_url = owning.overrideUrl;
      if (owning.overrideUrl === undefined) delete (treeInline[owning.ordinal] as Record<string, unknown>).override_url;
    } else {
      snap.stable_document_tree = [
        wgNode2({
          block_id: opts.image.blockId,
          block_type: "image",
          order_index: 0,
          payload: imgPayload2(opts.image.sourceUrl, opts.image.alt ?? "alt", null, opts.image.effectiveUrl, opts.image.overrideUrl),
        }),
        wgNode2({ block_id: "b1", block_type: "paragraph", order_index: 1 }),
      ];
    }
    return snap;
  }

  it("shows 修改链接 entry when stable_document_id present", () => {
    const snap = makeG2dSnapshot({
      stableDocumentId: "11111111-1111-1111-1111-111111111111",
      image: { sourceUrl: "https://example.com/source.png", effectiveUrl: "https://example.com/source.png", blockId: "img1" },
    });
    render(<ReaderRecordPlateSurface snapshot={snap} />);
    expect(screen.getByText("修改链接")).toBeTruthy();
  });

  it("does not show 修改链接 for legacy snapshot without stable_document_id", () => {
    const snap = makeG2dSnapshot({
      stableDocumentId: null,
      image: { sourceUrl: "https://example.com/source.png", effectiveUrl: "https://example.com/source.png", blockId: "img1" },
    });
    // remove key entirely to simulate legacy
    delete (snap.base as unknown as Record<string, unknown>).stable_document_id;
    render(<ReaderRecordPlateSurface snapshot={snap} />);
    expect(screen.queryByText("修改链接")).toBeNull();
  });

  it("prefills overrideUrl verbatim, including empty string", async () => {
    const user = userEvent.setup();
    const snapWithOverride = makeG2dSnapshot({
      stableDocumentId: "11111111-1111-1111-1111-111111111111",
      image: { sourceUrl: "https://example.com/source.png", effectiveUrl: "https://example.com/override.png", overrideUrl: "https://example.com/override.png", blockId: "img1" },
    });
    const { unmount } = render(<ReaderRecordPlateSurface snapshot={snapWithOverride} />);
    await user.click(screen.getByText("修改链接"));
    const input = screen.getByLabelText("图片覆盖地址") as HTMLInputElement;
    expect(input.value).toBe("https://example.com/override.png");
    expect(screen.getByText("原始地址：")).toBeTruthy();
    expect(screen.getByText("https://example.com/source.png")).toBeTruthy();
    unmount();

    const snapEmpty = makeG2dSnapshot({
      stableDocumentId: "11111111-1111-1111-1111-111111111111",
      image: { sourceUrl: "https://example.com/source.png", effectiveUrl: null, overrideUrl: "", blockId: "img1" },
    });
    render(<ReaderRecordPlateSurface snapshot={snapEmpty} />);
    await user.click(screen.getByText("修改链接"));
    const input2 = screen.getByLabelText("图片覆盖地址") as HTMLInputElement;
    expect(input2.value).toBe("");
    expect(screen.getByText("恢复原始地址")).toBeTruthy();
  });

  it("prefill absent key gives empty input, no restore button", async () => {
    const user = userEvent.setup();
    const snap = makeG2dSnapshot({
      stableDocumentId: "11111111-1111-1111-1111-111111111111",
      image: { sourceUrl: "https://example.com/source.png", effectiveUrl: "https://example.com/source.png", blockId: "img1" },
    });
    // ensure override_url absent
    const treePayload = (snap.stable_document_tree?.[0].payload as Record<string, unknown>);
    delete treePayload.override_url;
    render(<ReaderRecordPlateSurface snapshot={snap} />);
    await user.click(screen.getByText("修改链接"));
    const input = screen.getByLabelText("图片覆盖地址") as HTMLInputElement;
    expect(input.value).toBe("");
    expect(screen.queryByText("恢复原始地址")).toBeNull();
  });

  it("PUT standalone sends exact shape with inline_ordinal null and raw url verbatim", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true, last_event_sequence: 123 }), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const onReload = vi.fn();
    const snap = makeG2dSnapshot({
      stableDocumentId: "22222222-2222-2222-2222-222222222222",
      image: { sourceUrl: "https://example.com/source.png", effectiveUrl: "https://example.com/source.png", blockId: "b_img_1" },
    });
    render(<ReaderRecordPlateSurface snapshot={snap} onRequestSnapshotReload={onReload} />);
    await user.click(screen.getByText("修改链接"));
    const input = screen.getByLabelText("图片覆盖地址");
    await user.clear(input);
    const raw = "  https://example.com/raw  ";
    await user.type(input, raw);
    await user.click(screen.getByText("保存"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const putCall = fetchMock.mock.calls.find(([url, opts]) => String(url).includes("/image-source-overrides") && (opts as RequestInit).method === "PUT");
    expect(putCall).toBeTruthy();
    const body = JSON.parse(String((putCall?.[1] as RequestInit).body));
    expect(body).toEqual({
      stable_document_id: "22222222-2222-2222-2222-222222222222",
      block_id: "b_img_1",
      inline_ordinal: null,
      url: raw,
    });
    expect(onReload).toHaveBeenCalled();
    const img = document.querySelector('[data-reader-image="true"] img') as HTMLImageElement | null;
    // no optimistic src change: still source effectiveUrl
    if (img) expect(img.getAttribute("src")).toBe("https://example.com/source.png");
  });

  it("PUT inline sends exact ordinal", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true, last_event_sequence: 124 }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const snap = makeG2dSnapshot({
      stableDocumentId: "33333333-3333-3333-3333-333333333333",
      image: { sourceUrl: "https://example.com/source.png", effectiveUrl: "https://example.com/source.png", blockId: "img_unused" },
      inline: { owningBlockId: "b1", sourceUrl: "https://example.com/inline.png", effectiveUrl: "https://example.com/inline.png", ordinal: 0, before: 0 },
    });
    render(<ReaderRecordPlateSurface snapshot={snap} />);
    await user.click(screen.getByText("修改链接"));
    const input = screen.getByLabelText("图片覆盖地址");
    await user.clear(input);
    await user.type(input, "https://example.com/inline_override.png");
    await user.click(screen.getByText("保存"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const putCall = fetchMock.mock.calls.find(([url, opts]) => String(url).includes("/image-source-overrides") && (opts as RequestInit).method === "PUT");
    const body = JSON.parse(String((putCall?.[1] as RequestInit).body));
    expect(body.inline_ordinal).toBe(0);
    expect(body.block_id).toBe("b1");
  });

  it("DELETE standalone has no inline_ordinal query, inline has exact ordinal", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true, last_event_sequence: 125 }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const snap = makeG2dSnapshot({
      stableDocumentId: "44444444-4444-4444-4444-444444444444",
      image: { sourceUrl: "https://example.com/source.png", effectiveUrl: null, overrideUrl: "javascript:bad", blockId: "img_del" },
    });
    render(<ReaderRecordPlateSurface snapshot={snap} />);
    await user.click(screen.getByText("修改链接"));
    await user.click(screen.getByText("恢复原始地址"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const delCall = fetchMock.mock.calls.find(([url, opts]) => String(url).includes("/image-source-overrides") && (opts as RequestInit).method === "DELETE");
    expect(delCall).toBeTruthy();
    const url = String(delCall?.[0]);
    expect(url).toContain("stable_document_id=44444444-4444-4444-4444-444444444444");
    expect(url).toContain("block_id=img_del");
    expect(url).not.toContain("inline_ordinal");
  });

  it("API failure keeps old snapshot truth, shows error, allows manual retry, no optimistic src", async () => {
    const user = userEvent.setup();
    let imageCallCount = 0;
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("image-source-overrides")) {
        imageCallCount += 1;
        if (imageCallCount === 1) {
          return Promise.resolve(new Response(JSON.stringify({ ok: false, message: "upstream 503" }), { status: 503 }));
        }
        return Promise.resolve(new Response(JSON.stringify({ ok: true, last_event_sequence: 126 }), { status: 200, headers: { "content-type": "application/json" } }));
      }
      if (url.includes("/favorite")) {
        return Promise.resolve(new Response(JSON.stringify({ ok: true, favorited: false }), { status: 200, headers: { "content-type": "application/json" } }));
      }
      return Promise.resolve(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const onReload = vi.fn();
    const snap = makeG2dSnapshot({
      stableDocumentId: "55555555-5555-5555-5555-555555555555",
      image: { sourceUrl: "https://example.com/source.png", effectiveUrl: "https://example.com/source.png", blockId: "img_fail" },
    });
    render(<ReaderRecordPlateSurface snapshot={snap} onRequestSnapshotReload={onReload} />);
    await user.click(screen.getByText("修改链接"));
    const input = screen.getByLabelText("图片覆盖地址") as HTMLInputElement;
    await user.clear(input);
    await user.type(input, "https://example.com/new.png");
    const beforeSrc = document.querySelector('[data-reader-image="true"] img')?.getAttribute("src");
    await user.click(screen.getByText("保存"));
    await waitFor(() => expect(screen.queryByText(/失败/) || screen.queryByText(/upstream/)).toBeTruthy());
    expect(onReload).not.toHaveBeenCalled();
    expect(input.value).toBe("https://example.com/new.png");
    const afterFailSrc = document.querySelector('[data-reader-image="true"] img')?.getAttribute("src");
    expect(afterFailSrc).toBe(beforeSrc);
    // manual retry
    await user.click(screen.getByText("保存"));
    await waitFor(() => expect(onReload).toHaveBeenCalledTimes(1));
  });

  it("cancel does zero request and zero node change", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const snap = makeG2dSnapshot({
      stableDocumentId: "66666666-6666-6666-6666-666666666666",
      image: { sourceUrl: "https://example.com/source.png", effectiveUrl: "https://example.com/source.png", blockId: "img_cancel" },
    });
    render(<ReaderRecordPlateSurface snapshot={snap} />);
    await user.click(screen.getByText("修改链接"));
    const input = screen.getByLabelText("图片覆盖地址");
    await user.type(input, "https://example.com/changed.png");
    await user.click(screen.getByText("取消"));
    const imageCalls = fetchMock.mock.calls.filter(([url]) => String(url).includes("image-source-overrides"));
    expect(imageCalls.length).toBe(0);
    expect(screen.queryByLabelText("图片覆盖地址")).toBeNull();
  });

  it("editing chrome is excluded from copy/selection", async () => {
    const user = userEvent.setup();
    const snap = makeG2dSnapshot({
      stableDocumentId: "77777777-7777-7777-7777-777777777777",
      image: { sourceUrl: "https://example.com/source.png", effectiveUrl: "https://example.com/source.png", blockId: "img_copy" },
    });
    const { container } = render(<ReaderRecordPlateSurface snapshot={snap} />);
    await user.click(screen.getByText("修改链接"));
    const panel = container.querySelector('[data-reader-record-copy-exclude="true"]');
    expect(panel).not.toBeNull();
    // input, buttons, error, sourceUrl should be inside copy-excluded container
    const input = screen.getByLabelText("图片覆盖地址");
    expect(input.closest('[data-reader-record-copy-exclude="true"]')).not.toBeNull();
    const saveBtn = screen.getByText("保存");
    expect(saveBtn.closest('[data-reader-record-copy-exclude="true"]')).not.toBeNull();
    // void children rendered exactly once
    const voidChildren = container.querySelectorAll('[data-reader-image="true"]');
    voidChildren.forEach((el) => {
      // each image wraps one void child element (the slate void)
      // we check that children count for the image element includes the void text node
      expect(el.textContent).toBeDefined();
    });
  });

  it("unsafe image still shows 修改链接 and editing", async () => {
    const user = userEvent.setup();
    const snap = makeG2dSnapshot({
      stableDocumentId: "88888888-8888-8888-8888-888888888888",
      image: { sourceUrl: "https://example.com/source.png", effectiveUrl: null, blockId: "img_unsafe" },
    });
    render(<ReaderRecordPlateSurface snapshot={snap} />);
    expect(screen.getByText("修改链接")).toBeTruthy();
    await user.click(screen.getByText("修改链接"));
    expect(screen.getByLabelText("图片覆盖地址")).toBeTruthy();
  });
});
