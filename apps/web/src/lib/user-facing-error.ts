/**
 * 全站统一的用户可读错误闸口。
 *
 * 纪律（与 Ask 链路的 ask-error-messages 同源）：原始 error.message、
 * 上游 FastAPI detail、HTML 错误页文本（"Unexpected token '<' …"）
 * 永不进 UI。一律经这里映射为固定中文文案。
 *
 * 两个入口：
 * - userFacingErrorMessage(error)：catch 到的任意 thrown value；
 * - userFacingPayloadMessage(payload)：BFF 返回的 { status, code, message }。
 *   已是干净中文的 BFF message 原样放行（looksLikeSafeUserCopy），
 *   英文/技术串折叠为按 code/status 映射的固定文案。
 */

/** 通用兜底。 */
export const GENERIC_ERROR_MESSAGE = "出了点问题，请稍后重试。";

/** fetch 级网络失败。 */
export const NETWORK_ERROR_MESSAGE = "网络连接异常，请检查网络后重试。";

/** 5xx / 上游不可用 / 打到 HTML 错误页。 */
export const SERVER_UNAVAILABLE_MESSAGE = "服务暂时不可用，请稍后重试。";

/** 404 / *_not_found。 */
export const CONTENT_NOT_FOUND_MESSAGE = "内容不存在或已被删除。";

/** 401/403 / auth_required。 */
export const AUTH_EXPIRED_MESSAGE = "登录状态已失效，请重新登录。";

/** True for fetch-level network failures（与 Ask 链路同规则）。 */
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

/**
 * response.json() 打到 HTML 错误页（Next.js error page / 网关 502 页）
 * 时的典型 SyntaxError："Unexpected token '<' … is not valid JSON"。
 */
function isHtmlParseError(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false;
  }
  const msg = error.message;
  return msg.includes("not valid JSON") || msg.includes("Unexpected token");
}

/** True only for user-initiated cancellations (AbortController). */
export function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

/**
 * 任意 thrown value → 固定中文文案。AbortError 返回 ""（用户主动取消，
 * 调用方不应显示错误）。error.message 永不透传。
 */
export function userFacingErrorMessage(
  error: unknown,
  fallback: string = GENERIC_ERROR_MESSAGE,
): string {
  if (isAbortError(error)) {
    return "";
  }
  if (isNetworkError(error)) {
    return NETWORK_ERROR_MESSAGE;
  }
  if (isHtmlParseError(error)) {
    return SERVER_UNAVAILABLE_MESSAGE;
  }
  return fallback;
}

/** 含 CJK 且不含技术标记的 message 视为已由 BFF 写好的安全文案。 */
export function looksLikeSafeUserCopy(message: string): boolean {
  if (!/[一-龥]/.test(message)) {
    return false;
  }
  return !/[<>{}]|\b(fetch|JSON|token|Error|undefined|null|DOCTYPE)\b/i.test(message);
}

/**
 * 任意 thrown value → 用户可读文案（宽松版）：BFF/调用方写好中文的
 * Error.message（looksLikeSafeUserCopy）放行；网络/HTML 解析/英文技术串
 * 折叠为固定文案。用于 error 经由 `new Error(payload.message)` 包装的
 * 链路（提交流、内容检查、登录等）。
 */
export function userFacingErrorCopy(
  error: unknown,
  fallback: string = GENERIC_ERROR_MESSAGE,
): string {
  if (isAbortError(error)) {
    return "";
  }
  if (error instanceof Error) {
    const message = error.message.trim();
    if (message && looksLikeSafeUserCopy(message)) {
      return message;
    }
  }
  return userFacingErrorMessage(error, fallback);
}

/** BFF 错误 payload 里的已知 code → 固定中文文案。 */
const KNOWN_ERROR_CODE_MESSAGES: Record<string, string> = {
  auth_required: AUTH_EXPIRED_MESSAGE,
  record_not_found: CONTENT_NOT_FOUND_MESSAGE,
  article_not_found: CONTENT_NOT_FOUND_MESSAGE,
  entry_not_found: CONTENT_NOT_FOUND_MESSAGE,
  upstream_error: SERVER_UNAVAILABLE_MESSAGE,
  upstream_unavailable: SERVER_UNAVAILABLE_MESSAGE,
  insufficient_credits: "当前积分不足，请稍后再试。",
};

/**
 * BFF { status, code, message } → 用户可读文案：
 * 1. 干净中文 message 直接放行（BFF 自己写好的）；
 * 2. 已知 code 映射；
 * 3. status 5xx / 0 → 服务不可用，401/403 → 登录失效，404 → 不存在；
 * 4. 其余 → 调用方固定 fallback。英文原文永不放行。
 */
export function userFacingPayloadMessage(
  payload: { status?: number; code?: string; message?: string } | null | undefined,
  fallback: string = GENERIC_ERROR_MESSAGE,
): string {
  const message = payload?.message;
  if (typeof message === "string" && message.trim() && looksLikeSafeUserCopy(message.trim())) {
    return message.trim();
  }
  const code = payload?.code;
  if (code && KNOWN_ERROR_CODE_MESSAGES[code]) {
    return KNOWN_ERROR_CODE_MESSAGES[code];
  }
  const status = payload?.status;
  if (status === 401 || status === 403) {
    return AUTH_EXPIRED_MESSAGE;
  }
  if (status === 404) {
    return CONTENT_NOT_FOUND_MESSAGE;
  }
  if (status === 0 || (status !== undefined && status >= 500)) {
    return SERVER_UNAVAILABLE_MESSAGE;
  }
  return fallback;
}
