import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type {
  TaskSubmitRequestDto,
  TaskSubmitResponseDto,
  TaskStatusResponseDto,
} from "@/types/api/tasks";

export function submitUpstreamAnalysisTask(
  payload: TaskSubmitRequestDto,
  sessionToken: string,
): Promise<UpstreamResult<TaskSubmitResponseDto>> {
  return fastApiFetch<TaskSubmitResponseDto>("/analysis-tasks", {
    method: "POST",
    sessionToken,
    body: JSON.stringify(payload),
  });
}

export function getUpstreamAnalysisTaskStatus(
  taskId: string,
  sessionToken: string,
): Promise<UpstreamResult<TaskStatusResponseDto>> {
  return fastApiFetch<TaskStatusResponseDto>(`/analysis-tasks/${encodeURIComponent(taskId)}`, {
    method: "GET",
    sessionToken,
  });
}
