/**
 * ASK-COT — deterministic typed projection of the agentic process into
 * turn-scoped Chain-of-Thought view models.
 *
 * Input is ALWAYS the reducer-sanitized output (`AgenticActivityState` or
 * a frozen `AgenticProcessSnapshot`) plus the server-projected reasoning
 * fields — never raw wire payloads. The reducer already enforces the
 * privacy allowlist (phase/summary whitelists, ≤120-char summaries) and
 * the ordering rules (monotonic sequence, dup/out-of-order drops, web
 * upsert by activity_id, terminal freeze).
 *
 * Output view models are leak-proof by construction: they carry no
 * `sequence`, `toolName`, `activityId`, `turnRunId`, `messageId`,
 * `terminal_reason`, or server `summary` — only stable step ids, fixed
 * typed labels, statuses, durations, attempt counts, and hostname-only
 * web domains. Warning/error copy is never projected here; Prompt Kit
 * SystemMessage is the sole owner of those texts.
 *
 * Frozen step projection matrix (SSE contract, backend frozen at
 * 6d664864): the matrix keys on `(phase, tool_name)` triples, never on
 * summary copy. Known contract gaps handled here:
 * - G1 article tools have no correlation id ⇒ fold by step id, last-wins.
 * - G2 composing/validating emit started only ⇒ completion inferred from
 *   supersession (composing) and turn terminal (host stages only).
 * - G3 cold history carries no steps ⇒ snapshot-driven only; the cold
 *   render path is reasoning-only by construction (snapshot always null).
 * - G4 per-attempt web outcome is not on the wire ⇒ display turn_outcome
 *   status plus authoritative attempt/call counts only.
 *
 * Explicit-result tools (reading-context / searching-article / web-search)
 * become `complete` only on a matching completed/ok result. Supersession
 * and success terminal must NEVER invent a success checkmark for them.
 * Only host stages (agent-running / composing-answer / validating-evidence)
 * may be inferred complete via supersession or a success terminal.
 */

import type {
  AgenticActivityState,
  AgenticActivityStep,
} from "./agentic-activity";
import type { AgenticCitationDisplayItem } from "./agentic-evidence";
import type {
  AgenticProcessSnapshot,
  AgenticProcessSnapshotStep,
  ReaderAskMessageDto,
} from "@/types/api/reader-ask";

export type TurnProcessStepId =
  | "agent-running"
  | "reading-context"
  | "searching-article"
  | "web-search"
  | "composing-answer"
  | "validating-evidence";

export type TurnProcessStepStatus =
  | "active"
  | "complete"
  | "degraded"
  | "failed"
  | "interrupted";

/** Fixed typed step labels — never server summary / error / warning copy. */
export const TURN_PROCESS_STEP_LABELS: Record<TurnProcessStepId, string> = {
  "agent-running": "分析问题",
  "reading-context": "读取文章",
  "searching-article": "检索文章",
  "web-search": "网页查询",
  "composing-answer": "组织回答",
  "validating-evidence": "核对依据",
};

/**
 * Tool steps that require an explicit completed/ok (or degraded/failed)
 * result row. Without that row they stay interrupted / active — never
 * inferred complete from supersession or a success terminal.
 */
const EXPLICIT_RESULT_STEP_IDS = new Set<TurnProcessStepId>([
  "reading-context",
  "searching-article",
  "web-search",
]);

/**
 * Host stages whose completion may be inferred from supersession or a
 * success terminal (started-only on the wire).
 */
const INFERRED_RESULT_STEP_IDS = new Set<TurnProcessStepId>([
  "agent-running",
  "composing-answer",
  "validating-evidence",
]);

export type ProcessStepView = {
  id: TurnProcessStepId;
  /** Fixed typed label (see {@link TURN_PROCESS_STEP_LABELS}). */
  label: string;
  status: TurnProcessStepStatus;
  /** Duration of the status-winning result event; null while in flight. */
  durationMs: number | null;
  /** Pre-rendered attempt hint for the web step (e.g. 已尝试 2 次). */
  attempts: string | null;
  /** Hostname-only web domains; populated only post-completed. */
  domains: string[];
};

export type TurnProcessReasoningView = {
  /** Server-projected (redacted) reasoning text. Render verbatim. */
  text: string;
  truncated: boolean;
  streaming: boolean;
};

export type TurnProcessView = {
  /** false ⇒ render nothing. Never an empty shell or placeholder. */
  visible: boolean;
  header: {
    state: "running" | "settled";
    /** Settled copy selector: 思考过程 (reasoning present) / 处理过程. */
    titleHint: "thinking" | "processing";
    /** Running-only live step label; null when settled or before first event. */
    liveSummary: string | null;
    /** Settled-only whole seconds; null while running or when unknown. */
    durationS: number | null;
  };
  /** First-seen order; never pre-rendered pending steps. */
  steps: ProcessStepView[];
  reasoning: TurnProcessReasoningView | null;
  /** Trigger aria-label; terminal copy wins over stale running summaries. */
  ariaLabel: string;
};

