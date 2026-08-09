/**
 * Unit coverage for the Ask Claread user-facing error mapping.
 *
 * The module is the single source of truth for fixed Chinese error copy:
 * terminal_reason / final_status / stream-error-code maps, network vs abort
 * classification, and the whitelist gate that blocks raw error strings.
 */
import { describe, expect, it } from "vitest";

import {
  ASK_INCOMPLETE_MESSAGE,
  ASK_UNAVAILABLE_MESSAGE,
  FINAL_STATUS_BUBBLE_MESSAGES,
  FINAL_STATUS_MESSAGES,
  KNOWN_STREAM_ERROR_CODES,
  NETWORK_ERROR_MESSAGE,
  WEB_SEARCH_UNAVAILABLE_MESSAGE,
  TERMINAL_REASON_MESSAGES,
  formatAgenticTerminalMessage,
  formatStreamErrorMessage,
  interruptedBubbleMessage,
  isAbortError,
  isNetworkError,
  toUserFacingErrorMessage,
  userFacingErrorMessage,
} from "./ask-error-messages";

const REASON_CASES = [
  ["agent_run_failed", "回答生成失败，请稍后重试。"],
  ["agent_output_invalid", "回答格式校验失败，请重试提问。"],
  ["budget_exhausted", "本轮处理额度已用完，请稍后重试。"],
  ["document_unavailable", "当前文档暂不可用，请稍后重试。"],
  ["baseline_unavailable", "阅读上下文暂不可用，请稍后重试。"],
  ["evidence_scope_invariant_violation", "回答依据校验异常，请重试提问。"],
] as const;

describe("formatAgenticTerminalMessage", () => {
  it.each(REASON_CASES)(
    "maps terminal_reason %s to fixed Chinese copy (prod and DEV)",
    (reason, expected) => {
      const payload = { final_status: "failed", terminal_reason: reason };
      expect(formatAgenticTerminalMessage(payload, { dev: false })).toBe(expected);
      expect(formatAgenticTerminalMessage(payload, { dev: true })).toBe(expected);
    },
  );

  it("maps final_status when the reason is unknown", () => {
    expect(
      formatAgenticTerminalMessage(
        { final_status: "context_stale", terminal_reason: "generation mismatch" },
        { dev: false },
      ),
    ).toBe(FINAL_STATUS_MESSAGES.context_stale);
    expect(
      formatAgenticTerminalMessage(
        { final_status: "invalid_citations", terminal_reason: null },
        { dev: false },
      ),
    ).toBe(FINAL_STATUS_MESSAGES.invalid_citations);
    expect(
      formatAgenticTerminalMessage({ final_status: "cancelled", terminal_reason: null }, { dev: false }),
    ).toBe(FINAL_STATUS_MESSAGES.cancelled);
  });

  it("unknown reason + failed: production shows the fixed fallback", () => {
    expect(
      formatAgenticTerminalMessage(
        { final_status: "failed", terminal_reason: "UnexpectedModelBehavior: boom" },
        { dev: false },
      ),
    ).toBe(ASK_UNAVAILABLE_MESSAGE);
    expect(
      formatAgenticTerminalMessage({ final_status: "failed", terminal_reason: null }, { dev: false }),
    ).toBe(ASK_UNAVAILABLE_MESSAGE);
  });

  it("unknown reason: DEV passes the raw reason through for debugging only", () => {
    expect(
      formatAgenticTerminalMessage(
        { final_status: "failed", terminal_reason: "some_new_reason" },
        { dev: true },
      ),
    ).toBe("some_new_reason");
    // No reason at all → fixed fallback even in DEV.
    expect(
      formatAgenticTerminalMessage({ final_status: "failed", terminal_reason: null }, { dev: true }),
    ).toBe(ASK_UNAVAILABLE_MESSAGE);
  });
});

describe("formatStreamErrorMessage", () => {
  it("trusts an explicit backend user_message", () => {
    expect(
      formatStreamErrorMessage(
        { user_message: "服务繁忙，请稍后再试。", detail: "Raw: UnexpectedModelBehavior" },
        { dev: false },
      ),
    ).toBe("服务繁忙，请稍后再试。");
  });

  it("maps known codes without leaking detail", () => {
    const shown = formatStreamErrorMessage(
      { code: "SSE_PARSE_ERROR", detail: 'Failed to parse SSE data for event "message.delta": oops' },
      { dev: false },
    );
    expect(shown).toBe(KNOWN_STREAM_ERROR_CODES.SSE_PARSE_ERROR);
    expect(shown).not.toContain("oops");
    expect(shown).not.toContain("Failed to parse");
  });

  it("maps web_search_unavailable to one fixed friendly message", () => {
    const shown = formatStreamErrorMessage(
      { code: "web_search_unavailable", detail: "provider route and secret query" },
      { dev: false },
    );
    expect(shown).toBe(WEB_SEARCH_UNAVAILABLE_MESSAGE);
    expect(shown).not.toContain("provider");
    expect(shown).not.toContain("secret query");
  });

  it("never leaks raw detail for unknown codes (prod fallback, DEV code-only)", () => {
    const data = { code: "WEIRD_CODE", detail: "UnexpectedModelBehavior: structured output invalid" };
    const prod = formatStreamErrorMessage(data, { dev: false });
    const dev = formatStreamErrorMessage(data, { dev: true });
    expect(prod).toBe(ASK_UNAVAILABLE_MESSAGE);
    expect(dev).toBe("WEIRD_CODE: 详情见日志");
    for (const shown of [prod, dev]) {
      expect(shown).not.toContain("UnexpectedModelBehavior");
      expect(shown).not.toContain("structured output invalid");
    }
  });

  it("falls back when nothing usable is present", () => {
    expect(formatStreamErrorMessage({}, { dev: false })).toBe(ASK_UNAVAILABLE_MESSAGE);
    expect(formatStreamErrorMessage({ detail: "backend exploded" }, { dev: false })).toBe(
      ASK_UNAVAILABLE_MESSAGE,
    );
  });
});

