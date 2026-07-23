/**
 * R4-A6-T3: single source of truth for user-facing Ask Claread error copy.
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

/** Generic interrupted-bubble copy when no final_status refinement applies. */
export const INTERRUPTED_BUBBLE_FALLBACK_MESSAGE = "输出中断，可重新生成。";

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
  /Ask Claread (初始化|线程列表|模型列表|加载失败)/,
  /重置会话失败|动作确认失败|删除补充失败/,
  /上下文文章搜索失败/,
  /请求失败/,
  /没有找到这轮.*澄清对应的原始问题/,
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
