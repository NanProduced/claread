/**
 * Pure frontend projection for Agentic Ask activity/progress SSE.
 *
 * Privacy: only projects server-whitelisted phase/summary fields. Never stores
 * tool args, queries, locators, evidence handles, fingerprints, or raw errors.
 */

export const AGENTIC_EXECUTION_VERSION = "reader_record_ask_agentic_v2" as const;

function warnIgnoredProgress(reason: string): void {
  if (process.env.NODE_ENV === "development") {
    console.warn("[AskAgenticActivity] ignored progress", reason);
  }
}

export type AgenticActivityPhase =
  | "agent_running"
  | "analysis"
  | "reading_context"
  | "searching_article"
  | "searching_web"
  | "composing_answer"
  | "validating_evidence";

export type AgenticActivityKind =
  | "started"
  | "completed"
  | "unavailable"
  | "failed";

export type AgenticActivityOutcome = "success" | "empty" | "degraded" | "failed";

export type AgenticActivityStatus =
  | "idle"
  | "running"
  | "degraded"
  | "completed"
  | "failed"
  | "cancelled";

export type AgenticActivityToolName =
  | "read_range"
  | "search_current_article"
  | "expand_evidence"
  | "search_web";

export type AgenticActivityId = "article_evidence" | "web_search";

export type AgenticActivityStep = {
  /** Local UI order; never used as the server progress watermark. */
  localOrdinal: number;
  /** Server progress sequence, or the current watermark for a synthetic step. */
  sequence: number;
  phase: AgenticActivityPhase;
  activity: AgenticActivityKind;
  summary: string;
  elapsedMs: number;
  toolName: AgenticActivityToolName | null;
  status: "running" | "ok" | "unavailable" | "failed" | null;
  outcome: AgenticActivityOutcome | null;
  durationMs: number | null;
  activityId: AgenticActivityId | null;
  attemptCount: number | null;
  callSequence: number | null;
};

export type AgenticActivityState = {
  status: AgenticActivityStatus;
  /** Last accepted monotonic sequence (0 when idle / reset). */
  lastSequence: number;
  /** Next local order slot for first-seen steps and synthetic answering rows. */
  nextLocalOrdinal: number;
  currentPhase: AgenticActivityPhase | null;
  currentSummary: string | null;
  currentActivity: AgenticActivityKind | null;
  currentToolName: AgenticActivityToolName | null;
  currentStatus: AgenticActivityStep["status"];
  currentDurationMs: number | null;
  elapsedMs: number;
  steps: AgenticActivityStep[];
  turnRunId: string | null;
  messageId: string | null;
  hasUnavailable: boolean;
  /** Confirmed article evidence outcomes for the named article accumulator. */
  articleOutcomeObservations: AgenticActivityOutcome[];
};

export type AgenticActivityProgressInput = {
  execution_version?: string | null;
  sequence?: number | null;
  phase?: string | null;
  activity?: string | null;
  summary?: string | null;
  elapsed_ms?: number | null;
  tool_name?: string | null;
  status?: string | null;
  outcome?: string | null;
  duration_ms?: number | null;
  activity_id?: string | null;
  attempt_count?: number | null;
  call_sequence?: number | null;
};

export type AgenticActivityEvent =
  | { type: "reset" }
  | {
      type: "run_started";
      messageId?: string | null;
      turnRunId?: string | null;
    }
  | { type: "answer_started"; generationId?: number | null }
  | { type: "answer_completed" }
  | {
      type: "answer_interrupted";
      finalStatus?: "failed" | "cancelled" | "context_stale" | "invalid_citations" | string | null;
    }
  | { type: "progress"; payload: AgenticActivityProgressInput }
  | { type: "completed" }
  | {
      type: "terminal";
      finalStatus?: "failed" | "cancelled" | "context_stale" | "invalid_citations" | string | null;
    };

const PHASES = new Set<AgenticActivityPhase>([
  "agent_running",
  "analysis",
  "reading_context",
  "searching_article",
  "searching_web",
  "composing_answer",
  "validating_evidence",
]);

const ACTIVITIES = new Set<AgenticActivityKind>([
  "started",
  "completed",
  "unavailable",
  "failed",
]);
const ACTIVITY_IDS = new Set<AgenticActivityId>([
  "article_evidence",
  "web_search",
]);

const TOOLS = new Set<AgenticActivityToolName>([
  "read_range",
  "search_current_article",
  "expand_evidence",
  "search_web",
]);

const STEP_STATUSES = new Set<NonNullable<AgenticActivityStep["status"]>>([
  "running",
  "ok",
  "unavailable",
  "failed",
]);
const OUTCOMES = new Set<AgenticActivityOutcome>([
  "success",
  "empty",
  "degraded",
  "failed",
]);

