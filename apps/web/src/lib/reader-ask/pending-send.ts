/**
 * ASK-RETRY-CONTRACT-R8 — pending send / recovery helpers.
 *
 * Authority is client_submission_id (stored on PendingSendRequest).
 * Map keys may be local-assistant-* or a canonical UUID after
 * message.started rekey. Never delete pending until trusted terminal
 * or successful hydrate.
 */

import type { PendingSendRequest } from "./retry-target";
import {
  isLocalOptimisticMessageId,
  isPersistedAssistantMessageId,
} from "./browser-paths";
import type { RetryTarget } from "./retry-target";

/** Active bubble id for UI updates after optional message.started. */
export function resolveActiveAssistantId(
  streamingId: string | null | undefined,
  tempAssistantId: string,
): string {
  return streamingId && streamingId.length > 0 ? streamingId : tempAssistantId;
}

export function messageMatchesActiveAssistant(
  messageId: string,
  activeAssistantId: string,
  tempAssistantId: string,
): boolean {
  return (
    messageId === activeAssistantId ||
    messageId === tempAssistantId
  );
}

/**
 * Move pending entry from temp local id to canonical UUID without drop.
 * No-op if fromId missing or same as toId.
 */
export function rekeyPendingSend(
  map: Map<string, PendingSendRequest>,
  fromId: string,
  toId: string,
): PendingSendRequest | null {
  if (!fromId || !toId || fromId === toId) {
    return map.get(toId) ?? map.get(fromId) ?? null;
  }
  const pending = map.get(fromId);
  if (!pending) {
    return map.get(toId) ?? null;
  }
  map.delete(fromId);
  const next: PendingSendRequest = {
    ...pending,
    localAssistantId: toId,
  };
  map.set(toId, next);
  return next;
}

/** Remove pending entries for any of the given assistant keys. */
export function clearPendingSendKeys(
  map: Map<string, PendingSendRequest>,
  ...ids: Array<string | null | undefined>
): void {
  for (const id of ids) {
    if (id) {
      map.delete(id);
    }
  }
}

/**
 * R8: UUID that still has a pending recovery entry must resend (same
 * client_submission_id), never /retry regenerate.
 */
export function classifyRetryTargetWithPending(
  messageId: string,
  hasPendingResend: boolean,
  classifyBase: (id: string) => RetryTarget | null,
): RetryTarget | null {
  const id = messageId.trim();
  if (!id) {
    return null;
  }
  if (hasPendingResend) {
    return {
      kind: "pending_submission",
      localAssistantId: id,
      ctaLabel: "重新发送",
      ctaAction: "resend",
    };
  }
  return classifyBase(id);
}

/** True when id is still a client-only optimistic assistant. */
export function isLocalAssistantPendingId(messageId: string): boolean {
  return (
    isLocalOptimisticMessageId(messageId) &&
    messageId.startsWith("local-assistant-")
  );
}

/**
 * After message.started the bubble may be UUID while recovery is still open.
 * Resend must be offered when pending map has the key — even if UUID.
 */
export function shouldOfferResendNotRetry(
  messageId: string,
  hasPendingResend: boolean,
): boolean {
  if (hasPendingResend) {
    return true;
  }
  return isLocalAssistantPendingId(messageId);
}

export function isCanonicalAssistantId(messageId: string): boolean {
  return isPersistedAssistantMessageId(messageId);
}
