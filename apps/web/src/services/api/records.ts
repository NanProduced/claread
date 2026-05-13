import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type { RecordResponseDto } from "@/types/api/records";

export function getUpstreamRecordById(
  recordId: string,
  sessionToken: string,
): Promise<UpstreamResult<RecordResponseDto>> {
  return fastApiFetch<RecordResponseDto>(`/records/${encodeURIComponent(recordId)}`, {
    sessionToken,
  });
}

export function getUpstreamRecordByClientId(
  clientRecordId: string,
  sessionToken: string,
): Promise<UpstreamResult<RecordResponseDto>> {
  return fastApiFetch<RecordResponseDto>(
    `/records/by-client-id/${encodeURIComponent(clientRecordId)}`,
    { sessionToken },
  );
}
