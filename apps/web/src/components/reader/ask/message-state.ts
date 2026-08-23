/**
 * Ask message state: SSE event coalescing into UI message patches, plus
 * cold-history normalization and assistant block projection (pure).
 *
 * The panel keeps page-level orchestration; this module owns the
 * turn-message state machine.
 */
import {
  aggregateArticleEvidenceOutcome,
  type AgenticActivityEvent,
  type AgenticActivityOutcome,
} from "./agentic-activity";
import { formatStreamErrorMessage } from "./ask-error-messages";
import {
  isReaderAskAgenticCompletedPayload,
  isReaderAskAgenticProgressPayload,
  isReaderAskAgenticReasoningCompletedPayload,
  isReaderAskAgenticReasoningDeltaPayload,
  isReaderAskAgenticReasoningStartedPayload,
  isReaderAskAgenticRunStartedPayload,
  isReaderAskAgenticTerminalPayload,
  isReaderAskContextCompactionPayload,
} from "./sse";
import {
  isReaderAskAgenticAnswerBlockList,
  isReaderAskAgenticCitationList,
  isReaderAskAgenticFinalStatus,
  isReaderAskWebSearchSummary,
  READER_ASK_AGENTIC_EXECUTION_VERSION,
} from "@/types/api/reader-ask";
import type {
  ReaderAskAgenticCompletedPayloadDto,
  ReaderAskAgenticProgressPayloadDto,
  ReaderAskAgenticTerminalPayloadDto,
  ReaderAskAgenticTerminalStatusDto,
  ReaderAskMessageDto,
  ReaderAskMessageUiStateDto,
  ReaderAskStreamEnvelopeDto,
  ReaderAskUiMessageDto,
  ReaderAskWebSearchSummaryDto,
} from "@/types/api/reader-ask";

function isDevMode(): boolean {
  return process.env.NODE_ENV !== "production";
}

/** Map an SSE `error` envelope to user-facing copy (never raw detail). */
function formatStreamError(event: ReaderAskStreamEnvelopeDto): string {
  return formatStreamErrorMessage(
    event.data as { user_message?: unknown; code?: unknown; detail?: unknown },
    { dev: isDevMode() },
  );
}

type AskPanelBlockKind =
  | "answer";

export type AskPanelBlock = {
  kind: AskPanelBlockKind;
};

export type AskPanelConversationItem = {
  id: string;
  role: ReaderAskMessageDto["role"];
  status: ReaderAskMessageDto["status"];
  message: ReaderAskUiMessageDto;
  blocks: AskPanelBlock[];
};

type MessageUpdater = ( updater: (messages: ReaderAskUiMessageDto[]) => ReaderAskUiMessageDto[] ) => void;

/**
 * Creates a throttled streaming message updater that batches SSE updates
 * via requestAnimationFrame instead of calling flushSync per chunk.
 * High-frequency events (message.delta, reasoning.delta) are coalesced;
 * low-frequency events (started/completed/interrupted) flush immediately.
 */
function createStreamingCommit(updateMessage: MessageUpdater) {
  let pendingUpdater: Parameters<MessageUpdater>[0] | null = null;
  let rafId: number | null = null;

  function flush() {
    rafId = null;
    if (pendingUpdater !== null) {
      const updater = pendingUpdater;
      pendingUpdater = null;
      updateMessage(updater);
    }
  }

  function scheduleFlush() {
    if (rafId === null) {
      rafId = requestAnimationFrame(flush);
    }
  }

  return function commitStreamingMessageUpdate(
    updater: Parameters<MessageUpdater>[0],
    immediate: boolean = false,
  ) {
    if (typeof window === "undefined") {
      updateMessage(updater);
      return;
    }

    if (immediate) {
      // Cancel any pending batched update and apply immediately
      if (rafId !== null) {
        cancelAnimationFrame(rafId);
        rafId = null;
      }
      if (pendingUpdater !== null) {
        // Apply the pending batch first so we don't lose it
        const prev = pendingUpdater;
        pendingUpdater = null;
        updateMessage(prev);
      }
      updateMessage(updater);
      return;
    }

    // Batch: compose with any pending updater
    if (pendingUpdater === null) {
      pendingUpdater = updater;
    } else {
      const prev = pendingUpdater;
      pendingUpdater = (messages: ReaderAskUiMessageDto[]) =>
        updater(prev(messages));
    }
    scheduleFlush();
  };
}

function agenticTerminalMessageStatus(
  finalStatus: ReaderAskAgenticTerminalStatusDto,
): "failed" | "interrupted" {
  // Hard failures keep failed; soft/cancel terminals reuse interrupted.
  return finalStatus === "failed" ? "failed" : "interrupted";
}

type SynchronousOptionalActivityState = {
  lastProgressSequence: number;
  webSearchOutcome: AgenticActivityOutcome | null;
  articleOutcomeObservations: AgenticActivityOutcome[];
  settled: boolean;
};