describe("network / abort classification", () => {
  it("detects network errors by message shape", () => {
    expect(isNetworkError(new TypeError("Failed to fetch"))).toBe(true);
    expect(isNetworkError(new Error("NetworkError when fetching resource"))).toBe(true);
    expect(isNetworkError(new TypeError("fetch aborted"))).toBe(true);
    expect(isNetworkError(new Error("UnexpectedModelBehavior: boom"))).toBe(false);
    // Non-Error values are never classified as network errors.
    expect(isNetworkError("Failed to fetch")).toBe(false);
    expect(isNetworkError(null)).toBe(false);
    expect(isNetworkError(undefined)).toBe(false);
  });

  it("detects abort errors by name only", () => {
    const domAbort = new DOMException("The operation was aborted.", "AbortError");
    expect(isAbortError(domAbort)).toBe(true);
    const named = new Error("aborted");
    named.name = "AbortError";
    expect(isAbortError(named)).toBe(true);
    expect(isAbortError(new Error("AbortError mentioned in text"))).toBe(false);
    expect(isAbortError(null)).toBe(false);
  });

  it("toUserFacingErrorMessage: abort → empty, network → fixed, else fallback", () => {
    const abort = new DOMException("aborted", "AbortError");
    expect(toUserFacingErrorMessage(abort, "重置会话失败。")).toBe("");
    expect(toUserFacingErrorMessage(new TypeError("Failed to fetch"), "重置会话失败。")).toBe(
      NETWORK_ERROR_MESSAGE,
    );
    expect(
      toUserFacingErrorMessage(new Error("UnexpectedModelBehavior: boom"), "重置会话失败。"),
    ).toBe("重置会话失败。");
    // Raw string throws are not Errors → fallback, never passthrough.
    expect(toUserFacingErrorMessage("raw string", "动作确认失败。")).toBe("动作确认失败。");
  });
});

describe("userFacingErrorMessage whitelist", () => {
  it("passes known friendly Chinese messages through untouched", () => {
    const friendly = [
      ...Object.values(TERMINAL_REASON_MESSAGES),
      ...Object.values(FINAL_STATUS_MESSAGES),
      ...Object.values(FINAL_STATUS_BUBBLE_MESSAGES),
      ...Object.values(KNOWN_STREAM_ERROR_CODES),
      NETWORK_ERROR_MESSAGE,
      ASK_UNAVAILABLE_MESSAGE,
      ASK_INCOMPLETE_MESSAGE,
      "Ask Claread 初始化失败。",
      "Ask Claread 线程列表加载失败。",
      "Ask Claread 模型列表加载失败。",
      "Ask Claread 加载失败。",
      "重置会话失败。",
      "动作确认失败。",
      "删除补充失败。",
      "发送消息失败。",
      "重新生成失败。",
      "上下文文章搜索失败。",
      "请求失败。",
      "当前积分不足：剩余 1 点，本次 Ask Claread 至少需要 10 点。本轮请求未发送给模型。",
      "没有找到这轮澄清对应的原始问题，暂时无法继续当前讨论。",
      "没有找到这轮资产澄清对应的原始问题，暂时无法继续当前讨论。",
    ];
    for (const msg of friendly) {
      expect(userFacingErrorMessage(msg)).toBe(msg);
    }
  });

  it("blocks raw error strings with the fixed incomplete-answer copy", () => {
    const raw = [
      "Failed to fetch",
      "NetworkError when fetching resource",
      "UnexpectedModelBehavior: structured output invalid",
      "TypeError: Cannot read properties of undefined",
      "HTTP 500 Internal Server Error",
      "agentic_model_unconfigured: no validated model for reader_ask route",
    ];
    for (const msg of raw) {
      expect(userFacingErrorMessage(msg)).toBe(ASK_INCOMPLETE_MESSAGE);
    }
  });

  it("treats empty / whitespace input as incomplete answer", () => {
    expect(userFacingErrorMessage("")).toBe(ASK_INCOMPLETE_MESSAGE);
    expect(userFacingErrorMessage("   ")).toBe(ASK_INCOMPLETE_MESSAGE);
  });
});

describe("interruptedBubbleMessage", () => {
  it("refines the interrupted bubble copy by final_status", () => {
    expect(interruptedBubbleMessage("context_stale")).toBe("上下文已更新，回答已中断。");
    expect(interruptedBubbleMessage("invalid_citations")).toBe("引用校验失败，回答已中断。");
    expect(interruptedBubbleMessage("cancelled")).toBe("本次回答已取消。");
  });

  it("keeps the generic regeneration note otherwise", () => {
    expect(interruptedBubbleMessage(null)).toBe("输出中断，可重新生成。");
    expect(interruptedBubbleMessage(undefined)).toBe("输出中断，可重新生成。");
    expect(interruptedBubbleMessage("failed")).toBe("输出中断，可重新生成。");
  });
});
