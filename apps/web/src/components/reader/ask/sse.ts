import type {
  ReaderAskStreamEnvelopeDto,
  ReaderAskStreamEventName,
} from "@/types/api/reader-ask";
import {
  isReaderAskAgenticCompletedPayload,
  isReaderAskContextCompactionPayload,
  isReaderAskAgenticProgressPayload,
  isReaderAskAgenticReasoningCompletedPayload,
  isReaderAskAgenticReasoningDeltaPayload,
  isReaderAskAgenticReasoningStartedPayload,
  isReaderAskAgenticRunStartedPayload,
  isReaderAskAgenticTerminalPayload,
  READER_ASK_AGENTIC_EXECUTION_VERSION,
} from "@/types/api/reader-ask";
import {
  isTrustedTerminalEvent,
  makeLogicalTerminalResult,
  matchesTurnIdentity,
  parseSubmissionReconcilePayload,
  TurnLifecycleMetrics,
  type LogicalTerminalResult,
  type TerminalFinalStatus,
  type TurnIdentity,
} from "./turn-lifecycle";

export {
  isReaderAskAgenticCompletedPayload,
  isReaderAskContextCompactionPayload,
  isReaderAskAgenticProgressPayload,
  isReaderAskAgenticReasoningCompletedPayload,
  isReaderAskAgenticReasoningDeltaPayload,
  isReaderAskAgenticReasoningStartedPayload,
  isReaderAskAgenticRunStartedPayload,
  isReaderAskAgenticTerminalPayload,
  READER_ASK_AGENTIC_EXECUTION_VERSION,
};

function parseSseChunk(chunk: string): ReaderAskStreamEnvelopeDto[] {
  return chunk
    .split("\n\n")
    .map((part) => part.trim())
    .filter(Boolean)
    .flatMap((part) => {
      const lines = part.split("\n");
      const event = lines.find((line) => line.startsWith("event:"))?.slice(6).trim();
      const data = lines
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trim())
        .join("\n");

      if (!event || !data) {
        return [];
      }

      try {
        // Preserve typed agentic payloads as-is (no remapping to legacy shapes
        // such as content_md / article_rag). Unknown event names still pass
        // through for forward-compat; consumers must not treat them as success.
        return [
          {
            event: event as ReaderAskStreamEventName,
            data: JSON.parse(data) as Record<string, unknown>,
          },
        ];
      } catch (parseError) {
        return [
          {
            event: "error" as ReaderAskStreamEventName,
            data: {
              code: "SSE_PARSE_ERROR",
              detail: `Failed to parse SSE data for event "${event}": ${parseError instanceof Error ? parseError.message : String(parseError)}`,
              raw_data: data,
            },
          },
        ];
      }
    });
}

/**
 * Inspect a parsed envelope and decide whether it represents a trusted
 * typed terminal for the active turn identity. Returns the matching
 * {@link LogicalTerminalResult} or `null` if the frame is not a trusted
 * terminal (or is a foreign / stale terminal that must be ignored).
 *
 * Foreign / stale handling: when an `identity` is captured, any trusted
 * terminal whose message_id / thread_id / turn_run_id does not match is
 * ignored. This prevents a late terminal for a previous turn from
 * unlocking the composer for the current turn.
 */
