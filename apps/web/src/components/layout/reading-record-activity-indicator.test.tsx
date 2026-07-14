/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
});

describe("ReadingRecordActivityIndicator", () => {
  it.each([
    ["/app/reader-record/reading_record_default"],
    ["/app/reader-plate"],
    ["/app/reader-plate?record_id=reading_record_default"],
    ["/app/read"],
  ])("hides on %s without polling the legacy task", async (pathname) => {
    renderIndicator(pathname);

    expect(fetchCurrentAnalysisTask).not.toHaveBeenCalled();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("renders nothing when neither activity nor legacy task exists", async () => {
    renderIndicator("/app/library");

    await waitFor(() => {
      expect(fetchCurrentAnalysisTask).toHaveBeenCalled();
    });

    expect(screen.queryByRole("status")).toBeNull();
  });

  it("falls back to a legacy-only card when only legacy task exists", async () => {
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