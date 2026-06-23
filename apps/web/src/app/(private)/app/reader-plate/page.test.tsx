/** @vitest-environment jsdom */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { appReaderPlateRoute } from "@/lib/routes";
import type { ReaderPlateSnapshotDto } from "@/types/api/reader-plate";

import ReaderPlatePage from "./page";

const navigationMock = vi.hoisted(() => ({
  replace: vi.fn(),
  searchParams: new URLSearchParams(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: navigationMock.replace,
  }),
  useSearchParams: () => navigationMock.searchParams,
}));

vi.mock("@/lib/reader-plate-snapshot/polling", () => ({
  useReaderPlatePolling: () => ({
    isPolling: false,
    error: null,
  }),
}));

vi.mock("@/components/reader/plate/ReaderPlateSnapshotSurface", () => ({
  ReaderPlateSnapshotSurface: ({ value }: { value: unknown[] }) => (
    <div data-testid="reader-plate-snapshot">snapshot units: {value.length}</div>
  ),
}));

function makeSnapshot(recordId = "rec_submit_1"): ReaderPlateSnapshotDto {
  return {
    schema_kind: "reader_plate_snapshot",
    snapshot_id: "snap_1",
    snapshot_taken_at: "2026-06-22T00:00:00Z",
    last_event_sequence: 1,
    record_id: recordId,
    record: {
      title: "Reader Plate Page Fixture",
      created_at: "2026-06-22T00:00:00Z",
      source_type: "plain_text",
      source_metadata: {},
      generation: 1,
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

beforeEach(() => {
  navigationMock.replace.mockReset();
  navigationMock.searchParams = new URLSearchParams();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/**
 * Static contract guard: the Web reader-plate page must not import or
 * reference the legacy `render_scene_json` / `/scene` path. Behavioural
 * coverage for the product path lives in:
 *   - apps/web/tests/e2e/reader-plate-smoke.spec.ts (Playwright, mocked BFF)
 *   - apps/web/src/components/reader/plate/ReaderPlateSnapshotSurface.test.tsx
 *
 * This unit guard exists to fail fast if a contributor accidentally
 * reintroduces the old contract.
 */
describe("ReaderPlatePage static contract", () => {
  it("page.tsx does not reference render_scene_json or /scene", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/app/(private)/app/reader-plate/page.tsx"),
      "utf-8",
    );
    expect(source).not.toContain("render_scene_json");
    expect(source).not.toContain("/scene");
  });

  it("BFF route handlers only proxy the new /reader/records contract", () => {
    const routesDir = resolve(
      process.cwd(),
      "src/app/api/web/reader-plate",
    );
    const submitRoute = readFileSync(
      resolve(routesDir, "submit/route.ts"),
      "utf-8",
    );
    const snapshotRoute = readFileSync(
      resolve(routesDir, "[recordId]/snapshot/route.ts"),
      "utf-8",
    );
    const eventsRoute = readFileSync(
      resolve(routesDir, "[recordId]/events/route.ts"),
      "utf-8",
    );

    for (const [name, source] of [
      ["submit", submitRoute],
      ["snapshot", snapshotRoute],
      ["events", eventsRoute],
    ] as const) {
      expect(source, `route ${name} must not import /scene adapter`).not.toContain(
        "/scene",
      );
      expect(source, `route ${name} must not reference render_scene_json`).not.toContain(
        "render_scene_json",
      );
    }
  });
});

describe("ReaderPlatePage submit-to-record path", () => {
  it("submits plain text, stores the returned snapshot, and replaces the URL with record_id", async () => {
    const snapshot = makeSnapshot();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe("/api/web/reader-plate/submit");
      return new Response(
        JSON.stringify({
          ok: true,
          record_id: "rec_submit_1",
          base_id: "base_1",
          article_ready_sequence: 1,
          snapshot,
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ReaderPlatePage />);

    fireEvent.change(screen.getByPlaceholderText("Paste an English article here"), {
      target: { value: "This is a short English article." },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始解析" }));

    await screen.findByTestId("reader-plate-snapshot");
    expect(screen.getByTestId("reader-plate-snapshot").textContent).toBe(
      "snapshot units: 1",
    );
    expect(navigationMock.replace).toHaveBeenCalledWith(appReaderPlateRoute("rec_submit_1"));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/web/reader-plate/submit",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ plainText: "This is a short English article." }),
      }),
    );
  });

  it("loads an existing record from record_id query", async () => {
    navigationMock.searchParams = new URLSearchParams("record_id=rec_existing_1");
    const snapshot = makeSnapshot("rec_existing_1");
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe(
        "/api/web/reader-plate/rec_existing_1/snapshot",
      );
      return new Response(JSON.stringify({ ok: true, ...snapshot }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ReaderPlatePage />);

    await screen.findByTestId("reader-plate-snapshot");
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/web/reader-plate/rec_existing_1/snapshot",
        expect.objectContaining({ method: "GET" }),
      );
    });
    expect(navigationMock.replace).not.toHaveBeenCalled();
  });
});
