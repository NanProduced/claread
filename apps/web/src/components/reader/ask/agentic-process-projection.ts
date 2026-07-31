/**
 * ASK-UI-NOTION-R0 — LearnerProcessView projection.
 *
 * Deterministic typed projection of the agentic process into a strict
 * turn-scoped Learner Process view model. This is NOT a Chain-of-Thought
 * surface: model reasoning, server summaries, queries, URLs, tool names,
 * provider/model identity, evh handles, run/message ids, terminal_reason
 * and exception text are absent by construction — never rendered, never
 * hidden via CSS, never carried in aria-labels or hidden DOM nodes.
 *
 * Input is ALWAYS the reducer-sanitized output (`AgenticActivityState` or
 * a frozen `AgenticProcessSnapshot`) — never raw wire payloads. The
 * reducer already enforces the privacy allowlist (phase/summary
 * whitelists, ≤120-char summaries) and the ordering rules (monotonic
 * sequence, dup/out-of-order drops, web upsert by activity_id, terminal
 * freeze).
 *
 * Output view models are leak-proof by construction: they carry only
 * stable step ids, fixed typed Chinese labels, statuses, durations,
 * legitimate attempt counts, and hostname-only web domains (sourced
 * exclusively from final effective citations). Warning/error copy is
 * never projected here; Prompt Kit SystemMessage is the sole owner of
 * those texts.
 *
 * R3 user-visible stage policy (supersedes the R0 settled-hide rule):
 * - 理解问题 (understanding-question): host-provable lifecycle step.
 *   Shown for every turn that accepted agentic.run_started (any non-idle
 *   activity, or a frozen snapshot). Never a wire step — no phase maps to
 *   it — and carries no wire duration.
 * - 阅读本文 (reading-context): only when the article/read tool really ran.
 * - 网页查询 (web-search): only when web search really ran.
 * - 整理回答 (composing-answer): visible BOTH while streaming and after
 *   settle. R0 hid it after settle, which left web/article turns without
 *   their final learner step — R3 keeps it: complete after a success
 *   terminal, interrupted on failure / cancellation / stale EOF. When the
 *   wire carries no composing row (pure-answer turns), a host-provable
 *   step is injected instead — never from a model summary guess.
 * - Internal stages (agent-running / searching-article /
 *   validating-evidence) are tracked for fold/supersession logic but
 *   NEVER appear in the visible steps list, header copy, or aria-label.
 *   Tool / provider names, retrieval and evidence-validation internals
 *   are never surfaced.
 *
 * Wire steps vs host-provable lifecycle steps: the visible step list is
 * the union of (a) wire steps that really happened (folded reducer rows)
 * and (b) host-provable lifecycle steps whose evidence is the turn
 * lifecycle itself (run accepted ⇒ 理解问题; answer exists/settled ⇒
 * 整理回答). Model summaries never decide which steps appear. Steps are
 * presented in a canonical learner order (理解问题 → 阅读本文 → 网页查询
 * → 整理回答), not wire arrival order. Each step shows only a trustworthy
 * wire duration; host steps carry none (never fabricated).
 *
 * Settled visibility:
 * - EVERY successful answer preserves a learner-facing process summary:
 *   pure-answer → "已整理回答", article → "已根据当前文章整理", web →
 *   "已查询网页 · N 个来源". Failed/cancelled turns still render the
 *   collapsed panel with interrupted step status; the SystemMessage owns
 *   the failure copy.
 * - Cold history (no activity, no snapshot) renders steps NEVER — a
 *   reload cannot reconstruct the run — but still renders the safe
 *   reasoning section when the persisted reasoning projection exists.
 *
 * Safe reasoning (R3 restore): the view carries the server-side safe
 * reasoning projection ONLY (reasoning_md / reasoning_status /
 * reasoning_truncated — the reasoning_projection_v1 redacted text). Raw
 * provider chain-of-thought, system prompts, tool args, queries, URLs,
 * evh handles, run/message ids and exception text never reach this view.
 *
 * Frozen step projection matrix (SSE contract, backend frozen at
 * 6d664864): the matrix keys on `(phase, tool_name)` triples, never on
 * summary copy. Known contract gaps handled here:
 * - G1 article tools have no correlation id ⇒ fold by step id, last-wins.
 * - G2 composing/validating emit started only ⇒ completion inferred from
 *   supersession (composing) and turn terminal (host stages only).
 * - G3 cold history carries no steps ⇒ snapshot-driven only; the cold
 *   render path never fabricates steps (snapshot is always null after a
 *   reload). R3: a cold turn may still render the safe reasoning section
 *   when the persisted reasoning projection exists — reasoning-only, no
 *   invented process history.
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
  ReaderAskContextCompactionUiStateDto,
  ReaderAskMessageDto,
} from "@/types/api/reader-ask";

export type TurnProcessStepId =
  | "context-compaction"
  | "understanding-question"
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

/**
 * Fixed typed step labels — never server summary / error / warning copy.
 * Internal-stage labels are kept for fold/supersession bookkeeping only
 * and are NEVER rendered to users.
 */
