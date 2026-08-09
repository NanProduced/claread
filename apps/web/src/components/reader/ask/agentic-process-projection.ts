/**
 * Privacy-safe projection for the agentic v2 Answer Process disclosure.
 *
 * The reducer is the only wire boundary. This module consumes reducer state or
 * an in-memory snapshot and emits fixed learner-facing labels plus bounded
 * lifecycle metadata. It never forwards server summaries, tool names, ids,
 * queries, URLs, evidence handles, provider payloads, or reasoning text.
 */

import type {
  AgenticActivityOutcome,
  AgenticActivityState,
  AgenticActivityStep,
} from "./agentic-activity";
import type { AgenticCitationDisplayItem } from "./agentic-evidence";
import type {
  AgenticProcessSnapshot,
  AgenticProcessSnapshotStep,
  ReaderAskContextCompactionUiStateDto,
  ReaderAskWebSearchSummaryDto,
} from "@/types/api/reader-ask";

export type TurnProcessStepId =
  | "analysis"
  | "article-evidence"
  | "web-evidence"
  | "answering"
  | "citation-check";

export type TurnProcessStepStatus =
  | "active"
  | "complete"
  | "degraded"
  | "failed"
  | "interrupted";

export type TurnProcessStepLifecycle = "active" | "settled";

export type TurnProcessStepOutcome =
  | "success"
  | "empty"
  | "degraded"
  | "failed"
  | "interrupted";

export const TURN_PROCESS_STEP_LABELS: Record<TurnProcessStepId, string> = {
  analysis: "分析问题",
  "article-evidence": "查找文章依据",
  "web-evidence": "查询网页",
  answering: "生成回答",
  "citation-check": "检查引用",
};

const PUBLIC_STEP_IDS = new Set<TurnProcessStepId>([
  "analysis",
  "article-evidence",
  "web-evidence",
  "answering",
  "citation-check",
]);

export type ProcessStepView = {
  id: TurnProcessStepId;
  label: string;
  status: TurnProcessStepStatus;
  lifecycle: TurnProcessStepLifecycle;
  outcome: TurnProcessStepOutcome | null;
  /** Only dynamic metadata is allowed here: no-results or degraded. */
  detail: "no_results" | "degraded" | null;
  durationMs: number | null;
  attempts: string | null;
  domains: string[];
};

export type TurnProcessView = {
  visible: boolean;
  header: {
    state: "running" | "settled";
    liveSummary: string | null;
    settledCopy: string | null;
    durationS: number | null;
  };
  steps: ProcessStepView[];
  ariaLabel: string;
  webSourceCount: number;
};

export type TurnProcessProjectionInput = {
  activity?: AgenticActivityState | null;
  snapshot?: AgenticProcessSnapshot | null;
  citations?: readonly AgenticCitationDisplayItem[] | null;
  isStreaming?: boolean;
  webSearchSummary?: ReaderAskWebSearchSummaryDto | null;
  /** Legacy compatibility input; v2 deliberately ignores these fields. */
  reasoningMd?: string | null;
  reasoningStatus?: "idle" | "streaming" | "completed" | "interrupted" | null;
  reasoningTruncated?: boolean | null;
  /** Same-session status only; it is not a process step. */
  contextCompaction?: ReaderAskContextCompactionUiStateDto | null;
};

type SourceStatus = AgenticActivityState["status"];

type ProjectableStep = {
  localOrdinal: number;
  phase: AgenticProcessSnapshotStep["phase"];
  activity: AgenticProcessSnapshotStep["activity"];
  status: AgenticProcessSnapshotStep["status"];
  outcome: AgenticActivityOutcome | null;
  durationMs: number | null;
  attemptCount: number | null;
  callSequence: number | null;
};

type SourceState = {
  status: SourceStatus;
  currentPhase: AgenticProcessSnapshotStep["phase"] | null;
  currentActivity: AgenticProcessSnapshotStep["activity"] | null;
  currentStatus: AgenticProcessSnapshotStep["status"];
  elapsedMs: number;
  hasUnavailable: boolean;
  steps: readonly ProjectableStep[];
};

type StepFold = {
  id: TurnProcessStepId;
  rawStatus: "running" | "ok" | "unavailable" | "failed";
  outcome: AgenticActivityOutcome | null;
  durationMs: number | null;
  attemptCount: number | null;
  callSequence: number | null;
  everSuperseded: boolean;
};

