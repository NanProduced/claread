import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type {
  ReaderAskActionConfirmRequestDto,
  ReaderAskActionConfirmResponseDto,
  ReaderAskContextRecordSearchResponseDto,
  ReaderAskDeleteSupplementResponseDto,
  ReaderAskMessageStreamRequestDto,
  ReaderAskThreadCreateRequestDto,
  ReaderAskThreadDetailDto,
  ReaderAskThreadListResponseDto,
  ReaderAskThreadSummaryDto,
} from "@/types/api/reader-ask";

const DEFAULT_BASE_URL = "http://127.0.0.1:8000";

function getBaseUrl(): string {
  const raw =
    process.env.CLAREAD_FASTAPI_BASE_URL ??
    process.env.CLAREAD_API_BASE_URL ??
    DEFAULT_BASE_URL;

  return raw.replace(/\/+$/, "");
}

export function listUpstreamReaderAskThreads(
  recordId: string,
  sessionToken: string,
): Promise<UpstreamResult<ReaderAskThreadListResponseDto>> {
  const searchParams = new URLSearchParams({ record_id: recordId });
  return fastApiFetch<ReaderAskThreadListResponseDto>(`/reader-ask/threads?${searchParams.toString()}`, {
    sessionToken,
  });
}

export function listUpstreamReaderAskContextRecords(
  query: string,
  excludeRecordId: string | null,
  sessionToken: string,
): Promise<UpstreamResult<ReaderAskContextRecordSearchResponseDto>> {
  const searchParams = new URLSearchParams({ query });
  if (excludeRecordId) {
    searchParams.set("exclude_record_id", excludeRecordId);
  }
  return fastApiFetch<ReaderAskContextRecordSearchResponseDto>(
    `/reader-ask/context-records?${searchParams.toString()}`,
    {
      sessionToken,
    },
  );
}

export function createUpstreamReaderAskThread(
  body: ReaderAskThreadCreateRequestDto,
  sessionToken: string,
): Promise<UpstreamResult<ReaderAskThreadSummaryDto>> {
  return fastApiFetch<ReaderAskThreadSummaryDto>("/reader-ask/threads", {
    method: "POST",
    sessionToken,
    body: JSON.stringify(body),
  });
}

export function getUpstreamReaderAskThread(
  threadId: string,
  sessionToken: string,
): Promise<UpstreamResult<ReaderAskThreadDetailDto>> {
  return fastApiFetch<ReaderAskThreadDetailDto>(`/reader-ask/threads/${threadId}`, {
    sessionToken,
  });
}

export function resetUpstreamReaderAskThread(
  threadId: string,
  sessionToken: string,
): Promise<UpstreamResult<ReaderAskThreadDetailDto>> {
  return fastApiFetch<ReaderAskThreadDetailDto>(`/reader-ask/threads/${threadId}/reset`, {
    method: "POST",
    sessionToken,
  });
}

export function deleteUpstreamReaderAskSupplement(
  supplementId: string,
  sessionToken: string,
): Promise<UpstreamResult<ReaderAskDeleteSupplementResponseDto>> {
  return fastApiFetch<ReaderAskDeleteSupplementResponseDto>(
    `/reader-ask/supplements/${supplementId}`,
    {
      method: "DELETE",
      sessionToken,
    },
  );
}

export function confirmUpstreamReaderAskAction(
  threadId: string,
  actionId: string,
  body: ReaderAskActionConfirmRequestDto,
  sessionToken: string,
): Promise<UpstreamResult<ReaderAskActionConfirmResponseDto>> {
  return fastApiFetch<ReaderAskActionConfirmResponseDto>(
    `/reader-ask/threads/${threadId}/actions/${actionId}/confirm`,
    {
      method: "POST",
      sessionToken,
      body: JSON.stringify(body),
    },
  );
}

export async function createUpstreamReaderAskStream(
  threadId: string,
  body: ReaderAskMessageStreamRequestDto,
  sessionToken: string,
): Promise<Response> {
  return fetch(`${getBaseUrl()}/reader-ask/threads/${threadId}/messages/stream`, {
    method: "POST",
    headers: {
      accept: "text/event-stream",
      authorization: `Bearer ${sessionToken}`,
      "content-type": "application/json",
    },
    body: JSON.stringify(body),
    cache: "no-store",
  });
}

export async function retryUpstreamReaderAskMessage(
  threadId: string,
  messageId: string,
  sessionToken: string,
): Promise<Response> {
  return fetch(`${getBaseUrl()}/reader-ask/threads/${threadId}/messages/${messageId}/retry/stream`, {
    method: "POST",
    headers: {
      accept: "text/event-stream",
      authorization: `Bearer ${sessionToken}`,
    },
    cache: "no-store",
  });
}