const TERMINAL_STATUSES = new Set<AgenticActivityStatus>([
  "completed",
  "failed",
  "cancelled",
]);

export function createIdleAgenticActivityState(): AgenticActivityState {
  return {
    status: "idle",
    lastSequence: 0,
    nextLocalOrdinal: 0,
    currentPhase: null,
    currentSummary: null,
    currentActivity: null,
    currentToolName: null,
    currentStatus: null,
    currentDurationMs: null,
    elapsedMs: 0,
    steps: [],
    turnRunId: null,
    messageId: null,
    hasUnavailable: false,
    articleOutcomeObservations: [],
  };
}

function asPhase(value: unknown): AgenticActivityPhase | null {
  return typeof value === "string" && PHASES.has(value as AgenticActivityPhase)
    ? (value as AgenticActivityPhase)
    : null;
}

function asActivity(value: unknown): AgenticActivityKind | null {
  return typeof value === "string" && ACTIVITIES.has(value as AgenticActivityKind)
    ? (value as AgenticActivityKind)
    : null;
}

function asActivityId(value: unknown): AgenticActivityId | null {
  return typeof value === "string" && ACTIVITY_IDS.has(value as AgenticActivityId)
    ? (value as AgenticActivityId)
    : null;
}

function asToolName(value: unknown): AgenticActivityToolName | null {
  return typeof value === "string" && TOOLS.has(value as AgenticActivityToolName)
    ? (value as AgenticActivityToolName)
    : null;
}

function asStepStatus(value: unknown): AgenticActivityStep["status"] {
  return typeof value === "string" && STEP_STATUSES.has(value as NonNullable<AgenticActivityStep["status"]>)
    ? (value as NonNullable<AgenticActivityStep["status"]>)
    : null;
}

function asOutcome(value: unknown): AgenticActivityOutcome | null {
  return typeof value === "string" && OUTCOMES.has(value as AgenticActivityOutcome)
    ? (value as AgenticActivityOutcome)
    : null;
}

/**
 * Aggregate the confirmed outcomes belonging to article_evidence.
 *
 * This is deliberately separate from web_search: article tools report
 * evidence availability across several calls, so a successful call is
 * authoritative even if a later call fails. Unknown values are fail-closed
 * as degraded when callers use this helper directly; the wire reducer drops
 * them before they can enter the observation list.
 */
export function aggregateArticleEvidenceOutcome(
  observations: readonly unknown[],
): AgenticActivityOutcome | null {
  const confirmed: AgenticActivityOutcome[] = [];
  let hasUnknown = false;
  for (const observation of observations) {
    if (observation == null) {
      continue;
    }
    const outcome = asOutcome(observation);
    if (outcome == null) {
      hasUnknown = true;
      continue;
    }
    confirmed.push(outcome);
  }
  if (confirmed.includes("success")) {
    return "success";
  }
  if (hasUnknown || confirmed.includes("degraded")) {
    return "degraded";
  }
  const kinds = new Set(confirmed);
  if (kinds.size > 1) {
    return "degraded";
  }
  if (kinds.has("empty")) {
    return "empty";
  }
  if (kinds.has("failed")) {
    return "failed";
  }
  return null;
}

function asNonNegativeInt(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return null;
  }
  const n = Math.trunc(value);
  return n >= 0 ? n : null;
}

function asPositiveInt(value: unknown): number | null {
  const n = asNonNegativeInt(value);
  return n != null && n >= 1 ? n : null;
}

function sanitizeSummary(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  // Hard cap for UI safety; server already enforces short summaries.
  return trimmed.slice(0, 120);
}

/**
 * Reduce one agentic activity event into the next pure UI state.
 *
 * Ordering rules:
 * - sequence must strictly increase
 * - duplicate / out-of-order progress is ignored
 * - completed/terminal freezes further progress
 * - reset clears everything for the next user turn
 */
