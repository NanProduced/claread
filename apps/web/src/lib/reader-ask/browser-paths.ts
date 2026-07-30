/**
 * ASK-RETRY-CONTRACT-R0/R4 — single source of truth for Browser → Next BFF
 * Ask paths. UI code must never hand-write FastAPI upstream paths
 * (especially `/retry/stream`); that suffix exists only on the BFF →
 * FastAPI adapter in `services/api/reader-ask.ts`.
 */

/** Canonical server UUID (strict). */
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/**
 * True when the id is a client-only optimistic placeholder
 * (`local-assistant-*` / `local-user-*`). These must never appear in a
 * retry URL.
 */
export function isLocalOptimisticMessageId(messageId: string): boolean {
  return (
    messageId.startsWith("local-assistant-") ||
    messageId.startsWith("local-user-")
  );
}

/**
 * ASK-RETRY-CONTRACT-R4 — strict UUID only for regenerate targets.
 * Arbitrary non-`local-*` strings (e.g. `msg-assistant-1`) are NOT
 * persisted server identities.
 */
export function isPersistedAssistantMessageId(messageId: string): boolean {
  const id = messageId.trim();
  if (!id || isLocalOptimisticMessageId(id)) {
    return false;
  }
  return UUID_RE.test(id);
}

/** Browser-facing stream path (Next BFF). */
export function browserAskStreamPath(threadId: string): string {
  return `/api/web/reader-ask/threads/${encodeURIComponent(threadId)}/messages/stream`;
}

/**
 * Browser-facing retry path (Next BFF).
 * Contract: POST …/messages/{assistantMessageId}/retry
 * — never `/retry/stream` (that is upstream-only).
 */
export function browserAskRetryPath(
  threadId: string,
  assistantMessageId: string,
): string {
  return `/api/web/reader-ask/threads/${encodeURIComponent(threadId)}/messages/${encodeURIComponent(assistantMessageId)}/retry`;
}

/**
 * Browser-facing submission reconcile path (Next BFF).
 * Used when the first stream may have been accepted server-side but the
 * client never observed `message.started`.
 */
export function browserAskSubmissionPath(
  threadId: string,
  clientSubmissionId: string,
): string {
  return `/api/web/reader-ask/threads/${encodeURIComponent(threadId)}/submissions/${encodeURIComponent(clientSubmissionId)}`;
}
