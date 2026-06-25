/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  fetchAnalysisTaskStatus,
  fetchCurrentAnalysisTask,
} from "@/lib/analysis-task-client";
import { ReadingRecordActivityIndicator } from "./reading-record-activity-indicator";

const navigationMock = vi.hoisted(() => ({
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => navigationMock,
}));

vi.mock("@/lib/analysis-task-client", () => ({
  fetchAnalysisTaskStatus: vi.fn(),
  fetchCurrentAnalysisTask: vi.fn(),
  isAnalysisTerminalStatus: (status: string) =>
    ["succeeded", "failed", "cancelled", "expired"].includes(status),
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
    expect(String(input)).toBe(
      "/api/web/reading-records?limit=8&productState=processing%2Creadable_enhancing%2Caction_required%2Cfailed",
    );

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
  vi.mocked(fetchCurrentAnalysisTask).mockResolvedValue({
    ok: true,
    hasActive: false,
    task: null,
  });
  vi.mocked(fetchAnalysisTaskStatus).mockResolvedValue({
    ok: true,
    status: "running",
    taskId: "task_1",
    recordId: "legacy_record_1",
    readerUrl: "/app/reader/legacy_record_1",
  });
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
  ])("hides on %s without fetching activity data", (pathname) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    renderIndicator(pathname);

    expect(fetchMock).not.toHaveBeenCalled();
    expect(fetchCurrentAnalysisTask).not.toHaveBeenCalled();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("fetches active Reading Records with productState filtering and renders the card", async () => {
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
        productState: "readable_enhancing",
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

    fireEvent.click(screen.getByRole("button", { name: "打开阅读记录" }));

    expect(navigationMock.push).toHaveBeenCalledWith(
      "/app/reader-record/reading_record_attention",
    );
  });

  it("merges a legacy active task into the same Reading Record card", async () => {
    stubReadingRecords([
      makeReadingRecord({
        title: "Merged Activity Record",
        productState: "processing",
      }),
    ]);
    vi.mocked(fetchCurrentAnalysisTask).mockResolvedValue({
      ok: true,
      hasActive: true,
      task: {
        taskId: "task_legacy",
        recordId: "legacy_record_1",
        status: "running",
        readerUrl: "/app/reader/legacy_record_1",
      },
    });

    renderIndicator();

    expect(await screen.findByText("处理中")).toBeTruthy();
    expect(screen.getByText("Merged Activity Record")).toBeTruthy();
    expect(screen.getByText("旧任务")).toBeTruthy();
    expect(
      screen.getByText("另有旧任务仍在透读，可通过旧入口继续查看。"),
    ).toBeTruthy();
    expect(screen.getAllByRole("status")).toHaveLength(1);
  });

  it("falls back to a legacy-only card when no Reading Record activity exists", async () => {
    stubReadingRecords([]);
    vi.mocked(fetchCurrentAnalysisTask).mockResolvedValue({
      ok: true,
      hasActive: true,
      task: {
        taskId: "task_legacy",
        recordId: "legacy_record_1",
        status: "running",
        readerUrl: "/app/reader/legacy_record_1",
      },
    });

    renderIndicator();

    expect(await screen.findByText("旧任务处理中")).toBeTruthy();
    expect(screen.getByText("旧 Reader 任务仍在运行")).toBeTruthy();
    expect(screen.getByText("Legacy")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "打开旧任务" }));

    expect(navigationMock.push).toHaveBeenCalledWith(
      "/app/reader/legacy_record_1",
    );
  });
});
