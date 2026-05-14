import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type {
  UserAnnotationCreateRequestDto,
  UserAnnotationListResponseDto,
  UserAnnotationResponseDto,
} from "@/types/api/annotations";

export interface ListUserAnnotationsParams {
  analysisRecordId?: string;
  limit?: number;
  offset?: number;
}

export function listUserAnnotations(
  sessionToken: string,
  params: ListUserAnnotationsParams = {},
): Promise<UpstreamResult<UserAnnotationListResponseDto>> {
  const searchParams = new URLSearchParams();

  if (params.analysisRecordId) {
    searchParams.set("analysis_record_id", params.analysisRecordId);
  }

  if (params.limit !== undefined) {
    searchParams.set("limit", String(params.limit));
  }

  if (params.offset !== undefined) {
    searchParams.set("offset", String(params.offset));
  }

  const query = searchParams.toString();

  return fastApiFetch<UserAnnotationListResponseDto>(
    `/user-annotations${query ? `?${query}` : ""}`,
    { sessionToken },
  );
}

export function createUserAnnotation(
  sessionToken: string,
  body: UserAnnotationCreateRequestDto,
): Promise<UpstreamResult<UserAnnotationResponseDto>> {
  return fastApiFetch<UserAnnotationResponseDto>("/user-annotations", {
    method: "POST",
    sessionToken,
    body: JSON.stringify(body),
  });
}