export type TurnProcessProjectionInput = {
  /** Live reducer state (streaming turns). */
  activity?: AgenticActivityState | null;
  /** Frozen snapshot (settled turns in the same session). */
  snapshot?: AgenticProcessSnapshot | null;
  reasoningMd?: string | null;
  reasoningStatus?: ReaderAskMessageDto["reasoning_status"];
  reasoningTruncated?: boolean | null;
  /** Projected citations (AgenticWebSources' source of truth, reused). */
  citations?: readonly AgenticCitationDisplayItem[] | null;
  /** Message-level streaming flag (message.status === "streaming"). */
  isStreaming?: boolean;
};

type SourceStatus =
  | "idle"
  | "running"
  | "degraded"
  | "completed"
  | "failed"
  | "cancelled";

/** Minimal step row shared by live activity and frozen snapshot folds. */
type ProjectableStep = {
  phase: AgenticProcessSnapshotStep["phase"];
  activity: AgenticProcessSnapshotStep["activity"];
  status: AgenticProcessSnapshotStep["status"];
  durationMs: number | null;
  attemptCount: number | null;
  callSequence: number | null;
};

type SourceState = {
  status: SourceStatus;
  currentPhase: AgenticProcessSnapshotStep["phase"] | null;
  elapsedMs: number;
  hasUnavailable: boolean;
  steps: readonly ProjectableStep[];
};

type StepFold = {
  id: TurnProcessStepId;
  /** Wire status of the chronologically last row for this step. */
  rawStatus: "running" | "ok" | "unavailable" | "failed";
  durationMs: number | null;
  attemptCount: number | null;
  callSequence: number | null;
  /** A different step's rows arrived after this fold's last started row. */
  everSuperseded: boolean;
};

function stepIdOf(phase: AgenticProcessSnapshotStep["phase"]): TurnProcessStepId {
  switch (phase) {
    case "reading_context":
      return "reading-context";
    case "searching_article":
      return "searching-article";
    case "searching_web":
      return "web-search";
    case "composing_answer":
      return "composing-answer";
    case "validating_evidence":
      return "validating-evidence";
    case "agent_running":
    default:
      // Unknown/future phases fail soft into the generic agent step;
      // never invent a new public step id.
      return "agent-running";
  }
}

