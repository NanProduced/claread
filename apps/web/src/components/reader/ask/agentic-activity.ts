/**
 * Pure frontend projection for Agentic Ask activity/progress SSE.
 *
 * Privacy: only projects server-whitelisted phase/summary fields. Never stores
 * tool args, queries, locators, evidence handles, fingerprints, or raw errors.
 */

export const AGENTIC_EXECUTION_VERSION = "reader_record_ask_agentic_v1" as const;

export type AgenticActivityPhase =
  | "agent_running"
  | "reading_context"
  | "searching_article"
  | "composing_answer"
  | "validating_evidence";

export type AgenticActivityKind =
  | "started"
  | "completed"
  | "unavailable"
  | "failed";

export type AgenticActivityStatus =
  | "idle"
  | "running"
  | "degraded"
  | "completed"
  | "failed"
  | "cancelled";

export type AgenticActivityToolName =
  | "read_range"
  | "search_current_article";

export type AgenticActivityStep = {
  sequence: number;
  phase: AgenticActivityPhase;
  activity: AgenticActivityKind;
  summary: string;
  elapsedMs: number;
  toolName: AgenticActivityToolName | null;
  status: "running" | "ok" | "unavailable" | "failed" | null;
  durationMs: number | null;
};

export type AgenticActivityState = {
  status: AgenticActivityStatus;
  /** Last accepted monotonic sequence (0 when idle / reset). */
  lastSequence: number;
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
  duration_ms?: number | null;
};

export type AgenticActivityEvent =
  | { type: "reset" }
  | {
      type: "run_started";
      messageId?: string | null;
      turnRunId?: string | null;
    }
  | { type: "progress"; payload: AgenticActivityProgressInput }
  | { type: "completed" }
  | {
      type: "terminal";
      finalStatus?: "failed" | "cancelled" | "context_stale" | "invalid_citations" | string | null;
    };

const PHASES = new Set<AgenticActivityPhase>([
  "agent_running",
  "reading_context",
  "searching_article",
  "composing_answer",
  "validating_evidence",
]);

const ACTIVITIES = new Set<AgenticActivityKind>([
  "started",
  "completed",
  "unavailable",
  "failed",
]);

const TOOLS = new Set<AgenticActivityToolName>([
  "read_range",
  "search_current_article",
]);

const STEP_STATUSES = new Set<NonNullable<AgenticActivityStep["status"]>>([
  "running",
  "ok",
  "unavailable",
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

function asNonNegativeInt(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return null;
  }
  const n = Math.trunc(value);
  return n >= 0 ? n : null;
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
      currentPhase: "agent_running",
      currentActivity: "started",
      currentSummary: "正在分析当前文章",
      currentStatus: "running",
      messageId: event.messageId ?? null,
      turnRunId: event.turnRunId ?? null,
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
        currentPhase: "agent_running",
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
    return state;
  }

  const payload = event.payload;
  if (
    payload.execution_version != null &&
    payload.execution_version !== AGENTIC_EXECUTION_VERSION
  ) {
    return state;
  }

  const sequence = asNonNegativeInt(payload.sequence);
  if (sequence == null || sequence <= state.lastSequence) {
    return state;
  }

  const phase = asPhase(payload.phase);
  const summary = sanitizeSummary(payload.summary);
  if (phase == null || summary == null) {
    return state;
  }

  const activity = asActivity(payload.activity) ?? "started";
  const toolName = asToolName(payload.tool_name);
  const stepStatus = asStepStatus(payload.status);
  const elapsedMs = asNonNegativeInt(payload.elapsed_ms) ?? state.elapsedMs;
  const durationMs = asNonNegativeInt(payload.duration_ms);

  const step: AgenticActivityStep = {
    sequence,
    phase,
    activity,
    summary,
    elapsedMs,
    toolName,
    status: stepStatus,
    durationMs,
  };

  const hasUnavailable =
    state.hasUnavailable ||
    activity === "unavailable" ||
    stepStatus === "unavailable";

  const nextStatus: AgenticActivityStatus =
    activity === "failed" || stepStatus === "failed"
      ? "running" // tool-level failure is not whole-turn failure; agent may continue
      : hasUnavailable
        ? "degraded"
        : "running";

  return {
    ...state,
    status: nextStatus,
    lastSequence: sequence,
    currentPhase: phase,
    currentSummary: summary,
    currentActivity: activity,
    currentToolName: toolName,
    currentStatus: stepStatus,
    currentDurationMs: durationMs,
    elapsedMs,
    hasUnavailable,
    steps: [...state.steps, step],
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
