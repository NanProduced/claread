/** @vitest-environment jsdom */

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ReaderPlateSnapshotDto } from "@/types/api/reader-plate";

import ReadingRecordPage from "./plate-page";

const usePollingMock = vi.fn(() => ({ error: null }));

vi.mock("@/lib/reader-plate-snapshot/polling", () => ({
  useReaderPlatePolling: () => {
    usePollingMock();
    return { error: null };
  },
}));

vi.mock("@/components/reader/plate", () => ({
  ReaderRecordPlateSurface: ({ snapshot }: { snapshot: ReaderPlateSnapshotDto }) => (
    <div
      data-testid="reader-record-plate-surface"
      data-record-id={snapshot.record_id}
    />
  ),
}));

vi.mock("./ReaderOpenedBeacon", () => ({
  ReaderOpenedBeacon: () => null,
}));

function makeSnapshot(recordId: string): ReaderPlateSnapshotDto {
  return {
    record_id: recordId,
    record: { id: recordId, generation: 1 } as unknown as ReaderPlateSnapshotDto["record"],
    base: { base_id: "base_1" } as unknown as ReaderPlateSnapshotDto["base"],
    enhancement_layers: [],
    user_assets: [],
    last_event_sequence: 0,
  } as unknown as ReaderPlateSnapshotDto;
}

function renderPage(recordId: string) {
  return render(<ReadingRecordPage params={{ recordId }} />);
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  usePollingMock.mockClear();
});

describe("canonical Reader route", () => {
  it("loads the snapshot through the canonical record BFF and mounts Plate", async () => {
    const snapshot = makeSnapshot("rr_canonical_1");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true, ...snapshot }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderPage(snapshot.record_id);

    await screen.findByTestId("reader-record-plate-surface");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/web/reader/records/rr_canonical_1/snapshot",
      { method: "GET", headers: { accept: "application/json" } },
    );
    expect(screen.getByTestId("reader-record-plate-surface").getAttribute("data-record-id")).toBe(
      snapshot.record_id,
    );
  });

  it("keeps the route Plate-only and does not expose a Workbench switch", async () => {
    const snapshot = makeSnapshot("rr_plate_only");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ ok: true, ...snapshot }), { status: 200 }),
      ),
    );

    renderPage(snapshot.record_id);

    await screen.findByTestId("reader-record-plate-surface");
    expect(screen.queryByTestId("reader-record-workbench-surface")).toBeNull();
    await waitFor(() => expect(usePollingMock).toHaveBeenCalled());
  });

  it("keeps not-ready records recoverable without a legacy route fallback", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            ok: false,
            status: 409,
            code: "record_not_ready",
            message: "文档仍在解析，请稍后重试。",
          }),
          { status: 409, headers: { "content-type": "application/json" } },
        ),
      ),
    );

    renderPage("rr_not_ready");

    expect(await screen.findByText("文档仍在解析")).toBeTruthy();
    expect(screen.queryByRole("link", { name: /reader-record/i })).toBeNull();
  });
});