function stepIdOf(
  phase: AgenticProcessSnapshotStep["phase"],
): TurnProcessStepId | null {
  switch (phase) {
    case "analysis":
      return "analysis";
    case "reading_context":
    case "searching_article":
      return "article-evidence";
    case "searching_web":
      return "web-evidence";
    case "composing_answer":
      return "answering";
    case "validating_evidence":
      return "citation-check";
    default:
      // agent_running and future phases are not learner-facing steps.
      return null;
  }
}

function sourceFromActivity(activity: AgenticActivityState): SourceState {
  return {
    status: activity.status,
    currentPhase: activity.currentPhase,
    currentActivity: activity.currentActivity,
    currentStatus: activity.currentStatus,
    elapsedMs: activity.elapsedMs,
    hasUnavailable: activity.hasUnavailable,
    steps: activity.steps.map(
      (step: AgenticActivityStep): ProjectableStep => ({
        localOrdinal: step.localOrdinal,
        phase: step.phase,
        activity: step.activity,
        status: step.status,
        outcome: step.outcome,
        durationMs: step.durationMs,
        attemptCount: step.attemptCount,
        callSequence: step.callSequence,
      }),
    ),
  };
}

function sourceFromSnapshot(snapshot: AgenticProcessSnapshot): SourceState {
  return {
    status: snapshot.status,
    currentPhase: null,
    currentActivity: null,
    currentStatus: null,
    elapsedMs: snapshot.elapsedMs,
    hasUnavailable: snapshot.hasUnavailable,
    steps: snapshot.steps,
  };
}

const MAX_DOMAIN_CHIPS = 8;

/** Return hostname-only chips from final public web citations. */
export function extractWebDomains(
  citations: readonly AgenticCitationDisplayItem[] | null | undefined,
): string[] {
  if (!citations || citations.length === 0) {
    return [];
  }
  const seen = new Set<string>();
  const domains: string[] = [];
  for (const citation of citations) {
    if (citation.sourceKind !== "web" || typeof citation.url !== "string") {
      continue;
    }
    try {
      const hostname = new URL(citation.url).hostname
        .toLowerCase()
        .replace(/^www\./, "");
      if (!hostname || seen.has(hostname)) {
        continue;
      }
      seen.add(hostname);
      domains.push(hostname);
    } catch {
      // Malformed URLs never become a UI chip.
    }
  }
  return domains.length <= MAX_DOMAIN_CHIPS
    ? domains
    : [...domains.slice(0, MAX_DOMAIN_CHIPS), `+${domains.length - MAX_DOMAIN_CHIPS}`];
}

function countWebCitations(
  citations: readonly AgenticCitationDisplayItem[] | null | undefined,
): number {
  return extractWebDomains(citations).filter((domain) => !domain.startsWith("+")).length;
}

function webAttemptHint(fold: StepFold): string | null {
  if (fold.id !== "web-evidence") {
    return null;
  }
  if (fold.attemptCount != null && fold.attemptCount > 1) {
    return `已尝试 ${fold.attemptCount} 次`;
  }
  if (fold.callSequence != null && fold.callSequence > 1) {
    return `已调用 ${fold.callSequence} 次`;
  }
  return null;
}

function outcomeFromWebSummary(
  summary: ReaderAskWebSearchSummaryDto | null | undefined,
): TurnProcessStepOutcome | null {
  switch (summary?.outcome) {
    case "completed":
      return "success";
    case "no_results":
      return "empty";
    case "unavailable":
    case "timeout":
      return "degraded";
    case "failed":
      return "failed";
    default:
      return null;
  }
}

function statusFromOutcome(
  outcome: TurnProcessStepOutcome,
): TurnProcessStepStatus {
  switch (outcome) {
    case "success":
    case "empty":
      return "complete";
    case "degraded":
      return "degraded";
    case "failed":
      return "failed";
    case "interrupted":
      return "interrupted";
  }
}

