import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type {
  FeedbackCreateRequestDto,
  FeedbackListResponseDto,
  FeedbackResponseDto,
} from "@/types/api/feedback";

export function submitUpstreamFeedback(
  payload: FeedbackCreateRequestDto,
  sessionToken: string,
): Promise<UpstreamResult<FeedbackResponseDto>> {
  return fastApiFetch<FeedbackResponseDto>("/feedback", {
    method: "POST",
    sessionToken,
    body: JSON.stringify(payload),
  });
}

export function listUpstreamFeedback(
  sessionToken: string,
  cursor?: string,
  limit?: number,
  feedbackScope?: string,
  clientPlatform?: string,
  clientSurface?: string,
  status?: string,
): Promise<UpstreamResult<FeedbackListResponseDto>> {
  const params = new URLSearchParams();
  if (cursor) params.set("cursor", cursor);
  if (limit) params.set("limit", String(limit));
  if (feedbackScope) params.set("feedback_scope", feedbackScope);
  if (clientPlatform) params.set("client_platform", clientPlatform);
  if (clientSurface) params.set("client_surface", clientSurface);
  if (status) params.set("status", status);
  const qs = params.toString();
  return fastApiFetch<FeedbackListResponseDto>(`/feedback${qs ? `?${qs}` : ""}`, {
    method: "GET",
    sessionToken,
  });
}

export function deleteUpstreamFeedback(
  feedbackId: string,
  sessionToken: string,
): Promise<UpstreamResult<null>> {
  return fastApiFetch<null>(`/feedback/${feedbackId}`, {
    method: "DELETE",
    sessionToken,
  });
}
