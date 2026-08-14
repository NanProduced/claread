/**
 * Turn stream lifecycle typed contract (Web).
 *
 * Mirrors the backend contract in
 * `services/api/app/services/reader_record_ask/turn_lifecycle.py`.
 *
 * State machine
 * -------------
 *
 *   idle → running → finalizing → committed
 *                        │
 *                        ├──→ failed
 *                        └──→ cancelled
 *
 * Critical invariants
 * -------------------
 *
 * - HTTP EOF is transport cleanup, NOT a business terminal. Only a
 *   trusted typed terminal event (message.completed /
 *   agentic.terminal / typed message.interrupted duplicate / parse-error /
 *   abort) may move the lifecycle into a terminal state.
 * - A terminal is TRUSTED only when its message_id / thread_id /
 *   turn_run_id match the active turn identity captured at
 *   agentic.run_started. Foreign / stale terminals are ignored and
 *   never unlock the composer.
 * - Provisional answer deltas never write to canonical answer slots.
 *   Only message.completed atomically replaces them.
 * - Any terminal other than `committed` discards the provisional
 *   preview. Failure / cancel / retry never preserves half answers.
 * - Terminal writes are idempotent: a `cancelled` arriving after
 *   `committed` (or vice versa) must not flip the row back.
 *
 * This module is intentionally dependency-free (no React, no fetch)
 * so it can be imported by both the SSE consumer and Vitest contract
 * tests.
 */

export type TurnLifecycleState =
  | "idle"
  | "running"
  | "finalizing"
  | "committed"
  | "failed"
  | "cancelled";

export const TERMINAL_STATES: ReadonlySet<TurnLifecycleState> = new Set([
  "committed",
  "failed",
  "cancelled",
]);

export const TRUSTED_TERMINAL_EVENT_NAMES: ReadonlySet<string> = new Set([
  "message.completed",
  "agentic.terminal",
  "message.interrupted",
  // Duplicate-submission short-circuit is a logical terminal —
  // the host must hydrate via GET and must not leave a streaming bubble.
  "submission.reconcile",
]);

export type TerminalFinalStatus =
  | "ok"
  | "failed"
  | "cancelled"
  | "context_stale";

export type LogicalTerminalKind =
  | "completed"
  | "terminal"
  | "interrupted"
  | "abort"
  | "parse_error"
  | "eof"
  /** Server returned submission.reconcile (duplicate / in-flight). */
  | "submission_reconcile";

/** Public fields from SSE `submission.reconcile` (no secrets). */
export interface SubmissionReconcileTerminalPayload {
  readonly clientSubmissionId: string;
  readonly threadId: string;
  readonly status: string;
  readonly userMessageId: string | null;
  readonly assistantMessageId: string | null;
  readonly terminalCode: string | null;
  readonly actionHint: string | null;
  readonly claimGeneration: number | null;
}

export interface TurnIdentity {
  readonly messageId: string;
  readonly threadId: string;
  readonly turnRunId: string;
}

export interface LogicalTerminalResult {
  readonly kind: LogicalTerminalKind;
  readonly identity: TurnIdentity | null;
  readonly finalStatus: TerminalFinalStatus | null;
  readonly terminalReason: string | null;
  readonly receivedAt: number; // epoch ms
  /** Present when kind === "submission_reconcile". */
  readonly submissionReconcile?: SubmissionReconcileTerminalPayload | null;
}

export const STALE_STREAM_TERMINAL_REASON = "stale_stream_reconciled";

export function isTerminalState(state: TurnLifecycleState): boolean {
  return TERMINAL_STATES.has(state);
}

export function isTrustedTerminalEvent(eventName: string): boolean {
  return TRUSTED_TERMINAL_EVENT_NAMES.has(eventName);
}

export function stateForFinalStatus(
  finalStatus: TerminalFinalStatus | string | null | undefined,
): TurnLifecycleState {
  if (finalStatus === null || finalStatus === undefined || finalStatus === "") {
    return "failed";
  }
  switch (finalStatus) {
    case "ok":
      return "committed";
    case "failed":
      return "failed";
    case "cancelled":
      return "cancelled";
    case "context_stale":
      return "failed";
    default:
      return "failed";
  }
}

