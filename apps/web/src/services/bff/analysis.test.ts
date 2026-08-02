import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

vi.mock("@/services/bff/session", () => ({
  getWebSession: vi.fn(),
}));

vi.mock("@/services/api/tasks", () => ({
  getUpstreamAnalysisTaskStatus: vi.fn(),
  getUpstreamCurrentAnalysisTask: vi.fn(),
  submitUpstreamAnalysisTask: vi.fn(),
}));

import { getWebSession } from "@/services/bff/session";
import {
  getUpstreamCurrentAnalysisTask,
  submitUpstreamAnalysisTask,
} from "@/services/api/tasks";
import { appReaderRoute } from "@/lib/routes";
import {
  getCurrentAnalysisTaskFromWeb,
  submitAnalysisFromWeb,
} from "./analysis";

const mockSession = {
  kind: "authenticated",
  sessionToken: "session-token",
  source: "cookie",
} as const;

describe("analysis BFF active task projection", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("projects an active upstream task for the web client", async () => {
    vi.mocked(getUpstreamCurrentAnalysisTask).mockResolvedValue({
      ok: true,
      data: {
        has_active: true,
        task: {
          task_id: "task-1",
          record_id: "record-1",
          cloud_record_id: "record-1",
          client_record_id: "client-1",
          status: "running",
          failure_code: null,
          failure_message: null,
          quota_cost_points: 1,
          queued_at: "2026-05-31T00:00:00Z",
          started_at: "2026-05-31T00:00:01Z",
          finished_at: null,
          created_at: "2026-05-31T00:00:00Z",
          updated_at: "2026-05-31T00:00:02Z",
        },
      },
    });

    await expect(getCurrentAnalysisTaskFromWeb()).resolves.toEqual({
      ok: true,
      hasActive: true,
      task: {
        taskId: "task-1",
        recordId: "record-1",
        status: "running",
        readerUrl: appReaderRoute("record-1"),
        failureCode: null,
        failureMessage: null,
      },
    });
  });

  it("returns no active task when upstream has none", async () => {
    vi.mocked(getUpstreamCurrentAnalysisTask).mockResolvedValue({
      ok: true,
      data: {
        has_active: false,
        task: null,
      },
    });

    await expect(getCurrentAnalysisTaskFromWeb()).resolves.toEqual({
      ok: true,
      hasActive: false,
      task: null,
    });
  });

  it("turns active-task submit conflicts into a recoverable task", async () => {
    vi.mocked(submitUpstreamAnalysisTask).mockResolvedValue({
      ok: false,
      status: 409,
      message: "You already have an active analysis task.",
      payload: {
        task_id: "conflict-task",
        cloud_record_id: "conflict-record",
        status: "running",
      },
    });
    vi.mocked(getUpstreamCurrentAnalysisTask).mockResolvedValue({
      ok: true,
      data: {
        has_active: true,
        task: {
          task_id: "active-task",
          record_id: "active-record",
          cloud_record_id: "active-record",
          client_record_id: null,
          status: "running",
          failure_code: null,
          failure_message: null,
          quota_cost_points: 1,
          queued_at: "2026-05-31T00:00:00Z",
          started_at: null,
          finished_at: null,
          created_at: "2026-05-31T00:00:00Z",
          updated_at: "2026-05-31T00:00:02Z",
        },
      },
    });

    await expect(
      submitAnalysisFromWeb({
        text: "This is a short English article.",
        readingGoal: "daily_reading",
        readingVariant: "intermediate_reading",
      }),
    ).resolves.toMatchObject({
      ok: true,
      taskId: "active-task",
      recordId: "active-record",
      status: "running",
      readerUrl: appReaderRoute("active-record"),
    });
  });

  it("keeps analysis submit landing on the canonical Reader route", async () => {
    vi.mocked(submitUpstreamAnalysisTask).mockResolvedValue({
      ok: true,
      data: {
        task_id: "legacy-task-1",
        record_id: "legacy-record-row-1",
        cloud_record_id: "legacy-cloud-record-1",
        client_record_id: "client-1",
        status: "succeeded",
        created: true,
      },
    });

    await expect(
      submitAnalysisFromWeb({
        text: "This is a short English article.",
        readingGoal: "daily_reading",
        readingVariant: "intermediate_reading",
      }),
    ).resolves.toEqual({
      ok: true,
      taskId: "legacy-task-1",
      recordId: "legacy-cloud-record-1",
      status: "succeeded",
      readerUrl: appReaderRoute("legacy-cloud-record-1"),
      message: "解析完成，正在打开 Reader。",
    });
  });

  it("rejects current-task lookup for anonymous users", async () => {
    vi.mocked(getWebSession).mockResolvedValue({
      kind: "anonymous",
      source: "none",
    });

    await expect(getCurrentAnalysisTaskFromWeb()).resolves.toMatchObject({
      ok: false,
      status: 401,
      code: "auth_required",
    });
  });
});
