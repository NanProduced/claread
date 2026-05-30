import type { TaskStatusDto } from "@/types/api/tasks";

export interface WebAnalysisTaskView {
  taskId: string;
  recordId: string;
  status: TaskStatusDto;
  readerUrl: string;
  failureCode?: string | null;
  failureMessage?: string | null;
}

export interface WebAnalysisCurrentTaskResponse {
  ok: boolean;
  message?: string;
  hasActive?: boolean;
  task?: WebAnalysisTaskView | null;
}

export interface WebAnalysisTaskStatusResponse {
  ok: boolean;
  message?: string;
  taskId?: string;
  recordId?: string;
  status?: TaskStatusDto;
  readerUrl?: string;
  failureCode?: string | null;
  failureMessage?: string | null;
}

export const ANALYSIS_TERMINAL_STATUSES = new Set<TaskStatusDto>([
  "succeeded",
  "failed",
  "cancelled",
  "expired",
]);

export function isAnalysisTerminalStatus(status: TaskStatusDto | undefined): boolean {
  return Boolean(status && ANALYSIS_TERMINAL_STATUSES.has(status));
}

export async function fetchCurrentAnalysisTask(): Promise<WebAnalysisCurrentTaskResponse> {
  const response = await fetch("/api/web/analysis/current", {
    method: "GET",
  });
  const payload = (await response.json()) as WebAnalysisCurrentTaskResponse;

  if (!response.ok || !payload.ok) {
    throw new Error(payload.message || "查询当前解析任务失败。");
  }

  return payload;
}

export async function fetchAnalysisTaskStatus(taskId: string): Promise<WebAnalysisTaskStatusResponse> {
  const response = await fetch(`/api/web/analysis/tasks/${encodeURIComponent(taskId)}`, {
    method: "GET",
  });
  const payload = (await response.json()) as WebAnalysisTaskStatusResponse;

  if (!response.ok || !payload.ok) {
    throw new Error(payload.message || "查询任务状态失败。");
  }

  return payload;
}