export function reduceAgenticActivityEvent(
  state: AgenticActivityState,
  event: AgenticActivityEvent,
): AgenticActivityState {
  if (event.type === "reset") {
    return createIdleAgenticActivityState();
  }

  if (event.type === "run_started") {
    // New turn always wins over residual progress from a previous stream.
    return {
      ...createIdleAgenticActivityState(),
      status: "running",
      messageId: event.messageId ?? null,
      turnRunId: event.turnRunId ?? null,
    };
  }

  if (event.type === "answer_started") {
    if (state.status === "idle" || TERMINAL_STATUSES.has(state.status)) {
      return state;
    }
    const localOrdinal = state.nextLocalOrdinal;
    const existingIndex = state.steps.findIndex(
      (step) => step.phase === "composing_answer",
    );
    const step: AgenticActivityStep = {
      localOrdinal,
      sequence: state.lastSequence,
      phase: "composing_answer",
      activity: "started",
      summary: "正在生成回答",
      elapsedMs: state.elapsedMs,
      toolName: null,
      status: "running",
      outcome: null,
      durationMs: null,
      activityId: null,
      attemptCount: null,
      callSequence: null,
    };
    const steps =
      existingIndex < 0
        ? [...state.steps, step]
        : state.steps.map((existing, index) =>
            index === existingIndex ? step : existing,
          );
    return {
      ...state,
      status: state.hasUnavailable ? "degraded" : "running",
      currentPhase: step.phase,
      currentSummary: step.summary,
      currentActivity: step.activity,
      currentToolName: null,
      currentStatus: step.status,
      currentDurationMs: null,
      steps,
      nextLocalOrdinal: localOrdinal + 1,
    };
  }

  if (event.type === "answer_completed" || event.type === "answer_interrupted") {
    if (state.status === "idle" || TERMINAL_STATUSES.has(state.status)) {
      return state;
    }
    const existingIndex = state.steps.findIndex(
      (step) => step.phase === "composing_answer",
    );
    if (existingIndex < 0) {
      return state;
    }
    const isCompleted = event.type === "answer_completed";
    const existing = state.steps[existingIndex];
    const step: AgenticActivityStep = {
      ...existing,
      activity: isCompleted ? "completed" : "failed",
      summary: isCompleted ? "已生成回答" : "回答未能完成",
      status: isCompleted ? "ok" : "failed",
      outcome: isCompleted ? "success" : "failed",
    };
    return {
      ...state,
      currentPhase: step.phase,
      currentSummary: step.summary,
      currentActivity: step.activity,
      currentToolName: null,
      currentStatus: step.status,
      currentDurationMs: step.durationMs,
      steps: state.steps.map((item, index) =>
        index === existingIndex ? step : item,
      ),
    };
  }

  if (event.type === "completed") {
    if (state.status === "idle") {
      return state;
    }
    return {
      ...state,
      status: "completed",
      currentActivity: state.currentActivity ?? "completed",
      currentStatus: state.currentStatus === "unavailable" ? "unavailable" : "ok",
    };
  }

  if (event.type === "terminal") {
    if (state.status === "idle" && state.steps.length === 0) {
      // Still surface a terminal when a stream failed before any activity.
      const failedStatus =
        event.finalStatus === "cancelled" ? "cancelled" : "failed";
      return {
        ...createIdleAgenticActivityState(),
        status: failedStatus,
        currentPhase: null,
        currentActivity: "failed",
        currentSummary:
          failedStatus === "cancelled" ? "本轮回答已取消" : "本轮回答未能完成",
        currentStatus: "failed",
      };
    }
    const failedStatus =
      event.finalStatus === "cancelled" ? "cancelled" : "failed";
    return {
      ...state,
      status: failedStatus,
      currentActivity: "failed",
      currentStatus: "failed",
      currentSummary:
        state.currentSummary ??
        (failedStatus === "cancelled" ? "本轮回答已取消" : "本轮回答未能完成"),
    };
  }

  // progress
  if (TERMINAL_STATUSES.has(state.status)) {
    warnIgnoredProgress("terminal_state");
    return state;
  }

  const payload = event.payload;
  if (
    payload.execution_version != null &&
    payload.execution_version !== AGENTIC_EXECUTION_VERSION
  ) {
    warnIgnoredProgress("execution_version");
    return state;
  }

  const sequence = asNonNegativeInt(payload.sequence);
  if (sequence == null || sequence <= state.lastSequence) {
    warnIgnoredProgress("sequence");
    return state;
  }

  const phase = asPhase(payload.phase);
  const summary = sanitizeSummary(payload.summary);
  if (phase == null || summary == null) {
    warnIgnoredProgress("invalid_phase_or_summary");
    return state;
  }

  const activity = asActivity(payload.activity) ?? "started";
  const toolName = asToolName(payload.tool_name);
  const stepStatus = asStepStatus(payload.status);
  const parsedOutcome = asOutcome(payload.outcome);
  // The DTO guard rejects unknown outcomes. Keep this reducer fail-closed as
  // well for direct callers and malformed scripted events: an explicit but
  // unknown outcome is a failure, never an implicit success.
  const incomingOutcome =
    parsedOutcome ?? (payload.outcome == null ? null : "failed");
  // Answering is driven by identity-valid message.delta events. Citation
  // checking is driven by the backend's typed started/completed/failed rows.
  if (phase === "composing_answer") {
    warnIgnoredProgress("composing_answer_not_public");
    return state;
  }
  const elapsedMs = asNonNegativeInt(payload.elapsed_ms) ?? state.elapsedMs;
  const durationMs = asNonNegativeInt(payload.duration_ms);
  const activityId = asActivityId(payload.activity_id);
  const isArticleEvidenceActivity =
    activityId === "article_evidence" ||
    (activityId == null &&
      (phase === "reading_context" || phase === "searching_article"));
  const existingActivityIndex =
    activityId != null
      ? state.steps.findIndex((existing) => existing.activityId === activityId)
      : phase === "analysis" || phase === "validating_evidence"
        ? state.steps.findIndex((existing) => existing.phase === phase)
        : -1;
  const existingActivity =
    existingActivityIndex < 0 ? null : state.steps[existingActivityIndex];
  const articleOutcomeObservations =
    isArticleEvidenceActivity && incomingOutcome != null
      ? [...state.articleOutcomeObservations, incomingOutcome]
      : state.articleOutcomeObservations;
  const outcome =
    activityId === "web_search"
      ? incomingOutcome ?? existingActivity?.outcome ?? null
      : isArticleEvidenceActivity
        ? aggregateArticleEvidenceOutcome(articleOutcomeObservations)
        : incomingOutcome ?? existingActivity?.outcome ?? null;
  const preserveConfirmedOutcome =
    existingActivity?.outcome != null &&
    outcome === existingActivity.outcome &&
    (incomingOutcome == null ||
      activityId === "web_search" ||
      isArticleEvidenceActivity);
  const reportedAttemptCount = asNonNegativeInt(payload.attempt_count);
  const reportedCallSequence = asPositiveInt(payload.call_sequence);
  // A started event intentionally has no confirmed provider count. Keep the
  // last one while the stable activity step is updated, and never let a late
  // event regress an already-confirmed count.
  const attemptCount =
    reportedAttemptCount == null
      ? (existingActivity?.attemptCount ?? null)
      : Math.max(existingActivity?.attemptCount ?? 0, reportedAttemptCount);
  const callSequence =
    reportedCallSequence == null
      ? (existingActivity?.callSequence ?? null)
      : Math.max(existingActivity?.callSequence ?? 0, reportedCallSequence);

  const effectiveActivity = preserveConfirmedOutcome
    ? existingActivity.activity
    : activity;
  const effectiveSummary = preserveConfirmedOutcome
    ? existingActivity.summary
    : summary;
  const effectiveToolName = preserveConfirmedOutcome
    ? existingActivity.toolName
    : toolName;
  const effectiveStatus = preserveConfirmedOutcome
    ? existingActivity.status
    : stepStatus;
  const effectiveDurationMs =
    durationMs ?? (preserveConfirmedOutcome ? existingActivity.durationMs : null);
  const step: AgenticActivityStep = {
    localOrdinal:
      existingActivity == null
        ? state.nextLocalOrdinal
        : existingActivity.localOrdinal,
    sequence,
    phase,
    activity: effectiveActivity,
    summary: effectiveSummary,
    elapsedMs,
    toolName: effectiveToolName,
    status: effectiveStatus,
    outcome,
    durationMs: effectiveDurationMs,
    activityId,
    attemptCount,
    callSequence,
  };

  const hasUnavailable =
    state.hasUnavailable ||
    effectiveActivity === "unavailable" ||
    effectiveStatus === "unavailable" ||
    outcome === "degraded";

  const nextStatus: AgenticActivityStatus =
    effectiveActivity === "failed" || effectiveStatus === "failed" || outcome === "failed"
      ? "running" // tool-level failure is not whole-turn failure; agent may continue
      : hasUnavailable
        ? "degraded"
        : "running";

  const steps =
    existingActivityIndex < 0
      ? [...state.steps, step]
      : state.steps.map((existing, index) =>
          index === existingActivityIndex ? step : existing,
        );

  return {
    ...state,
    status: nextStatus,
    lastSequence: sequence,
    currentPhase: phase,
    currentSummary: effectiveSummary,
    currentActivity: effectiveActivity,
    currentToolName: effectiveToolName,
    currentStatus: effectiveStatus,
    currentDurationMs: effectiveDurationMs,
    elapsedMs,
    hasUnavailable,
    steps,
    nextLocalOrdinal:
      existingActivity == null
        ? state.nextLocalOrdinal + 1
        : state.nextLocalOrdinal,
    articleOutcomeObservations,
  };
}

export function isAgenticActivityVisible(state: AgenticActivityState): boolean {
  return state.status === "running" || state.status === "degraded";
}

export function agenticActivityAriaLabel(state: AgenticActivityState): string {
  // Terminal copy must win over a stale running summary.
  if (state.status === "failed") {
    return "本轮回答未能完成";
  }
  if (state.status === "cancelled") {
    return "本轮回答已取消";
  }
  if (state.status === "completed") {
    return "本轮回答已完成";
  }
  if (state.currentSummary) {
    return state.currentSummary;
  }
  if (state.status === "degraded") {
    return "Ask Claread 正在继续处理";
  }
  if (state.status === "running") {
    return "Ask Claread 正在工作";
  }
  return "";
}
