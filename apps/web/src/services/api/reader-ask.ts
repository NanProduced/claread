import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type {
  ReaderAskActionConfirmRequestDto,
  ReaderAskActionConfirmResponseDto,
  ReaderAskAttachmentDto,
  ReaderAskEntryActionDto,
  ReaderAskContextRecordSearchResponseDto,
  ReaderAskDeleteSupplementResponseDto,
  ReaderAskMessageRetryRequestDto,
  ReaderAskMessageStreamRequestDto,
  ReaderAskModelOptionListResponseDto,
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

export type ReaderAskTransportScope = "analysis" | "reading_record";

interface ReaderRecordAskMessageRequestDto {
  content: string;
  entry_action?: ReaderAskEntryActionDto | null;
  model?: string | null;
  anchor?: Record<string, unknown> | null;
}

function readingRecordAskPath(recordId: string, suffix = ""): string {
  return `/reader/records/${encodeURIComponent(recordId)}/ask${suffix}`;
}

function readingRecordAnchorFromAttachments(
  attachments: ReaderAskAttachmentDto[],
): Record<string, unknown> | null {
  for (const attachment of attachments) {
    const candidate = (attachment.metadata as ReaderAskAttachmentDto["metadata"] & {
      reading_record_anchor?: unknown;
    }).reading_record_anchor;
    if (candidate && typeof candidate === "object") {
      return candidate as Record<string, unknown>;
    }
  }
  return null;
}

function toReadingRecordAskMessageRequest(
  body: ReaderAskMessageStreamRequestDto,
): ReaderRecordAskMessageRequestDto {
  return {
    content: body.content,
    entry_action: body.entry_action ?? null,
    model: body.model ?? null,
    anchor: readingRecordAnchorFromAttachments(body.attachments),
  };
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

export function listUpstreamReadingRecordAskThreads(
  recordId: string,
  sessionToken: string,
): Promise<UpstreamResult<ReaderAskThreadListResponseDto>> {
  return fastApiFetch<ReaderAskThreadListResponseDto>(readingRecordAskPath(recordId, "/threads"), {
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

export function listUpstreamReaderAskModelOptions(
  sessionToken: string,
): Promise<UpstreamResult<ReaderAskModelOptionListResponseDto>> {
  return fastApiFetch<ReaderAskModelOptionListResponseDto>("/reader-ask/model-options", {
    sessionToken,
  });
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

export function createUpstreamReadingRecordAskDefaultThread(
  recordId: string,
  sessionToken: string,
): Promise<UpstreamResult<ReaderAskThreadSummaryDto>> {
  return fastApiFetch<ReaderAskThreadSummaryDto>(readingRecordAskPath(recordId, "/threads/default"), {
    method: "POST",
    sessionToken,
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

export function getUpstreamReadingRecordAskThread(
  recordId: string,
  threadId: string,
  sessionToken: string,
): Promise<UpstreamResult<ReaderAskThreadDetailDto>> {
  return fastApiFetch<ReaderAskThreadDetailDto>(readingRecordAskPath(recordId, `/threads/${threadId}`), {
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

export function resetUpstreamReadingRecordAskThread(
  recordId: string,
  threadId: string,
  sessionToken: string,
): Promise<UpstreamResult<ReaderAskThreadDetailDto>> {
  return fastApiFetch<ReaderAskThreadDetailDto>(readingRecordAskPath(recordId, `/threads/${threadId}/reset`), {
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

export function deleteUpstreamReadingRecordAskSupplement(
  recordId: string,
  supplementId: string,
  sessionToken: string,
): Promise<UpstreamResult<ReaderAskDeleteSupplementResponseDto>> {
  return fastApiFetch<ReaderAskDeleteSupplementResponseDto>(
    readingRecordAskPath(recordId, `/supplements/${supplementId}`),
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

export function confirmUpstreamReadingRecordAskAction(
  recordId: string,
  threadId: string,
  actionId: string,
  body: ReaderAskActionConfirmRequestDto,
  sessionToken: string,
): Promise<UpstreamResult<ReaderAskActionConfirmResponseDto>> {
  return fastApiFetch<ReaderAskActionConfirmResponseDto>(
    readingRecordAskPath(recordId, `/threads/${threadId}/actions/${actionId}/confirm`),
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

export async function createUpstreamReadingRecordAskStream(
  recordId: string,
  threadId: string,
  body: ReaderAskMessageStreamRequestDto,
  sessionToken: string,
): Promise<Response> {
  return fetch(`${getBaseUrl()}${readingRecordAskPath(recordId, `/threads/${threadId}/messages/stream`)}`, {
    method: "POST",
    headers: {
      accept: "text/event-stream",
      authorization: `Bearer ${sessionToken}`,
      "content-type": "application/json",
    },
    body: JSON.stringify(toReadingRecordAskMessageRequest(body)),
    cache: "no-store",
  });
}

/** Regenerate (not resume/continue) the assistant answer. Calls the upstream retry endpoint. */
export async function retryUpstreamReaderAskMessage(
  threadId: string,
  messageId: string,
  body: ReaderAskMessageRetryRequestDto,
  sessionToken: string,
): Promise<Response> {
  return fetch(`${getBaseUrl()}/reader-ask/threads/${threadId}/messages/${messageId}/retry/stream`, {
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

export async function retryUpstreamReadingRecordAskMessage(
  recordId: string,
  threadId: string,
  messageId: string,
  body: ReaderAskMessageRetryRequestDto,
  sessionToken: string,
): Promise<Response> {
  return fetch(`${getBaseUrl()}${readingRecordAskPath(recordId, `/threads/${threadId}/messages/${messageId}/retry/stream`)}`, {
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
