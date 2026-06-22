/** @vitest-environment jsdom */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ReaderPlateSnapshotDto } from "@/types/api/reader-plate";

import ReadingRecordPage from "./page";

const SOURCE_TEXT = "Institutional memory shapes policy choices.";
const TRANSLATION_TEXT = "制度记忆会塑造政策选择。";

function makeSnapshot(recordId = "rec_product_1"): ReaderPlateSnapshotDto {
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

afterEach(() => {
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
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe("/api/web/reader-plate/rec_product_1/snapshot");
      return new Response(JSON.stringify({ ok: true, ...snapshot }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const { container } = render(
      <ReadingRecordPage params={{ recordId: "rec_product_1" }} />,
    );

    await screen.findByTestId("reader-record-workbench-surface");
    expect(screen.getByText(SOURCE_TEXT)).toBeTruthy();
    expect(screen.getByText(TRANSLATION_TEXT)).toBeTruthy();
    expect(screen.getByText("粘贴导入")).toBeTruthy();
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
});
