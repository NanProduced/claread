/**
 * ASK-UX-MOBILE-R0-R2 / Task 3: unit coverage for AskSystemNotice projection
 * functions. Verifies scope, severity, dismissibility, CTA and
 * relatedMessageId binding — and that the `message` field is sourced from
 * ask-error-messages.ts typed copy (never raw error text).
 */
import { describe, expect, it } from "vitest";

import {
  ASK_INCOMPLETE_MESSAGE,
  ASK_UNAVAILABLE_MESSAGE,
} from "./ask-error-messages";
import {
  isFullTurnError,
  projectActionFailureNotice,
  projectClarifyWarningNotice,
  projectOptionalToolWarning,
  projectPanelInitNotice,
  projectSendFailureNotice,
  projectSupplementFailureNotice,
  projectTurnTerminalNotice,
} from "./ask-system-notice";

describe("projectTurnTerminalNotice", () => {
  it("hard failure (agent_run_failed) → turn / error, bound to message, retry CTA", () => {
    const notice = projectTurnTerminalNotice({
      messageId: "msg_1",
      finalStatus: "failed",
      terminalReason: "agent_run_failed",
      dev: false,
    });
    expect(notice.scope).toBe("turn");
    expect(notice.severity).toBe("error");
    expect(notice.relatedMessageId).toBe("msg_1");
    expect(notice.dismissible).toBe(false);
    expect(notice.cta).toEqual({ label: "重新生成", action: "retry" });
    expect(notice.message).toBe("回答生成失败，请稍后重试。");
  });

  it("soft final_status (context_stale) → turn / warning", () => {
    const notice = projectTurnTerminalNotice({
      messageId: "msg_2",
      finalStatus: "context_stale",
      terminalReason: null,
      dev: false,
    });
    expect(notice.scope).toBe("turn");
    expect(notice.severity).toBe("warning");
    expect(notice.relatedMessageId).toBe("msg_2");
  });

  it("cancelled → turn / warning, no CTA", () => {
    const notice = projectTurnTerminalNotice({
      messageId: "msg_3",
      finalStatus: "cancelled",
      terminalReason: null,
      dev: false,
    });
    expect(notice.scope).toBe("turn");
    expect(notice.severity).toBe("warning");
    expect(notice.cta).toBeUndefined();
  });
});

describe("projectPanelInitNotice", () => {
  it("init → panel / error, not dismissible, reload CTA", () => {
    const notice = projectPanelInitNotice({
      kind: "init",
      message: ASK_UNAVAILABLE_MESSAGE,
    });
    expect(notice.scope).toBe("panel");
    expect(notice.severity).toBe("error");
    expect(notice.dismissible).toBe(false);
    expect(notice.cta).toEqual({ label: "重新加载", action: "reload" });
    expect(notice.message).toBe(ASK_UNAVAILABLE_MESSAGE);
  });

  it("capability → panel / error, not dismissible, reload CTA", () => {
    const notice = projectPanelInitNotice({
      kind: "capability",
      message: ASK_UNAVAILABLE_MESSAGE,
    });
    expect(notice.scope).toBe("panel");
    expect(notice.severity).toBe("error");
    expect(notice.dismissible).toBe(false);
    expect(notice.cta).toEqual({ label: "重新加载", action: "reload" });
  });

  it("history_restore → panel / warning, dismissible, no CTA", () => {
    const notice = projectPanelInitNotice({
      kind: "history_restore",
      message: ASK_INCOMPLETE_MESSAGE,
    });
    expect(notice.scope).toBe("panel");
    expect(notice.severity).toBe("warning");
    expect(notice.dismissible).toBe(true);
    expect(notice.cta).toBeUndefined();
  });
});

describe("projectSendFailureNotice", () => {
  it("→ turn / action, bound to message, not dismissible, retry CTA (persisted)", () => {
    const notice = projectSendFailureNotice({
      messageId: "msg_send_1",
      message: ASK_UNAVAILABLE_MESSAGE,
    });
    expect(notice.scope).toBe("turn");
    expect(notice.severity).toBe("action");
    expect(notice.relatedMessageId).toBe("msg_send_1");
    expect(notice.dismissible).toBe(false);
    expect(notice.cta).toEqual({ label: "重新生成", action: "retry" });
  });

  it("pending submission → action + 重新发送 CTA", () => {
    const notice = projectSendFailureNotice({
      messageId: "local-assistant-1",
      message: "这次消息尚未完成提交，请重新发送。",
      target: "pending",
    });
    expect(notice.severity).toBe("action");
    expect(notice.cta).toEqual({ label: "重新发送", action: "resend" });
  });
});

