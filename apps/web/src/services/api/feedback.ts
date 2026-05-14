import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type {
  FeedbackCreateRequestDto,
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