function createSynchronousOptionalActivityState(): SynchronousOptionalActivityState {
  return {
    lastProgressSequence: 0,
    webSearchOutcome: null,
    articleOutcomeObservations: [],
    settled: false,
  };
}

function mapWebSearchSummaryOutcome(
  summary: ReaderAskWebSearchSummaryDto,
): AgenticActivityOutcome {
  switch (summary.outcome) {
    case "completed":
      return "success";
    case "no_results":
      return "empty";
    case "unavailable":
    case "timeout":
      return "degraded";
    case "failed":
      return "failed";
  }
}

function recordSynchronousOptionalProgress(
  state: SynchronousOptionalActivityState,
  payload: ReaderAskAgenticProgressPayloadDto,
): void {
  if (state.settled) {
    return;
  }
  const sequence = payload.sequence;
  if (
    sequence == null ||
    !Number.isSafeInteger(sequence) ||
    sequence <= state.lastProgressSequence
  ) {
    return;
  }
  state.lastProgressSequence = sequence;
  if (payload.outcome == null) {
    return;
  }
  if (payload.activity_id === "web_search") {
    state.webSearchOutcome = payload.outcome;
  } else if (payload.activity_id === "article_evidence") {
    state.articleOutcomeObservations.push(payload.outcome);
  }
}

function settleSynchronousOptionalActivity(
  state: SynchronousOptionalActivityState,
  webSearchSummary: ReaderAskWebSearchSummaryDto | null,
): void {
  // A valid message.completed Host summary is authoritative for web_search.
  // A null summary means that no completed web search summary was supplied;
  // preserve the last trusted live outcome instead of guessing success.
  if (webSearchSummary !== null) {
    state.webSearchOutcome = mapWebSearchSummaryOutcome(webSearchSummary);
  }
  state.settled = true;
}

function hasStableOptionalToolWarning(
  state: SynchronousOptionalActivityState,
): boolean {
  const articleOutcome = aggregateArticleEvidenceOutcome(
    state.articleOutcomeObservations,
  );
  return (
    state.webSearchOutcome === "degraded" ||
    state.webSearchOutcome === "failed" ||
    articleOutcome === "degraded" ||
    articleOutcome === "failed"
  );
}