describe("projectActionFailureNotice", () => {
  it("→ turn / error, bound to message, dismissible, NO regenerate CTA", () => {
    const notice = projectActionFailureNotice({
      messageId: "msg_action_1",
      message: "动作确认失败。",
    });
    expect(notice.scope).toBe("turn");
    expect(notice.severity).toBe("error");
    expect(notice.relatedMessageId).toBe("msg_action_1");
    expect(notice.dismissible).toBe(true);
    // Critical: action failure must NOT offer "重新生成" — regenerating
    // would discard the action context. The user retries via the action
    // card directly.
    expect(notice.cta).toBeUndefined();
  });
});

describe("projectSupplementFailureNotice", () => {
  it("→ turn / error, bound to message, dismissible, NO regenerate CTA", () => {
    const notice = projectSupplementFailureNotice({
      messageId: "msg_supp_1",
      message: "删除补充失败。",
    });
    expect(notice.scope).toBe("turn");
    expect(notice.severity).toBe("error");
    expect(notice.relatedMessageId).toBe("msg_supp_1");
    expect(notice.dismissible).toBe(true);
    expect(notice.cta).toBeUndefined();
  });
});

describe("projectClarifyWarningNotice", () => {
  it("→ turn / warning, bound to message, dismissible, no CTA", () => {
    const notice = projectClarifyWarningNotice({
      messageId: "msg_clarify_1",
      message: ASK_INCOMPLETE_MESSAGE,
    });
    expect(notice.scope).toBe("turn");
    expect(notice.severity).toBe("warning");
    expect(notice.relatedMessageId).toBe("msg_clarify_1");
    expect(notice.dismissible).toBe(true);
    expect(notice.cta).toBeUndefined();
  });
});

describe("projectOptionalToolWarning", () => {
  it("returns null when there is no warning message", () => {
    expect(
      projectOptionalToolWarning({ messageId: "msg_4", message: null }),
    ).toBeNull();
  });

  it("with message → turn / warning, bound to message, dismissible, no CTA", () => {
    const notice = projectOptionalToolWarning({
      messageId: "msg_5",
      message: ASK_INCOMPLETE_MESSAGE,
    });
    expect(notice).not.toBeNull();
    expect(notice?.scope).toBe("turn");
    expect(notice?.severity).toBe("warning");
    expect(notice?.relatedMessageId).toBe("msg_5");
    expect(notice?.dismissible).toBe(true);
    expect(notice?.cta).toBeUndefined();
  });
});

describe("isFullTurnError", () => {
  it("true for turn / error", () => {
    const hard = projectTurnTerminalNotice({
      messageId: "msg_6",
      finalStatus: "failed",
      terminalReason: "agent_run_failed",
      dev: false,
    });
    expect(isFullTurnError(hard)).toBe(true);
  });

  it("false for turn / warning", () => {
    const soft = projectTurnTerminalNotice({
      messageId: "msg_7",
      finalStatus: "context_stale",
      terminalReason: null,
      dev: false,
    });
    expect(isFullTurnError(soft)).toBe(false);
  });

  it("false for null", () => {
    expect(isFullTurnError(null)).toBe(false);
  });

  it("false for panel / error", () => {
    const panel = projectPanelInitNotice({
      kind: "init",
      message: ASK_UNAVAILABLE_MESSAGE,
    });
    expect(isFullTurnError(panel)).toBe(false);
  });

  it("true for send failure (whole turn failed)", () => {
    const send = projectSendFailureNotice({
      messageId: "msg_send_full",
      message: ASK_UNAVAILABLE_MESSAGE,
    });
    expect(isFullTurnError(send)).toBe(true);
  });

  it("false for action failure (turn may still have a valid answer)", () => {
    const action = projectActionFailureNotice({
      messageId: "msg_action_full",
      message: "动作确认失败。",
    });
    expect(isFullTurnError(action)).toBe(false);
  });

  it("false for supplement failure (turn may still have a valid answer)", () => {
    const supp = projectSupplementFailureNotice({
      messageId: "msg_supp_full",
      message: "删除补充失败。",
    });
    expect(isFullTurnError(supp)).toBe(false);
  });

  it("false for clarify warning", () => {
    const clarify = projectClarifyWarningNotice({
      messageId: "msg_clarify_full",
      message: ASK_INCOMPLETE_MESSAGE,
    });
    expect(isFullTurnError(clarify)).toBe(false);
  });
});
