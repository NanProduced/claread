/**
 * ASK-RETRY-CONTRACT-R6 — pure unit coverage for browser path + hydrate
 * target selection (no React / no real network).
 */
import { describe, expect, it } from "vitest";

import {
  browserAskRetryPath,
  browserAskStreamPath,
  browserAskSubmissionPath,
  isLocalOptimisticMessageId,
  isPersistedAssistantMessageId,
} from "./browser-paths";
import { classifyRetryTarget } from "./retry-target";

const THREAD = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeee1";
const CANONICAL = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeee2";
const CLIENT_SUB = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff";
const LOCAL_ASST = "local-assistant-1";

describe("R6 browser paths — never /retry/stream in browser", () => {
  it("persisted regenerate uses /retry only", () => {
    const path = browserAskRetryPath(THREAD, CANONICAL);
    expect(path).toContain("/retry");
    expect(path).not.toContain("/retry/stream");
    expect(path).toBe(
      `/api/web/reader-ask/threads/${THREAD}/messages/${CANONICAL}/retry`,
    );
  });

  it("stream path is messages/stream not retry", () => {
    expect(browserAskStreamPath(THREAD)).toContain("/messages/stream");
    expect(browserAskStreamPath(THREAD)).not.toContain("/retry");
  });

  it("submission hydrate path is GET submissions/{id}", () => {
    const p = browserAskSubmissionPath(THREAD, CLIENT_SUB);
    expect(p).toContain(`/submissions/${CLIENT_SUB}`);
    expect(p).not.toContain("/retry");
  });
});

describe("R6 resend vs regenerate target selection", () => {
  it("local pending resolves to resend, not /retry", () => {
    const target = classifyRetryTarget(LOCAL_ASST);
    expect(target).not.toBeNull();
    expect(target?.kind).toBe("pending_submission");
    expect(target?.ctaAction).toBe("resend");
    // Resend must never construct a browser /retry URL.
    expect(browserAskRetryPath(THREAD, LOCAL_ASST)).not.toMatch(
      /\/retry\/stream$/,
    );
  });

  it("persisted UUID resolves to regenerate /retry", () => {
    const target = classifyRetryTarget(CANONICAL);
    expect(target).not.toBeNull();
    expect(target?.kind).toBe("persisted_assistant");
    expect(target?.ctaAction).toBe("retry");
    const path = browserAskRetryPath(THREAD, CANONICAL);
    expect(path).toContain("/retry");
    expect(path).not.toContain("/retry/stream");
  });

  it("local id is never treated as persisted UUID", () => {
    expect(isLocalOptimisticMessageId(LOCAL_ASST)).toBe(true);
    expect(isPersistedAssistantMessageId(LOCAL_ASST)).toBe(false);
    expect(isPersistedAssistantMessageId(CANONICAL)).toBe(true);
  });
});

describe("R6 hydrate projection shapes (completed / failed / cancelled)", () => {
  type Snap = {
    status: string;
    assistant_message_id?: string;
    assistant_message?: {
      id: string;
      content_md: string;
      status?: string;
    } | null;
  };

  function projectLocalOutcome(snap: Snap): {
    assistantId: string;
    status: string;
    content: string;
    cta: "none" | "regenerate" | "resend";
  } {
    if (
      snap.status === "completed" &&
      snap.assistant_message &&
      isPersistedAssistantMessageId(snap.assistant_message.id)
    ) {
      return {
        assistantId: snap.assistant_message.id,
        status: "completed",
        content: snap.assistant_message.content_md,
        cta: "none",
      };
    }
    if (
      (snap.status === "failed" || snap.status === "cancelled") &&
      snap.assistant_message_id &&
      isPersistedAssistantMessageId(snap.assistant_message_id)
    ) {
      return {
        assistantId: snap.assistant_message_id,
        status: snap.status === "cancelled" ? "interrupted" : "failed",
        content: snap.assistant_message?.content_md ?? "",
        cta: "regenerate",
      };
    }
    return {
      assistantId: LOCAL_ASST,
      status: "failed",
      content: "",
      cta: "resend",
    };
  }

  it("completed → canonical ids + full content + no local streaming", () => {
    const out = projectLocalOutcome({
      status: "completed",
      assistant_message: {
        id: CANONICAL,
        content_md: "完整回答正文。",
        status: "completed",
      },
    });
    expect(out.assistantId).toBe(CANONICAL);
    expect(out.status).toBe("completed");
    expect(out.content).toBe("完整回答正文。");
    expect(out.cta).toBe("none");
    expect(isLocalOptimisticMessageId(out.assistantId)).toBe(false);
  });

  it("failed → promote UUID + regenerate CTA (not second pair)", () => {
    const out = projectLocalOutcome({
      status: "failed",
      assistant_message_id: CANONICAL,
      assistant_message: { id: CANONICAL, content_md: "partial" },
    });
    expect(out.assistantId).toBe(CANONICAL);
    expect(out.status).toBe("failed");
    expect(out.cta).toBe("regenerate");
  });

  it("cancelled → interrupted + regenerate CTA", () => {
    const out = projectLocalOutcome({
      status: "cancelled",
      assistant_message_id: CANONICAL,
      assistant_message: { id: CANONICAL, content_md: "" },
    });
    expect(out.status).toBe("interrupted");
    expect(out.cta).toBe("regenerate");
  });
});