export function createSseMessageHandler(
  initialMessageId: string,
  updateMessage: MessageUpdater,
  onMessageIdAssigned: ((assignedId: string) => void) | undefined,
  onError: (message: string) => void,
  onAgenticActivity?: (event: AgenticActivityEvent) => void,
  // Canonical terminal-notice callback. Fired after a
  // trusted identity check passes (see applyAgenticTerminal). The panel uses
  // projectTurnTerminalNotice to build the AskSystemNotice from these fields
  // — it must NOT hand-craft a notice from the formatted message string.
  // Foreign / stale terminals (mismatched message_id / thread_id /
  // turn_run_id vs. the active run identity) are dropped silently: no
  // notice, no UI change, no composer unlock.
  onTerminalNotice?: (args: {
    messageId: string;
    finalStatus: string | null;
    terminalReason: string | null;
  }) => void,
  // Canonical optional-tool warning callback. Fired
  // from applyAgenticCompleted only when the final public activity fold is
  // degraded or failed. The panel uses projectOptionalToolWarning to build
  // a dismissible turn-scoped warning notice bound to the canonical
  // assistant message_id. This notice is the SOLE presentation owner for
  // the optional-tool warning — the Web activity / Sources area must not
  // duplicate it. The synchronous fold is reset on run_started (per-turn).
  onOptionalToolWarning?: (args: { messageId: string }) => void,
) {
  let currentMessageId = initialMessageId;
  // Agentic terminal may arrive as both agentic.terminal and message.interrupted
  // with the same payload; only apply UI terminal side-effects once per stream.
  let agenticTerminalHandled = false;
  // Synchronous, provider-neutral outcome fold for the warning decision.
  // React activity reduction is intentionally separate: a completed frame
  // can arrive before its async reducer update, so this handler keeps only
  // the typed activity id, server sequence, and public outcome fields needed
  // to settle the single SystemMessage warning.
  let synchronousOptionalActivity = createSynchronousOptionalActivityState();
  let optionalToolWarningFired = false;
  let contextCompactionIdentity: {
    messageId: string;
    threadId: string;
    turnRunId: string;
  } | null = null;
  // identity of the active run, captured when agentic.run_started
  // is accepted. Every v2 event that can mutate the turn must match this
  // identity, including the user-visible provider reasoning stream.
  let activeRunIdentity: {
    messageId: string;
    threadId: string;
    turnRunId: string;
  } | null = null;
  let reasoningSequence: number | null = null;
  let reasoningSealed = false;
  // generation_id tracking for message.preview_reset /
  // message.delta attribution. ``null`` means no preview_reset has been
  // accepted yet — the first generation (generation_id=0) is implicitly
  // active. After a trusted preview_reset, only deltas whose
  // generation_id matches ``activeGenerationId`` are applied to
  // provisional_content_md; stale-generation deltas are discarded so
  // the provisional preview never mixes text from two generations.
  let activeGenerationId: number | null = null;
  // Answering is a public lifecycle step only after the first identity-valid
  // message.delta for the active generation. A preview reset starts a fresh
  // generation and therefore permits one new answer_started event.
  let answerGenerationStarted: number | null = null;
  const commitStreamingMessageUpdate = createStreamingCommit(updateMessage);

  function matchesActiveRunIdentity(payload: {
    message_id?: string | null;
    thread_id?: string | null;
    turn_run_id?: string | null;
  }): boolean {
    return (
      activeRunIdentity === null ||
      (payload.message_id === activeRunIdentity.messageId &&
        payload.thread_id === activeRunIdentity.threadId &&
        payload.turn_run_id === activeRunIdentity.turnRunId)
    );
  }

  function applyAgenticCompleted(payload: ReaderAskAgenticCompletedPayloadDto) {
    // The SSE consumer is the trust owner and never dispatches an unattributed
    // v2 terminal. This local guard protects against foreign/stale frames once
    // run_started has established an identity, without maintaining a second
    // competing pre-start trust policy in the UI handler.
    if (!matchesActiveRunIdentity(payload)) {
      return;
    }
    reasoningSealed = true;
    // Capture the streaming temp id BEFORE reassignment so we can still find it.
    const previousMessageId = currentMessageId;
    if (payload.message_id) {
      currentMessageId = payload.message_id;
      onMessageIdAssigned?.(payload.message_id);
    }
    onAgenticActivity?.({ type: "answer_completed" });
    onAgenticActivity?.({ type: "completed" });
    settleSynchronousOptionalActivity(
      synchronousOptionalActivity,
      payload.web_search,
    );
    // The warning is derived from the final stable Host outcome, not from a
    // historical unavailable frame. SystemMessage remains its only owner;
    // the activity projection receives no warning copy or provider detail.
    if (
      !optionalToolWarningFired &&
      payload.message_id &&
      hasStableOptionalToolWarning(synchronousOptionalActivity)
    ) {
      optionalToolWarningFired = true;
      onOptionalToolWarning?.({ messageId: payload.message_id });
    }
    commitStreamingMessageUpdate((messages) =>
      messages.map((message) => {
        if (
          message.id !== previousMessageId &&
          message.id !== currentMessageId &&
          message.id !== payload.message_id
        ) {
          return message;
        }
        return {
          ...message,
          id: payload.message_id,
          thread_id: payload.thread_id || message.thread_id,
          status: "completed",
          // Agentic wire field is answer_text; map into the UI content slot only.
          content_md: payload.answer_text,
          // Atomically drop the provisional preview
          // when the canonical answer arrives. The provisional slot must
          // never survive a committed terminal.
          provisional_content_md: null,
          // Reader Record Ask v2 has no legacy action, evidence, tool,
          // response-card, article-RAG, or supplement projection. Clear any
          // stale fields from a reused retry/history row instead of allowing
          // them to survive through object spread.
          citations: [],
          action_proposals: [],
          tool_trace: [],
          evidence: [],
          trace_summary: null,
          disambiguation: null,
          external_asset_disambiguation: null,
          response_cards: [],
          resolved_context: null,
          context_plan: null,
          resolved_context_input: null,
          run_info: null,
          supplement_candidates: [],
          persisted_supplements: [],
          follow_up_suggestions: [],
          reasoning_md: message.reasoning_md ?? null,
          reasoning_status:
            message.reasoning_status === "streaming"
              ? "completed"
              : message.reasoning_status ?? null,
          reasoning_truncated: message.reasoning_truncated ?? null,
          reasoning_visibility_status:
            message.reasoning_visibility_status ?? null,
          learner_reasoning_text: null,
          learner_reasoning_status: null,
          learner_reasoning_stage: null,
          learner_reasoning_sequence: null,
          replan_status: "idle",
          compacting: false,
          regenerate_preview: false,
          // Public v2: no raw evidence / handles in browser state.
          agentic_evidence: null,
          agentic_evidence_scope: null,
          // Semantic answer blocks with public citation_ids.
          agentic_answer_blocks: payload.answer_blocks ?? null,
          // Finalizer-minted public citations for InlineCitation only.
          agentic_citations: payload.citations ?? null,
          // Turn-level web search summary (null when search not invoked).
          agentic_web_search: payload.web_search ?? null,
        };
      }),
    true);
  }

  function applyAgenticTerminal(payload: ReaderAskAgenticTerminalPayloadDto) {
    if (agenticTerminalHandled) {
      return;
    }
    // Foreign / stale terminal guard. If a trusted
    // run_started was accepted, the terminal must match its identity
    // exactly (message_id / thread_id / turn_run_id). A foreign or stale
    // terminal is dropped silently: no notice, no UI change, no composer
    // unlock, no agentic-activity terminal dispatch. This prevents a
    // late-arriving terminal from a previous turn from creating a notice
    // or unlocking the composer for the wrong turn.
    if (!matchesActiveRunIdentity(payload)) {
      return;
    }
    agenticTerminalHandled = true;
    // Capture the streaming temp id BEFORE reassignment so we can still find it.
    const previousMessageId = currentMessageId;
    if (payload.message_id) {
      currentMessageId = payload.message_id;
      onMessageIdAssigned?.(payload.message_id);
    }
    onAgenticActivity?.({
      type: "answer_interrupted",
      finalStatus: payload.final_status,
    });
    onAgenticActivity?.({
      type: "terminal",
      finalStatus: payload.final_status,
    });
    // Fire the canonical terminal-notice callback with
    // the typed fields. The panel uses projectTurnTerminalNotice to build
    // the AskSystemNotice. We no longer route the formatted string through
    // onError (which the panel would hand-craft into a notice). onError is
    // now reserved for legacy stream-level `error` events only.
    const terminalMessageId = payload.message_id || currentMessageId;
    const terminalFinalStatus =
      typeof payload.final_status === "string" ? payload.final_status : null;
    const terminalReason =
      typeof payload.terminal_reason === "string" && payload.terminal_reason.trim()
        ? payload.terminal_reason.trim()
        : null;
    onTerminalNotice?.({
      messageId: terminalMessageId,
      finalStatus: terminalFinalStatus,
      terminalReason,
    });
    const nextStatus = agenticTerminalMessageStatus(payload.final_status);
    commitStreamingMessageUpdate((messages) =>
      messages.map((message) => {
        if (
          message.id !== previousMessageId &&
          message.id !== currentMessageId &&
          message.id !== payload.message_id
        ) {
          return message;
        }
        return {
          ...message,
          id: payload.message_id || message.id,
          thread_id: payload.thread_id || message.thread_id,
          status: nextStatus,
          // Keep the typed terminal status so the interrupted
          // bubble can refine its copy (context_stale / cancelled / …).
          final_status: payload.final_status,
          // Non-ok terminals must NEVER preserve
          // the provisional preview as canonical. Drop the provisional
          // slot and keep `content_md` exactly as it was before this
          // turn started (empty for a fresh turn, or the previous
          // canonical answer when this was a retry/regenerate). This
          // fixes the bug where an output-validator failure left a
          // half answer visible in the bubble.
          content_md: message.content_md,
          provisional_content_md: null,
          reasoning_md: message.reasoning_md ?? null,
          reasoning_status:
            message.reasoning_md || message.reasoning_status === "streaming"
              ? "interrupted"
              : null,
          reasoning_truncated: message.reasoning_truncated ?? null,
          reasoning_visibility_status:
            message.reasoning_visibility_status ?? null,
          // Failed/cancelled turns never keep learner reasoning in cold history;
          // drop hot provisional summary as well (silent, no error UI).
          learner_reasoning_text: null,
          learner_reasoning_status: null,
          learner_reasoning_stage: null,
          learner_reasoning_sequence: null,
          replan_status: "idle",
          compacting: false,
          regenerate_preview: false,
          // Terminals never carry navigable sources or displayable citations.
          agentic_evidence: null,
          agentic_evidence_scope: null,
          agentic_answer_blocks: null,
          agentic_citations: null,
          citations: [],
          action_proposals: [],
          tool_trace: [],
          evidence: [],
          trace_summary: null,
          disambiguation: null,
          external_asset_disambiguation: null,
          response_cards: [],
          resolved_context: null,
          context_plan: null,
          resolved_context_input: null,
          run_info: null,
          supplement_candidates: [],
          persisted_supplements: [],
          follow_up_suggestions: [],
        };
      }),
    true);
  }

  return function handleSseEvent(event: ReaderAskStreamEnvelopeDto) {
    if (
      event.event === "context.compaction.started" ||
      event.event === "context.compaction.completed" ||
      event.event === "context.compaction.failed" ||
      event.event === "context.compaction.fallback"
    ) {
      if (!isReaderAskContextCompactionPayload(event.data)) {
        return;
      }
      const payload = event.data;
      if (event.event === "context.compaction.started") {
        if (payload.message_id !== currentMessageId) {
          currentMessageId = payload.message_id;
          onMessageIdAssigned?.(payload.message_id);
        }
        contextCompactionIdentity = {
          messageId: payload.message_id,
          threadId: payload.thread_id,
          turnRunId: payload.turn_run_id,
        };
      } else if (
        contextCompactionIdentity == null ||
        contextCompactionIdentity.messageId !== payload.message_id ||
        contextCompactionIdentity.threadId !== payload.thread_id ||
        contextCompactionIdentity.turnRunId !== payload.turn_run_id
      ) {
        return;
      }
      const status =
        event.event === "context.compaction.started"
          ? "running"
          : event.event === "context.compaction.completed"
            ? "completed"
            : event.event === "context.compaction.fallback"
              ? "fallback"
              : "failed";
      commitStreamingMessageUpdate(
        (messages) =>
          messages.map((message) =>
            message.id === currentMessageId
              ? {
                  ...message,
                  context_compaction: {
                    status,
                    elapsedMs: payload.elapsed_ms,
                  },
                }
              : message,
          ),
        true,
      );
      return;
    }

    // Agentic-only progress events are non-terminal. They update the activity
    // indicator only — never complete or fail the assistant bubble.
    if (event.event === "agentic.run_started") {
      if (isReaderAskAgenticRunStartedPayload(event.data)) {
        if (event.data.message_id) {
          currentMessageId = event.data.message_id;
          onMessageIdAssigned?.(event.data.message_id);
        }
        // Capture the active run identity for subsequent public
        // activity and answer lifecycle events.
        activeRunIdentity = {
          messageId: event.data.message_id,
          threadId: event.data.thread_id,
          turnRunId: event.data.turn_run_id,
        };
        activeGenerationId = 0;
        answerGenerationStarted = null;
        reasoningSequence = null;
        reasoningSealed = false;
        // A retry owns a fresh stream; clear the previous attempt first.
        commitStreamingMessageUpdate(
          (messages) =>
            messages.map((message) =>
              message.id === currentMessageId
                ? {
                    ...message,
                    reasoning_md: null,
                    reasoning_status: null,
                    reasoning_truncated: null,
                    reasoning_visibility_status: null,
                    learner_reasoning_text: null,
                    learner_reasoning_status: null,
                    learner_reasoning_stage: null,
                    learner_reasoning_sequence: null,
                  }
                : message,
            ),
          true,
        );
        // Reset the synchronous outcome fold for the new turn. An outcome or
        // warning from a previous turn must never bleed into this one.
        synchronousOptionalActivity = createSynchronousOptionalActivityState();
        optionalToolWarningFired = false;
        onAgenticActivity?.({
          type: "run_started",
          messageId: event.data.message_id ?? currentMessageId,
          turnRunId: event.data.turn_run_id ?? null,
        });
      }
      return;
    }

    if (event.event === "agentic.progress") {
      if (isReaderAskAgenticProgressPayload(event.data)) {
        const progressPayload = event.data;
        recordSynchronousOptionalProgress(
          synchronousOptionalActivity,
          progressPayload,
        );
        onAgenticActivity?.({
          type: "progress",
          payload: progressPayload,
        });
      }
      return;
    }

    if (event.event === "agentic.terminal") {
      if (isReaderAskAgenticTerminalPayload(event.data)) {
        applyAgenticTerminal(event.data);
      }
      return;
    }

    if (event.event === "agentic.reasoning.started") {
      if (
        agenticTerminalHandled ||
        reasoningSealed ||
        reasoningSequence !== null ||
        !isReaderAskAgenticReasoningStartedPayload(event.data) ||
        !matchesActiveRunIdentity(event.data)
      ) {
        return;
      }
      const payload = event.data;
      reasoningSequence = 0;
      commitStreamingMessageUpdate(
        (messages) =>
          messages.map((message) =>
            message.id === currentMessageId || message.id === payload.message_id
              ? {
                  ...message,
                  reasoning_md: "",
                  reasoning_status: "streaming",
                  reasoning_truncated: false,
                  reasoning_visibility_status: null,
                }
              : message,
          ),
        true,
      );
      return;
    }

    if (event.event === "agentic.reasoning.delta") {
      if (
        agenticTerminalHandled ||
        reasoningSealed ||
        reasoningSequence == null ||
        !isReaderAskAgenticReasoningDeltaPayload(event.data) ||
        !matchesActiveRunIdentity(event.data) ||
        event.data.seq !== reasoningSequence + 1
      ) {
        return;
      }
      const payload = event.data;
      reasoningSequence = payload.seq;
      commitStreamingMessageUpdate((messages) =>
        messages.map((message) =>
          message.id === currentMessageId || message.id === payload.message_id
            ? {
                ...message,
                reasoning_md: `${message.reasoning_md ?? ""}${payload.delta}`,
                reasoning_status: "streaming",
              }
            : message,
        ),
      );
      return;
    }

    if (event.event === "agentic.reasoning.completed") {
      if (
        agenticTerminalHandled ||
        reasoningSealed ||
        reasoningSequence == null ||
        !isReaderAskAgenticReasoningCompletedPayload(event.data) ||
        !matchesActiveRunIdentity(event.data) ||
        event.data.seq !== reasoningSequence + 1
      ) {
        return;
      }
      const payload = event.data;
      reasoningSequence = payload.seq;
      reasoningSealed = true;
      commitStreamingMessageUpdate(
        (messages) =>
          messages.map((message) =>
            message.id === currentMessageId || message.id === payload.message_id
              ? {
                  ...message,
                  reasoning_status: payload.has_content ? "completed" : null,
                  reasoning_truncated: payload.truncated,
                  reasoning_visibility_status: payload.visibility_status,
                }
              : message,
          ),
        true,
      );
      return;
    }

    if (event.event === "agentic.learner_reasoning.snapshot") {
      // Retired projector event: never open a second live reasoning lane.
      return;
    }

    if (event.event === "message.started") {
      const messageId = String((event.data as { message_id?: unknown }).message_id ?? currentMessageId);
      currentMessageId = messageId;
      onMessageIdAssigned?.(messageId);
      return;
    }

    if (event.event === "message.preview_reset") {
      // Canonical message.preview_reset wire. The server emits this at a
      // tool-result / ModelRetry boundary BEFORE the new generation
      // streams its first delta. The client MUST clear
      // provisional_content_md (the in-progress preview) but MUST NOT
      // touch canonical content_md. Only deltas whose generation_id
      // matches the new generation are applied afterwards.
      //
      // Trust validation: if an active run identity was captured at
      // agentic.run_started, the reset's message_id / thread_id /
      // turn_run_id must match it exactly — foreign / stale resets are
      // ignored (no UI mutation). If no run_started was seen yet, the
      // reset is accepted only when it targets the current message id
      // (fail-closed against unattributed resets).
      const payload = event.data as {
        generation_id?: unknown;
        message_id?: unknown;
        thread_id?: unknown;
        turn_run_id?: unknown;
        reason?: unknown;
      };
      const resetGenerationId =
        typeof payload.generation_id === "number" &&
        Number.isInteger(payload.generation_id)
          ? payload.generation_id
          : null;
      if (resetGenerationId === null || resetGenerationId < 1) {
        // Invalid generation_id — ignore the reset (fail-closed).
        return;
      }
      const resetMessageId =
        typeof payload.message_id === "string" ? payload.message_id : null;
      const resetThreadId =
        typeof payload.thread_id === "string" ? payload.thread_id : null;
      const resetTurnRunId =
        typeof payload.turn_run_id === "string" ? payload.turn_run_id : null;
      if (activeRunIdentity !== null) {
        if (
          resetMessageId !== activeRunIdentity.messageId ||
          resetThreadId !== activeRunIdentity.threadId ||
          resetTurnRunId !== activeRunIdentity.turnRunId
        ) {
          // Foreign / stale reset — ignore.
          return;
        }
      } else if (resetMessageId !== currentMessageId) {
        // No run_started captured and the reset does not target the
        // current message — ignore (fail-closed).
        return;
      }
      const currentGenerationId = activeGenerationId ?? 0;
      if (resetGenerationId <= currentGenerationId) {
        // Duplicate / stale reset — never clear a newer preview.
        return;
      }
      activeGenerationId = resetGenerationId;
      answerGenerationStarted = null;
      commitStreamingMessageUpdate((messages) =>
        messages.map((message) =>
          message.id === currentMessageId
            ? {
                ...message,
                // Clear the provisional preview only. Canonical
                // content_md is never touched by a reset — it is
                // replaced atomically by message.completed.
                provisional_content_md: "",
                regenerate_preview: false,
              }
            : message,
        ),
        true,
      );
      return;
    }

    if (event.event === "message.delta") {
      const payload = event.data as {
        delta?: unknown;
        generation_id?: unknown;
        message_id?: unknown;
        thread_id?: unknown;
        turn_run_id?: unknown;
      };
      const delta = String(payload.delta ?? "");
      if (
        activeRunIdentity !== null &&
        (payload.message_id !== activeRunIdentity.messageId ||
          payload.thread_id !== activeRunIdentity.threadId ||
          payload.turn_run_id !== activeRunIdentity.turnRunId)
      ) {
        // Agentic answer deltas are turn-owned. A matching generation is
        // insufficient when the message/thread/run identity is foreign.
        return;
      }
      // Attribute the message.delta to the active generation. After a
      // trusted preview_reset, only deltas whose generation_id matches
      // activeGenerationId are applied — stale-generation deltas (from
      // an older generation whose preview was just cleared) are
      // discarded so the provisional preview never mixes text from two
      // generations. Before any preview_reset, generation_id is
      // expected to be 0 (or absent for forward-compat with streams
      // that do not tag deltas).
      const deltaGenerationId =
        typeof payload.generation_id === "number" &&
        Number.isInteger(payload.generation_id)
          ? payload.generation_id
          : null;
      if (activeGenerationId !== null) {
        if (deltaGenerationId !== activeGenerationId) {
          // Stale-generation delta — discard.
          return;
        }
      } else if (deltaGenerationId !== null && deltaGenerationId !== 0) {
        // No preview_reset seen yet but the delta carries a non-zero
        // generation_id — discard (the matching preview_reset was
        // lost or arrived out of order).
        return;
      }
      if (activeRunIdentity !== null) {
        const generationId = activeGenerationId ?? 0;
        if (answerGenerationStarted !== generationId) {
          answerGenerationStarted = generationId;
          onAgenticActivity?.({ type: "answer_started", generationId });
        }
      }
      // message.delta accumulates into the provisional
      // preview slot only. `content_md` is reserved for the canonical
      // answer that arrives atomically via `message.completed`. This
      // guarantees that an output-validator failure / cancel / abort
      // never preserves a half answer as canonical. The `regenerate_preview`
      // flag is kept for legacy callers but no longer drives a replace-vs-
      // append decision on `content_md` — both paths append to the
      // provisional slot, which is reset on retry boundary (see
      // `resetForRetryBoundary` callers).
      commitStreamingMessageUpdate((messages) =>
        messages.map((message) =>
          message.id === currentMessageId
            ? {
                ...message,
                provisional_content_md: message.regenerate_preview
                  ? delta
                  : `${message.provisional_content_md ?? ""}${delta}`,
                regenerate_preview: false,
                compacting: false,
              }
            : message,
        ),
      );
      return;
    }

    if (event.event === "replan.started") {
      commitStreamingMessageUpdate((messages) =>
        messages.map((message) =>
          message.id === currentMessageId
            ? { ...message, replan_status: "replanning" }
            : message,
        ),
        true,
      );
      return;
    }

    if (event.event === "context.compacting") {
      commitStreamingMessageUpdate((messages) =>
        messages.map((message) =>
          message.id === currentMessageId
            ? { ...message, compacting: true }
            : message,
        ),
        true,
      );
      return;
    }

    if (event.event === "message.completed") {
      // `message.completed` is a canonical v2 commit event. Any markerless
      // or v1/history payload is ignored; no legacy answer projection is
      // allowed to reach the Reader Record UI.
      if (isReaderAskAgenticCompletedPayload(event.data)) {
        applyAgenticCompleted(event.data);
        return;
      }
      return;

    }

    if (event.event === "message.interrupted") {
      // Agentic non-ok terminal may be duplicated on message.interrupted, but
      // only the canonical typed v2 payload is trusted.
      if (isReaderAskAgenticTerminalPayload(event.data)) {
        applyAgenticTerminal(event.data);
        return;
      }

      return;
    }

    if (event.event === "error") {
      onError(formatStreamError(event));
      commitStreamingMessageUpdate((messages) =>
        messages.map((message) =>
          message.id === currentMessageId
            ? {
                ...message,
                status: "failed",
                compacting: false,
                replan_status: "idle",
                // Drop provisional preview on
                // stream error; never preserve half answers.
                provisional_content_md: null,
              }
            : message,
        ),
      true);
    }
  };
}

