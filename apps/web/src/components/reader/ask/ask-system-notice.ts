/**
 * AskSystemNotice: single source of truth for system notices in the Ask panel.
 *
 * (a) This module is the only place that constructs AskSystemNotice instances.
 *     UI layers (turn bubble, panel banner, composer) consume these notices
 *     and render them via <SystemMessage />; they must not assemble their own.
 *
 * (b) The `message` field MUST always be typed Chinese copy sourced from
 *     ./ask-error-messages.ts. Raw provider errors, exception messages, HTTP
 *     bodies, and internal reason codes MUST NOT appear in `message`. The
 *     single, documented exception is the DEV-only raw-reason passthrough
 *     inside formatAgenticTerminalMessage (gated by the `dev` flag), used for
 *     local debugging only and never enabled in production.
 *
 * (c) Only strings exported from ask-error-messages.ts (or produced by its
 * formatters) are allowed in `message`. Callers of projectPanelInitNotice,
 * projectSendFailureNotice, projectActionFailureNotice,
 * projectSupplementFailureNotice, projectClarifyWarningNotice and
 * projectOptionalToolWarning are responsible for passing typed copy (e.g.
 * ASK_UNAVAILABLE_MESSAGE, NETWORK_ERROR_MESSAGE, OPTIONAL_TOOL_WARNING_MESSAGE,
 * FINAL_STATUS_MESSAGES[…]) — never error.message, provider codes, or
 * exception text.
 */

import {
  FINAL_STATUS_MESSAGES,
  TERMINAL_REASON_MESSAGES,
  WEB_SEARCH_UNAVAILABLE_MESSAGE,
  formatAgenticTerminalMessage,
} from "./ask-error-messages";

/** Where a notice is anchored in the Ask UI. */
export type AskSystemNoticeScope = "turn" | "panel" | "composer";

/** Severity; mirrors the <SystemMessage /> variant set. */
export type AskSystemNoticeSeverity = "action" | "warning" | "error";

/** CTA intent — the UI layer maps each value to a concrete handler. */
export type AskSystemNoticeCtaAction =
  | "retry"
  | "resend"
  | "disable_web_resend"
  | "reload"
  | "dismiss";

export interface AskSystemNoticeCta {
  label: string;
  action: AskSystemNoticeCtaAction;
}

/**
 * A system notice rendered by <SystemMessage />. `message` is always typed
 * copy from ask-error-messages.ts; never raw error text.
 */
export interface AskSystemNotice {
  id: string;
  scope: AskSystemNoticeScope;
  severity: AskSystemNoticeSeverity;
  message: string;
  relatedMessageId?: string;
  dismissible: boolean;
  cta?: AskSystemNoticeCta;
}

/** terminal_reason values that represent hard agent failures. */
const HARD_FAILURE_REASONS: ReadonlySet<string> = new Set(
  Object.keys(TERMINAL_REASON_MESSAGES),
);

/** final_status values that represent soft / non-fatal outcomes. */
const SOFT_FINAL_STATUSES: ReadonlySet<string> = new Set(
  Object.keys(FINAL_STATUS_MESSAGES),
);

const RETRY_CTA: AskSystemNoticeCta = { label: "重新生成", action: "retry" };
const RESEND_CTA: AskSystemNoticeCta = { label: "重新发送", action: "resend" };
const RELOAD_CTA: AskSystemNoticeCta = { label: "重新加载", action: "reload" };

/**
 * Project a non-ok agentic terminal payload to a turn-scoped notice bound to
 * the failing message.
 *
 * Severity is "error" for hard failures (any known terminal_reason, plus
 * unknown reasons / generic fallbacks) and "warning" for soft final_status
 * values (context_stale / invalid_citations / cancelled). CTA is
 * "重新生成 / retry" for everything except a pure cancel (cancelled with no
 * hard terminal_reason), which is not retryable.
 *
 * `message` is produced by formatAgenticTerminalMessage, so it is always typed
 * copy (the only raw passthrough is the DEV-only, `dev`-gated reason used for
 * local debugging).
 */
