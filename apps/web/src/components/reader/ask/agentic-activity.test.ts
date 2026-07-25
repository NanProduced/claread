/** @vitest-environment node */
import { describe, expect, it } from "vitest";
import {
  agenticActivityAriaLabel,
  createIdleAgenticActivityState,
  isAgenticActivityVisible,
  reduceAgenticActivityEvent,
  type AgenticActivityState,
} from "./agentic-activity";

function progress(
  sequence: number,
  phase: string,
  summary: string,
  extras: Record<string, unknown> = {},
) {
  return {
    type: "progress" as const,
    payload: {
      execution_version: "reader_record_ask_agentic_v2",
      sequence,
      phase,
      summary,
      activity: "started",
      elapsed_ms: sequence * 10,
      ...extras,
    },
  };
}

function runningState(): AgenticActivityState {
  return reduceAgenticActivityEvent(createIdleAgenticActivityState(), {
    type: "run_started",
    messageId: "msg-1",
    turnRunId: "turn-1",
  });
}

describe("reduceAgenticActivityEvent", () => {
  it("starts running on run_started", () => {
    const next = runningState();
    expect(next.status).toBe("running");
    expect(next.currentPhase).toBe("agent_running");
    expect(next.currentSummary).toBe("正在分析当前文章");
    expect(next.messageId).toBe("msg-1");
    expect(isAgenticActivityVisible(next)).toBe(true);
  });

  it("updates phase/summary by increasing sequence", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(
      state,
      progress(1, "reading_context", "正在读取文章上下文", {
        activity: "started",
        tool_name: "read_range",
        status: "running",
      }),
    );
    state = reduceAgenticActivityEvent(
      state,
      progress(2, "reading_context", "已读取相关上下文", {
        activity: "completed",
        tool_name: "read_range",
        status: "ok",
        duration_ms: 12,
      }),
    );
    expect(state.lastSequence).toBe(2);
    expect(state.currentPhase).toBe("reading_context");
    expect(state.currentSummary).toBe("已读取相关上下文");
    expect(state.currentDurationMs).toBe(12);
    expect(state.steps).toHaveLength(2);
  });

  it("ignores duplicate sequence", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(
      state,
      progress(1, "reading_context", "正在读取文章上下文"),
    );
    const next = reduceAgenticActivityEvent(
      state,
      progress(1, "composing_answer", "正在组织回答"),
    );
    expect(next).toEqual(state);
    expect(next.currentSummary).toBe("正在读取文章上下文");
  });

  it("ignores out-of-order older sequence", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(
      state,
      progress(3, "composing_answer", "正在组织回答"),
    );
    const next = reduceAgenticActivityEvent(
      state,
      progress(2, "reading_context", "正在读取文章上下文"),
    );
    expect(next).toEqual(state);
    expect(next.currentPhase).toBe("composing_answer");
  });

  it("ignores progress after completed", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(
      state,
      progress(1, "composing_answer", "正在组织回答"),
    );
    state = reduceAgenticActivityEvent(state, { type: "completed" });
    const next = reduceAgenticActivityEvent(
      state,
      progress(2, "validating_evidence", "正在核对回答依据"),
    );
    expect(next.status).toBe("completed");
    expect(next.currentSummary).toBe("正在组织回答");
    expect(isAgenticActivityVisible(next)).toBe(false);
  });

  it("stops activity on terminal failed", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(
      state,
      progress(1, "agent_running", "正在分析当前文章"),
    );
    state = reduceAgenticActivityEvent(state, {
      type: "terminal",
      finalStatus: "failed",
    });
    expect(state.status).toBe("failed");
    expect(isAgenticActivityVisible(state)).toBe(false);
    expect(agenticActivityAriaLabel(state)).toBe("本轮回答未能完成");
    const late = reduceAgenticActivityEvent(
      state,
      progress(2, "composing_answer", "正在组织回答"),
    );
    expect(late.status).toBe("failed");
    expect(late.currentPhase).toBe("agent_running");
  });

  it("marks unavailable as degraded and still allows completed", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(
      state,
      progress(1, "searching_article", "正在检索当前文章", {
        activity: "started",
        tool_name: "search_current_article",
        status: "running",
      }),
    );
    state = reduceAgenticActivityEvent(
      state,
      progress(2, "searching_article", "当前文章检索暂不可用", {
        activity: "unavailable",
        tool_name: "search_current_article",
        status: "unavailable",
      }),
    );
    expect(state.status).toBe("degraded");
    expect(state.hasUnavailable).toBe(true);
    expect(isAgenticActivityVisible(state)).toBe(true);

    state = reduceAgenticActivityEvent(
      state,
      progress(3, "composing_answer", "正在组织回答"),
    );
    state = reduceAgenticActivityEvent(state, { type: "completed" });
    expect(state.status).toBe("completed");
    expect(isAgenticActivityVisible(state)).toBe(false);
  });

  it("reset clears previous activity for a new question", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(
      state,
      progress(4, "validating_evidence", "正在核对回答依据"),
    );
    state = reduceAgenticActivityEvent(state, { type: "reset" });
    expect(state).toEqual(createIdleAgenticActivityState());
  });

  it("does not accept non-agentic execution_version payloads", () => {
    const state = runningState();
    const next = reduceAgenticActivityEvent(state, {
      type: "progress",
      payload: {
        execution_version: "legacy_v0",
        sequence: 1,
        phase: "agent_running",
        summary: "should ignore",
      },
    });
    expect(next).toEqual(state);
  });

  it("ignores progress with unknown phase or empty summary", () => {
    const state = runningState();
    const afterUnknownPhase = reduceAgenticActivityEvent(
      state,
      progress(1, "not_a_phase", "x"),
    );
    expect(afterUnknownPhase.lastSequence).toBe(0);
    const afterEmptySummary = reduceAgenticActivityEvent(
      state,
      progress(1, "agent_running", "   "),
    );
    expect(afterEmptySummary.lastSequence).toBe(0);
  });

  it("never keeps internal-looking fields from the payload", () => {
    let state = runningState();
    const dirtyPayload = {
      execution_version: "reader_record_ask_agentic_v2",
      sequence: 1,
      phase: "reading_context",
      summary: "正在读取文章上下文",
      activity: "started",
      tool_name: "read_range",
      query: "SECRET_QUERY",
      locator: { offset: 99 },
      reasoning_content: "CHAIN",
      handle_id: "HANDLE",
    } as Record<string, unknown>;
    state = reduceAgenticActivityEvent(state, {
      type: "progress",
      payload: dirtyPayload,
    });
    const serialized = JSON.stringify(state);
    expect(serialized).not.toContain("SECRET_QUERY");
    expect(serialized).not.toContain("CHAIN");
    expect(serialized).not.toContain("HANDLE");
    expect(serialized).not.toContain("locator");
    expect(state.currentSummary).toBe("正在读取文章上下文");
  });
});
