import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type { ReadingRecordListResponseDto } from "@/types/api/reading-records";

/**
 * Upstream client for the Reading Record list endpoint.
 *
 * Targets the new endpoint introduced by the reader-agentic-orchestration
 * initiative:
 *   - GET /reader/records
 *
 * This module intentionally does NOT touch the legacy `/records` endpoints.
 */

export interface ListReadingRecordsParams {
  limit?: number;
}

export function listUpstreamReadingRecords(
  sessionToken: string,
  params: ListReadingRecordsParams = {},
): Promise<UpstreamResult<ReadingRecordListResponseDto>> {
  const searchParams = new URLSearchParams();

  if (params.limit !== undefined) {
    searchParams.set("limit", String(params.limit));
  }

  const query = searchParams.toString();

  return fastApiFetch<ReadingRecordListResponseDto>(
    `/reader/records${query ? `?${query}` : ""}`,
    { sessionToken },
  );
}