export function projectTurnTerminalNotice(args: {
  messageId: string;
  finalStatus?: string | null;
  terminalReason?: string | null;
  dev: boolean;
}): AskSystemNotice {
  const reason =
    typeof args.terminalReason === "string" && args.terminalReason.trim()
      ? args.terminalReason.trim()
      : null;
  const status =
    typeof args.finalStatus === "string" ? args.finalStatus : null;

  const isHardReason = reason !== null && HARD_FAILURE_REASONS.has(reason);
  const isSoftStatus = status !== null && SOFT_FINAL_STATUSES.has(status);

  // Soft final_status → warning; everything else (hard reason, unknown
  // reason, unknown status, generic fallback) is a real failure → error.
  const severity: AskSystemNoticeSeverity = isSoftStatus ? "warning" : "error";

  // A pure cancel (no hard terminal_reason) is the only non-retryable case.
  const isPureCancel = status === "cancelled" && !isHardReason;
  const cta: AskSystemNoticeCta | undefined = isPureCancel
    ? undefined
    : RETRY_CTA;

  const message = formatAgenticTerminalMessage(
    { final_status: status, terminal_reason: reason },
    { dev: args.dev },
  );

  return {
    id: `turn:terminal:${args.messageId}`,
    scope: "turn",
    severity,
    message,
    relatedMessageId: args.messageId,
    dismissible: false,
    cta,
  };
}

/**
 * Project a panel-level init / restore / capability failure.
 *
 * Severity is "error" (init / capability) or "warning" (history_restore). The
 * notice is dismissible only for history_restore. init / capability offer a
 * "重新加载 / reload" CTA.
 *
 * `message` MUST be typed copy from ask-error-messages.ts (caller
 * responsibility) — never error.message or raw provider text.
 */
export function projectPanelInitNotice(args: {
  kind: "init" | "history_restore" | "capability";
  message: string;
}): AskSystemNotice {
  const isError = args.kind === "init" || args.kind === "capability";
  return {
    id: `panel:${args.kind}`,
    scope: "panel",
    severity: isError ? "error" : "warning",
    message: args.message,
    dismissible: !isError,
    cta: isError ? RELOAD_CTA : undefined,
  };
}

/**
 * Project a send / retry transport failure (network error, non-ok response,
 * or thrown exception during the SSE request).
 *
 * ASK-RETRY-CONTRACT-R1:
 * - Pending/optimistic submissions use severity `action` + CTA 重新发送
 *   (never hit `/retry`).
 * - Persisted assistant regenerates use severity `action` + CTA 重新生成
 *   for retryable transport/service issues (no red error border).
 * - Callers may force `error` only for true validation / unrecoverable
 *   cases via `severity: "error"`.
 *
 * `message` MUST be typed copy from ask-error-messages.ts (caller
 * responsibility — pass `toUserFacingErrorMessage(error, fallback)` output,
 * never `error.message`).
 */
export function projectSendFailureNotice(args: {
  messageId: string;
  message: string;
  /**
   * `pending` → 重新发送; `persisted` / default → 重新生成.
   */
  target?: "pending" | "persisted";
  severity?: "action" | "error";
}): AskSystemNotice {
  const isPending = args.target === "pending";
  return {
    id: `turn:send:${args.messageId}`,
    scope: "turn",
    severity: args.severity ?? "action",
    message: args.message,
    relatedMessageId: args.messageId,
    dismissible: false,
    cta: isPending ? RESEND_CTA : RETRY_CTA,
  };
}

/** Typed pre-stream Web Search capability failure with a safe recovery CTA. */
export function projectWebSearchUnavailableNotice(args: {
  messageId: string;
}): AskSystemNotice {
  return {
    id: `turn:web-search-unavailable:${args.messageId}`,
    scope: "turn",
    severity: "action",
    message: WEB_SEARCH_UNAVAILABLE_MESSAGE,
    relatedMessageId: args.messageId,
    dismissible: false,
    cta: {
      label: "关闭联网并重新发送",
      action: "disable_web_resend",
    },
  };
}

