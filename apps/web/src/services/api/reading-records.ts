import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type {
  ReaderRecordDeletedResponseDto,
  ReaderRecordOpenedResponseDto,
  ReaderRecordRecentRemovedResponseDto,
  ReaderRecoveryResponseDto,
  ReadingRecordListResponseDto,
} from "@/types/api/reading-records";

/**
 * Upstream client for the Reading Record list endpoint.
 *
 * Targets the new endpoint introduced by the Reader orchestration
 * initiative:
 *   - GET /reader/records
 *
 * This module intentionally does NOT touch the legacy `/records` endpoints.
 */

export interface ListReadingRecordsParams {
  limit?: number;
  query?: string;
  productStates?: string[];
  recentOnly?: boolean;
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

  if (params.recentOnly === true) {
    searchParams.set("recent_only", "true");
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

export function hideReaderRecordFromRecent(
  sessionToken: string,
  recordId: string,
): Promise<UpstreamResult<ReaderRecordRecentRemovedResponseDto>> {
  return fastApiFetch<ReaderRecordRecentRemovedResponseDto>(
    `/reader/records/${encodeURIComponent(recordId)}/recent`,
    { sessionToken, method: "DELETE" },
  );
}

export function deleteReaderRecord(
  sessionToken: string,
  recordId: string,
): Promise<UpstreamResult<ReaderRecordDeletedResponseDto>> {
  return fastApiFetch<ReaderRecordDeletedResponseDto>(
    `/reader/records/${encodeURIComponent(recordId)}`,
    { sessionToken, method: "DELETE" },
  );
}

/**
 * Manual same-generation recovery. No request body: identity comes from
 * the session token and the trigger is fixed to manual on the backend.
 */
export function recoverReaderRecordUpstream(
  sessionToken: string,
  recordId: string,
): Promise<UpstreamResult<ReaderRecoveryResponseDto>> {
  return fastApiFetch<ReaderRecoveryResponseDto>(
    `/reader/records/${encodeURIComponent(recordId)}/recovery`,
    { sessionToken, method: "POST" },
  );
}
