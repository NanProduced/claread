import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type {
  ReaderAskAttachmentDto,
  ReaderAskEntryActionDto,
  ReaderAskMessageRetryRequestDto,
  ReaderAskMessageStreamRequestDto,
  ReaderAskModelOptionListResponseDto,
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

interface ReaderRecordAskMessageRequestDto {
  content: string;
  entry_action?: ReaderAskEntryActionDto | null;
  model?: string | null;
  /**
   * Legacy single-selection compatibility entry. Old callers send one
   * anchor here; new Web requests send the full set via `focus_anchors`
   * (below) and keep this as the primary (first) anchor so a pre-plural
   * backend still sees a selection. When `focus_anchors` is present the
   * backend treats it as canonical and ignores this field beyond
   * compatibility.
   */
  anchor?: Record<string, unknown> | null;
  /**
   * Canonical plural focus anchors (≤4: one auto-ingested selection plus
   * up to three pinned selections): every auto/manual selection anchor
   * the composer carries, in slot order (auto first, then pinned manuals).
   * The backend gates each anchor fail-closed against the same
   * record/base/generation/document.
   */
  focus_anchors?: Record<string, unknown>[] | null;
  /**
   * User-visible web search request mode (mirrors backend `WebSearchMode`).
   * Forwarded to the Reading Record Ask upstream so the host can decide
   * whether to mount the `search_web` capability for this turn. `allowed`
   * only grants turn capability; it never forces a search.
   */
  web_search_mode?: WebSearchModeDto;
  /** Idempotent client submission identity. */
  client_submission_id?: string | null;
}

function readingRecordAskPath(recordId: string, suffix = ""): string {
  return `/reader/records/${encodeURIComponent(recordId)}/ask${suffix}`;
}

/** Transport cap mirrors the backend schema. */
// One auto-ingested emphasis selection plus up to three user-pinned
// selections.  This must match the visible composer slot contract.
const MAX_READER_RECORD_FOCUS_ANCHORS = 4;

/**
 * Collect EVERY reading_record_anchor carried by the attachments —
 * auto selection first, then pinned manual selections, in attachment
 * order. Deduped by the canonical anchor identity (record/base/
 * generation/unit/segment/offsets/hash — never display text) and capped
 * at the transport maximum. R3 P2: this replaces the old first-anchor-
 * wins extraction, which silently dropped selections 2..N.
 */
function readingRecordFocusAnchorsFromAttachments(
  attachments: ReaderAskAttachmentDto[],
): Record<string, unknown>[] {
  const seen = new Set<string>();
  const anchors: Record<string, unknown>[] = [];
  for (const attachment of attachments) {
    const candidate = (attachment.metadata as ReaderAskAttachmentDto["metadata"] & {
      reading_record_anchor?: unknown;
    }).reading_record_anchor;
    if (!candidate || typeof candidate !== "object") {
      continue;
    }
    const anchor = candidate as Record<string, unknown>;
    const fingerprint = JSON.stringify([
      anchor.record_id,
      anchor.base_id,
      anchor.generation,
      anchor.unit_id,
      anchor.anchor_segment_id,
      anchor.start_offset,
      anchor.end_offset,
      anchor.text_hash,
    ]);
    if (seen.has(fingerprint)) {
      continue;
    }
    seen.add(fingerprint);
    anchors.push(anchor);
    if (anchors.length >= MAX_READER_RECORD_FOCUS_ANCHORS) {
      break;
    }
  }
  return anchors;
}

function toReadingRecordAskMessageRequest(
  body: ReaderAskMessageStreamRequestDto,
): ReaderRecordAskMessageRequestDto {
  // R3 P2 — forward ALL selection anchors (auto + manual), never just
  // the first. `anchor` keeps the primary for pre-plural backends.
  const focusAnchors = readingRecordFocusAnchorsFromAttachments(body.attachments);
  return {
    content: body.content,
    entry_action: body.entry_action ?? null,
    model: body.model ?? null,
    anchor: focusAnchors[0] ?? null,
    focus_anchors: focusAnchors.length > 0 ? focusAnchors : null,
    // Forward the user-visible web search request mode so the host can
    // mount the `search_web` capability for this turn. Default to `disabled`
    // when omitted — `allowed` only grants capability, never forces a search.
    web_search_mode: body.web_search_mode ?? "disabled",
    client_submission_id: body.client_submission_id ?? null,
  };
}

export function listUpstreamReadingRecordAskThreads(
  recordId: string,
  sessionToken: string,
): Promise<UpstreamResult<ReaderAskThreadListResponseDto>> {
  return fastApiFetch<ReaderAskThreadListResponseDto>(readingRecordAskPath(recordId, "/threads"), {
    sessionToken,
  });
}

export function listUpstreamReadingRecordAskModelOptions(
  recordId: string,
  sessionToken: string,
): Promise<UpstreamResult<ReaderAskModelOptionListResponseDto>> {
  return fastApiFetch<ReaderAskModelOptionListResponseDto>(
    readingRecordAskPath(recordId, "/model-options"),
    { sessionToken },
  );
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

export function getUpstreamReadingRecordAskThread(
  recordId: string,
  threadId: string,
  sessionToken: string,
): Promise<UpstreamResult<ReaderAskThreadDetailDto>> {
  return fastApiFetch<ReaderAskThreadDetailDto>(readingRecordAskPath(recordId, `/threads/${threadId}`), {
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

export async function createUpstreamReadingRecordAskStream(
  recordId: string,
  threadId: string,
  body: ReaderAskMessageStreamRequestDto,
  sessionToken: string,
  signal?: AbortSignal,
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
    // ASK-TURN-LIFECYCLE R1: see createUpstreamReaderAskStream.
    signal,
  });
}

/**
 * Regenerate (not resume/continue) the assistant answer.
 * Upstream FastAPI path is always `/retry/stream` — Browser never sees this.
 */
export async function retryUpstreamReadingRecordAskMessage(
  recordId: string,
  threadId: string,
  messageId: string,
  body: ReaderAskMessageRetryRequestDto,
  sessionToken: string,
  signal?: AbortSignal,
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
    // ASK-TURN-LIFECYCLE R1: see createUpstreamReaderAskStream.
    signal,
  });
}

/** ASK-RETRY-CONTRACT-R4 — FastAPI submission reconcile GET. */
export async function getUpstreamReadingRecordAskSubmission(
  recordId: string,
  threadId: string,
  clientSubmissionId: string,
  sessionToken: string,
): Promise<UpstreamResult<import("@/types/api/reader-ask").ReaderAskSubmissionReconcileDto>> {
  return fastApiFetch(
    readingRecordAskPath(
      recordId,
      `/threads/${encodeURIComponent(threadId)}/submissions/${encodeURIComponent(clientSubmissionId)}`,
    ),
    {
      method: "GET",
      sessionToken,
    },
  );
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
