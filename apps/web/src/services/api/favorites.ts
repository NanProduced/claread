import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type {
  FavoriteCreateRequestDto,
  FavoriteCreateResponseDto,
  FavoriteDeleteResponseDto,
  FavoriteListResponseDto,
} from "@/types/api/favorites";

export function listFavorites(
  sessionToken: string,
): Promise<UpstreamResult<FavoriteListResponseDto>> {
  return fastApiFetch<FavoriteListResponseDto>("/favorites", { sessionToken });
}

export function createFavorite(
  sessionToken: string,
  body: FavoriteCreateRequestDto,
): Promise<UpstreamResult<FavoriteCreateResponseDto>> {
  return fastApiFetch<FavoriteCreateResponseDto>("/favorites", {
    method: "POST",
    sessionToken,
    body: JSON.stringify(body),
  });
}

export function deleteFavoriteByAnalysisRecordId(
  sessionToken: string,
  analysisRecordId: string,
): Promise<UpstreamResult<FavoriteDeleteResponseDto>> {
  return fastApiFetch<FavoriteDeleteResponseDto>(
    `/favorites/${encodeURIComponent(analysisRecordId)}`,
    {
      method: "DELETE",
      sessionToken,
    },
  );
}

export function deleteFavoriteByTarget(
  sessionToken: string,
  targetType: string,
  targetKey: string,
): Promise<UpstreamResult<FavoriteDeleteResponseDto>> {
  const searchParams = new URLSearchParams({
    target_type: targetType,
    target_key: targetKey,
  });

  return fastApiFetch<FavoriteDeleteResponseDto>(`/favorites/target?${searchParams.toString()}`, {
    method: "DELETE",
    sessionToken,
  });
}
