import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type {
  VocabularyCreateRequestDto,
  VocabularyListResponseDto,
  VocabularyUpsertResponseDto,
} from "@/types/api/vocabulary";

export interface ListVocabularyParams {
  page?: number;
  limit?: number;
  masteryStatus?: string;
  lite?: boolean;
}

export function listVocabulary(
  sessionToken: string,
  params: ListVocabularyParams = {},
): Promise<UpstreamResult<VocabularyListResponseDto>> {
  const searchParams = new URLSearchParams();

  if (params.page !== undefined) {
    searchParams.set("page", String(params.page));
  }

  if (params.limit !== undefined) {
    searchParams.set("limit", String(params.limit));
  }

  if (params.masteryStatus) {
    searchParams.set("mastery_status", params.masteryStatus);
  }

  if (params.lite === true) {
    searchParams.set("lite", "true");
  }

  const query = searchParams.toString();

  return fastApiFetch<VocabularyListResponseDto>(
    `/vocabulary${query ? `?${query}` : ""}`,
    { sessionToken },
  );
}

export function createVocabulary(
  sessionToken: string,
  body: VocabularyCreateRequestDto,
): Promise<UpstreamResult<VocabularyUpsertResponseDto>> {
  return fastApiFetch<VocabularyUpsertResponseDto>("/vocabulary", {
    method: "POST",
    sessionToken,
    body: JSON.stringify(body),
  });
}