function sourceFromActivity(activity: AgenticActivityState): SourceState {
  return {
    status: activity.status,
    currentPhase: activity.currentPhase,
    elapsedMs: activity.elapsedMs,
    hasUnavailable: activity.hasUnavailable,
    steps: activity.steps.map(
      (step: AgenticActivityStep): ProjectableStep => ({
        phase: step.phase,
        activity: step.activity,
        status: step.status,
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
    elapsedMs: snapshot.elapsedMs,
    hasUnavailable: snapshot.hasUnavailable,
    steps: snapshot.steps,
  };
}

const MAX_DOMAIN_CHIPS = 8;

/**
 * Extract display hostnames from web citations. Hostname-only — never
 * full URLs (query strings could carry provider parameters). Dedupes
 * preserving first-seen order; caps with a `+N` overflow chip.
 */
export function extractWebDomains(
  citations: readonly AgenticCitationDisplayItem[] | null | undefined,
): string[] {
  if (!citations || citations.length === 0) {
    return [];
  }
  const seen = new Set<string>();
  const domains: string[] = [];
  for (const citation of citations) {
    if (citation.sourceKind !== "web") {
      continue;
    }
    if (typeof citation.url !== "string" || citation.url.length === 0) {
      continue;
    }
    let hostname: string;
    try {
      hostname = new URL(citation.url).hostname.toLowerCase();
    } catch {
      continue;
    }
    if (!hostname) {
      continue;
    }
    const display = hostname.replace(/^www\./, "");
    if (!display || seen.has(display)) {
      continue;
    }
    seen.add(display);
    domains.push(display);
  }
  if (domains.length <= MAX_DOMAIN_CHIPS) {
    return domains;
  }
  const overflow = domains.length - MAX_DOMAIN_CHIPS;
  return [...domains.slice(0, MAX_DOMAIN_CHIPS), `+${overflow}`];
}

function webAttemptHint(fold: StepFold): string | null {
  if (fold.id !== "web-search") {
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

/**
 * Resolve display status for one folded step.
 *
 * Explicit-result tools require a matching result row (ok / unavailable /
 * failed). Host stages may be inferred complete when superseded or when
 * the turn reaches a success terminal.
 */
function resolveStepStatus(
  fold: StepFold,
  liveTurn: boolean,
  turnCompleted: boolean,
  currentStepId: TurnProcessStepId | null,
): TurnProcessStepStatus {
  if (fold.rawStatus === "ok") {
    return "complete";
  }
  if (fold.rawStatus === "unavailable") {
    return "degraded";
  }
  if (fold.rawStatus === "failed") {
    return "failed";
  }

  const canInferComplete = INFERRED_RESULT_STEP_IDS.has(fold.id);
  const requiresExplicitResult = EXPLICIT_RESULT_STEP_IDS.has(fold.id);

  if (liveTurn) {
    if (fold.id === currentStepId) {
      return "active";
    }
    if (fold.everSuperseded) {
      // Agent moved on without a result for this step.
      return canInferComplete ? "complete" : "interrupted";
    }
    return "active";
  }

  if (turnCompleted) {
    // Success terminal: only host stages may be inferred complete.
    if (canInferComplete) {
      return "complete";
    }
    if (requiresExplicitResult) {
      return "interrupted";
    }
    return "interrupted";
  }

  // failed / cancelled / stale EOF: unfinished steps are interrupted.
  return "interrupted";
}

/**
 * Fold reducer steps into stable typed step views.
 *
 * Rules (frozen matrix):
 * - steps key by step id, ordered by first-seen appearance;
 * - status/duration: chronological last-wins (a tool may fail then
 *   retry successfully within one turn — failed-priority would lie);
 * - labels are fixed typed copy, never server summary;
 * - explicit-result tools without a result stay interrupted when the
 *   agent moves on or the turn ends successfully;
 * - host stages may close via supersession / success terminal;
 * - at a non-ok terminal, still-running steps become `interrupted`,
 *   NEVER `complete` — a checkmark on an unfinished step is a lie;
 * - `unavailable` renders `degraded` with NO warning copy (the turn
 *   SystemMessage is the sole warning owner).
 */
function projectSteps(
  source: SourceState | null,
  citations: readonly AgenticCitationDisplayItem[] | null | undefined,
  liveTurn: boolean,
): ProcessStepView[] {
  if (!source || source.steps.length === 0) {
    return [];
  }
  const folds: StepFold[] = [];
  const indexById = new Map<TurnProcessStepId, number>();
  let openFoldId: TurnProcessStepId | null = null;

  for (const step of source.steps) {
    const id = stepIdOf(step.phase);
    const isStartedRow = step.activity === "started";
    let foldIndex = indexById.get(id);
    if (foldIndex === undefined) {
      foldIndex = folds.length;
      indexById.set(id, foldIndex);
      folds.push({
        id,
        rawStatus: "running",
        durationMs: null,
        attemptCount: null,
        callSequence: null,
        everSuperseded: false,
      });
    }
    const fold = folds[foldIndex];
    // A different step appearing while this one is open supersedes it.
    if (openFoldId !== null && openFoldId !== id) {
      const openFold = folds[indexById.get(openFoldId) ?? -1];
      if (openFold && openFold.rawStatus === "running") {
        openFold.everSuperseded = true;
      }
    }
    // Last-wins status; duration tracks the winning row
    // (started rows carry null, hiding stale durations during re-calls).
    fold.rawStatus =
      step.status === "ok" ||
      step.status === "unavailable" ||
      step.status === "failed"
        ? step.status
        : "running";
    fold.durationMs = step.durationMs;
    if (step.attemptCount != null) {
      fold.attemptCount = step.attemptCount;
    }
    if (step.callSequence != null) {
      fold.callSequence = step.callSequence;
    }
    openFoldId = isStartedRow ? id : null;
  }

  const turnCompleted = source.status === "completed";
  const currentStepId =
    liveTurn && source.currentPhase != null ? stepIdOf(source.currentPhase) : null;
  const domains = turnCompleted ? extractWebDomains(citations) : [];

  return folds.map((fold) => {
    const status = resolveStepStatus(fold, liveTurn, turnCompleted, currentStepId);
    return {
      id: fold.id,
      label: TURN_PROCESS_STEP_LABELS[fold.id],
      status,
      durationMs: fold.durationMs,
      attempts: webAttemptHint(fold),
      domains: fold.id === "web-search" && status === "complete" ? domains : [],
    };
  });
}

function projectReasoning(
  input: TurnProcessProjectionInput,
): TurnProcessReasoningView | null {
  const text = input.reasoningMd ?? "";
  const streaming = input.reasoningStatus === "streaming";
  if (!streaming && text.trim().length === 0) {
    return null;
  }
  return {
    text,
    truncated: input.reasoningTruncated === true,
    streaming,
  };
}

function projectAriaLabel(
  source: SourceState | null,
  liveTurn: boolean,
): string {
  if (!source) {
    return liveTurn ? "Ask Claread 正在工作" : "";
  }
  if (source.status === "failed") {
    return "本轮回答未能完成";
  }
  if (source.status === "cancelled") {
    return "本轮回答已取消";
  }
  if (source.status === "completed") {
    return "本轮回答已完成";
  }
  if (liveTurn) {
    // Neutral fixed label only — never server error / warning summary.
    if (source.currentPhase != null) {
      return TURN_PROCESS_STEP_LABELS[stepIdOf(source.currentPhase)];
    }
    return "Ask Claread 正在工作";
  }
  // A frozen snapshot still marked running/degraded means the stream
  // ended without a business terminal (stale EOF reconcile path). The
  // turn never completed — say so; never reuse a stale running summary.
  return "本轮回答未能完成";
}

/**
 * Project the full turn process view. Prefers live activity, falls back
 * to the frozen snapshot, then to reasoning-only (cold history).
 */
export function projectTurnProcess(
  input: TurnProcessProjectionInput,
): TurnProcessView {
  const isStreaming = input.isStreaming === true;
  const source = input.activity
    ? sourceFromActivity(input.activity)
    : input.snapshot
      ? sourceFromSnapshot(input.snapshot)
      : null;
  // A snapshot is frozen by definition — it never drives the live state.
  // (A snapshot still marked running/degraded means the stream ended
  // without a business terminal; it settles as interrupted, never as a
  // permanent shimmer.)
  const sourceIsLive = input.activity != null;
  const liveTurn =
    isStreaming ||
    (sourceIsLive &&
      (source?.status === "running" || source?.status === "degraded"));
  const settled = !liveTurn;

  const steps = projectSteps(source, input.citations, liveTurn);
  const reasoning = projectReasoning(input);

  const elapsedMs = source?.elapsedMs ?? 0;
  const durationS =
    settled && elapsedMs > 0 ? Math.max(1, Math.round(elapsedMs / 1000)) : null;
  // Live header uses the fixed label of the current phase — never server
  // summary (which may carry unavailable / failed warning copy).
  const liveSummary =
    liveTurn && source?.currentPhase != null
      ? TURN_PROCESS_STEP_LABELS[stepIdOf(source.currentPhase)]
      : null;
  const hasReasoning = reasoning !== null && reasoning.text.trim().length > 0;

  const visible = isStreaming || steps.length > 0 || reasoning !== null;

  return {
    visible,
    header: {
      state: liveTurn ? "running" : "settled",
      titleHint: hasReasoning ? "thinking" : "processing",
      liveSummary,
      durationS,
    },
    steps,
    reasoning,
    ariaLabel: projectAriaLabel(source, liveTurn),
  };
}

/**
 * Build the in-memory UI snapshot written on the message at settle time.
 *
 * Contract:
 * - UI memory only — never serialized to the server, DTO wire, or DOM.
 * - Returns null when the turn never bound a run identity (`turnRunId`
 *   absent) — legacy lanes and pre-run failures never produce a snapshot.
 * - Steps omit server `summary` (labels come from fixed typed mapping at
 *   project time). Control fields (`sequence`, `toolName`, `activityId`,
 *   attempt/call counts) may be retained for deterministic reprojection
 *   and are internal — not public DOM/view fields.
 * - Cold history always has snapshot=null (normalizeReaderAskMessages).
 */
export function buildAgenticProcessSnapshot(
  activity: AgenticActivityState,
): AgenticProcessSnapshot | null {
  if (activity.turnRunId == null) {
    return null;
  }
  if (
    activity.status !== "completed" &&
    activity.status !== "failed" &&
    activity.status !== "cancelled" &&
    activity.status !== "running" &&
    activity.status !== "degraded"
  ) {
    return null;
  }
  const steps: AgenticProcessSnapshotStep[] = activity.steps.map(
    (step: AgenticActivityStep) => ({
      sequence: step.sequence,
      phase: step.phase,
      activity: step.activity,
      elapsedMs: step.elapsedMs,
      toolName: step.toolName,
      status: step.status,
      durationMs: step.durationMs,
      activityId: step.activityId,
      attemptCount: step.attemptCount,
      callSequence: step.callSequence,
    }),
  );
  return {
    execution_version: "reader_record_ask_agentic_v2",
    status: activity.status,
    elapsedMs: activity.elapsedMs,
    hasUnavailable: activity.hasUnavailable,
    steps,
  };
}
