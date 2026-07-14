import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type {
  ReaderRecordOpenedResponseDto,
  ReadingRecordListResponseDto,
} from "@/types/api/reading-records";

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
  query?: string;
  productStates?: string[];
}

export function listUpstreamReadingRecords(
  sessionToken: string,
  params: ListReadingRecordsParams = {},
): Promise<UpstreamResult<ReadingRecordListResponseDto>> {
  const searchParams = new URLSearchParams();

  if (params.limit !== undefined) {
    searchParams.set("limit", String(params.limit));
  }

  const normalizedQuery = params.query?.trim();
  if (normalizedQuery) {
    searchParams.set("query", normalizedQuery);
  }

  if (params.productStates && params.productStates.length > 0) {
    searchParams.set("product_state", params.productStates.join(","));
  }

  const query = searchParams.toString();

  return fastApiFetch<ReadingRecordListResponseDto>(
    `/reader/records${query ? `?${query}` : ""}`,
    { sessionToken },
  );
}

export function markReaderRecordOpened(
  sessionToken: string,
  recordId: string,
): Promise<UpstreamResult<ReaderRecordOpenedResponseDto>> {
  return fastApiFetch<ReaderRecordOpenedResponseDto>(
    `/reader/records/${encodeURIComponent(recordId)}/opened`,
    { sessionToken, method: "POST" },
  );
}