export function isTrustedTerminal(
  result: LogicalTerminalResult,
): boolean {
  return (
    result.kind === "completed" ||
    result.kind === "terminal" ||
    result.kind === "interrupted" ||
    result.kind === "abort" ||
    result.kind === "parse_error" ||
    result.kind === "submission_reconcile"
  );
}

export function resultingState(
  result: LogicalTerminalResult,
): TurnLifecycleState {
  switch (result.kind) {
    case "completed":
      return "committed";
    case "terminal":
    case "interrupted":
      return result.finalStatus === "cancelled" ? "cancelled" : "failed";
    case "abort":
      return "cancelled";
    case "parse_error":
      return "failed";
    case "eof":
      return "failed";
    case "submission_reconcile": {
      const st = result.submissionReconcile?.status;
      if (st === "completed") return "committed";
      if (st === "cancelled") return "cancelled";
      // streaming / claimed / failed / not_found — host hydrates further
      return "failed";
    }
  }
}

export function matchesTurnIdentity(
  identity: TurnIdentity,
  candidate: {
    messageId?: string | null;
    threadId?: string | null;
    turnRunId?: string | null;
  },
): boolean {
  const { messageId, threadId, turnRunId } = candidate;
  return (
    Boolean(messageId) &&
    Boolean(threadId) &&
    Boolean(turnRunId) &&
    messageId === identity.messageId &&
    threadId === identity.threadId &&
    turnRunId === identity.turnRunId
  );
}

export function makeLogicalTerminalResult(
  kind: LogicalTerminalKind,
  init: {
    identity?: TurnIdentity | null;
    finalStatus?: TerminalFinalStatus | null;
    terminalReason?: string | null;
    submissionReconcile?: SubmissionReconcileTerminalPayload | null;
  } = {},
): LogicalTerminalResult {
  return {
    kind,
    identity: init.identity ?? null,
    finalStatus: init.finalStatus ?? null,
    terminalReason: init.terminalReason ?? null,
    receivedAt: Date.now(),
    submissionReconcile: init.submissionReconcile ?? null,
  };
}

/** Parse public SSE submission.reconcile data into a typed payload. */
export function parseSubmissionReconcilePayload(
  data: Record<string, unknown> | null | undefined,
): SubmissionReconcileTerminalPayload | null {
  if (!data || typeof data !== "object") return null;
  const clientSubmissionId =
    typeof data.client_submission_id === "string"
      ? data.client_submission_id
      : null;
  const threadId =
    typeof data.thread_id === "string" ? data.thread_id : null;
  const status = typeof data.status === "string" ? data.status : null;
  if (!clientSubmissionId || !threadId || !status) return null;
  return {
    clientSubmissionId,
    threadId,
    status,
    userMessageId:
      typeof data.user_message_id === "string" ? data.user_message_id : null,
    assistantMessageId:
      typeof data.assistant_message_id === "string"
        ? data.assistant_message_id
        : null,
    terminalCode:
      typeof data.terminal_code === "string" ? data.terminal_code : null,
    actionHint:
      typeof data.action_hint === "string" ? data.action_hint : null,
    claimGeneration:
      typeof data.claim_generation === "number" ? data.claim_generation : null,
  };
}