/**
 * Normalize thread-detail / thread-list messages into UI state.
 *
 * Reader Record Ask v2 thread detail is the sole history input. This mapper
 * validates public answer blocks, citations, web-search summary, and
 * learner-reasoning fields, then clears every legacy analysis/article-RAG/
 * action/supplement projection before render.
 *
 * Markerless and agentic-v1 assistant history is rejected here; there is no
 * second history lane or legacy fallback in the Reader web client.
 *
 * The SSE merge path already calls the mapper inline; this helper covers
 * the cold-load / reset paths that bypass streaming. The mapper is
 * idempotent — it only reads `status` / `should_attach` / `context_ids`
 * / `citations` and produces the safe shape — so re-running it on a
 * message that already carries a safe sidecar is a no-op.
 */
function normalizeReaderAskMessages(
  messages: ReaderAskMessageDto[] | ReaderAskUiMessageDto[],
): ReaderAskUiMessageDto[] {
  return messages.flatMap((message) => {
    const uiState = message as Partial<ReaderAskMessageUiStateDto>;
    const isAssistantMessage = message.role === "assistant";
    const isCanonicalV2Assistant =
      isAssistantMessage &&
      message.execution_version === READER_ASK_AGENTIC_EXECUTION_VERSION;
    // Reading Record v2 history: fail closed on the execution marker before
    // mapping any assistant content. Markerless, v1, and forged assistant
    // rows are not a second history lane and must not render.
    if (isAssistantMessage && !isCanonicalV2Assistant) {
      return [];
    }
    // Public v2 never hydrates raw agentic evidence / handles into UI state.
    const agenticEvidence = null;
    const agenticAnswerBlocks = isCanonicalV2Assistant && isReaderAskAgenticAnswerBlockList(
      message.agentic_answer_blocks,
    )
      ? message.agentic_answer_blocks
      : null;
    const agenticCitations = isCanonicalV2Assistant && isReaderAskAgenticCitationList(message.agentic_citations)
      ? message.agentic_citations
      : null;
    // Validate the web-search summary with the same guard as the hot SSE path.
    // Malformed summaries must be coerced to null rather than half-accepted.
    const agenticWebSearch = isCanonicalV2Assistant && isReaderAskWebSearchSummary(
      uiState.agentic_web_search,
    )
      ? (uiState.agentic_web_search ?? null)
      : null;
    const finalStatus = isAssistantMessage && isReaderAskAgenticFinalStatus(message.final_status)
      ? message.final_status
      : null;
    const reasoningText =
      isCanonicalV2Assistant &&
      typeof message.reasoning_md === "string" &&
      message.reasoning_md.trim()
        ? message.reasoning_md
        : null;
    const reasoningVisibility =
      message.reasoning_visibility_status === "complete" ||
      message.reasoning_visibility_status === "truncated" ||
      message.reasoning_visibility_status === "blocked"
        ? message.reasoning_visibility_status
        : null;

    // Non-ok terminals never keep citations or web-search summary (matches
    // hot applyAgenticTerminal — a terminal turn did not produce a completed
    // answer, so any persisted web_search would be a forgery).
    let finalAnswerBlocks = agenticAnswerBlocks;
    let finalCitations = agenticCitations;
    let finalWebSearch = agenticWebSearch;
    if (finalStatus != null && finalStatus !== "ok") {
      finalAnswerBlocks = null;
      finalCitations = null;
      finalWebSearch = null;
    }

    return {
      ...message,
      // Backend already projected content_md / status for completed & terminal.
      // Never invent answers for terminals; keep content_md as returned.
      // The execution marker belongs to the assistant turn. User messages
      // remain ordinary chat entries even though the thread is v2-only.
      execution_version: isAssistantMessage ? message.execution_version : null,
      final_status: finalStatus,
      // Public v2: never hydrate raw evidence / scope identity into browser state.
      agentic_evidence: agenticEvidence,
      agentic_evidence_scope: null,
      agentic_answer_blocks: finalAnswerBlocks,
      agentic_citations: finalCitations,
      agentic_web_search: finalWebSearch,
      // Article-RAG is not a v2 browser surface. Drop any stale persisted
      // sidecar instead of allowing it to survive through object spread.
      article_rag: null,
      // The backend already validated and persisted the exact text that was
      // published to this user; preserve whitespace on cold restore.
      reasoning_md: reasoningText,
      reasoning_status: reasoningText
        ? finalStatus != null && finalStatus !== "ok"
          ? "interrupted"
          : "completed"
        : null,
      reasoning_truncated:
        reasoningText && uiState.reasoning_truncated === true ? true : null,
      reasoning_visibility_status: reasoningText ? reasoningVisibility : null,
      learner_reasoning_text:
        isAssistantMessage &&
        typeof message.learner_reasoning_text === "string" &&
        message.learner_reasoning_text.trim()
          ? message.learner_reasoning_text.trim()
          : null,
      learner_reasoning_status: isAssistantMessage && message.learner_reasoning_text
        ? "completed"
        : null,
      learner_reasoning_stage: isAssistantMessage
        ? message.learner_reasoning_stage ?? null
        : null,
      // Never surface agentic items through the legacy evidence channel.
      citations: [],
      action_proposals: [],
      tool_trace: [],
      evidence: [],
      trace_summary: null,
      disambiguation: null,
      external_asset_disambiguation: null,
      response_cards: [],
      resolved_context: null,
      context_plan: null,
      resolved_context_input: null,
      run_info: null,
      supplement_candidates: [],
      persisted_supplements: [],
      follow_up_suggestions: [],
      // Cold history never carries a provisional
      // preview. Only the canonical `content_md` is persisted server-side.
      provisional_content_md: null,
      // Cold v2 turns render the canonical answer only; the typed
      // process steps are session-memory only and never persist across reload.
      agentic_process_snapshot: null,
      context_compaction: null,
    } as ReaderAskUiMessageDto;
  });
}

/** Exported for unit tests of cold-load normalization. */
export { normalizeReaderAskMessages };

export function buildAssistantBlocks(message: ReaderAskUiMessageDto): AskPanelBlock[] {
  // Reader Record Ask v2 has one assistant disclosure owner: the answer
  // block, which owns learner_reasoning, ChainOfThought, canonical citations,
  // and the typed web-search sources. Legacy action, context, evidence,
  // reasoning, supplement, and follow-up blocks have no render lane.
  void message;
  return [{ kind: "answer" }];
}