function classifyTrustedTerminal(
  envelope: ReaderAskStreamEnvelopeDto,
  identity: TurnIdentity | null,
): LogicalTerminalResult | null {
  const { event, data } = envelope;
  if (!isTrustedTerminalEvent(event)) {
    return null;
  }
  const payload = (data ?? {}) as Record<string, unknown>;

  // submission.reconcile is a logical terminal independent of
  // agentic turn identity — it arrives *instead* of a model stream when
  // the same client_submission_id already has a claim/pair. No identity
  // match is required (and no fake agentic.terminal(ok) is emitted).
  if (event === "submission.reconcile") {
    const reconcile = parseSubmissionReconcilePayload(payload);
    if (reconcile === null) {
      return makeLogicalTerminalResult("parse_error");
    }
    const finalStatus: TerminalFinalStatus | null =
      reconcile.status === "completed"
        ? "ok"
        : reconcile.status === "failed"
          ? "failed"
          : reconcile.status === "cancelled"
            ? "cancelled"
            : null;
    return makeLogicalTerminalResult("submission_reconcile", {
      identity: null,
      finalStatus,
      terminalReason: reconcile.terminalCode,
      submissionReconcile: reconcile,
    });
  }

  const candidateMessageId =
    typeof payload.message_id === "string" ? payload.message_id : null;
  const candidateThreadId =
    typeof payload.thread_id === "string" ? payload.thread_id : null;
  const candidateTurnRunId =
    typeof payload.turn_run_id === "string" ? payload.turn_run_id : null;

  // If we already captured an active turn identity, a terminal that does
  // not match it is foreign / stale and must NOT unlock the composer.
  if (identity !== null) {
    if (
      !matchesTurnIdentity(identity, {
        messageId: candidateMessageId,
        threadId: candidateThreadId,
        turnRunId: candidateTurnRunId,
      })
    ) {
      return null;
    }
  } else {
    // A v2 terminal cannot establish its own trust. Until a valid
    // agentic.run_started binds the active turn identity, every v2
    // message.completed / agentic.terminal / message.interrupted frame is
    // unattributed and must be ignored even when it carries a complete tuple.
    // There is no pre-v2 compatibility lane in the Reader Record web client.
    return null;
  }

  if (event === "message.completed") {
    // Canonical success terminal — must validate as the typed v2 payload.
    if (!isReaderAskAgenticCompletedPayload(payload)) {
      // Not a valid canonical completion — treat as parse_error / failed.
      return makeLogicalTerminalResult("parse_error");
    }
    const finalStatus: TerminalFinalStatus = "ok";
    return makeLogicalTerminalResult("completed", {
      identity: {
        messageId: candidateMessageId as string,
        threadId: candidateThreadId as string,
        turnRunId: candidateTurnRunId as string,
      },
      finalStatus,
    });
  }

  // agentic.terminal or its typed message.interrupted duplicate.
  // Both event names must carry the canonical v2 non-ok terminal payload.
  if (event === "agentic.terminal") {
    if (!isReaderAskAgenticTerminalPayload(payload)) {
      return makeLogicalTerminalResult("parse_error");
    }
  } else if (event === "message.interrupted") {
    if (!isReaderAskAgenticTerminalPayload(payload)) {
      return makeLogicalTerminalResult("parse_error");
    }
  }

  const rawStatus = payload.final_status;
  const finalStatus: TerminalFinalStatus | null =
    typeof rawStatus === "string" &&
    (rawStatus === "failed" ||
      rawStatus === "cancelled" ||
      rawStatus === "context_stale" ||
      rawStatus === "ok")
      ? (rawStatus as TerminalFinalStatus)
      : null;
  const terminalReason =
    typeof payload.terminal_reason === "string"
      ? payload.terminal_reason
      : null;
  const kind = event === "agentic.terminal" ? "terminal" : "interrupted";
  return makeLogicalTerminalResult(kind, {
    identity: {
      messageId: candidateMessageId as string,
      threadId: candidateThreadId as string,
      turnRunId: candidateTurnRunId as string,
    },
    finalStatus,
    terminalReason,
  });
}

/**
 * Consume a Reader Ask SSE stream.
 *
 * Returns a {@link LogicalTerminalResult} describing how the stream ended:
 *
 * - `completed` — `message.completed` with a valid v2 payload observed
 *   for the active turn. The composer may be unlocked immediately.
 * - `terminal` / `interrupted` — a typed `agentic.terminal` /
 *   `message.interrupted` duplicate observed for the active turn. Composer
 *   unlocks.
 * - `abort` — the supplied `AbortSignal` fired. Composer unlocks and the
 *   host must persist a `cancelled` terminal.
 * - `parse_error` — an `SSE_PARSE_ERROR` was emitted. The stream is
 *   considered corrupted; subsequent frames cannot be trusted.
 * - `eof` — HTTP body closed without a typed terminal. The host must
 *   run stale-stream reconciliation; this is NOT a composer unlock by
 *   itself.
 *
 * Critical invariants:
 *
 * 1. As soon as a trusted terminal is observed for the active turn,
 *    the reader is cancelled and any late frames are ignored.
 * 2. The HTTP stream may stay open after the terminal frame — this
 *    function does NOT wait for EOF.
 * 3. Foreign / stale terminals (whose message_id / thread_id /
 *    turn_run_id do not match the active identity captured at
 *    `agentic.run_started`) are ignored and never terminate the stream.
 * 4. The active identity is captured from the first valid
 *    `agentic.run_started` frame. Without it, v2 terminal frames are
 *    unattributed and ignored; no legacy terminal shape is accepted.
 */