export const TURN_PROCESS_STEP_LABELS: Record<TurnProcessStepId, string> = {
  "context-compaction": "压缩上下文",
  "understanding-question": "理解问题",
  "agent-running": "分析问题",
  "reading-context": "阅读本文",
  "searching-article": "检索文章",
  "web-search": "网页查询",
  "composing-answer": "整理回答",
  "validating-evidence": "核对依据",
};

/**
 * R3 — user-visible wire step ids, identical for streaming and settled.
 * R0's settled filter dropped `composing-answer` after settle, which hid
 * the final learner step of real tool turns on completion; R3 keeps it
 * (status resolves to complete / interrupted from the turn outcome).
 * Internal stages (agent-running, searching-article, validating-evidence)
 * are tracked for fold logic but filtered out of `TurnProcessView.steps`.
 */
const VISIBLE_STEP_IDS = new Set<TurnProcessStepId>([
  "reading-context",
  "web-search",
  "composing-answer",
]);

/**
 * R3 — canonical learner presentation order. Wire steps may arrive in
 * any order; learners always see 理解问题 → 阅读本文 → 网页查询 →
 * 整理回答. Internal ids carry ranks too (never rendered).
 */
const CANONICAL_STEP_ORDER: Record<TurnProcessStepId, number> = {
  "context-compaction": 0,
  "understanding-question": 1,
  "reading-context": 2,
  "searching-article": 3,
  "web-search": 4,
  "validating-evidence": 5,
  "agent-running": 6,
  "composing-answer": 7,
};

/** Internal step ids — never appear in visible steps, header, or aria. */
const INTERNAL_STEP_IDS = new Set<TurnProcessStepId>([
  "agent-running",
  "searching-article",
  "validating-evidence",
]);

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

/**
 * R3 — safe reasoning projection view. Built ONLY from the server-side
 * safe fields (reasoning_md / reasoning_status / reasoning_truncated —
 * the reasoning_projection_v1 redacted text). Never raw provider CoT.
 */
export type TurnProcessReasoningView = {
  /** Server-projected (redacted) reasoning text. Render verbatim. */
  text: string;
  truncated: boolean;
  streaming: boolean;
};

/**
 * R0.2 / R3 — strict LearnerProcessView. Carries ONLY:
 * - fixed stage id / fixed Chinese label / status / limited duration /
 *   legitimate attempt count / hostname chips sourced from final
 *   effective citations;
 * - the server safe reasoning projection (R3), rendered as 思考要点.
 *
 * It deliberately does NOT carry: raw provider reasoning, server summary,
 * query, URL, tool name, provider, model, evh, run/message id,
 * terminal_reason, exception text, or citation placeholder text.
 */
export type TurnProcessView = {
  /** false ⇒ render nothing. Never an empty shell or placeholder. */
  visible: boolean;
  header: {
    state: "running" | "settled";
    /**
     * Running-only live summary. Maps the current phase to a user-facing
     * label (正在理解问题 / 正在阅读本文 / 正在查询网页 / 正在整理回答).
     * Internal phases fall back to the last visible phase's running
     * label, or "正在整理回答" as a neutral default. Null when settled.
     */
    liveSummary: string | null;
    /**
     * Settled-only one-liner copy. "已整理回答 · Ns" /
     * "已根据当前文章整理 · Ns" / "已查询网页 · N 个来源 · Ns" / "未完成" /
     * "已取消". Reasoning-only cold history shows "思考过程". Null while
     * running.
     */
    settledCopy: string | null;
    /** Settled-only whole seconds; null while running or when unknown. */
    durationS: number | null;
  };
  /**
   * Visible learner steps (R3): host lifecycle steps (理解问题 /
   * 整理回答) plus real wire tool steps, in canonical learner order.
   * Internal stages are filtered out. Empty for reasoning-only cold
   * history — never fabricated.
   */
  steps: ProcessStepView[];
  /** R3 — safe reasoning projection; null when empty and not streaming. */
  reasoning: TurnProcessReasoningView | null;
  /** Trigger aria-label; terminal copy wins over stale running summaries. */
  ariaLabel: string;
  /** Number of effective web citations (for "N 个来源" copy). */
  webSourceCount: number;
};

