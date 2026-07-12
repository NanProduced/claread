/** @vitest-environment jsdom */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { Suspense } from "react";
import { computeUtf16FNV1a } from "@claread/contracts";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  READER_TEXT_RANGE_HASH_ALGORITHM,
  READER_TEXT_RANGE_OFFSET_UNIT,
} from "@/types/api/reader-plate";
import type {
  ReaderEventPollResponseDto,
  ReaderEventResponseDto,
  ReaderPlateSnapshotDto,
  ReaderSnapshotUserAssetDto,
  ReaderSentenceAnalysisNodeDto,
} from "@/types/api/reader-plate";
import type { WebDictResult } from "@/types/api/dict";

import { toast as toastImpl } from "@/components/primitives/toast";
import ReadingRecordPage from "./page";
import {
  DEFAULT_READER_RECORD_SURFACE_MODE,
  type ReaderRecordSurfaceMode,
} from "./reader-record-surface-mode";

vi.mock("@/components/primitives/toast", () => ({
  toast: {
    warning: vi.fn(),
    dismiss: vi.fn(),
  },
}));

const toastMock = toastImpl as unknown as {
  warning: ReturnType<typeof vi.fn>;
  dismiss: ReturnType<typeof vi.fn>;
};

const SOURCE_TEXT = "Institutional memory shapes policy choices.";
const TRANSLATION_TEXT = "制度记忆会塑造政策选择。";

function makeUserHighlightAsset(
  overrides: Partial<ReaderSnapshotUserAssetDto> = {},
): ReaderSnapshotUserAssetDto {
  return {
    asset_id: "asset_highlight_1",
    asset_type: "quick_highlight",
    owner: "user",
    reading_record_id: "rec_product_1",
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

function makeSnapshot(
  recordId = "rec_product_1",
  recordOverrides: Partial<ReaderPlateSnapshotDto["record"]> = {},
  options?: {
    enhancementProgress?: ReaderPlateSnapshotDto["enhancement_progress"];
    lastEventSequence?: number;
    translationScope?: "unit" | "anchor_segment";
    translationText?: string;
    withGrammarMark?: boolean;
    withSentenceAnalysis?: boolean;
    userAssets?: ReaderSnapshotUserAssetDto[];
    withVocabularyMark?: boolean;
  },
): ReaderPlateSnapshotDto {
  return {
    schema_kind: "reader_plate_snapshot",
    snapshot_id: "snap_1",
    snapshot_taken_at: "2026-06-22T00:00:00Z",
    last_event_sequence: options?.lastEventSequence ?? 1,
    record_id: recordId,
    record: {
      title: recordOverrides.title ?? "Reading Record Page Fixture",
      display_title_zh: recordOverrides.display_title_zh ?? null,
      title_generation_status:
        recordOverrides.title_generation_status ?? "pending",
      title_generation_error_code:
        recordOverrides.title_generation_error_code ?? null,
      title_generation_error_message:
        recordOverrides.title_generation_error_message ?? null,
      reading_goal: recordOverrides.reading_goal ?? "daily_reading",
      reading_variant:
        recordOverrides.reading_variant ?? "intensive_reading",
      created_at: recordOverrides.created_at ?? "2026-06-22T00:00:00Z",
      source_type: recordOverrides.source_type ?? "text",
      source_metadata: recordOverrides.source_metadata ?? {},
      generation: recordOverrides.generation ?? 1,
      product_state:
        recordOverrides.product_state ?? "readable_enhancing",
      readiness_state: recordOverrides.readiness_state ?? "article_ready",
    },
    base: {
      base_id: "base_1",
      content_sha256: "sha256_1",
      canonicalizer_version: "canonicalizer_test",
      builder_version: "builder_test",
      segmenter_version: "segmenter_test",
      hash_algorithm: "fnv1a32-utf16",
      text_length_utf16: SOURCE_TEXT.length,
    },
    navigation: {
      units: [
        {
          unit_id: "unit_1",
          order_index: 1,
          unit_type: "body",
          boundary_quality: "normal",
          base_start_utf16: 0,
          base_end_utf16: SOURCE_TEXT.length,
          text_hash: "hash_1",
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
        base_end_utf16: SOURCE_TEXT.length,
        unit_start_utf16: 0,
        unit_end_utf16: SOURCE_TEXT.length,
        text_hash: "hash_1",
        hash_algorithm: "fnv1a32-utf16",
      },
    ],
    ...(options?.enhancementProgress
      ? { enhancement_progress: options.enhancementProgress }
      : {}),
    value: [
      {
        type: "reader_unit",
        owner: "stable",
        base_id: "base_1",
        unit_id: "unit_1",
        order_index: 1,
        unit_type: "body",
        boundary_quality: "normal",
        base_start_utf16: 0,
        base_end_utf16: SOURCE_TEXT.length,
        text_hash: "hash_1",
        hash_algorithm: "fnv1a32-utf16",
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
                text_hash: "hash_1",
                hash_algorithm: "fnv1a32-utf16",
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
                    reader_vocabulary_marks: options?.withVocabularyMark
                      ? [
                          {
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
                          },
                        ]
                      : [],
                    reader_grammar_note_marks: options?.withGrammarMark
                      ? [
                          {
                            mark_id: "mark_grammar_memory",
                            item_id: "grammar_entry_memory",
                            owner: "system_ai",
                            layer_id: "layer_grammar_1",
                            item_type: "grammar_note",
                            anchor_segment_id: "seg_1",
                            start_offset: 0,
                            end_offset: 20,
                            selected_text: "Institutional memory",
                            segment_start_utf16: 0,
                            segment_end_utf16: 20,
                            starts_here: true,
                            ends_here: true,
                            span_index: 0,
                            span_count: 1,
                            show_note_chip: true,
                            grammar_point: "名词短语主语",
                            pattern: "adjective + noun",
                            note: "Institutional memory 是主语名词短语。",
                          },
                        ]
                      : [],
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
            children: [{ text: options?.translationText ?? TRANSLATION_TEXT }],
          },
          ...(options?.withSentenceAnalysis
            ? [
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
                  selected_text: SOURCE_TEXT,
                  label: "句子结构",
                  analysis: "Institutional memory 是主语名词短语。",
                  chunks: [
                    {
                      order: 1,
                      label: "主语",
                      text: "Institutional memory",
                    },
                    {
                      order: 2,
                      label: "谓语",
                      text: "shapes policy choices",
                    },
                  ],
                  children: [
                    { text: "Institutional memory 是主语名词短语。" },
                  ],
                } satisfies ReaderSentenceAnalysisNodeDto,
              ]
            : []),
        ],
      },
    ],
    enhancement_layers: [],
    parsed_decisions: [],
    user_assets: options?.userAssets ?? [],
    ask_supplements: [],
  };
}