export async function consumeReaderAskSse(
  response: Response,
  onEvent: (event: ReaderAskStreamEnvelopeDto) => void,
  signal?: AbortSignal,
  metrics?: TurnLifecycleMetrics,
): Promise<LogicalTerminalResult> {
  if (!response.body) {
    throw new Error("Reader Ask stream body is missing.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let activeIdentity: TurnIdentity | null = null;
  let terminalResult: LogicalTerminalResult | null = null;

  // Attach abort listener so we can break out of a pending reader.read()
  // when the consumer aborts mid-stream. Without this, a slow / hung
  // upstream body would block the abort for the full TCP timeout.
  let abortHandler: (() => void) | null = null;
  if (signal) {
    if (signal.aborted) {
      // Already aborted before we even started reading.
      try {
        reader.cancel().catch(() => {});
      } catch {
        // ignore
      }
      metrics?.markTerminalReceived();
      return makeLogicalTerminalResult("abort");
    }
    abortHandler = () => {
      // Cancel the reader to unblock any pending read().
      try {
        void reader.cancel().catch(() => {});
      } catch {
        // ignore
      }
    };
    signal.addEventListener("abort", abortHandler, { once: true });
  }

  try {
    while (terminalResult === null) {
      if (signal?.aborted) {
        metrics?.markTerminalReceived();
        terminalResult = makeLogicalTerminalResult("abort");
        break;
      }
      const { value, done } = await reader.read();
      if (signal?.aborted) {
        metrics?.markTerminalReceived();
        terminalResult = makeLogicalTerminalResult("abort");
        break;
      }
      if (done) {
        // Process any trailing buffer before reporting EOF.
        if (buffer.trim()) {
          for (const event of parseSseChunk(buffer)) {
            buffer = "";
            // Trailing-buffer path uses the same trust-then-dispatch
            // rule as the main loop. A foreign / stale terminal in the
            // trailing buffer MUST NOT mutate UI state.
            if (
              activeIdentity === null &&
              event.event === "agentic.run_started" &&
              isReaderAskAgenticRunStartedPayload(event.data)
            ) {
              const data = event.data as Record<string, unknown>;
              activeIdentity = {
                messageId: String(data.message_id),
                threadId: String(data.thread_id),
                turnRunId: String(data.turn_run_id),
              };
            }
            if (isTrustedTerminalEvent(event.event)) {
              const classified = classifyTrustedTerminal(event, activeIdentity);
              if (classified === null) {
                // Foreign / stale trailing terminal — skip dispatch.
                continue;
              }
              onEvent(event);
              markEventMetrics(metrics, event);
              markFrontendTerminalMetrics(metrics, classified);
              terminalResult = classified;
              break;
            }
            onEvent(event);
            markEventMetrics(metrics, event);
            if (
              event.event === "error" &&
              (event.data as Record<string, unknown>)?.code === "SSE_PARSE_ERROR"
            ) {
              metrics?.markTerminalReceived();
              terminalResult = makeLogicalTerminalResult("parse_error");
              break;
            }
          }
        }
        if (terminalResult === null) {
          metrics?.markTerminalReceived();
          terminalResult = makeLogicalTerminalResult("eof");
        }
        break;
      }
      buffer += decoder.decode(value, { stream: true });

      const boundary = buffer.lastIndexOf("\n\n");
      if (boundary === -1) {
        continue;
      }

      const ready = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const events = parseSseChunk(ready);
      for (const event of events) {
        if (terminalResult !== null) {
          break;
        }

        // Terminal identity MUST be verified before any UI mutation.
        // Capture identity from agentic.run_started as early as possible —
        // this must happen before classifyTrustedTerminal so the first
        // terminal after run_started can be matched against the active
        // identity.
        if (
          activeIdentity === null &&
          event.event === "agentic.run_started" &&
          isReaderAskAgenticRunStartedPayload(event.data)
        ) {
          const data = event.data as Record<string, unknown>;
          activeIdentity = {
            messageId: String(data.message_id),
            threadId: String(data.thread_id),
            turnRunId: String(data.turn_run_id),
          };
        }

        // Terminal candidate: a frame whose event name is a trusted
        // terminal event. We MUST classify it BEFORE dispatching to
        // onEvent so that a foreign / stale terminal never mutates UI
        // state (message, activity, error, currentMessageId, sending).
        if (isTrustedTerminalEvent(event.event)) {
          const classified = classifyTrustedTerminal(event, activeIdentity);
          if (classified === null) {
            // Foreign / stale terminal (event name matches a trusted
            // terminal, but identity does not match the active turn).
            // Skip dispatch entirely — do NOT call onEvent, do NOT
            // mutate metrics, do NOT terminate the stream.
            continue;
          }
          // Trusted terminal for the active turn — dispatch to UI,
          // mark metrics, then terminate the consumer.
          onEvent(event);
          markEventMetrics(metrics, event);
          markFrontendTerminalMetrics(metrics, classified);
          terminalResult = classified;
          break;
        }

        // Non-terminal event — dispatch to UI and mark phase metrics.
        onEvent(event);
        markEventMetrics(metrics, event);

        // SSE_PARSE_ERROR is a typed corruption terminal — stop reading
        // immediately and report parse_error. It is NOT a trusted
        // terminal event name (it arrives on the ``error`` channel), so
        // it is handled here after dispatch.
        if (
          event.event === "error" &&
          (event.data as Record<string, unknown>)?.code === "SSE_PARSE_ERROR"
        ) {
          metrics?.markTerminalReceived();
          terminalResult = makeLogicalTerminalResult("parse_error");
          break;
        }
      }

      if (terminalResult !== null) {
        // Cancel the reader to unblock any pending read() on the upstream
        // body. The producer may keep the HTTP connection open after the
        // typed terminal frame — we do NOT wait for EOF.
        try {
          void reader.cancel().catch(() => {});
        } catch {
          // ignore — reader may already be released/closed.
        }
        break;
      }
    }

    // If we exited the loop without classifying a terminal (e.g., parse
    // error in the trailing buffer), default to eof / parse_error.
    if (terminalResult === null) {
      metrics?.markTerminalReceived();
      terminalResult = makeLogicalTerminalResult("eof");
    }
    return terminalResult;
  } finally {
    if (signal && abortHandler) {
      signal.removeEventListener("abort", abortHandler);
    }
    reader.releaseLock?.();
  }
}

/**
 * Mark phase timestamps on the host-supplied metrics
 * object based on the arriving event. Only the metrics object is
 * mutated — never log content, reasoning text, or secrets here.
 *
 * Mapping (frontend approximation of backend phases):
 *
 * - ``agentic.learner_reasoning.snapshot`` → ``first_reasoning``. The
 *   learner snapshot is the sole public reasoning marker; provider/raw
 *   reasoning events never drive this metric.
 * - ``message.delta`` → ``first_answer_delta`` / ``last_answer_delta``.
 * - a non-started ``agentic.progress`` row with phase
 *   ``validating_evidence`` → ``validation_done``.
 * - ``message.completed`` → ``validation_done`` (idempotent) AND
 *   ``persistence_done``. The backend only emits
 *   ``message.completed`` after the canonical answer is durable, so
 *   arrival is a faithful proxy for both phases on the client.
 */
function markEventMetrics(
  metrics: TurnLifecycleMetrics | undefined,
  event: ReaderAskStreamEnvelopeDto,
): void {
  if (metrics === undefined) {
    return;
  }
  switch (event.event) {
    case "agentic.learner_reasoning.snapshot":
      metrics.markFirstReasoning();
      break;
    case "message.delta":
      metrics.markAnswerDelta();
      break;
    case "agentic.progress": {
      const data = event.data as Record<string, unknown> | null;
      const phase = data?.phase;
      if (
        phase === "validating_evidence" &&
        data?.activity !== "started"
      ) {
        metrics.markValidationDone();
      }
      break;
    }
    case "message.completed":
      // message.completed arrives only after the backend has persisted
      // the canonical answer, so it marks both validation completion
      // (idempotent) and persistence success.
      metrics.markValidationDone();
      metrics.markPersistenceDone();
      break;
    default:
      break;
  }
}

/**
 * Mark ``terminal_received`` on the host-supplied
 * metrics object when a trusted terminal (or abort / parse_error / eof)
 * has been classified. Called exactly once per turn — the metrics
 * class is idempotent so duplicate calls are safe.
 */
function markFrontendTerminalMetrics(
  metrics: TurnLifecycleMetrics | undefined,
  result: LogicalTerminalResult,
): void {
  if (metrics === undefined) {
    return;
  }
  // terminal_received marks the moment the SSE consumer classified a
  // trusted terminal frame (or abort / parse_error / eof). This is the
  // client-side counterpart of the backend ``terminal_sent`` metric.
  metrics.markTerminalReceived();
  // If the terminal is a parse_error or eof without prior
  // validation_done, we cannot infer anything — leave validation_done
  // null. The host (AiWorkspacePanel) is responsible for marking
  // composer_enabled when setSending(false) runs.
  void result;
}
