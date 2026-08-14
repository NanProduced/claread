/**
 * Single source of truth for user-facing Ask Claread error copy.
 *
 * Everything the user may see on the agentic Ask error path is a fixed
 * Chinese string defined here:
 * - `terminal_reason` → fixed message (consumed in production, not DEV-only);
 * - `final_status` → fixed message / interrupted-bubble copy;
 * - known stream-error codes → fixed message;
 * - network vs abort classification for thrown errors;
 * - a whitelist gate (`userFacingErrorMessage`) that blocks raw error
 *   strings (`Failed to fetch`, `UnexpectedModelBehavior`, backend detail,
 *   HTTP bodies) from ever reaching the composer banner.
 *
 * Raw error content must never be returned from these helpers; unknown
 * inputs collapse to fixed fallbacks (DEV-only raw passthrough exists in
 * the agentic terminal formatter for debugging and is re-gated by the
 * whitelist before render).
 */

/** Generic non-ok terminal fallback when nothing more specific applies. */
export const ASK_UNAVAILABLE_MESSAGE = "Ask Claread 暂时不可用。";

/** Banner fallback for errors that carry no known friendly message. */
export const ASK_INCOMPLETE_MESSAGE = "这次回答没有完成。请稍后重试，或换一种问法。";

/** Fixed copy for fetch-level network failures. */
export const NETWORK_ERROR_MESSAGE = "网络连接失败，请检查网络后重试。";

/** Stable pre-stream capability failure owned by the browser UI. */
export const WEB_SEARCH_UNAVAILABLE_MESSAGE = "当前模型暂不支持联网搜索。";

/**
 * A pending/optimistic submission never reached a
 * canonical assistant id. CTA is 重新发送, not 重新生成.
 */
export const PENDING_SUBMISSION_RESEND_MESSAGE =
  "这次消息尚未完成提交，请重新发送。";

/** Retry target is not a persisted server UUID (BFF 409). */
export const RETRY_TARGET_NOT_PERSISTED_MESSAGE =
  "这轮回答尚未保存，请重新发送，不要直接重新生成。";

/** Retry lane missing / untrusted (backend 409). */
export const RETRY_LANE_UNKNOWN_MESSAGE =
  "无法确认这轮回答的执行链路，请新建提问，不要跨链重试。";

/** Generic interrupted-bubble copy when no final_status refinement applies. */
export const INTERRUPTED_BUBBLE_FALLBACK_MESSAGE = "输出中断，可重新生成。";

/**
 * Optional-tool warning: the turn succeeded (final_status=ok) but an
 * optional tool (e.g. web search) was unavailable or failed during the
 * run. The answer is still canonical; this is a non-blocking warning.
 */
export const OPTIONAL_TOOL_WARNING_MESSAGE = "部分可选能力暂不可用，回答已正常生成。";

/** Fixed quiet explanations for failed/degraded public process steps. */
export const PROCESS_STEP_ISSUE_MESSAGES: Readonly<
  Record<string, Readonly<{ failed: string; degraded: string }>>
> = {
  analysis: {
    failed: "问题分析未完成。",
    degraded: "问题分析仅部分完成。",
  },
  "article-evidence": {
    failed: "文章依据查找未完成。",
    degraded: "部分文章依据暂不可用。",
  },
  "web-evidence": {
    failed: "联网信息查询未完成。",
    degraded: "部分联网信息暂不可用。",
  },
  answering: {
    failed: "回答生成未完成。",
    degraded: "回答仅部分生成。",
  },
  "citation-check": {
    failed: "引用检查未通过，本轮回答未完成。",
    degraded: "部分引用未通过检查。",
  },
};

/** A clarification action no longer has the user turn it must replay. */
export const CLARIFICATION_CONTEXT_MISSING_MESSAGE =
  "没有找到这轮澄清对应的原始问题，暂时无法继续当前讨论。";

/** An asset clarification action no longer has the user turn it must replay. */
export const ASSET_CLARIFICATION_CONTEXT_MISSING_MESSAGE =
  "没有找到这轮资产澄清对应的原始问题，暂时无法继续当前讨论。";

/** terminal_reason → fixed Chinese message (production + DEV). */
export const TERMINAL_REASON_MESSAGES: Record<string, string> = {
  agent_run_failed: "回答生成失败，请稍后重试。",
  agent_output_invalid: "回答格式校验失败，请重试提问。",
  budget_exhausted: "本轮处理额度已用完，请稍后重试。",
  document_unavailable: "当前文档暂不可用，请稍后重试。",
  baseline_unavailable: "阅读上下文暂不可用，请稍后重试。",
  evidence_scope_invariant_violation: "回答依据校验异常，请重试提问。",
};

/** final_status → fixed Chinese banner message. */
export const FINAL_STATUS_MESSAGES: Record<string, string> = {
  context_stale: "阅读上下文已更新，请重试提问。",
  invalid_citations: "回答引用校验失败，请重试提问。",
  cancelled: "本次回答已取消。",
};

/** final_status → interrupted-bubble inline copy. */
export const FINAL_STATUS_BUBBLE_MESSAGES: Record<string, string> = {
  context_stale: "上下文已更新，回答已中断。",
  invalid_citations: "引用校验失败，回答已中断。",
  cancelled: "本次回答已取消。",
};

