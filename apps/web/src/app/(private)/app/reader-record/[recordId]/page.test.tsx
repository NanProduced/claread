/** @vitest-environment jsdom */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ReaderPlateSnapshotDto } from "@/types/api/reader-plate";
import type { WebDictResult } from "@/types/api/dict";

import ReadingRecordPage from "./page";

const SOURCE_TEXT = "Institutional memory shapes policy choices.";
const TRANSLATION_TEXT = "制度记忆会塑造政策选择。";

function makeSnapshot(
  recordId = "rec_product_1",
  recordOverrides: Partial<ReaderPlateSnapshotDto["record"]> = {},
  options?: { withVocabularyMark?: boolean },
): ReaderPlateSnapshotDto {
  return {
    schema_kind: "reader_plate_snapshot",
    snapshot_id: "snap_1",
    snapshot_taken_at: "2026-06-22T00:00:00Z",
    last_event_sequence: 1,
    record_id: recordId,
    record: {
      title: "Reading Record Page Fixture",
      created_at: "2026-06-22T00:00:00Z",
      source_type: "text",
      source_metadata: {},
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
      ...recordOverrides,
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
            target_scope: "anchor_segment",
            target_key: "seg_1",
            anchor_segment_id: "seg_1",
            target_language: "zh",
            confidence: "normal",
            notes: [],
            children: [{ text: TRANSLATION_TEXT }],
          },
        ],
      },
    ],
    enhancement_layers: [],
    parsed_decisions: [],
    user_assets: [],
    ask_supplements: [],
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

function installReaderRecordFetchMock(
  snapshot: ReaderPlateSnapshotDto,
  dictResult: WebDictResult = makeDictionaryEntryResult(),
) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);

    if (url === `/api/web/reader-plate/${snapshot.record_id}/snapshot`) {
      return new Response(JSON.stringify({ ok: true, ...snapshot }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }

    if (url.startsWith("/api/web/dict/lookup?")) {
      return new Response(JSON.stringify(dictResult), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }

    if (url.startsWith("/api/web/dict/entry?")) {
      return new Response(JSON.stringify(dictResult), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }

    throw new Error(`Unexpected fetch: ${url}`);
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function firstTextNode(element: HTMLElement): Text | null {
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  return walker.nextNode() as Text | null;
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
  vi.unstubAllGlobals();
});

describe("ReadingRecordPage static contract", () => {
  it("page and Workbench-backed surface do not reference legacy scene or analysis task data planes", () => {
    const sources = [
      "src/app/(private)/app/reader-record/[recordId]/page.tsx",
      "src/components/reader/ReaderRecordWorkbenchSurface.tsx",
    ].map((path) => readFileSync(resolve(process.cwd(), path), "utf-8"));

    sources.forEach((source) => {
      expect(source).not.toContain("render_scene_json");
      expect(source).not.toContain("/scene");
      expect(source).not.toContain("analysis-tasks");
    });
  });
});

describe("ReadingRecordPage direct load", () => {
  it("loads snapshot data from the reader-plate BFF and renders the Workbench-backed reading surface", async () => {
    const snapshot = makeSnapshot();
    const fetchMock = installReaderRecordFetchMock(snapshot);

    const { container } = render(
      <ReadingRecordPage params={{ recordId: "rec_product_1" }} />,
    );

    await screen.findByTestId("reader-record-workbench-surface");
    expect(screen.getByText(SOURCE_TEXT)).toBeTruthy();
    expect(screen.getByText(TRANSLATION_TEXT)).toBeTruthy();
    expect(screen.getByText("粘贴导入")).toBeTruthy();
    expect(screen.getByTestId("reader-record-status-banner").textContent).toContain(
      "可读增强中",
    );
    expect(screen.getByTestId("reader-record-readiness-state").textContent).toContain(
      "当前阶段：正文可读",
    );
    expect(
      container.querySelector(
        '[data-reader-anchor="sentence"][data-sentence-id="sent_1"]',
      ),
    ).not.toBeNull();
    expect(
      container.querySelector('[data-reader-sentence-text="true"]'),
    ).not.toBeNull();
    expect(screen.getByRole("button", { name: /Ask Claread/ })).toHaveProperty(
      "disabled",
      true,
    );
    expect(screen.getByRole("button", { name: /笔记\/高亮/ })).toHaveProperty(
      "disabled",
      true,
    );
    expect(screen.getByRole("button", { name: /词典保存/ })).toHaveProperty(
      "disabled",
      true,
    );
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/web/reader-plate/rec_product_1/snapshot",
        expect.objectContaining({ method: "GET" }),
      );
    });
  });

  it("supports token click lookup and renders a read-only quick peek result", async () => {
    const snapshot = makeSnapshot("rec_lookup_1");
    const fetchMock = installReaderRecordFetchMock(snapshot);

    const { container } = render(
      <ReadingRecordPage params={{ recordId: "rec_lookup_1" }} />,
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

    const { container } = render(
      <ReadingRecordPage params={{ recordId: "rec_mark_lookup_1" }} />,
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

    const { container } = render(
      <ReadingRecordPage params={{ recordId: "rec_selection_lookup_1" }} />,
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
    const snapshot = makeSnapshot("rec_failed_1", {
      product_state: "failed",
      readiness_state: "initial_enhancement_ready",
    });
    installReaderRecordFetchMock(snapshot);

    const { container } = render(
      <ReadingRecordPage params={{ recordId: "rec_failed_1" }} />,
    );

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
    expect(screen.getByText(SOURCE_TEXT)).toBeTruthy();
    expect(screen.getByText(TRANSLATION_TEXT)).toBeTruthy();
    expect(
      container.querySelector(
        '[data-reader-anchor="sentence"][data-sentence-id="sent_1"]',
      ),
    ).not.toBeNull();
  });

  it("shows action-required status without blocking the current snapshot render", async () => {
    const snapshot = makeSnapshot("rec_action_1", {
      product_state: "action_required",
      readiness_state: "article_ready",
    });
    installReaderRecordFetchMock(snapshot);

    render(<ReadingRecordPage params={{ recordId: "rec_action_1" }} />);

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
    expect(screen.getByText(SOURCE_TEXT)).toBeTruthy();
  });
});
