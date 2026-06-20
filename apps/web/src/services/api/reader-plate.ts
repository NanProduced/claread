import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type {
  ReaderEventPollResponseDto,
  ReaderPlainTextSubmitRequestDto,
  ReaderPlainTextSubmitResponseDto,
  ReaderPlateSnapshotDto,
} from "@/types/api/reader-plate";

/**
 * Upstream client for the Reader Plate vertical slice.
 *
 * Targets the new endpoints introduced by the reader-agentic-orchestration
 * initiative:
 *   - POST /reader/records/plain-text
 *   - GET  /reader/records/{record_id}/snapshot
 *   - GET  /reader/records/{record_id}/events
 *
 * This module intentionally does NOT touch the legacy `/scene` endpoints.
 */

export function submitUpstreamReaderPlainText(
  payload: ReaderPlainTextSubmitRequestDto,
  sessionToken: string,
): Promise<UpstreamResult<ReaderPlainTextSubmitResponseDto>> {
  return fastApiFetch<ReaderPlainTextSubmitResponseDto>(
    `/reader/records/plain-text`,
    {
      method: "POST",
      sessionToken,
      body: JSON.stringify(payload),
    },
  );
}

export function getUpstreamReaderPlateSnapshot(
  recordId: string,
  sessionToken: string,
): Promise<UpstreamResult<ReaderPlateSnapshotDto>> {
  return fastApiFetch<ReaderPlateSnapshotDto>(
    `/reader/records/${encodeURIComponent(recordId)}/snapshot`,
    { sessionToken },
  );
}

export interface PollUpstreamReaderEventsParams {
  afterSequence?: number;
  limit?: number;
}

export function pollUpstreamReaderEvents(
  recordId: string,
  sessionToken: string,
  params: PollUpstreamReaderEventsParams = {},
): Promise<UpstreamResult<ReaderEventPollResponseDto>> {
  const searchParams = new URLSearchParams();

  if (params.afterSequence !== undefined) {
    searchParams.set("after_sequence", String(params.afterSequence));
  }

  if (params.limit !== undefined) {
    searchParams.set("limit", String(params.limit));
  }

  const query = searchParams.toString();

  return fastApiFetch<ReaderEventPollResponseDto>(
    `/reader/records/${encodeURIComponent(recordId)}/events${query ? `?${query}` : ""}`,
    { sessionToken },
  );
}