/** Known SSE `error` event codes → fixed Chinese message. */
export const KNOWN_STREAM_ERROR_CODES: Record<string, string> = {
  SSE_PARSE_ERROR: "数据解析异常，请重试。",
  web_search_unavailable: WEB_SEARCH_UNAVAILABLE_MESSAGE,
};

/**
 * True for fetch-level network failures. Matched by message shape only —
 * browsers report `TypeError: Failed to fetch` / `NetworkError…`; raw
 * strings and non-Error values are never classified as network errors.
 */
export function isNetworkError(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false;
  }
  const msg = error.message.toLowerCase();
  return (
    msg.includes("failed to fetch") ||
    msg.includes("networkerror") ||
    (error instanceof TypeError && (msg.includes("fetch") || msg.includes("network")))
  );
}

/** True only for user-initiated cancellations (AbortController). */
export function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

/**
 * Normalize any thrown value into a fixed Chinese message:
 * - AbortError → "" (user cancelled; callers must not surface an error);
 * - network failure → fixed network copy;
 * - anything else → the caller-provided fixed fallback. `error.message`
 *   is never passed through.
 */
export function toUserFacingErrorMessage(error: unknown, fallback: string): string {
  if (isAbortError(error)) {
    return "";
  }
  if (isNetworkError(error)) {
    return NETWORK_ERROR_MESSAGE;
  }
  return fallback;
}

/**
 * Map a non-ok agentic terminal payload to user-facing copy:
 * 1. exact `terminal_reason` map (production and DEV);
 * 2. `final_status` map;
 * 3. DEV-only raw reason passthrough for unknown reasons (debugging);
 * 4. fixed generic fallback.
 */
export function formatAgenticTerminalMessage(
  payload: { final_status?: string | null; terminal_reason?: string | null },
  options: { dev: boolean },
): string {
  const reason =
    typeof payload.terminal_reason === "string" && payload.terminal_reason.trim()
      ? payload.terminal_reason.trim()
      : null;
  if (reason && TERMINAL_REASON_MESSAGES[reason]) {
    return TERMINAL_REASON_MESSAGES[reason];
  }
  const status = typeof payload.final_status === "string" ? payload.final_status : null;
  if (status && FINAL_STATUS_MESSAGES[status]) {
    return FINAL_STATUS_MESSAGES[status];
  }
  return options.dev && reason ? reason : ASK_UNAVAILABLE_MESSAGE;
}

/**
 * Map an SSE `error` event payload to user-facing copy. An explicit
 * backend `user_message` is trusted; known codes map to fixed copy; raw
 * `detail` is never surfaced (DEV shows the code only).
 */
export function formatStreamErrorMessage(
  data: { user_message?: unknown; code?: unknown; detail?: unknown },
  options: { dev: boolean },
): string {
  const userMessage =
    typeof data.user_message === "string" && data.user_message.trim()
      ? data.user_message.trim()
      : null;
  if (userMessage) {
    return userMessage;
  }
  const code = typeof data.code === "string" && data.code.trim() ? data.code.trim() : null;
  if (code && KNOWN_STREAM_ERROR_CODES[code]) {
    return KNOWN_STREAM_ERROR_CODES[code];
  }
  return options.dev && code ? `${code}: 详情见日志` : ASK_UNAVAILABLE_MESSAGE;
}

/** Interrupted-bubble inline copy refined by message-level final_status. */
export function interruptedBubbleMessage(
  finalStatus: string | null | undefined,
): string {
  if (finalStatus && FINAL_STATUS_BUBBLE_MESSAGES[finalStatus]) {
    return FINAL_STATUS_BUBBLE_MESSAGES[finalStatus];
  }
  return INTERRUPTED_BUBBLE_FALLBACK_MESSAGE;
}

/**
 * Fixed friendly messages allowed to reach the composer banner. Anything
 * not matching collapses to the fixed incomplete-answer copy — this is the
 * last gate that keeps raw error strings out of the UI.
 */
const KNOWN_FRIENDLY_PATTERNS: readonly RegExp[] = [
  /网络连接失败/,
  /回答生成失败/,
  /回答格式校验失败/,
  /本轮处理额度/,
  /当前文档暂不可用/,
  /阅读上下文暂不可用/,
  /回答依据校验异常/,
  /上下文已更新/,
  /引用校验失败/,
  /本次回答已取消/,
  /Ask Claread 暂时不可用/,
  /这次回答没有完成/,
  /数据解析异常/,
  /当前积分不足/,
  /发送消息失败/,
  /重新生成失败/,
  /这次消息尚未完成提交/,
  /这轮回答尚未保存/,
  /无法确认这轮回答的执行链路/,
  /Ask Claread (初始化|线程列表|模型列表|加载失败)/,
  /重置会话失败|动作确认失败|删除补充失败/,
  /上下文文章搜索失败/,
  /请求失败/,
  /没有找到这轮.*澄清对应的原始问题/,
  /部分可选能力暂不可用/,
  /当前模型暂不支持联网搜索/,
];

/** Whitelist gate for the composer banner — never passes raw errors. */
export function userFacingErrorMessage(errorMessage: string): string {
  const msg = errorMessage.trim();
  if (!msg) {
    return ASK_INCOMPLETE_MESSAGE;
  }
  if (KNOWN_FRIENDLY_PATTERNS.some((pattern) => pattern.test(msg))) {
    return msg;
  }
  return ASK_INCOMPLETE_MESSAGE;
}
