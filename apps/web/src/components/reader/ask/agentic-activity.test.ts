/** @vitest-environment node */
import { describe, expect, it } from "vitest";
import {
  aggregateArticleEvidenceOutcome,
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
  it("binds a run without fabricating a learner step", () => {
    const next = runningState();
    expect(next.status).toBe("running");
    expect(next.currentPhase).toBeNull();
    expect(next.currentSummary).toBeNull();
    expect(next.steps).toEqual([]);
    expect(next.messageId).toBe("msg-1");
    expect(isAgenticActivityVisible(next)).toBe(true);
  });

  it("accepts only real analysis lifecycle rows for 分析问题", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(state, progress(1, "analysis", "正在分析问题", {
      activity: "started",
      status: "running",
    }));
    expect(state.steps).toHaveLength(1);
    expect(state.steps[0].phase).toBe("analysis");

    state = reduceAgenticActivityEvent(state, progress(2, "analysis", "已完成问题分析", {
      activity: "completed",
      status: "ok",
    }));
    expect(state.steps[0]).toMatchObject({
      phase: "analysis",
      activity: "completed",
      status: "ok",
    });
  });

  it("starts and settles answering only from explicit answer lifecycle events", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(state, { type: "answer_started", generationId: 0 });
    expect(state.steps).toHaveLength(1);
    expect(state.steps[0]).toMatchObject({
      phase: "composing_answer",
      activity: "started",
      status: "running",
    });

    state = reduceAgenticActivityEvent(state, { type: "answer_completed" });
    expect(state.steps[0]).toMatchObject({
      phase: "composing_answer",
      activity: "completed",
      status: "ok",
    });
  });

  it("keeps server progress sequence separate from synthetic answer order", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(
      state,
      progress(1, "analysis", "正在分析问题", {
        activity: "started",
        status: "running",
      }),
    );
    const analysisOrdinal = state.steps[0].localOrdinal;

    state = reduceAgenticActivityEvent(state, {
      type: "answer_started",
      generationId: 0,
    });
    expect(state.lastSequence).toBe(1);
    expect(state.steps[1].localOrdinal).toBeGreaterThan(analysisOrdinal);

    state = reduceAgenticActivityEvent(state, { type: "answer_completed" });
    expect(state.lastSequence).toBe(1);

    state = reduceAgenticActivityEvent(
      state,
      progress(2, "searching_web", "正在查询网页", {
        activity: "started",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "running",
      }),
    );
    expect(state.lastSequence).toBe(2);
    expect(state.steps.map((step) => step.phase)).toEqual([
      "analysis",
      "composing_answer",
      "searching_web",
    ]);

    const duplicate = reduceAgenticActivityEvent(
      state,
      progress(2, "searching_article", "不应覆盖网页步骤", {
        activity: "started",
        activity_id: "article_evidence",
        status: "running",
      }),
    );
    expect(duplicate).toEqual(state);
  });

  it("allows a new generation to recreate answering without consuming progress sequence", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(state, {
      type: "answer_started",
      generationId: 0,
    });
    state = reduceAgenticActivityEvent(state, {
      type: "answer_completed",
    });
    state = reduceAgenticActivityEvent(state, {
      type: "answer_started",
      generationId: 1,
    });
    expect(state.lastSequence).toBe(0);
    expect(state.steps[0].activity).toBe("started");

    state = reduceAgenticActivityEvent(
      state,
      progress(1, "searching_article", "正在查找文章依据", {
        activity: "started",
        tool_name: "search_current_article",
        activity_id: "article_evidence",
        status: "running",
      }),
    );
    expect(state.lastSequence).toBe(1);
    expect(state.steps.map((step) => step.activityId)).toEqual([
      null,
      "article_evidence",
    ]);
  });

  it("upserts the article evidence activity across both production tools", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(state, progress(1, "searching_article", "正在查找文章依据", {
      activity: "started",
      tool_name: "expand_evidence",
      activity_id: "article_evidence",
      status: "running",
    }));
    state = reduceAgenticActivityEvent(state, progress(2, "searching_article", "已扩展证据", {
      activity: "completed",
      tool_name: "expand_evidence",
      activity_id: "article_evidence",
      status: "ok",
    }));
    state = reduceAgenticActivityEvent(state, progress(3, "searching_article", "正在查找文章依据", {
      activity: "started",
      tool_name: "search_current_article",
      activity_id: "article_evidence",
      status: "running",
    }));
    expect(state.steps).toHaveLength(1);
    expect(state.steps[0]).toMatchObject({
      activityId: "article_evidence",
      toolName: "search_current_article",
      status: "running",
    });
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
      progress(3, "analysis", "正在分析问题"),
    );
    const next = reduceAgenticActivityEvent(
      state,
      progress(2, "reading_context", "正在读取文章上下文"),
    );
    expect(next).toEqual(state);
    expect(next.currentPhase).toBe("analysis");
  });

  it("ignores progress after completed", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(
      state,
      { type: "answer_started", generationId: 0 },
    );
    state = reduceAgenticActivityEvent(state, { type: "completed" });
    const next = reduceAgenticActivityEvent(
      state,
      progress(2, "validating_evidence", "正在核对回答依据"),
    );
    expect(next.status).toBe("completed");
    expect(next.currentSummary).toBe("正在生成回答");
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

// ---------------------------------------------------------------------------
// searching_web phase + search_web tool projection
//
// The web search phase and tool are user-visible progress signals. They must
// be accepted by the reducer (whitelisted in PHASES / TOOLS) and projected
// like any other phase/tool — no special handling, no internal data leak.
// ---------------------------------------------------------------------------

describe("reduceAgenticActivityEvent — searching_web phase + search_web tool", () => {
  it("accepts searching_web as a valid phase", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(
      state,
      progress(1, "searching_web", "正在联网搜索", {
        activity: "started",
        tool_name: "search_web",
        status: "running",
      }),
    );
    expect(state.lastSequence).toBe(1);
    expect(state.currentPhase).toBe("searching_web");
    expect(state.currentSummary).toBe("正在联网搜索");
    expect(state.currentToolName).toBe("search_web");
    expect(state.currentStatus).toBe("running");
    expect(isAgenticActivityVisible(state)).toBe(true);
  });

  it("accepts search_web as a valid tool_name", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(
      state,
      progress(1, "searching_web", "正在联网搜索", {
        activity: "started",
        tool_name: "search_web",
        status: "running",
      }),
    );
    expect(state.currentToolName).toBe("search_web");
    expect(state.steps).toHaveLength(1);
    expect(state.steps[0].toolName).toBe("search_web");
  });

  it("completes a searching_web step without leaving the running state", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(
      state,
      progress(1, "searching_web", "正在联网搜索", {
        activity: "started",
        tool_name: "search_web",
        status: "running",
      }),
    );
    state = reduceAgenticActivityEvent(
      state,
      progress(2, "searching_web", "已检索网页来源", {
        activity: "completed",
        tool_name: "search_web",
        status: "ok",
        duration_ms: 240,
      }),
    );
    expect(state.status).toBe("running");
    expect(state.currentActivity).toBe("completed");
    expect(state.currentStatus).toBe("ok");
    expect(state.currentDurationMs).toBe(240);
    expect(state.currentToolName).toBe("search_web");
  });

  it("marks the activity as degraded when searching_web returns unavailable", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(
      state,
      progress(1, "searching_web", "正在联网搜索", {
        activity: "started",
        tool_name: "search_web",
        status: "running",
      }),
    );
    state = reduceAgenticActivityEvent(
      state,
      progress(2, "searching_web", "网页搜索暂不可用", {
        activity: "unavailable",
        tool_name: "search_web",
        status: "unavailable",
      }),
    );
    expect(state.status).toBe("degraded");
    expect(state.hasUnavailable).toBe(true);
    expect(isAgenticActivityVisible(state)).toBe(true);
  });

  it("treats search_web tool-level failure as non-terminal (agent may continue)", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(
      state,
      progress(1, "searching_web", "正在联网搜索", {
        activity: "started",
        tool_name: "search_web",
        status: "running",
      }),
    );
    state = reduceAgenticActivityEvent(
      state,
      progress(2, "searching_web", "网页搜索失败", {
        activity: "failed",
        tool_name: "search_web",
        status: "failed",
      }),
    );
    // Tool-level failure does not flip the whole turn to failed — agent
    // may still compose an answer from article context.
    expect(state.status).toBe("running");
    expect(state.currentActivity).toBe("failed");
    expect(state.currentStatus).toBe("failed");
  });

  it("transitions from searching_web to composing_answer", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(
      state,
      progress(1, "searching_web", "正在联网搜索", {
        activity: "started",
        tool_name: "search_web",
        status: "running",
      }),
    );
    state = reduceAgenticActivityEvent(state, {
      type: "answer_started",
      generationId: 0,
    });
    expect(state.currentPhase).toBe("composing_answer");
    expect(state.currentToolName).toBeNull();
    expect(state.steps).toHaveLength(2);
    expect(state.steps[0].phase).toBe("searching_web");
    expect(state.steps[1].phase).toBe("composing_answer");
  });

  it("never keeps internal-looking fields from a search_web payload", () => {
    let state = runningState();
    const dirtyPayload = {
      execution_version: "reader_record_ask_agentic_v2",
      sequence: 1,
      phase: "searching_web",
      summary: "正在联网搜索",
      activity: "started",
      tool_name: "search_web",
      query: "SECRET_WEB_QUERY",
      provider: "SECRET_PROVIDER",
      result_count: 99,
      handle_id: "WEB_HANDLE",
    } as Record<string, unknown>;
    state = reduceAgenticActivityEvent(state, {
      type: "progress",
      payload: dirtyPayload,
    });
    const serialized = JSON.stringify(state);
    expect(serialized).not.toContain("SECRET_WEB_QUERY");
    expect(serialized).not.toContain("SECRET_PROVIDER");
    expect(serialized).not.toContain("result_count");
    expect(serialized).not.toContain("WEB_HANDLE");
    expect(state.currentSummary).toBe("正在联网搜索");
    expect(state.currentToolName).toBe("search_web");
  });

  it("rejects unknown tool_name even when phase is searching_web", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(
      state,
      progress(1, "searching_web", "正在联网搜索", {
        activity: "started",
        tool_name: "search_the_internet",
        status: "running",
      }),
    );
    // Phase accepted, but unknown tool_name coerced to null.
    expect(state.currentPhase).toBe("searching_web");
    expect(state.currentToolName).toBeNull();
  });

  it("accepts searching_web without a tool_name (tool-less progress)", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(
      state,
      progress(1, "searching_web", "正在联网搜索"),
    );
    expect(state.currentPhase).toBe("searching_web");
    expect(state.currentToolName).toBeNull();
    expect(state.currentActivity).toBe("started");
  });

  it("upserts all web-search attempts into one stable activity_id step", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(
      state,
      progress(1, "searching_web", "正在联网搜索", {
        activity: "started",
        tool_name: "search_web",
        status: "running",
        activity_id: "web_search",
        attempt_count: null,
        call_sequence: 1,
      }),
    );
    state = reduceAgenticActivityEvent(
      state,
      progress(2, "searching_web", "未找到可用网页来源", {
        activity: "completed",
        tool_name: "search_web",
        status: "ok",
        activity_id: "web_search",
        attempt_count: 1,
        call_sequence: 1,
      }),
    );
    state = reduceAgenticActivityEvent(
      state,
      progress(3, "searching_web", "正在联网搜索", {
        activity: "started",
        tool_name: "search_web",
        status: "running",
        activity_id: "web_search",
        attempt_count: null,
        call_sequence: 2,
      }),
    );
    expect(state.steps[0].attemptCount).toBe(1);
    state = reduceAgenticActivityEvent(
      state,
      progress(4, "searching_web", "已检索网页来源", {
        activity: "completed",
        tool_name: "search_web",
        status: "ok",
        activity_id: "web_search",
        attempt_count: 2,
        call_sequence: 2,
        duration_ms: 240,
      }),
    );

    expect(state.steps).toHaveLength(1);
    expect(state.steps[0]).toMatchObject({
      activityId: "web_search",
      attemptCount: 2,
      callSequence: 2,
      summary: "已检索网页来源",
      durationMs: 240,
    });
  });

  it("never regresses a confirmed web-search attempt count", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(
      state,
      progress(1, "searching_web", "已完成网页搜索", {
        activity: "completed",
        tool_name: "search_web",
        status: "ok",
        activity_id: "web_search",
        attempt_count: 2,
        call_sequence: 2,
      }),
    );
    state = reduceAgenticActivityEvent(
      state,
      progress(2, "searching_web", "网页搜索暂不可用", {
        activity: "unavailable",
        tool_name: "search_web",
        status: "unavailable",
        activity_id: "web_search",
        attempt_count: 1,
        call_sequence: 1,
      }),
    );

    expect(state.steps).toHaveLength(1);
    expect(state.steps[0].attemptCount).toBe(2);
    expect(state.steps[0].callSequence).toBe(2);
  });

  it("uses the latest non-null Host aggregate for web outcome", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(
      state,
      progress(1, "searching_web", "started", {
        activity: "started",
        tool_name: "search_web",
        status: "running",
        outcome: null,
        activity_id: "web_search",
      }),
    );
    state = reduceAgenticActivityEvent(
      state,
      progress(2, "searching_web", "result", {
        activity: "completed",
        tool_name: "search_web",
        status: "ok",
        outcome: "empty",
        activity_id: "web_search",
      }),
    );
    state = reduceAgenticActivityEvent(
      state,
      progress(3, "searching_web", "late degraded result", {
        activity: "unavailable",
        tool_name: "search_web",
        status: "unavailable",
        outcome: "degraded",
        activity_id: "web_search",
      }),
    );

    expect(state.steps[0]).toMatchObject({
      outcome: "degraded",
      activity: "unavailable",
      status: "unavailable",
    });

    state = reduceAgenticActivityEvent(
      state,
      progress(4, "searching_web", "successful retry", {
        activity: "completed",
        tool_name: "search_web",
        status: "ok",
        outcome: "success",
        activity_id: "web_search",
      }),
    );
    expect(state.steps[0].outcome).toBe("success");

    state = reduceAgenticActivityEvent(
      state,
      progress(5, "searching_web", "started again", {
        activity: "started",
        tool_name: "search_web",
        status: "running",
        outcome: null,
        activity_id: "web_search",
      }),
    );
    expect(state.steps[0].outcome).toBe("success");
  });

  it("does not let a lower or duplicate web sequence overwrite the Host result", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(
      state,
      progress(2, "searching_web", "网页超时", {
        activity: "unavailable",
        tool_name: "search_web",
        status: "unavailable",
        outcome: "degraded",
        activity_id: "web_search",
      }),
    );
    const older = reduceAgenticActivityEvent(
      state,
      progress(1, "searching_web", "旧的空结果", {
        activity: "completed",
        tool_name: "search_web",
        status: "ok",
        outcome: "empty",
        activity_id: "web_search",
      }),
    );
    expect(older).toEqual(state);
    const duplicate = reduceAgenticActivityEvent(
      state,
      progress(2, "searching_web", "重复空结果", {
        activity: "completed",
        tool_name: "search_web",
        status: "ok",
        outcome: "empty",
        activity_id: "web_search",
      }),
    );
    expect(duplicate).toEqual(state);
  });

  it.each([
    [["empty", "degraded"], "degraded"],
    [["empty", "failed"], "degraded"],
    [["success", "failed"], "success"],
    [["empty", "empty"], "empty"],
    [["failed", "failed"], "failed"],
    [["unknown"], "degraded"],
  ] as const)("aggregates article evidence %j as %s", (observations, expected) => {
    expect(aggregateArticleEvidenceOutcome(observations)).toBe(expected);
  });

  it("keeps article confirmed outcome when a started/null row arrives", () => {
    let state = runningState();
    state = reduceAgenticActivityEvent(
      state,
      progress(1, "searching_article", "文章依据已找到", {
        activity: "completed",
        tool_name: "search_current_article",
        activity_id: "article_evidence",
        status: "ok",
        outcome: "success",
      }),
    );
    state = reduceAgenticActivityEvent(
      state,
      progress(2, "searching_article", "继续查找文章依据", {
        activity: "started",
        tool_name: "expand_evidence",
        activity_id: "article_evidence",
        status: "running",
        outcome: null,
      }),
    );
    expect(state.steps[0].outcome).toBe("success");
  });
});
