/**
 * Explicit RetryTarget classification.
 *
 * Never scatter `local-*` string checks through the panel. The UI must
 * distinguish:
 * - persisted assistant → CTA "重新生成" → browser `/retry`
 * - pending/optimistic submission → CTA "重新发送" → resend original
 *   SendRequest (never hit the retry endpoint)
 */

import {
  isLocalOptimisticMessageId,
  isPersistedAssistantMessageId,
} from "./browser-paths";
import type {
  ReaderAskAttachmentDto,
  ReaderAskEntryActionDto,
  ReaderAskMessageStreamRequestDto,
  WebSearchModeDto,
} from "@/types/api/reader-ask";

export type RetryTargetKind = "persisted_assistant" | "pending_submission";

export type RetryTarget =
  | {
      kind: "persisted_assistant";
      assistantMessageId: string;
      /** Footer / notice CTA copy. */
      ctaLabel: "重新生成";
      ctaAction: "retry";
    }
  | {
      kind: "pending_submission";
      /** Local optimistic assistant id (never used in a retry URL). */
      localAssistantId: string;
      ctaLabel: "重新发送";
      ctaAction: "resend";
    };

/** Original send payload retained for pending-submission resend. */
export type PendingSendRequest = {
  content: string;
  attachments: ReaderAskAttachmentDto[];
  entryAction: ReaderAskEntryActionDto;
  model: string | null | undefined;
  webSearchMode: WebSearchModeDto;
  /** Client-generated UUID for idempotent claim. */
  clientSubmissionId: string;
  /** Optimistic local pair ids (UI only). */
  localUserId: string;
  localAssistantId: string;
  threadId: string;
};

export function classifyRetryTarget(messageId: string): RetryTarget | null {
  const id = messageId.trim();
  if (!id) {
    return null;
  }
  if (isLocalOptimisticMessageId(id) && id.startsWith("local-assistant-")) {
    return {
      kind: "pending_submission",
      localAssistantId: id,
      ctaLabel: "重新发送",
      ctaAction: "resend",
    };
  }
  if (isPersistedAssistantMessageId(id)) {
    return {
      kind: "persisted_assistant",
      assistantMessageId: id,
      ctaLabel: "重新生成",
      ctaAction: "retry",
    };
  }
  // local-user or empty — fail-closed.
  return null;
}

/**
 * When recovery is still open for this bubble (including after
 * message.started promoted local → UUID), force pending_submission so
 * CTA is 重新发送 and never /retry.
 */
export function classifyRetryTargetForRecovery(
  messageId: string,
  hasOpenPendingResend: boolean,
): RetryTarget | null {
  if (hasOpenPendingResend) {
    const id = messageId.trim();
    if (!id) {
      return null;
    }
    return {
      kind: "pending_submission",
      localAssistantId: id,
      ctaLabel: "重新发送",
      ctaAction: "resend",
    };
  }
  return classifyRetryTarget(messageId);
}

/** Build the stream body from a retained pending send request. */
export function pendingSendToStreamBody(
  pending: PendingSendRequest,
  pageIdentity: ReaderAskMessageStreamRequestDto["page_identity"],
): ReaderAskMessageStreamRequestDto {
  return {
    content: pending.content,
    page_identity: pageIdentity,
    attachments: pending.attachments,
    entry_action: pending.entryAction,
    model: pending.model ?? null,
    web_search_mode: pending.webSearchMode,
    client_submission_id: pending.clientSubmissionId,
  };
}
