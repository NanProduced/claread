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
  WebSearchModeDto,
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
  /**
   * User-visible web search request mode (mirrors backend `WebSearchMode`).
   * Forwarded to the Reading Record Ask upstream so the host can decide
   * whether to mount the `search_web` capability for this turn. `allowed`
   * only grants turn capability; it never forces a search.
   */
  web_search_mode?: WebSearchModeDto;
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
    // Forward the user-visible web search request mode so the host can
    // mount the `search_web` capability for this turn. Default to `disabled`
    // when omitted — `allowed` only grants capability, never forces a search.
    web_search_mode: body.web_search_mode ?? "disabled",
  };
}

function toGenericReaderAskAttachmentMetadata(
  metadata: ReaderAskAttachmentDto["metadata"],
): Omit<ReaderAskAttachmentDto["metadata"], "reading_record_anchor"> {
  return {
    source_surface: metadata.source_surface,
    entry_action: metadata.entry_action ?? null,
    record_id: metadata.record_id ?? null,
    record_title: metadata.record_title ?? null,
    sentence_id: metadata.sentence_id ?? null,
    paragraph_id: metadata.paragraph_id ?? null,
    entry_id: metadata.entry_id ?? null,
    entry_type: metadata.entry_type ?? null,
    asset_id: metadata.asset_id ?? null,
    annotation_type: metadata.annotation_type ?? null,
    start_offset: metadata.start_offset ?? null,
    end_offset: metadata.end_offset ?? null,
    translation_zh: metadata.translation_zh ?? null,
    note: metadata.note ?? null,
    title: metadata.title ?? null,
    query: metadata.query ?? null,
    lookup_text: metadata.lookup_text ?? null,
    visual_tone: metadata.visual_tone ?? null,
  };
}

function toGenericReaderAskMessageRequest(
  body: ReaderAskMessageStreamRequestDto,
): ReaderAskMessageStreamRequestDto {
  return {
    ...body,
    attachments: body.attachments.map((attachment) => ({
      ...attachment,
      metadata: toGenericReaderAskAttachmentMetadata(attachment.metadata),
    })),
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
    body: JSON.stringify(toGenericReaderAskMessageRequest(body)),
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

export type ReadingRecordAskCitationNavigateResultDto = {
  status: string;
  location?: {
    unit_id?: string | null;
    anchor_segment_id?: string | null;
    canonical_text_start_utf16?: number | null;
    canonical_text_end_utf16?: number | null;
  } | null;
  reason?: string | null;
};

/** Secure citation navigation — path only; no fence body fields. */
export function navigateUpstreamReadingRecordAskCitation(
  recordId: string,
  messageId: string,
  citationId: string,
  sessionToken: string,
): Promise<UpstreamResult<ReadingRecordAskCitationNavigateResultDto>> {
  return fastApiFetch<ReadingRecordAskCitationNavigateResultDto>(
    readingRecordAskPath(
      recordId,
      `/messages/${encodeURIComponent(messageId)}/citations/${encodeURIComponent(citationId)}/navigate`,
    ),
    {
      method: "POST",
      sessionToken,
    },
  );
}