function resolveFold(
  fold: StepFold,
  source: SourceState,
  liveTurn: boolean,
  currentStepId: TurnProcessStepId | null,
  webSummary: ReaderAskWebSearchSummaryDto | null | undefined,
): { status: TurnProcessStepStatus; outcome: TurnProcessStepOutcome | null } {
  const summarizedWebOutcome =
    fold.id === "web-evidence" ? outcomeFromWebSummary(webSummary) : null;
  if (summarizedWebOutcome && source.status === "completed") {
    return {
      status: statusFromOutcome(summarizedWebOutcome),
      outcome: summarizedWebOutcome,
    };
  }
  if (fold.outcome != null) {
    if (fold.outcome === "failed" && fold.id === "answering" && source.status === "cancelled") {
      return { status: "interrupted", outcome: "interrupted" };
    }
    return {
      status: statusFromOutcome(fold.outcome),
      outcome: fold.outcome,
    };
  }
  if (fold.rawStatus === "ok") {
    const outcome = summarizedWebOutcome ?? "success";
    return { status: statusFromOutcome(outcome), outcome };
  }
  if (fold.rawStatus === "unavailable") {
    return { status: "degraded", outcome: "degraded" };
  }
  if (fold.rawStatus === "failed") {
    // A cancelled turn interrupts its answer rather than displaying an error.
    if (fold.id === "answering" && source.status === "cancelled") {
      return { status: "interrupted", outcome: "interrupted" };
    }
    return { status: "failed", outcome: "failed" };
  }

  if (liveTurn && fold.id === currentStepId) {
    return { status: "active", outcome: null };
  }
  return { status: "interrupted", outcome: "interrupted" };
}

/**
 * Fold reducer rows in first-seen order. Every public step needs its own
 * result row; supersession and a successful terminal never invent a checkmark.
 */
function projectSteps(
  source: SourceState | null,
  citations: readonly AgenticCitationDisplayItem[] | null | undefined,
  liveTurn: boolean,
  webSummary: ReaderAskWebSearchSummaryDto | null | undefined,
): ProcessStepView[] {
  if (!source || source.steps.length === 0) {
    return [];
  }

  const folds: StepFold[] = [];
  const indexById = new Map<TurnProcessStepId, number>();
  let openFoldId: TurnProcessStepId | null = null;

  // Reducer updates can replace a synthetic answering row after preview_reset.
  // Use the local UI ordinal for display order so that replacement never
  // borrows or mutates the server progress sequence watermark.
  const orderedSteps = [...source.steps].sort(
    (left, right) => left.localOrdinal - right.localOrdinal,
  );
  for (const step of orderedSteps) {
    const id = stepIdOf(step.phase);
    if (id == null || !PUBLIC_STEP_IDS.has(id)) {
      continue;
    }
    let index = indexById.get(id);
    if (index === undefined) {
      index = folds.length;
      indexById.set(id, index);
      folds.push({
        id,
        rawStatus: "running",
        outcome: null,
        durationMs: null,
        attemptCount: null,
        callSequence: null,
        everSuperseded: false,
      });
    }
    if (openFoldId != null && openFoldId !== id) {
      const openFold = folds[indexById.get(openFoldId) ?? -1];
      if (openFold?.rawStatus === "running") {
        openFold.everSuperseded = true;
      }
    }

    const fold = folds[index];
    fold.rawStatus =
      step.status === "ok" ||
      step.status === "unavailable" ||
      step.status === "failed"
        ? step.status
        : "running";
    if (step.outcome != null) {
      fold.outcome = step.outcome;
    }
    fold.durationMs = step.durationMs;
    if (step.attemptCount != null) {
      fold.attemptCount = step.attemptCount;
    }
    if (step.callSequence != null) {
      fold.callSequence = step.callSequence;
    }
    openFoldId = fold.rawStatus === "running" ? id : null;
  }

  const currentStepId =
    liveTurn && source.currentPhase != null
      ? stepIdOf(source.currentPhase)
      : null;
  const domains =
    source.status === "completed" ? extractWebDomains(citations) : [];
  return folds.flatMap((fold) => {
    const resolved = resolveFold(
      fold,
      source,
      liveTurn,
      currentStepId,
      webSummary,
    );
    const detail =
      resolved.outcome === "empty"
        ? "no_results"
        : resolved.outcome === "degraded"
          ? "degraded"
          : null;
    return [
      {
        id: fold.id,
        label: TURN_PROCESS_STEP_LABELS[fold.id],
        status: resolved.status,
        lifecycle: resolved.status === "active" ? "active" : "settled",
        outcome: resolved.outcome,
        detail,
        durationMs: fold.durationMs,
        attempts: webAttemptHint(fold),
        domains:
          fold.id === "web-evidence" && resolved.outcome === "success"
            ? domains
            : [],
      },
    ];
  });
}