function makeEnhancementProgress(
  overrides: Partial<
    NonNullable<ReaderPlateSnapshotDto["enhancement_progress"]>
  > = {},
): NonNullable<ReaderPlateSnapshotDto["enhancement_progress"]> {
  return {
    overall_status: "readable_enhancing",
    layers: [
      {
        capability: "translation",
        layer_type: "translation",
        status: "processing",
        job_status: "claimed",
        job_type: "translate_unit",
        job_id: "job_translation_1",
        target_type: "unit",
        target_scope: "unit",
        target_key: "unit_1",
      },
      {
        capability: "vocabulary",
        layer_type: "vocabulary",
        status: "queued",
        job_status: "queued",
        job_type: "build_vocabulary_layer",
        job_id: "job_vocabulary_1",
        target_type: "unit",
        target_scope: "unit",
        target_key: "unit_1",
      },
      {
        capability: "grammar",
        layer_type: "grammar_note",
        status: "not_started",
      },
    ],
    ...overrides,
  };
}

function makeDictionaryEntryResult(query = "memory"): WebDictResult {
  return {
    kind: "entry",
    query,
    provider: "test-dict",
    cached: false,
    entry: {
      id: 101,
      word: query,
      baseWord: query,
      homographNo: 1,
      phonetic: "/ˈmeməri/",
      meanings: [
        {
          partOfSpeech: "noun",
          definitions: [
            {
              meaning: "记忆；既有经验",
              example: "Institutional memory shapes policy choices.",
              exampleTranslation: "制度记忆会塑造政策选择。",
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

function makeReaderEvent(
  recordId: string,
  eventType: ReaderEventResponseDto["event_type"],
  options?: {
    payload?: Record<string, unknown>;
    sequence?: number;
  },
): ReaderEventResponseDto {
  return {
    id: `event_${options?.sequence ?? 2}`,
    reading_record_id: recordId,
    sequence: options?.sequence ?? 2,
    event_type: eventType,
    payload: options?.payload ?? {},
    source_run_id: null,
    source_job_id: null,
    source_layer_id: null,
    created_at: "2026-06-22T00:00:00Z",
  };
}

function makePollResponse(
  recordId: string,
  afterSequence: number,
  overrides: Partial<ReaderEventPollResponseDto> = {},
): { ok: true } & ReaderEventPollResponseDto {
  const nextAfterSequence =
    overrides.next_after_sequence ??
    overrides.last_event_sequence ??
    afterSequence;

  return {
    ok: true,
    reading_record_id: recordId,
    after_sequence: afterSequence,
    next_after_sequence: nextAfterSequence,
    last_event_sequence: overrides.last_event_sequence ?? afterSequence,
    has_more: false,
    truncated: false,
    reload_required: false,
    reload_reason: null,
    events: [],
    ...overrides,
  };
}

type ReaderRecordFetchMockOptions = {
  dictResult?: WebDictResult;
  askResponder?: (
    url: URL,
    init?: RequestInit,
  ) => Response | Promise<Response> | null | undefined;
  eventsResponder?: (url: URL) => Response | Promise<Response>;
  snapshots?: ReaderPlateSnapshotDto[];
};

function installReaderRecordFetchMock(
  snapshot: ReaderPlateSnapshotDto,
  options: ReaderRecordFetchMockOptions = {},
) {
  const dictResult = options.dictResult ?? makeDictionaryEntryResult();
  const snapshots = options.snapshots ?? [snapshot];
  let snapshotIndex = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const requestUrl = new URL(String(input), "http://localhost");

    if (options.askResponder) {
      const askResponse = await options.askResponder(requestUrl, init);
      if (askResponse) {
        return askResponse;
      }
    }

    if (
      requestUrl.pathname ===
      `/api/web/reader-plate/${snapshot.record_id}/snapshot`
    ) {
      const nextSnapshot =
        snapshots[Math.min(snapshotIndex, snapshots.length - 1)] ?? snapshot;
      snapshotIndex += 1;

      return new Response(JSON.stringify({ ok: true, ...nextSnapshot }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }

    if (
      requestUrl.pathname ===
      `/api/web/reader-plate/${snapshot.record_id}/events`
    ) {
      if (options.eventsResponder) {
        return await options.eventsResponder(requestUrl);
      }

      const afterSequence = Number(
        requestUrl.searchParams.get("after_sequence") ??
          String(snapshot.last_event_sequence),
      );

      return new Response(
        JSON.stringify(
          makePollResponse(snapshot.record_id, afterSequence, {
            next_after_sequence: snapshot.last_event_sequence,
            last_event_sequence: snapshot.last_event_sequence,
          }),
        ),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      );
    }

    if (requestUrl.pathname === "/api/web/dict/lookup") {
      return new Response(JSON.stringify(dictResult), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }

    if (requestUrl.pathname === "/api/web/dict/entry") {
      return new Response(JSON.stringify(dictResult), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }

    if (
      requestUrl.pathname ===
      `/api/web/reader-plate/records/${snapshot.record_id}/article-rag-index/status`
    ) {
      // Standalone ArticleRagStatusPanel tests can still exercise this endpoint.
      // ReaderRecordPage no longer mounts that status strip at the top level.
      return new Response(
        JSON.stringify({
          ok: true,
          status: "unavailable",
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }

    throw new Error(`Unexpected fetch: ${String(input)}`);
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function firstTextNode(element: HTMLElement): Text | null {
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  return walker.nextNode() as Text | null;
}

async function flushAsyncWork() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

function fulfilledRouteParams(recordId: string) {
  const value = { recordId };
  return Object.assign(Promise.resolve(value), {
    status: "fulfilled" as const,
    value,
  });
}

function renderReadingRecordPage(
  recordId: string,
  surfaceMode: ReaderRecordSurfaceMode | "default" = "default",
) {
  if (surfaceMode === "default") {
    Reflect.deleteProperty(globalThis, "__CLAREAD_READER_RECORD_SURFACE_MODE__");
  } else {
    globalThis.__CLAREAD_READER_RECORD_SURFACE_MODE__ = surfaceMode;
  }

  return render(
    <Suspense fallback={<div data-testid="reader-record-route-loading" />}>
      <ReadingRecordPage params={fulfilledRouteParams(recordId)} />
    </Suspense>,
  );
}

function installRangeGeometryStub(rect: DOMRect) {
  const prototype = Range.prototype as Range & {
    getClientRects?: () => DOMRect[];
    getBoundingClientRect?: () => DOMRect;
  };
  const originalGetClientRects = prototype.getClientRects;
  const originalGetBoundingClientRect = prototype.getBoundingClientRect;

  Object.defineProperty(prototype, "getClientRects", {
    configurable: true,
    value() {
      return [rect];
    },
  });
  Object.defineProperty(prototype, "getBoundingClientRect", {
    configurable: true,
    value() {
      return rect;
    },
  });

  return () => {
    if (originalGetClientRects) {
      Object.defineProperty(prototype, "getClientRects", {
        configurable: true,
        value: originalGetClientRects,
      });
    } else {
      Reflect.deleteProperty(
        prototype as unknown as Record<string, unknown>,
        "getClientRects",
      );
    }

    if (originalGetBoundingClientRect) {
      Object.defineProperty(prototype, "getBoundingClientRect", {
        configurable: true,
        value: originalGetBoundingClientRect,
      });
    } else {
      Reflect.deleteProperty(
        prototype as unknown as Record<string, unknown>,
        "getBoundingClientRect",
      );
    }
  };
}

function installElementScrollStub() {
  const prototype = HTMLElement.prototype as HTMLElement & {
    scrollTo?: (...args: unknown[]) => void;
  };
  const originalScrollTo = prototype.scrollTo;

  Object.defineProperty(prototype, "scrollTo", {
    configurable: true,
    value: vi.fn(),
  });

  return () => {
    if (originalScrollTo) {
      Object.defineProperty(prototype, "scrollTo", {
        configurable: true,
        value: originalScrollTo,
      });
    } else {
      Reflect.deleteProperty(
        prototype as unknown as Record<string, unknown>,
        "scrollTo",
      );
    }
  };
}

afterEach(() => {
  cleanup();
  window.getSelection()?.removeAllRanges();
  Reflect.deleteProperty(
    document as unknown as Record<string, unknown>,
    "caretRangeFromPoint",
  );
  Reflect.deleteProperty(globalThis, "__CLAREAD_READER_RECORD_SURFACE_MODE__");
  vi.useRealTimers();
  vi.unstubAllGlobals();
  toastMock.warning.mockClear();
  toastMock.dismiss.mockClear();
  toastMock.warning.mockReset();
  toastMock.dismiss.mockReset();
});

describe("ReadingRecordPage static contract", () => {
  it("page and Workbench-backed surface do not reference legacy scene or analysis task data planes", () => {
    const sources = [
      "src/app/(private)/app/reader-record/[recordId]/page.tsx",
      "src/app/(private)/app/reader-record/[recordId]/reader-record-surface-mode.ts",
      "src/components/reader/ReaderRecordWorkbenchSurface.tsx",
    ].map((path) => readFileSync(resolve(process.cwd(), path), "utf-8"));

    sources.forEach((source) => {
      expect(source).not.toContain("render_scene_json");
      expect(source).not.toContain("/scene");
      expect(source).not.toContain("analysis-tasks");
      expect(source).not.toContain("legacyAppReaderRoute");
    });
  });

  it("default Plate mode stays free of legacy adapters while wiring RR Ask through AiWorkspacePanel", () => {
    const pageSource = readFileSync(
      resolve(process.cwd(), "src/app/(private)/app/reader-record/[recordId]/page.tsx"),
      "utf-8",
    );
    const surfaceModeSource = readFileSync(
      resolve(
        process.cwd(),
        "src/app/(private)/app/reader-record/[recordId]/reader-record-surface-mode.ts",
      ),
      "utf-8",
    );
    const plateSurfaceSource = readFileSync(
      resolve(process.cwd(), "src/components/reader/plate/ReaderRecordPlateSurface.tsx"),
      "utf-8",
    );
    const projectionSource = readFileSync(
      resolve(
        process.cwd(),
        "src/lib/reader-plate/projection/reader-record-plate-document.ts",
      ),
      "utf-8",
    );

    [pageSource, surfaceModeSource, plateSurfaceSource, projectionSource].forEach((source) => {
      expect(source).not.toContain("adaptReaderPlateSnapshotToReaderVm");
      expect(source).not.toContain("renderSceneToPlateDocument");
      expect(source).not.toContain("render_scene_json");
      expect(source).not.toContain("analysis-tasks");
      expect(source).not.toContain("legacyAppReaderRoute");
      expect(source).not.toContain("/app/reader/");
      expect(source).not.toContain("/scene");
      expect(source).not.toContain("/api/web/reader-ask");
      expect(source).not.toContain("/api/web/reader-notes");
      expect(source).not.toContain("/api/web/reader-annotations");
      expect(source).not.toContain("/api/web/annotations");
    });

    expect(plateSurfaceSource).toContain("AiWorkspacePanel");
    expect(plateSurfaceSource).toContain('recordScope="reading_record"');
  });

  it("ReaderRecordPlateSurface uses converged Chinese copy instead of legacy English UI labels", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/components/reader/plate/ReaderRecordPlateSurface.tsx"),
      "utf-8",
    );

    // Action buttons must use Chinese labels matching the polished Workbench baseline.
    // Checks cover quoted string literals, JSX bare text, and status messages.
    const englishCopyPatterns = [
      // Action button labels (quoted and bare JSX text)
      '"Lookup"',
      '"Copy"',
      '"Highlight"',
      '"Note"',
      '"Save"',
      '"Cancel"',
      '"Close"',
      '"Saving"',
      '"Looking up"',
      '"Copied"',
      '"Copy failed"',
      // Section labels
      '"Vocabulary"',
      '"Grammar"',
      '"Sentence Structure"',
      '"User Highlight"',
      '"Comment"',
      // Status text fragments
      "overlapping annotations",
      "coming soon",
      "Dictionary lookup failed.",
      "Highlight saved",
      "Note saved",
      "Highlight save failed.",
      "Note save failed.",
      "Reading asset save failed.",
      "No concise definition is available for this entry.",
      "Multiple dictionary candidates found:",
      "No dictionary entry found.",
      // Inline labels that were previously English (template literals and quoted strings)
      "Example: ",
      "Reason: ",
      "Anchor ${",
      "Asset ${",
      "Selected: ",
      "Looking up...",
      // aria-labels and titles that were previously English
      'aria-label="Reader Record Plate actions"',
      'aria-label="Dismiss lookup"',
      'aria-label="Close active anchor details"',
      'aria-label="Active anchor details"',
      // disabled reasons that were previously English
      "Select stable source text to enable this action",
      "Action is currently unavailable",
      "Multi-segment selection is not supported yet",
    ];
    for (const pattern of englishCopyPatterns) {
      expect(source).not.toContain(pattern);
    }
  });

  it("ReaderRecordPlateSurface renders Chinese copy when vocabulary mark inspector is active", async () => {
    const snapshot = makeSnapshot("rec_copy_guard_1", {}, {
      translationScope: "unit",
      withVocabularyMark: true,
      withGrammarMark: true,
      withSentenceAnalysis: true,
      userAssets: [makeUserHighlightAsset()],
    });
    installReaderRecordFetchMock(snapshot);

    const { container } = renderReadingRecordPage("rec_copy_guard_1");

    await screen.findByTestId("reader-record-plate-surface");

    // The action strip is always rendered; verify its visible text uses Chinese.
    const actionStrip = container.querySelector<HTMLElement>(
      '[data-reader-record-actions="selection-state"]',
    );
    expect(actionStrip).not.toBeNull();
    if (actionStrip) {
      // Chinese hint must be present when no selection is active.
      expect(actionStrip.textContent).toContain("划取原文后可查词、复制、标记或记录笔记");
      // English copy must NOT be present in the rendered output, even conditionally.
      const englishLabels = [
        "Lookup",
        "Copy",
        "Highlight",
        "Note",
        "Saving",
        "Looking up",
        "Copied",
        "Copy failed",
        "coming soon",
        "Ask / Feedback coming soon",
        "Select stable source text to enable this action",
        "Action is currently unavailable",
        "Multi-segment selection is not supported yet",
        "Selected: ",
      ];
      for (const label of englishLabels) {
        expect(actionStrip.textContent).not.toContain(label);
      }
    }

    // Verify the full surface doesn't leak English section labels.
    const surface = container.querySelector<HTMLElement>(
      '[data-testid="reader-record-plate-surface"]',
    );
    expect(surface).not.toBeNull();
    if (surface) {
      const englishSurfaceLabels = [
        "Vocabulary",
        "Grammar",
        "Sentence Structure",
        "User Highlight",
        "Comment",
        "overlapping annotations",
        "Looking up...",
        "No concise definition is available for this entry.",
        "Multiple dictionary candidates found:",
        "No dictionary entry found.",
        "Example: ",
        "Reason: ",
      ];
      for (const label of englishSurfaceLabels) {
        expect(surface.textContent).not.toContain(label);
      }
    }
  });
});

describe("ReadingRecordPage direct load", () => {
  it("loads snapshot data from the reader-plate BFF and renders the default Plate surface", async () => {
    expect(DEFAULT_READER_RECORD_SURFACE_MODE).toBe("plate");

    const snapshot = makeSnapshot("rec_product_1", {}, {
      translationScope: "unit",
      userAssets: [makeUserHighlightAsset()],
    });
    const fetchMock = installReaderRecordFetchMock(snapshot);

    const { container } = renderReadingRecordPage("rec_product_1");

    await screen.findByTestId("reader-record-plate-surface");
    expect(screen.queryByTestId("reader-record-workbench-surface")).toBeNull();
    const sourceBlock = container.querySelector<HTMLElement>(
      '[data-reader-record-node="paragraph"]',
    );
    const translationBlock = container.querySelector<HTMLElement>(
      '[data-reader-record-node="blockquote"]',
    );
    expect(sourceBlock?.textContent).toContain(SOURCE_TEXT);
    expect(translationBlock?.textContent).toContain(TRANSLATION_TEXT);
    expect(screen.getByTestId("reader-record-plate-header")).toBeTruthy();
    expect(
      container.querySelector(
        '[data-reader-record-node="paragraph"][data-anchor-segment-id="seg_1"]',
      ),
    ).not.toBeNull();
    expect(
      container.querySelector(
        '[data-reader-record-user-highlight-asset-id="asset_highlight_1"]',
      ),
    ).not.toBeNull();
    for (const action of ["ask", "highlight", "note", "feedback"]) {
      expect(
        container.querySelector(`[data-reader-record-action="${action}"]`),
      ).toBeNull();
    }
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/web/reader-plate/rec_product_1/snapshot",
        expect.objectContaining({ method: "GET" }),
      );
    });
  });

  it("does not mount the article RAG status strip in the loaded reader page", async () => {
    const snapshot = makeSnapshot("rec_product_1", {}, {
      translationScope: "unit",
      userAssets: [makeUserHighlightAsset()],
    });
    const fetchMock = installReaderRecordFetchMock(snapshot);

    renderReadingRecordPage("rec_product_1");

    await screen.findByTestId("reader-record-plate-surface");

    expect(screen.queryByTestId("article-rag-status-panel")).toBeNull();
    expect(
      fetchMock.mock.calls.some(([u]) =>
        String(u).endsWith("/article-rag-index/status"),
      ),
    ).toBe(false);
  });

  it("keeps Workbench fallback available without changing the default Plate page", async () => {
    expect(DEFAULT_READER_RECORD_SURFACE_MODE).toBe("plate");

    const snapshot = makeSnapshot("rec_surface_mode_1", {}, {
      translationScope: "unit",
    });
    installReaderRecordFetchMock(snapshot);

    renderReadingRecordPage("rec_surface_mode_1", "workbench");

    await screen.findByTestId("reader-record-workbench-surface");
    expect(screen.queryByTestId("reader-record-plate-surface")).toBeNull();

    cleanup();
    vi.unstubAllGlobals();
    installReaderRecordFetchMock(snapshot);

    renderReadingRecordPage("rec_surface_mode_1");

    await screen.findByTestId("reader-record-plate-surface");
    expect(screen.queryByTestId("reader-record-workbench-surface")).toBeNull();
  });

  it.each([
    {
      recordId: "rec_plate_processing_1",
      overallStatus: "processing" as const,
      productState: "processing" as const,
      layerStatus: "processing" as const,
      label: "解析生成中",
    },
    {
      recordId: "rec_plate_readable_enhancing_1",
      overallStatus: "readable_enhancing" as const,
      productState: "readable_enhancing" as const,
      layerStatus: "queued" as const,
      label: "解析生成中",
    },
    {
      recordId: "rec_plate_failed_1",
      overallStatus: "failed" as const,
      productState: "failed" as const,
      layerStatus: "failed" as const,
      label: "部分解析失败",
    },
    {
      recordId: "rec_plate_action_required_1",
      overallStatus: "action_required" as const,
      productState: "action_required" as const,
      layerStatus: "action_required" as const,
      label: "需要确认",
    },
  ])(
    "renders lightweight Plate progress for $overallStatus without replacing the body",
    async ({ recordId, overallStatus, productState, layerStatus, label }) => {
      const snapshot = makeSnapshot(
        recordId,
        {
          product_state: productState,
          readiness_state: "article_ready",
        },
        {
          enhancementProgress: makeEnhancementProgress({
            overall_status: overallStatus,
            layers: [
              {
                capability: "translation",
                layer_type: "translation",
                status: layerStatus,
                job_status:
                  layerStatus === "failed" || layerStatus === "action_required"
                    ? "failed_terminal"
                    : layerStatus === "processing"
                      ? "claimed"
                      : "queued",
                job_type: "translate_unit",
                job_id: `job_${recordId}`,
                target_type: "unit",
                target_scope: "unit",
                target_key: "unit_1",
              },
            ],
          }),
          translationScope: "unit",
        },
      );
      installReaderRecordFetchMock(snapshot);

      const { container } = renderReadingRecordPage(recordId);

      await screen.findByTestId("reader-record-plate-surface");
      const progress = screen.getByTestId("reader-record-plate-header");
      const source = container.querySelector<HTMLElement>(
        '[data-reader-record-node="paragraph"]',
      );
      const translation = container.querySelector<HTMLElement>(
        '[data-reader-record-node="blockquote"]',
      );

      expect(progress.getAttribute("data-reader-record-reading-header")).toBe("intensive");
      expect(progress.textContent).toContain(label);
      expect(
        progress.querySelector('[data-reader-record-progress-status]'),
      ).toBeTruthy();
      expect(screen.queryByTestId("reader-record-status-banner")).toBeNull();
      expect(screen.queryByTestId("reader-record-enhancement-progress")).toBeNull();
      expect(source?.textContent).toContain(SOURCE_TEXT);
      expect(translation?.textContent).toContain(TRANSLATION_TEXT);
    },
  );

  it("shows queued and processing enhancement progress without changing the workbench shell", async () => {
    const snapshot = makeSnapshot(
      "rec_progress_1",
      {},
      { enhancementProgress: makeEnhancementProgress() },
    );
    installReaderRecordFetchMock(snapshot);

    renderReadingRecordPage("rec_progress_1", "workbench");

    await screen.findByTestId("reader-record-workbench-surface");
    const progress = screen.getByTestId("reader-record-enhancement-progress");
    expect(progress.textContent).toContain("增强进度");
    expect(progress.textContent).toContain("批注/增强处理中");
    expect(progress.textContent).toContain("译文");
    expect(progress.textContent).toContain("处理中");
    expect(progress.textContent).toContain("词汇");
    expect(progress.textContent).toContain("排队中");
    expect(screen.getAllByTestId("reader-record-enhancement-layer")).toHaveLength(3);
    expect(screen.getByText(SOURCE_TEXT)).toBeTruthy();
    expect(screen.getByText(TRANSLATION_TEXT)).toBeTruthy();
  });

  it("summarizes many enhancement rows into capability-level chips", async () => {
    const snapshot = makeSnapshot(
      "rec_many_progress_1",
      {},
      {
        enhancementProgress: makeEnhancementProgress({
          layers: [
            ...Array.from({ length: 8 }, (_, index) => ({
              capability: "translation" as const,
              layer_type: "translation" as const,
              status: "queued" as const,
              job_status: "queued" as const,
              job_type: "translate_unit",
              job_id: `job_translation_${index}`,
              target_type: "unit",
              target_scope: "unit" as const,
              target_key: `unit_${index}`,
            })),
            ...Array.from({ length: 3 }, (_, index) => ({
              capability: "vocabulary" as const,
              layer_type: "vocabulary" as const,
              status: "processing" as const,
              job_status: "claimed" as const,
              job_type: "build_vocabulary_layer",
              job_id: `job_vocabulary_${index}`,
              target_type: "unit",
              target_scope: "unit" as const,
              target_key: `unit_${index}`,
            })),
            {
              capability: "grammar",
              layer_type: "sentence_analysis",
              status: "failed",
              job_status: "failed_terminal",
              job_type: "build_grammar_bundle",
              job_id: "job_grammar_failed",
              target_type: "unit",
              target_scope: "unit",
              target_key: "unit_1",
            },
          ],
        }),
        translationScope: "unit",
      },
    );
    installReaderRecordFetchMock(snapshot);

    renderReadingRecordPage("rec_many_progress_1", "workbench");

    await screen.findByTestId("reader-record-workbench-surface");
    const chips = screen.getAllByTestId("reader-record-enhancement-layer");
    const progressText = screen.getByTestId(
      "reader-record-enhancement-progress",
    ).textContent;
    expect(chips).toHaveLength(3);
    expect(progressText).toContain("译文");
    expect(progressText).toContain("8 排队中");
    expect(progressText).toContain("词汇");
    expect(progressText).toContain("3 处理中");
    expect(progressText).toContain("语法");
    expect(progressText).toContain("1 失败");
  });

  it("starts polling reader events after the direct-load snapshot is ready", async () => {
    vi.useFakeTimers();
    const snapshot = makeSnapshot();
    const fetchMock = installReaderRecordFetchMock(snapshot);

    renderReadingRecordPage("rec_product_1", "workbench");

    await flushAsyncWork();
    expect(screen.getByTestId("reader-record-workbench-surface")).toBeTruthy();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    await flushAsyncWork();

    const eventCall = fetchMock.mock.calls.find(([input]) =>
      String(input).includes("/api/web/reader-plate/rec_product_1/events"),
    );
    expect(eventCall).toBeTruthy();

    const eventUrl = new URL(String(eventCall?.[0]), "http://localhost");
    expect(eventUrl.pathname).toBe("/api/web/reader-plate/rec_product_1/events");
    expect(eventUrl.searchParams.get("after_sequence")).toBe("1");
    expect(eventUrl.searchParams.get("limit")).toBe("100");
  });

  it("reloads snapshot when a layer_published event arrives and updates the default Plate surface", async () => {
    vi.useFakeTimers();
    const initialSnapshot = makeSnapshot(
      "rec_product_1",
      {},
      {
        enhancementProgress: makeEnhancementProgress({
          layers: [
            {
              capability: "translation",
              layer_type: "translation",
              status: "processing",
              job_status: "claimed",
              job_type: "translate_unit",
              job_id: "job_translation_1",
              target_type: "unit",
              target_scope: "unit",
              target_key: "unit_1",
            },
          ],
        }),
        translationScope: "unit",
      },
    );
    const refreshedSnapshot = makeSnapshot(
      "rec_product_1",
      {},
      {
        enhancementProgress: makeEnhancementProgress({
          overall_status: "ready",
          layers: [
            {
              capability: "translation",
              layer_type: "translation",
              status: "succeeded",
              job_status: "succeeded",
              job_type: "translate_unit",
              layer_id: "layer_translation_1",
              job_id: "job_translation_1",
              target_type: "unit",
              target_scope: "unit",
              target_key: "unit_1",
            },
          ],
        }),
        lastEventSequence: 2,
        translationScope: "unit",
        translationText: "制度记忆持续影响政策选择。",
      },
    );
    const fetchMock = installReaderRecordFetchMock(initialSnapshot, {
      snapshots: [initialSnapshot, refreshedSnapshot],
      eventsResponder: (url) => {
        const afterSequence = Number(url.searchParams.get("after_sequence") ?? "0");
        return new Response(
          JSON.stringify(
            makePollResponse(initialSnapshot.record_id, afterSequence, {
              last_event_sequence: 2,
              next_after_sequence: 2,
              events: [
                makeReaderEvent(initialSnapshot.record_id, "layer_published", {
                  payload: { layer_type: "translation" },
                }),
              ],
            }),
          ),
          {
            status: 200,
            headers: { "content-type": "application/json" },
          },
        );
      },
    });

    renderReadingRecordPage("rec_product_1", "default");

    await flushAsyncWork();
    expect(screen.getByTestId("reader-record-plate-surface")).toBeTruthy();
    expect(
      document.querySelector('[data-reader-record-node="blockquote"]')
        ?.textContent,
    ).toContain(TRANSLATION_TEXT);
    expect(screen.getByTestId("reader-record-plate-header").textContent).toContain(
      "解析生成中",
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    await flushAsyncWork();

    expect(
      document.querySelector('[data-reader-record-node="blockquote"]')
        ?.textContent,
    ).toContain("制度记忆持续影响政策选择。");
    expect(document.body.textContent).not.toContain(TRANSLATION_TEXT);
    expect(screen.getByTestId("reader-record-plate-header").textContent).toContain(
      "解析完成",
    );

    expect(
      fetchMock.mock.calls.filter(
        ([input]) =>
          String(input) === "/api/web/reader-plate/rec_product_1/snapshot",
      ).length,
    ).toBeGreaterThanOrEqual(2);
  });

  it("smokes snapshot polling reload into enhanced Plate content and progress updates", async () => {
    vi.useFakeTimers();
    const recordId = "rec_smoke_chain_1";
    const initialSnapshot = makeSnapshot(
      recordId,
      {
        product_state: "readable_enhancing",
        readiness_state: "article_ready",
      },
      {
        enhancementProgress: makeEnhancementProgress({
          overall_status: "readable_enhancing",
          layers: [
            {
              capability: "translation",
              layer_type: "translation",
              status: "processing",
              job_status: "claimed",
              job_type: "translate_unit",
              job_id: "job_translation_1",
              target_type: "unit",
              target_scope: "unit",
              target_key: "unit_1",
            },
            {
              capability: "vocabulary",
              layer_type: "vocabulary",
              status: "queued",
              job_status: "queued",
              job_type: "build_vocabulary_layer",
              job_id: "job_vocabulary_1",
              target_type: "unit",
              target_scope: "unit",
              target_key: "unit_1",
            },
            {
              capability: "grammar",
              layer_type: "grammar_note",
              status: "processing",
              job_status: "claimed",
              job_type: "build_grammar_bundle",
              job_id: "job_grammar_1",
              target_type: "unit",
              target_scope: "unit",
              target_key: "unit_1",
            },
          ],
        }),
      },
    );
    const refreshedSnapshot = makeSnapshot(
      recordId,
      {
        product_state: "readable_enhancing",
        readiness_state: "initial_enhancement_ready",
      },
      {
        enhancementProgress: makeEnhancementProgress({
          overall_status: "failed",
          layers: [
            {
              capability: "translation",
              layer_type: "translation",
              status: "succeeded",
              job_status: "succeeded",
              job_type: "translate_unit",
              layer_id: "layer_translation_1",
              job_id: "job_translation_1",
              target_type: "unit",
              target_scope: "unit",
              target_key: "unit_1",
            },
            {
              capability: "vocabulary",
              layer_type: "vocabulary",
              status: "succeeded",
              job_status: "succeeded",
              job_type: "build_vocabulary_layer",
              layer_id: "layer_vocab_1",
              job_id: "job_vocabulary_1",
              target_type: "unit",
              target_scope: "unit",
              target_key: "unit_1",
            },
            {
              capability: "grammar",
              layer_type: "grammar_note",
              status: "failed",
              job_status: "failed_terminal",
              job_type: "build_grammar_bundle",
              job_id: "job_grammar_1",
              target_type: "unit",
              target_scope: "unit",
              target_key: "unit_1",
              failure_code: "grammar_generation_failed",
              failure_message: "语法增强未完成。",
            },
          ],
        }),
        lastEventSequence: 3,
        translationText: "制度记忆持续影响政策选择。",
        withGrammarMark: true,
        withSentenceAnalysis: true,
        withVocabularyMark: true,
      },
    );
    const fetchMock = installReaderRecordFetchMock(initialSnapshot, {
      snapshots: [initialSnapshot, refreshedSnapshot],
      eventsResponder: (url) => {
        const afterSequence = Number(url.searchParams.get("after_sequence") ?? "0");
        return new Response(
          JSON.stringify(
            makePollResponse(recordId, afterSequence, {
              last_event_sequence: 3,
              next_after_sequence: 3,
              events: [
                makeReaderEvent(recordId, "layer_published", {
                  payload: { layer_type: "translation" },
                  sequence: 2,
                }),
                makeReaderEvent(recordId, "record_product_state_updated", {
                  payload: { product_state: "readable_enhancing" },
                  sequence: 3,
                }),
              ],
            }),
          ),
          {
            status: 200,
            headers: { "content-type": "application/json" },
          },
        );
      },
    });

    const { container } = renderReadingRecordPage(recordId, "workbench");

    await flushAsyncWork();
    expect(screen.getByTestId("reader-record-workbench-surface")).toBeTruthy();
    expect(screen.getByText(SOURCE_TEXT)).toBeTruthy();
    expect(screen.getByText(TRANSLATION_TEXT)).toBeTruthy();
    expect(screen.getByTestId("reader-record-enhancement-progress").textContent).toContain(
      "批注/增强处理中",
    );
    expect(screen.getByTestId("reader-record-enhancement-progress").textContent).toContain(
      "词汇·0/1 已完成 · 1 排队中",
    );
    expect(screen.getByTestId("reader-record-enhancement-progress").textContent).toContain(
      "语法·0/1 已完成 · 1 处理中",
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    await flushAsyncWork();

    const sentenceAnchor = container.querySelector(
      '[data-reader-anchor="sentence"][data-sentence-id="sent_1"]',
    );
    expect(sentenceAnchor?.textContent).toContain(SOURCE_TEXT);
    expect(screen.getByText("制度记忆持续影响政策选择。")).toBeTruthy();
    expect(screen.queryByText(TRANSLATION_TEXT)).toBeNull();
    expect(
      screen.getAllByText("Institutional memory 是主语名词短语。").length,
    ).toBeGreaterThan(0);
    expect(
      container.querySelector('[data-reader-mark-id="mark_vocab_memory"]'),
    ).not.toBeNull();
    expect(
      container.querySelector('[data-reader-mark-id="mark_grammar_memory"]'),
    ).not.toBeNull();

    const progressText = screen.getByTestId(
      "reader-record-enhancement-progress",
    ).textContent;
    expect(progressText).toContain("部分增强失败");
    expect(progressText).toContain("译文·1/1 已完成");
    expect(progressText).toContain("词汇·1/1 已完成");
    expect(progressText).toContain("语法·0/1 已完成 · 1 失败");
    expect(screen.getAllByTestId("reader-record-enhancement-layer")).toHaveLength(3);
    expect(screen.getByRole("button", { name: /Ask Claread/ })).toHaveProperty(
      "disabled",
      false,
    );
    expect(screen.getByRole("button", { name: /笔记\/高亮/ })).toHaveProperty(
      "disabled",
      true,
    );
    expect(screen.getByRole("button", { name: /词典保存/ })).toHaveProperty(
      "disabled",
      true,
    );
    expect(
      fetchMock.mock.calls.some(([input]) => {
        const url = String(input);
        return (
          url.includes("/api/web/vocabulary") ||
          url.includes("/api/web/reader-notes") ||
          url.includes("/api/web/annotations")
        );
      }),
    ).toBe(false);
  });

  it("opens the RR Ask panel and loads RR-scoped ask threads from the web BFF", async () => {
    const recordId = "record_ask_scope";
    const snapshot = makeSnapshot(recordId);
    const fetchMock = installReaderRecordFetchMock(snapshot, {
      askResponder: (url) => {
        if (url.pathname === "/api/web/reader-ask/model-options") {
          return new Response(
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
          );
        }

        if (
          url.pathname === "/api/web/reader-ask/threads" &&
          url.searchParams.get("record_scope") === "reading_record"
        ) {
          return new Response(
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
          );
        }

        if (
          url.pathname === "/api/web/reader-ask/threads/thread-rr-1" &&
          url.searchParams.get("record_scope") === "reading_record"
        ) {
          return new Response(
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
          );
        }

        return null;
      },
    });

    renderReadingRecordPage(recordId, "workbench");

    await flushAsyncWork();

    fireEvent.click(screen.getByRole("button", { name: /Ask Claread/ }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "收起 Ask Claread" })).toBeTruthy();
    });

    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes(
          `/api/web/reader-ask/threads?record_id=${recordId}&record_scope=reading_record`,
        ),
      ),
    ).toBe(true);
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes(
          `/api/web/reader-ask/threads/thread-rr-1?record_id=${recordId}&record_scope=reading_record`,
        ),
      ),
    ).toBe(true);
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/api/web/reader-ask/context-records"),
      ),
    ).toBe(false);
  });

  it("keeps the current reading surface visible when polling fails and shows a top-center connection toast", async () => {
    vi.useFakeTimers();
    const snapshot = makeSnapshot();
    installReaderRecordFetchMock(snapshot, {
      eventsResponder: () =>
        new Response(JSON.stringify({ ok: false, message: "事件轮询失败。" }), {
          status: 503,
          headers: { "content-type": "application/json" },
        }),
    });

    renderReadingRecordPage("rec_product_1", "workbench");

    await flushAsyncWork();
    expect(screen.getByTestId("reader-record-workbench-surface")).toBeTruthy();
    expect(screen.getByText(SOURCE_TEXT)).toBeTruthy();
    expect(screen.getByText(TRANSLATION_TEXT)).toBeTruthy();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    await flushAsyncWork();

    // No inline polling-error strip in the document flow.
    expect(screen.queryByTestId("reader-record-polling-error")).toBeNull();
    // toast.warning called with fixed id, top-center position, persistent
    // duration, and a retry action.
    expect(toastMock.warning).toHaveBeenCalledWith(
      "自动刷新已暂停",
      expect.objectContaining({
        id: "reader-record-polling-interrupted",
        position: "top-center",
        description: expect.stringContaining("事件轮询失败"),
        duration: Infinity,
        closeButton: true,
        action: expect.objectContaining({
          label: "重试",
        }),
      }),
    );
    // Reading surface and content remain visible.
    expect(screen.getByText(SOURCE_TEXT)).toBeTruthy();
    expect(screen.getByText(TRANSLATION_TEXT)).toBeTruthy();
    expect(screen.getByTestId("reader-record-workbench-surface")).toBeTruthy();
  });

  it("dismisses the connection toast when polling recovers and does not stack duplicate toasts", async () => {
    vi.useFakeTimers();
    const snapshot = makeSnapshot();
    let eventsFail = true;
    installReaderRecordFetchMock(snapshot, {
      eventsResponder: () => {
        if (eventsFail) {
          return new Response(JSON.stringify({ ok: false, message: "事件轮询失败。" }), {
            status: 503,
            headers: { "content-type": "application/json" },
          });
        }
        const afterSequence = snapshot.last_event_sequence;
        return new Response(
          JSON.stringify(
            makePollResponse(snapshot.record_id, afterSequence, {
              next_after_sequence: snapshot.last_event_sequence,
              last_event_sequence: snapshot.last_event_sequence,
            }),
          ),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      },
    });

    renderReadingRecordPage("rec_product_1", "workbench");

    await flushAsyncWork();

    // First polling tick fails → toast.warning called once.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    await flushAsyncWork();
    const firstWarningCount = toastMock.warning.mock.calls.length;
    expect(firstWarningCount).toBeGreaterThanOrEqual(1);

    // Second polling tick with the same error → should NOT create a second
    // toast (dedup via fixed id + lastShownErrorRef).
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    await flushAsyncWork();
    expect(toastMock.warning.mock.calls.length).toBe(firstWarningCount);

    // Recover: events succeed now.
    eventsFail = false;
    // Trigger a reload to clear the error (simulates user clicking retry
    // or the next polling tick succeeding after recovery). We advance time
    // enough for the polling to fire again, which should clear polling.error.
    // Since the events endpoint now succeeds, polling.error becomes null.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    await flushAsyncWork();

    // toast.dismiss should have been called with the fixed id to clear
    // the toast on recovery.
    expect(toastMock.dismiss).toHaveBeenCalledWith("reader-record-polling-interrupted");

    // Reading surface still renders.
    expect(screen.getByTestId("reader-record-workbench-surface")).toBeTruthy();
    expect(screen.getByText(SOURCE_TEXT)).toBeTruthy();
  });

  it("supports token click lookup and renders a read-only quick peek result", async () => {
    const snapshot = makeSnapshot("rec_lookup_1");
    const fetchMock = installReaderRecordFetchMock(snapshot);

    const { container } = renderReadingRecordPage("rec_lookup_1", "workbench");

    await screen.findByTestId("reader-record-workbench-surface");

    const sentenceTextElement = container.querySelector<HTMLElement>(
      '[data-reader-sentence-text="true"]',
    );
    const textNode = sentenceTextElement ? firstTextNode(sentenceTextElement) : null;
    expect(sentenceTextElement).not.toBeNull();
    expect(textNode).not.toBeNull();
    if (!sentenceTextElement || !textNode) {
      throw new Error("Expected sentence text node");
    }

    (
      document as Document & {
        caretRangeFromPoint?: (x: number, y: number) => Range | null;
      }
    ).caretRangeFromPoint = () => {
      const range = document.createRange();
      range.setStart(textNode, 15);
      range.collapse(true);
      return range;
    };

    fireEvent.click(sentenceTextElement, { clientX: 8, clientY: 8 });

    await screen.findByText("记忆；既有经验");
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).startsWith("/api/web/dict/lookup?"),
      ),
    ).toBe(true);
  });

  it("supports vocabulary mark click lookup and renders a read-only quick peek result", async () => {
    const snapshot = makeSnapshot("rec_mark_lookup_1", {}, { withVocabularyMark: true });
    const fetchMock = installReaderRecordFetchMock(snapshot);

    const { container } = renderReadingRecordPage(
      "rec_mark_lookup_1",
      "workbench",
    );

    await screen.findByTestId("reader-record-workbench-surface");

    const mark = container.querySelector<HTMLElement>(
      '[data-reader-mark-id="mark_vocab_memory"].reader-mark--interactive',
    );
    expect(mark).not.toBeNull();
    if (!mark) {
      throw new Error("Expected vocabulary mark");
    }

    const restoreRangeGeometry = installRangeGeometryStub({
      x: 0,
      y: 0,
      width: 64,
      height: 18,
      top: 0,
      left: 0,
      right: 64,
      bottom: 18,
      toJSON() {
        return this;
      },
    } as DOMRect);

    try {
      fireEvent.click(mark);

      await waitFor(() => {
        expect(
          fetchMock.mock.calls.some(([input]) =>
            String(input).startsWith("/api/web/dict/lookup?"),
          ),
        ).toBe(true);
      });
      const quickPeek = await screen.findByRole("dialog");
      expect(within(quickPeek).getByText("memory")).toBeTruthy();
      expect(within(quickPeek).getByText("/ˈmeməri/")).toBeTruthy();
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).includes("/api/web/vocabulary"),
        ),
      ).toBe(false);
    } finally {
      restoreRangeGeometry();
    }
  });

  it("supports selection lookup without calling vocabulary or user-asset persistence", async () => {
    const snapshot = makeSnapshot("rec_selection_lookup_1");
    const fetchMock = installReaderRecordFetchMock(snapshot);

    const { container } = renderReadingRecordPage(
      "rec_selection_lookup_1",
      "workbench",
    );

    await screen.findByTestId("reader-record-workbench-surface");

    const sentenceTextElement = container.querySelector<HTMLElement>(
      '[data-reader-sentence-text="true"]',
    );
    const textNode = sentenceTextElement ? firstTextNode(sentenceTextElement) : null;
    expect(sentenceTextElement).not.toBeNull();
    expect(textNode).not.toBeNull();
    if (!sentenceTextElement || !textNode) {
      throw new Error("Expected sentence text node");
    }

    const restoreRangeGeometry = installRangeGeometryStub({
      x: 0,
      y: 0,
      width: 64,
      height: 18,
      top: 0,
      left: 0,
      right: 64,
      bottom: 18,
      toJSON() {
        return this;
      },
    } as DOMRect);
    const restoreScrollTo = installElementScrollStub();

    const range = document.createRange();
    range.setStart(textNode, 14);
    range.setEnd(textNode, 20);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
    document.dispatchEvent(new Event("selectionchange"));
    try {
      const lookupButton = await screen.findByRole("button", { name: "查词" });
      fireEvent.click(lookupButton);

      await screen.findByTestId("reader-record-dictionary-panel");
      expect(screen.getAllByText("memory").length).toBeGreaterThan(0);
      expect(screen.getByText("记忆；既有经验")).toBeTruthy();
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).includes("/api/web/vocabulary"),
        ),
      ).toBe(false);
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).includes("/api/web/annotations"),
        ),
      ).toBe(false);
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).includes("/api/web/reader-notes"),
        ),
      ).toBe(false);
    } finally {
      restoreRangeGeometry();
      restoreScrollTo();
    }
  });

  it("shows enhancement failure status while keeping the reading body available", async () => {
    const snapshot = makeSnapshot(
      "rec_failed_1",
      {
        product_state: "failed",
        readiness_state: "initial_enhancement_ready",
      },
      {
        enhancementProgress: makeEnhancementProgress({
          overall_status: "failed",
          layers: [
            {
              capability: "grammar",
              layer_type: "sentence_analysis",
              status: "failed",
              job_status: "failed_terminal",
              job_type: "build_grammar_bundle",
              job_id: "job_grammar_1",
              target_type: "unit",
              target_scope: "unit",
              target_key: "unit_1",
              failure_code: "provider_timeout",
            },
          ],
        }),
      },
    );
    installReaderRecordFetchMock(snapshot);

    const { container } = renderReadingRecordPage("rec_failed_1", "workbench");

    await screen.findByTestId("reader-record-workbench-surface");
    expect(screen.getByTestId("reader-record-status-banner").textContent).toContain(
      "增强失败",
    );
    expect(screen.getByTestId("reader-record-status-banner").textContent).toContain(
      "正文和已发布内容仍可继续阅读",
    );
    expect(screen.getByTestId("reader-record-readiness-state").textContent).toContain(
      "当前阶段：初始增强已就绪",
    );
    expect(screen.getByTestId("reader-record-enhancement-progress").textContent).toContain(
      "部分增强失败",
    );
    expect(screen.getByTestId("reader-record-enhancement-progress").textContent).toContain(
      "语法·0/1 已完成 · 1 失败",
    );
    expect(screen.getByText(SOURCE_TEXT)).toBeTruthy();
    expect(screen.getByText(TRANSLATION_TEXT)).toBeTruthy();
    expect(
      container.querySelector(
        '[data-reader-anchor="sentence"][data-sentence-id="sent_1"]',
      ),
    ).not.toBeNull();
  });

  it("shows action-required status without blocking the current snapshot render", async () => {
    const snapshot = makeSnapshot(
      "rec_action_1",
      {
        product_state: "action_required",
        readiness_state: "article_ready",
      },
      {
        enhancementProgress: makeEnhancementProgress({
          overall_status: "action_required",
          layers: [
            {
              capability: "translation",
              layer_type: "translation",
              status: "action_required",
              job_status: "failed_terminal",
              job_type: "translate_unit",
              job_id: "job_translation_1",
              target_type: "unit",
              target_scope: "unit",
              target_key: "unit_1",
              failure_code: "reader_user_confirmation_required",
            },
          ],
        }),
      },
    );
    installReaderRecordFetchMock(snapshot);

    renderReadingRecordPage("rec_action_1", "workbench");

    await screen.findByTestId("reader-record-workbench-surface");
    expect(screen.getByTestId("reader-record-status-banner").textContent).toContain(
      "需要处理",
    );
    expect(screen.getByTestId("reader-record-status-banner").textContent).toContain(
      "本轮页面暂不提供处理动作",
    );
    expect(screen.getByTestId("reader-record-readiness-state").textContent).toContain(
      "当前阶段：正文可读",
    );
    expect(screen.getByTestId("reader-record-enhancement-progress").textContent).toContain(
      "需要处理",
    );
    expect(screen.getByTestId("reader-record-enhancement-progress").textContent).toContain(
      "译文·0/1 已完成 · 1 需处理",
    );
    expect(screen.getByText(SOURCE_TEXT)).toBeTruthy();
  });

  // T2.1: page reloadSnapshot in-flight skip coverage note.
  //
  // The page-level `reloadInFlightRef` returns `false` when a second
  // `reloadSnapshot` call overlaps with an in-flight reload (e.g. a user
  // asset save triggers `onRequestSnapshotReload` while a polling-triggered
  // reload is still fetching). This `false` return is the contract the
  // polling hook relies on to keep its cursor.
  //
  // The hook-level test "keeps cursor when onReloadRequired resolves false"
  // in polling.test.ts directly verifies this contract: when the parent
  // returns `false`, the cursor stays at the original `after_sequence` and
  // the next tick re-asks the same reload-required events. The page-level
  // guard is the mechanism that produces the `false`; testing it in
  // isolation here is impractical because the polling tick loop is
  // sequential (it `await`s the reload before scheduling the next tick),
  // so two concurrent reload calls cannot originate from the tick loop
  // alone — they require a user action overlapping with a pending fetch,
  // which needs a gated snapshot fetch that blocks the entire test.
});
