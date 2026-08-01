/**
 * Client reducer for agentic.learner_reasoning.snapshot (replace semantics).
 * Production AiWorkspacePanel must call reduceLearnerReasoningSnapshot only.
 */

export type LearnerReasoningStage =
  | "analyzing"
  | "article"
  | "web"
  | "synthesizing";

export type LearnerReasoningBasis = "article" | "web" | "general";

export type LearnerReasoningState = {
  text: string | null;
  status: "streaming" | "completed" | null;
  stage: LearnerReasoningStage | null;
  basis: LearnerReasoningBasis[];
  sequence: number;
  revision: number;
  messageId: string | null;
  threadId: string | null;
  turnRunId: string | null;
  settled: boolean;
};

export const EMPTY_LEARNER_REASONING_STATE: LearnerReasoningState = {
  text: null,
  status: null,
  stage: null,
  basis: [],
  sequence: 0,
  revision: 0,
  messageId: null,
  threadId: null,
  turnRunId: null,
  settled: false,
};

/** Unvalidated wire fragment (unknown/raw) — not accepted by the reducer. */
export type LearnerReasoningSnapshotWireUnknown = Record<string, unknown>;

/**
 * Accepted snapshot payload after guard validation.
 * generation_id is required (non-negative integer).
 */
export type LearnerReasoningSnapshotPayload = {
  execution_version: "reader_record_ask_agentic_v2";
  message_id: string;
  thread_id: string;
  turn_run_id: string;
  sequence: number;
  revision: number;
  generation_id: number;
  stage: LearnerReasoningStage;
  text: string;
  basis?: LearnerReasoningBasis[];
  policy_version: "learner_reasoning_v1";
  projection_policy_version?: "learner_reasoning_v1";
};

export type ActiveRunIdentity = {
  messageId: string;
  threadId: string;
  turnRunId: string;
};

function isLearnerStage(value: unknown): value is LearnerReasoningStage {
  return (
    value === "analyzing" ||
    value === "article" ||
    value === "web" ||
    value === "synthesizing"
  );
}

function isLearnerBasis(value: unknown): value is LearnerReasoningBasis {
  return value === "article" || value === "web" || value === "general";
}

const URL_RE = /https?:\/\//i;
const MD_LINK_RE = /\[[^\]]*\]\([^)]+\)/;
const HTML_RE = /<[^>]+>/;
const BEARER_RE = /Bearer\s+\S+/i;
const UUID_RE =
  /[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/;
const PROVIDER_RE =
  /\b(deepseek|qwen|dashscope|anthropic|openai|provider|token|evh_|turn_run|message_id)\b/i;

function isSafeLearnerText(text: string): boolean {
  const cleaned = text.trim();
  if (!cleaned || cleaned.length > 80) return false;
  if (/[\n\r]/.test(cleaned)) return false;
  if (URL_RE.test(cleaned)) return false;
  if (MD_LINK_RE.test(cleaned)) return false;
  if (HTML_RE.test(cleaned)) return false;
  if (BEARER_RE.test(cleaned)) return false;
  if (UUID_RE.test(cleaned)) return false;
  if (PROVIDER_RE.test(cleaned)) return false;
  // Require some CJK.
  const cjk = (cleaned.match(/[\u4e00-\u9fff]/g) ?? []).length;
  if (cjk / cleaned.length < 0.5) return false;
  return true;
}

export function isLearnerReasoningSnapshotPayload(
  data: unknown
): data is LearnerReasoningSnapshotPayload {
  if (!data || typeof data !== "object") return false;
  const d = data as Record<string, unknown>;
  if (typeof d.text !== "string" || !isSafeLearnerText(d.text)) return false;
  if (typeof d.sequence !== "number" || d.sequence < 1) return false;
  if (typeof d.revision !== "number" || d.revision < 1) return false;
  if (typeof d.generation_id !== "number" || d.generation_id < 0) return false;
  if (!Number.isInteger(d.generation_id)) return false;
  if (!isLearnerStage(d.stage)) return false;
  if (d.execution_version !== "reader_record_ask_agentic_v2") return false;
  const policy = d.policy_version ?? d.projection_policy_version;
  if (policy !== "learner_reasoning_v1") return false;
  if (typeof d.message_id !== "string" || !d.message_id) return false;
  if (typeof d.thread_id !== "string" || !d.thread_id) return false;
  if (typeof d.turn_run_id !== "string" || !d.turn_run_id) return false;
  if (d.basis !== undefined) {
    if (!Array.isArray(d.basis)) return false;
    for (const b of d.basis) {
      if (!isLearnerBasis(b)) return false;
    }
  }
  return true;
}

/**
 * Apply a snapshot with identity / sequence / terminal guards.
 * Requires activeRunIdentity — never uses contextCompactionIdentity.
 * Replace semantics: text is replaced, never appended.
 */
export function reduceLearnerReasoningSnapshot(
  state: LearnerReasoningState,
  payload: LearnerReasoningSnapshotPayload,
  active: ActiveRunIdentity | null,
  options?: { terminal?: boolean }
): LearnerReasoningState {
  if (options?.terminal || state.settled) {
    return state;
  }
  // Must have a trusted active run identity before accepting any snapshot.
  if (!active) {
    return state;
  }
  if (!isLearnerReasoningSnapshotPayload(payload)) {
    return state;
  }
  const messageId = String(payload.message_id ?? "");
  const threadId = String(payload.thread_id ?? "");
  const turnRunId = String(payload.turn_run_id ?? "");
  if (
    messageId !== active.messageId ||
    threadId !== active.threadId ||
    turnRunId !== active.turnRunId
  ) {
    return state; // foreign identity
  }
  const sequence = payload.sequence as number;
  if (sequence <= state.sequence) {
    return state; // duplicate / out-of-order
  }
  const text = (payload.text as string).trim();
  const basis = Array.isArray(payload.basis)
    ? (payload.basis.filter(isLearnerBasis) as LearnerReasoningBasis[])
    : [];
  return {
    text,
    status: "streaming",
    stage: payload.stage as LearnerReasoningStage,
    basis,
    sequence,
    revision: payload.revision as number,
    messageId,
    threadId,
    turnRunId,
    settled: false,
  };
}

export function settleLearnerReasoning(
  state: LearnerReasoningState
): LearnerReasoningState {
  if (!state.text) {
    return { ...state, settled: true, status: null };
  }
  return { ...state, settled: true, status: "completed" };
}

export function resetLearnerReasoning(): LearnerReasoningState {
  return { ...EMPTY_LEARNER_REASONING_STATE };
}

/** Map message fields from reducer state (production apply helper). */
export function learnerReasoningMessagePatch(
  state: LearnerReasoningState
): {
  learner_reasoning_text: string | null;
  learner_reasoning_status: "streaming" | "completed" | null;
  learner_reasoning_stage: LearnerReasoningStage | null;
  learner_reasoning_sequence: number | null;
} {
  return {
    learner_reasoning_text: state.text,
    learner_reasoning_status: state.status,
    learner_reasoning_stage: state.stage,
    learner_reasoning_sequence: state.sequence > 0 ? state.sequence : null,
  };
}
