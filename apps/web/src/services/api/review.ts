import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type {
  ReviewResultResponseDto,
  ReviewSubmitRequestDto,
  ReviewSubmitResultDto,
} from "@/types/api/review";
import type { VocabularyListResponseDto } from "@/types/api/vocabulary";

export function getUpstreamDueReviewVocabulary(
  sessionToken: string,
  limit = 20,
): Promise<UpstreamResult<VocabularyListResponseDto>> {
  const searchParams = new URLSearchParams({ limit: String(limit) });

  return fastApiFetch<VocabularyListResponseDto>(
    `/vocabulary/review/due?${searchParams.toString()}`,
    { sessionToken },
  );
}

export function submitUpstreamVocabularyReview(
  vocabId: string,
  result: ReviewSubmitResultDto,
  sessionToken: string,
): Promise<UpstreamResult<ReviewResultResponseDto>> {
  const body: ReviewSubmitRequestDto = { result };

  return fastApiFetch<ReviewResultResponseDto>(
    `/vocabulary/${encodeURIComponent(vocabId)}/review`,
    {
      method: "POST",
      sessionToken,
      body: JSON.stringify(body),
    },
  );
}