/**
 * Project an action-confirm failure. Turn-scoped, error severity, bound to
 * the assistant message that owns the action proposal. NOT retryable via
 * "重新生成" — regenerating the answer would discard the action context.
 * Dismissible so the user can clear the notice and retry the action card
 * directly. No CTA.
 *
 * `message` MUST be typed copy from ask-error-messages.ts (caller
 * responsibility).
 */
export function projectActionFailureNotice(args: {
  messageId: string;
  message: string;
}): AskSystemNotice {
  return {
    id: `turn:action:${args.messageId}`,
    scope: "turn",
    severity: "error",
    message: args.message,
    relatedMessageId: args.messageId,
    dismissible: true,
  };
}

/**
 * Project a supplement-delete failure. Turn-scoped, error severity, bound to
 * the assistant message that owns the supplement. NOT retryable via
 * "重新生成" — regenerating the answer would not retry the delete. Dismissible
 * so the user can clear the notice and retry the delete control directly.
 * No CTA.
 *
 * `message` MUST be typed copy from ask-error-messages.ts (caller
 * responsibility).
 */
export function projectSupplementFailureNotice(args: {
  messageId: string;
  message: string;
}): AskSystemNotice {
  return {
    id: `turn:supplement:${args.messageId}`,
    scope: "turn",
    severity: "error",
    message: args.message,
    relatedMessageId: args.messageId,
    dismissible: true,
  };
}

/**
 * Project a clarification warning (e.g. the original user question for a
 * clarification turn could not be located). Turn-scoped, warning severity,
 * dismissible, no CTA. Bound to the assistant message that triggered the
 * clarification.
 *
 * `message` MUST be typed copy from ask-error-messages.ts (caller
 * responsibility).
 */
export function projectClarifyWarningNotice(args: {
  messageId: string;
  message: string;
}): AskSystemNotice {
  return {
    id: `turn:clarify:${args.messageId}`,
    scope: "turn",
    severity: "warning",
    message: args.message,
    relatedMessageId: args.messageId,
    dismissible: true,
  };
}

/**
 * Project an optional-tool warning: the Agent turn succeeded, but an optional
 * tool produced a warning worth surfacing inline. Returns null when there is
 * no warning (`message === null`), so callers can short-circuit. Turn-scoped
 * and bound to the succeeding message; dismissible; no CTA.
 *
 * `message` MUST be typed copy from ask-error-messages.ts (caller
 * responsibility).
 */
export function projectOptionalToolWarning(args: {
  messageId: string;
  message: string | null;
}): AskSystemNotice | null {
  if (args.message === null) {
    return null;
  }
  return {
    id: `turn:tool:${args.messageId}`,
    scope: "turn",
    severity: "warning",
    message: args.message,
    relatedMessageId: args.messageId,
    dismissible: true,
  };
}

/**
 * True only for a turn-scoped, error-severity notice that carries a retry
 * CTA — i.e. the whole turn failed and the caller must not treat it as a
 * success. Action / supplement failures are error-severity and turn-scoped
 * but have NO retry CTA (regenerating would discard the action context),
 * so they are NOT full-turn errors. Soft turn warnings (e.g. an
 * optional-tool warning), panel errors, and null are also not full-turn
 * errors.
 */
export function isFullTurnError(notice: AskSystemNotice | null): boolean {
  // Full-turn transport/service failures use severity `action` with a
  // retry or resend CTA (ASK-RETRY-CONTRACT). Hard agent terminals stay
  // severity `error` with retry. Both count as full-turn issues.
  return (
    notice !== null &&
    notice.scope === "turn" &&
    (notice.severity === "error" || notice.severity === "action") &&
    (notice.cta?.action === "retry" || notice.cta?.action === "resend")
  );
}