/**
 * Per-turn lifecycle timing metrics (Web).
 *
 * Mirrors the backend ``_TurnLifecycleMetrics`` in
 * ``services/api/app/services/reader_record_ask/production_stream.py``.
 *
 * Records only timestamps relative to ``startedAt`` — never answer
 * content, reasoning text, citations, provider payloads, secrets, or
 * user input. The metric set is the union of backend-emitted and
 * frontend-only-emitted kinds:
 *
 * - ``first_reasoning`` — first ``agentic.learner_reasoning.snapshot``
 *   arrival. ``null`` if no learner snapshot was emitted this turn.
 * - ``first_answer_delta`` / ``last_answer_delta`` — first and last
 *   ``message.delta`` arrival times. ``null`` if no answer delta
 *   arrived (e.g., early validation failure). The gap
 *   ``last - first`` is the answer streaming duration.
 * - ``validation_done`` — signal that the host has reached the
 *   validation/finalization phase. On the frontend this is approximated
 *   by the first non-started ``agentic.progress`` event whose phase is
 *   ``validating_evidence`` OR the first ``message.completed`` frame,
 *   whichever fires first. ``null`` if the turn failed before finishing
 *   validation.
 * - ``persistence_done`` — successful commit timestamp. On the
 *   frontend this is approximated by the ``message.completed`` arrival
 *   (the backend only emits ``message.completed`` after the canonical
 *   answer is durable). ``null`` on failure paths.
 * - ``terminal_sent`` — backend-only metric (when the backend yielded
 *   the typed terminal SSE frame). Not tracked on the frontend.
 * - ``terminal_received`` — first typed terminal frame arrival at the
 *   SSE consumer (``message.completed`` / ``agentic.terminal`` /
 *   typed ``message.interrupted`` for the active turn, or ``parse_error`` /
 *   ``abort`` / ``eof``). Marks the moment the client should be able
 *   to start unlocking the composer.
 * - ``composer_enabled`` — the moment ``setSending(false)`` runs and
 *   the composer is actually interactive again. The gap
 *   ``composer_enabled - terminal_received`` is the client-side unlock
 *   latency; a large gap indicates the host is doing too much work
 *   between the terminal frame and the composer re-enable.
 *
 * All timestamps are ``performance.now()`` deltas (ms) from
 * ``startedAt`` so they are monotonic and clock-skew-immune. The
 * ``toJSON()`` method returns a log-safe object — no content or
 * secrets.
 */
export interface TurnLifecycleMetricSnapshot {
  readonly first_reasoning: number | null;
  readonly first_answer_delta: number | null;
  readonly last_answer_delta: number | null;
  readonly validation_done: number | null;
  readonly persistence_done: number | null;
  readonly terminal_received: number | null;
  readonly composer_enabled: number | null;
}

export class TurnLifecycleMetrics {
  private readonly startedAt: number;
  public first_reasoning: number | null = null;
  public first_answer_delta: number | null = null;
  public last_answer_delta: number | null = null;
  public validation_done: number | null = null;
  public persistence_done: number | null = null;
  public terminal_received: number | null = null;
  public composer_enabled: number | null = null;

  constructor(startedAt: number = performance.now()) {
    this.startedAt = startedAt;
  }

  private elapsed(): number {
    return Math.max(0, performance.now() - this.startedAt);
  }

  markFirstReasoning(): void {
    if (this.first_reasoning === null) {
      this.first_reasoning = this.elapsed();
    }
  }

  markAnswerDelta(): void {
    if (this.first_answer_delta === null) {
      this.first_answer_delta = this.elapsed();
    }
    this.last_answer_delta = this.elapsed();
  }

  markValidationDone(): void {
    if (this.validation_done === null) {
      this.validation_done = this.elapsed();
    }
  }

  markPersistenceDone(): void {
    this.persistence_done = this.elapsed();
  }

  markTerminalReceived(): void {
    if (this.terminal_received === null) {
      this.terminal_received = this.elapsed();
    }
  }

  markComposerEnabled(): void {
    if (this.composer_enabled === null) {
      this.composer_enabled = this.elapsed();
    }
  }

  toJSON(): TurnLifecycleMetricSnapshot {
    return {
      first_reasoning: this.first_reasoning,
      first_answer_delta: this.first_answer_delta,
      last_answer_delta: this.last_answer_delta,
      validation_done: this.validation_done,
      persistence_done: this.persistence_done,
      terminal_received: this.terminal_received,
      composer_enabled: this.composer_enabled,
    };
  }
}
