/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReadingRecordActivityIndicator } from "./reading-record-activity-indicator";

const navigationMock = vi.hoisted(() => ({
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => navigationMock,
}));

function makeReadingRecord(overrides: Record<string, unknown>) {
  return {
    readingRecordId: "reading_record_default",
    readerUrl: "/app/reader-record/reading_record_default",
    title: "Default Reading Record",
    createdAt: "2026-06-23T08:00:00.000Z",
    sourceType: "text",
    sourceMetadata: {},
    productState: "readable_enhancing",
    readinessState: "article_ready",
    lastEventSequence: 1,
    ...overrides,
  };
}

function stubReadingRecords(items: Array<Record<string, unknown>>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    expect(String(input)).toBe("/api/web/reading-records?limit=8");

    return new Response(
      JSON.stringify({
        ok: true,
        items,
        total: items.length,
        limit: 8,
      }),
      { status: 200, headers: { "content-type": "application/json" } },
    );
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderIndicator(pathname = "/app/library") {
  return render(<ReadingRecordActivityIndicator pathname={pathname} />);
}

beforeEach(() => {
  vi.resetAllMocks();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ReadingRecordActivityIndicator", () => {
  it.each([
    ["/app/reader-record/reading_record_default"],
    ["/app/reader-plate"],
    ["/app/reader-plate?record_id=reading_record_default"],
    ["/app/read"],
  ])("hides on %s without fetching Reading Records", (pathname) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    renderIndicator(pathname);

    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("fetches and renders on regular app shell pages", async () => {
    const fetchMock = stubReadingRecords([
      makeReadingRecord({
        title: "Visible from Library",
        productState: "readable_enhancing",
      }),
    ]);

    renderIndicator("/app/library");

    expect(await screen.findByText("可读·增强中")).toBeTruthy();
    expect(screen.getByText("Visible from Library")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("prioritizes action_required Reading Records and opens the returned readerUrl", async () => {
    stubReadingRecords([
      makeReadingRecord({
        readingRecordId: "reading_record_recent",
        readerUrl: "/app/reader-record/reading_record_recent",
        title: "Recent complete record",
        productState: "needs_confirmation",
      }),
      makeReadingRecord({
        readingRecordId: "reading_record_attention",
        readerUrl: "/app/reader-record/reading_record_attention",
        title: "Needs a decision",
        productState: "action_required",
      }),
    ]);

    renderIndicator();

    expect(await screen.findByText("需要处理")).toBeTruthy();
    expect(screen.getByText("Needs a decision")).toBeTruthy();
    expect(screen.queryByText("Recent complete record")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "打开新阅读记录" }));

    expect(navigationMock.push).toHaveBeenCalledWith(
      "/app/reader-record/reading_record_attention",
    );
  });

  it("shows failed Reading Records with visible failure text", async () => {
    stubReadingRecords([
      makeReadingRecord({
        readingRecordId: "reading_record_failed",
        readerUrl: "/app/reader-record/reading_record_failed",
        title: "Failed Reading Record",
        productState: "failed",
      }),
    ]);

    renderIndicator();

    expect(await screen.findByText("处理失败")).toBeTruthy();
    expect(screen.getByText("Failed Reading Record")).toBeTruthy();
  });

  it("falls back to the most recent Reading Record when no priority state exists", async () => {
    stubReadingRecords([
      makeReadingRecord({
        readingRecordId: "reading_record_recent",
        readerUrl: "/app/reader-record/reading_record_recent",
        title: "Recent Reading Record",
        productState: "needs_confirmation",
      }),
    ]);

    renderIndicator();

    expect(await screen.findByText("待确认")).toBeTruthy();
    expect(screen.getByText("Recent Reading Record")).toBeTruthy();
  });
});