function resolveRunningLiveSummary(
  source: SourceState | null,
  contextCompaction: ReaderAskContextCompactionUiStateDto | null | undefined,
  steps: ProcessStepView[],
): string | null {
  if (
    contextCompaction?.status === "running" &&
    contextCompaction.elapsedMs >= 300
  ) {
    return "正在整理较早对话";
  }
  if (source?.currentStatus === "running" && source.currentPhase != null) {
    switch (stepIdOf(source.currentPhase)) {
      case "analysis":
        return "正在分析问题";
      case "article-evidence":
        return "正在查找文章依据";
      case "web-evidence":
        return "正在查询网页";
      case "answering":
        return "正在生成回答";
    }
  }
  const active = [...steps].reverse().find((step) => step.status === "active");
  if (active) {
    return `正在${active.label}`;
  }
  return "Ask Claread 正在工作";
}

function settledCopy(source: SourceState | null): string | null {
  switch (source?.status) {
    case "completed":
      return "已完成";
    case "failed":
      return "未完成";
    case "cancelled":
      return "已取消";
    case "running":
    case "degraded":
      return "未完成";
    default:
      return null;
  }
}

function projectAriaLabel(
  source: SourceState | null,
  liveTurn: boolean,
  liveSummary: string | null,
): string {
  if (source?.status === "failed") {
    return "回答过程：本轮回答未能完成";
  }
  if (source?.status === "cancelled") {
    return "回答过程：本轮回答已取消";
  }
  if (source?.status === "completed") {
    return "回答过程：本轮回答已完成";
  }
  if (liveTurn) {
    return `回答过程：${liveSummary ?? "Ask Claread 正在工作"}`;
  }
  return source ? "回答过程：本轮回答未能完成" : "";
}

export function projectTurnProcess(
  input: TurnProcessProjectionInput,
): TurnProcessView {
  const source = input.activity
    ? sourceFromActivity(input.activity)
    : input.snapshot
      ? sourceFromSnapshot(input.snapshot)
      : null;
  const sourceIsLive = input.activity != null;
  const liveTurn =
    input.isStreaming === true ||
    (sourceIsLive &&
      (source?.status === "running" || source?.status === "degraded"));
  const settled = !liveTurn;
  const steps = projectSteps(
    source,
    input.citations,
    liveTurn,
    input.webSearchSummary,
  );
  const visible =
    liveTurn || (settled && source != null && source.status !== "idle");
  const finalSteps = visible ? steps : [];
  const liveSummary = liveTurn
    ? resolveRunningLiveSummary(
        source,
        input.contextCompaction,
        finalSteps,
      )
    : null;
  const elapsedMs = source?.elapsedMs ?? 0;
  const durationS =
    settled && elapsedMs > 0 ? Math.max(1, Math.round(elapsedMs / 1000)) : null;

  return {
    visible,
    header: {
      state: liveTurn ? "running" : "settled",
      liveSummary,
      settledCopy: settled ? settledCopy(source) : null,
      durationS,
    },
    steps: finalSteps,
    ariaLabel: projectAriaLabel(source, liveTurn, liveSummary),
    webSourceCount: countWebCitations(input.citations),
  };
}

/** Build the same-session UI-only snapshot before the reducer is reset. */
export function buildAgenticProcessSnapshot(
  activity: AgenticActivityState,
): AgenticProcessSnapshot | null {
  if (activity.turnRunId == null || activity.status === "idle") {
    return null;
  }
  const steps: AgenticProcessSnapshotStep[] = activity.steps.map((step) => ({
    localOrdinal: step.localOrdinal,
    sequence: step.sequence,
    phase: step.phase,
    activity: step.activity,
    elapsedMs: step.elapsedMs,
    toolName: step.toolName,
    status: step.status,
    outcome: step.outcome,
    durationMs: step.durationMs,
    activityId: step.activityId,
    attemptCount: step.attemptCount,
    callSequence: step.callSequence,
  }));
  return {
    execution_version: "reader_record_ask_agentic_v2",
    status: activity.status,
    elapsedMs: activity.elapsedMs,
    hasUnavailable: activity.hasUnavailable,
    steps,
  };
}
