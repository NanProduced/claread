import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type {
  UserAnnotationCreateRequestDto,
  UserAnnotationResponseDto,
  UserAnnotationUpdateRequestDto,
} from "@/types/api/annotations";

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

export function updateUserAnnotation(
  sessionToken: string,
  annotationId: string,
  body: UserAnnotationUpdateRequestDto,
): Promise<UpstreamResult<UserAnnotationResponseDto>> {
  return fastApiFetch<UserAnnotationResponseDto>(`/user-annotations/${encodeURIComponent(annotationId)}`, {
    method: "PATCH",
    sessionToken,
    body: JSON.stringify(body),
  });
}

export function deleteUserAnnotation(
  sessionToken: string,
  annotationId: string,
): Promise<UpstreamResult<{ ok: boolean }>> {
  return fastApiFetch<{ ok: boolean }>(`/user-annotations/${encodeURIComponent(annotationId)}`, {
    method: "DELETE",
    sessionToken,
  });
}
