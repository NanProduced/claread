import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type { ReaderSceneResponseDto } from "@/types/api/reader-scene";

export function getUpstreamReaderSceneById(
  recordId: string,
  sessionToken: string,
): Promise<UpstreamResult<ReaderSceneResponseDto>> {
  return fastApiFetch<ReaderSceneResponseDto>(`/reader/records/${encodeURIComponent(recordId)}/scene`, {
    sessionToken,
  });
}

export function getUpstreamReaderSceneByClientId(
  clientRecordId: string,
  sessionToken: string,
): Promise<UpstreamResult<ReaderSceneResponseDto>> {
  return fastApiFetch<ReaderSceneResponseDto>(
    `/reader/records/by-client-id/${encodeURIComponent(clientRecordId)}/scene`,
    { sessionToken },
  );
}
