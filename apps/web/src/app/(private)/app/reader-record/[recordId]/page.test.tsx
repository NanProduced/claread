/** @vitest-environment jsdom */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ReaderPlateSnapshotDto } from "@/types/api/reader-plate";

import ReadingRecordPage from "./page";

vi.mock("@/components/reader/plate/ReaderPlateSnapshotSurface", () => ({
  ReaderPlateSnapshotSurface: ({ value }: { value: unknown[] }) => (
    <div data-testid="reader-record-snapshot">snapshot units: {value.length}</div>
  ),
}));

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
      source_type: "plain_text",
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
      text_length_utf16: 16,
    },
    navigation: {
      units: [
        {
          unit_id: "unit_1",
          order_index: 1,
          unit_type: "body",
          boundary_quality: "normal",
          base_start_utf16: 0,
          base_end_utf16: 16,
          text_hash: "hash_1",
          hash_algorithm: "fnv1a32-utf16",
        },
      ],
    },
    anchor_segments: [],
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
        base_end_utf16: 16,
        text_hash: "hash_1",
        hash_algorithm: "fnv1a32-utf16",
        children: [],
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
  it("page.tsx does not reference render_scene_json or /scene", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/app/(private)/app/reader-record/[recordId]/page.tsx"),
      "utf-8",
    );
    expect(source).not.toContain("render_scene_json");
    expect(source).not.toContain("/scene");
  });
});

describe("ReadingRecordPage direct load", () => {
  it("loads snapshot data from the reader-plate BFF and renders the snapshot surface", async () => {
    const snapshot = makeSnapshot();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe("/api/web/reader-plate/rec_product_1/snapshot");
      return new Response(JSON.stringify({ ok: true, ...snapshot }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ReadingRecordPage params={{ recordId: "rec_product_1" }} />);

    await screen.findByTestId("reader-record-snapshot");
    expect(screen.getByTestId("reader-record-snapshot").textContent).toBe(
      "snapshot units: 1",
    );
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/web/reader-plate/rec_product_1/snapshot",
        expect.objectContaining({ method: "GET" }),
      );
    });
  });
});