export type TurnProcessProjectionInput = {
  /** Live reducer state (streaming turns). */
  activity?: AgenticActivityState | null;
  /** Frozen snapshot (settled turns in the same session). */
  snapshot?: AgenticProcessSnapshot | null;
  /** Projected citations (AgenticWebSources' source of truth, reused). */
  citations?: readonly AgenticCitationDisplayItem[] | null;
  /** Message-level streaming flag (message.status === "streaming"). */
  isStreaming?: boolean;
  /**
   * R3 — server-side SAFE reasoning projection fields only. Raw provider
   * reasoning must never be passed here. Sourced from the agentic
   * reasoning observer (hot) or the persisted history projection (cold).
   */
  reasoningMd?: string | null;
  reasoningStatus?: ReaderAskMessageDto["reasoning_status"];
  reasoningTruncated?: boolean | null;
  /** Same-session, Host-projected context compaction lifecycle. */
  contextCompaction?: ReaderAskContextCompactionUiStateDto | null;
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

/**
 * R3 — Running-phase to user-facing label map. Internal phases fall
 * back to the last visible learner step's running label, then to
 * "正在整理回答", so the header never exposes analysis / evidence /
 * validation internal stages.
 */
const VISIBLE_RUNNING_LABELS: Partial<Record<TurnProcessStepId, string>> = {
  "context-compaction": "正在压缩上下文",
  "understanding-question": "正在理解问题",
  "reading-context": "正在阅读本文",
  "web-search": "正在查询网页",
  "composing-answer": "正在整理回答",
};

/**
 * Resolve the running header summary. Never exposes internal-phase
 * labels (分析问题 / 检索文章 / 核对依据); falls back to the last visible
 * learner step's running label, then to "正在整理回答".
 */
function resolveRunningLiveSummary(
  source: SourceState | null,
  projectedSteps: ProcessStepView[],
): string | null {
  if (
    projectedSteps.some(
      (step) => step.id === "context-compaction" && step.status === "active",
    )
  ) {
    return VISIBLE_RUNNING_LABELS["context-compaction"] ?? null;
  }
  if (!source) {
    return "正在整理回答";
  }
  if (source.currentPhase != null) {
    const currentStepId = stepIdOf(source.currentPhase);
    const label = VISIBLE_RUNNING_LABELS[currentStepId];
    if (label) {
      return label;
    }
  }
  // Internal phase (or no current phase): fall back to the last visible
  // learner step that was injected or started, in canonical order.
  for (let i = projectedSteps.length - 1; i >= 0; i--) {
    const step = projectedSteps[i];
    if (INTERNAL_STEP_IDS.has(step.id)) {
      continue;
    }
    const label = VISIBLE_RUNNING_LABELS[step.id];
    if (label) {
      return label;
    }
  }
  return "正在整理回答";
}

/**
 * R0.2 — Count effective web citations by unique URL. Used only for the
 * "N 个来源" segment of the settled one-liner. Mirrors the dedup
 * semantics of `projectWebSources` in agentic-web-sources.tsx.
 */
function countWebCitations(
  citations: readonly AgenticCitationDisplayItem[] | null | undefined,
): number {
  if (!citations || citations.length === 0) {
    return 0;
  }
  const seen = new Set<string>();
  for (const citation of citations) {
    if (citation.sourceKind !== "web") {
      continue;
    }
    if (typeof citation.url !== "string" || citation.url.length === 0) {
      continue;
    }
    seen.add(citation.url);
  }
  return seen.size;
}

/**
 * R1-rework — Build the settled one-liner copy. Only fixed Chinese strings
 * interpolated with a duration / source count; never interpolates server
 * summary, error text, query, URL, tool name, or terminal_reason.
 *
 * R1-rework P0: EVERY successful answer must preserve a learner-facing
 * process summary — not just article/web turns. Pure-answer turns show
 * "已整理回答"; article-only turns show "已根据当前文章整理"; web turns
 * show "已查询网页 · N 个来源".
 */
function buildSettledCopy(
  source: SourceState | null,
  durationS: number | null,
  webSourceCount: number,
  hasWebStep: boolean,
  hasArticleStep: boolean,
): string | null {
  if (!source) {
    return null;
  }
  const durationSuffix = durationS != null ? ` · ${durationS}s` : "";
  if (source.status === "failed") {
    return "未完成";
  }
  if (source.status === "cancelled") {
    return "已取消";
  }
  // Stale-EOF: a snapshot frozen mid-run (running/degraded) never got a
  // business terminal. It settles as "未完成" — never a success copy.
  if (source.status === "running" || source.status === "degraded") {
    return "未完成";
  }
  // completed only. degraded-with-completed-status carries an optional-tool
  // warning owned by SystemMessage, not by this copy.
  if (hasWebStep) {
    return `已查询网页 · ${webSourceCount} 个来源${durationSuffix}`;
  }
  if (hasArticleStep) {
    return `已根据当前文章整理${durationSuffix}`;
  }
  // Pure-answer turn: no tool step, but still needs a learner-facing
  // summary per R1-rework P0.
  return `已整理回答${durationSuffix}`;
}

function projectAriaLabel(
  source: SourceState | null,
  liveTurn: boolean,
  liveSummary: string | null,
  hasReasoning: boolean,
): string {
  if (!source) {
    // R3 — reasoning-only cold history: no run lifecycle is provable, but
    // the persisted safe reasoning projection is. Label it plainly.
    if (hasReasoning) {
      return "思考过程";
    }
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
    return liveSummary ?? "Ask Claread 正在工作";
  }
  // A frozen snapshot still marked running/degraded means the stream
  // ended without a business terminal (stale EOF reconcile path). The
  // turn never completed — say so; never reuse a stale running summary.
  return "本轮回答未能完成";
}

/**
 * R3 — Project the server-side SAFE reasoning fields into the view.
 * Returns null when there is no active stream and no projected text, so
 * an empty reasoning_md never produces an empty shell. Only consumes
 * reasoning_md / reasoning_status / reasoning_truncated — the redacted
 * reasoning_projection_v1 output — never raw provider chain-of-thought.
 */
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

/**
 * R3 — Build the learner step list: host-provable lifecycle steps plus
 * real wire steps, in canonical learner order.
 *
 * Host steps are provable from the turn lifecycle, never guessed from a
 * model summary:
 * - 理解问题: the turn accepted agentic.run_started (any non-idle source
 *   state, or a frozen snapshot). Complete once any wire phase arrived
 *   or the turn completed; active while live with nothing started yet;
 *   interrupted when a non-ok terminal hit before any phase.
 * - 整理回答: injected when the wire carries no composing row. Settled
 *   turns always get it (the answer itself proves composition); live
 *   turns get it once no wire step is actively running (tool work done,
 *   answer being composed) — never alongside an active tool step, and
 *   never before the first lifecycle signal.
 *
 * Host steps carry no duration (no trustworthy wire source — never
 * fabricated). Reasoning-only cold history (source null) gets NO steps.
 */
function buildLearnerSteps(params: {
  visibleWireSteps: ProcessStepView[];
  hasAnyWirePhase: boolean;
  liveTurn: boolean;
  source: SourceState | null;
  visible: boolean;
  contextCompaction: ReaderAskContextCompactionUiStateDto | null;
}): ProcessStepView[] {
  const {
    visibleWireSteps,
    hasAnyWirePhase,
    liveTurn,
    source,
    visible,
    contextCompaction,
  } = params;
  if (!visible || source == null) {
    // Reasoning-only cold history or invisible turn: never fabricate
    // process steps.
    return [];
  }

  const turnCompleted = source.status === "completed";
  const steps: ProcessStepView[] = [];

  if (contextCompaction) {
    const compactionRunning = contextCompaction.status === "running";
    steps.push({
      id: "context-compaction",
      label: compactionRunning
        ? "正在压缩上下文"
        : contextCompaction.status === "failed"
          ? "上下文整理未完成"
          : "上下文已压缩",
      status: compactionRunning
        ? "active"
        : contextCompaction.status === "completed"
          ? "complete"
          : "degraded",
      durationMs:
        compactionRunning || contextCompaction.elapsedMs <= 0
          ? null
          : contextCompaction.elapsedMs,
      attempts: null,
      domains: [],
    });
  }

  // 理解问题 — provable for every accepted run.
  const understandingStatus: TurnProcessStepStatus = liveTurn
    ? hasAnyWirePhase
      ? "complete"
      : "active"
    : turnCompleted || hasAnyWirePhase
      ? "complete"
      : "interrupted";
  steps.push({
    id: "understanding-question",
    label: TURN_PROCESS_STEP_LABELS["understanding-question"],
    status: understandingStatus,
    durationMs: null,
    attempts: null,
    domains: [],
  });

  steps.push(...visibleWireSteps);

  // 整理回答 — host-proved when the wire lacks a composing row.
  const hasComposingFold = visibleWireSteps.some(
    (step) => step.id === "composing-answer",
  );
  if (!hasComposingFold) {
    const anyWireStepActive = visibleWireSteps.some(
      (step) => step.status === "active",
    );
    // Settled: always — a settled visible turn has an answer or an
    // outcome, so composition either completed or was interrupted.
    // Live: only once some wire phase arrived (the run moved past
    // understanding) AND no tool step is actively running — composing
    // starts after the first lifecycle signal, never alongside an
    // active tool step and never before the run did anything.
    if (!liveTurn || (hasAnyWirePhase && !anyWireStepActive)) {
      steps.push({
        id: "composing-answer",
        label: TURN_PROCESS_STEP_LABELS["composing-answer"],
        status: liveTurn ? "active" : turnCompleted ? "complete" : "interrupted",
        durationMs: null,
        attempts: null,
        domains: [],
      });
    }
  }

  return [...steps].sort(
    (a, b) => CANONICAL_STEP_ORDER[a.id] - CANONICAL_STEP_ORDER[b.id],
  );
}

/**
 * Project the full turn process view. Prefers live activity, falls back
 * to the frozen snapshot, then to the safe reasoning projection (cold
 * history renders reasoning-only — never fabricated steps).
 *
 * R3: settled turns keep 整理回答 visible (complete / interrupted), and
 * every accepted run shows the host 理解问题 lifecycle step. Visibility:
 * streaming, OR settled with a non-idle source state, OR a non-empty
 * safe reasoning projection.
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

  // Project all folds first (internal + visible) so supersession logic
  // stays correct, then filter to user-visible wire steps. R3: a single
  // filter for live and settled — composing-answer stays visible after
  // settle (R0's settled-hide dropped the final learner step).
  const allFolds = projectSteps(source, input.citations, liveTurn);
  const visibleWireSteps = allFolds.filter((step) =>
    VISIBLE_STEP_IDS.has(step.id),
  );

  const reasoning = projectReasoning(input);
  const hasReasoning = reasoning !== null;

  const webSourceCount = countWebCitations(input.citations);
  const hasWebStep = visibleWireSteps.some(
    (step) => step.id === "web-search",
  );
  const hasArticleStep = visibleWireSteps.some(
    (step) => step.id === "reading-context",
  );

  const elapsedMs = source?.elapsedMs ?? 0;
  const durationS =
    settled && elapsedMs > 0 ? Math.max(1, Math.round(elapsedMs / 1000)) : null;

  // R3 visibility: streaming always shows the disclosure; settled shows
  // it for any non-idle source state (EVERY successful answer preserves
  // a summary); cold history shows it only when the safe reasoning
  // projection exists (reasoning-only — process steps are never
  // fabricated without a snapshot).
  const visible =
    liveTurn ||
    (settled && source != null && source.status !== "idle") ||
    hasReasoning;

  const finalSteps = buildLearnerSteps({
    visibleWireSteps,
    hasAnyWirePhase: allFolds.length > 0,
    liveTurn,
    source,
    visible,
    contextCompaction: input.contextCompaction ?? null,
  });

  const liveSummary = liveTurn
    ? resolveRunningLiveSummary(source, finalSteps)
    : null;
  const settledCopy = settled
    ? (buildSettledCopy(
        source,
        durationS,
        webSourceCount,
        hasWebStep,
        hasArticleStep,
      ) ??
      // R3 — reasoning-only cold history has no run lifecycle to
      // summarize; label the disclosure after its only content.
      (source == null && hasReasoning ? "思考过程" : null))
    : null;

  return {
    visible,
    header: {
      state: liveTurn ? "running" : "settled",
      liveSummary,
      settledCopy,
      durationS,
    },
    steps: finalSteps,
    reasoning,
    ariaLabel: projectAriaLabel(source, liveTurn, liveSummary, hasReasoning),
    webSourceCount,
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
