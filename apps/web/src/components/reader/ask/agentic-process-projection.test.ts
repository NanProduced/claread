/** @vitest-environment node */
import { describe, expect, it } from "vitest";

import {
  createIdleAgenticActivityState,
  reduceAgenticActivityEvent,
  type AgenticActivityEvent,
  type AgenticActivityState,
} from "./agentic-activity";
import type { AgenticCitationDisplayItem } from "./agentic-evidence";
import {
  buildAgenticProcessSnapshot,
  extractWebDomains,
  projectTurnProcess,
  TURN_PROCESS_STEP_LABELS,
  type ProcessStepView,
} from "./agentic-process-projection";

function progress(
  sequence: number,
  phase: string,
  summary: string,
  extras: Record<string, unknown> = {},
): AgenticActivityEvent {
  return {
    type: "progress",
    payload: {
      execution_version: "reader_record_ask_agentic_v2",
      sequence,
      phase,
      summary,
      activity: "started",
      elapsed_ms: sequence * 100,
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

/** Feed a scripted event sequence through the REAL reducer. */
function reduce(events: AgenticActivityEvent[]): AgenticActivityState {
  return events.reduce(
    (state, event) => reduceAgenticActivityEvent(state, event),
    runningState(),
  );
}

function stepById(steps: ProcessStepView[], id: string): ProcessStepView {
  const found = steps.find((step) => step.id === id);
  if (!found) {
    throw new Error(`missing step ${id} in ${steps.map((s) => s.id).join(",")}`);
  }
  return found;
}

function webCitation(url: string, citationId = "c1"): AgenticCitationDisplayItem {
  return {
    citationId,
    sourceKind: "web",
    title: "网页来源",
    snippet: "",
    url,
    sourceTitle: "示例页面",
    description: null,
    publishedAt: null,
    retrievedAt: "2026-07-29T00:00:00Z",
  };
}

/** Injected error/warning summaries that must never appear in CoT view. */
const INJECTED_ERROR_SUMMARIES = [
  "网页搜索暂不可用",
  "证据扩展暂不可用",
  "读取文章上下文失败",
  "provider raw error: rate_limit",
  "terminal_reason: agent_run_failed",
] as const;

function assertNoInjectedErrorCopy(serialized: string) {
  for (const injected of INJECTED_ERROR_SUMMARIES) {
    expect(serialized, `must not contain injected summary: ${injected}`).not.toContain(
      injected,
    );
  }
}

describe("step id matrix", () => {
  it("maps every wire phase to a stable step id with fixed typed labels", () => {
    const state = reduce([
      progress(1, "agent_running", "开始分析", { activity: "started", status: "running" }),
      progress(2, "reading_context", "正在读取文章上下文", {
        activity: "started",
        tool_name: "read_range",
        status: "running",
      }),
      progress(3, "reading_context", "已读取相关上下文", {
        activity: "completed",
        tool_name: "read_range",
        status: "ok",
        duration_ms: 120,
      }),
      progress(4, "searching_article", "正在检索当前文章", {
        activity: "started",
        tool_name: "search_current_article",
        status: "running",
      }),
      progress(5, "searching_article", "已检索当前文章", {
        activity: "completed",
        tool_name: "search_current_article",
        status: "ok",
        duration_ms: 90,
      }),
      progress(6, "searching_web", "正在搜索网页", {
        activity: "started",
        tool_name: "search_web",
        activity_id: "web_search",
        call_sequence: 1,
        status: "running",
      }),
      progress(7, "composing_answer", "正在组织回答", { activity: "started", status: "running" }),
      progress(8, "validating_evidence", "正在核对回答依据", { activity: "started", status: "running" }),
    ]);
    const view = projectTurnProcess({ activity: state, isStreaming: true });
    expect(view.steps.map((s) => s.id)).toEqual([
      "agent-running",
      "reading-context",
      "searching-article",
      "web-search",
      "composing-answer",
      "validating-evidence",
    ]);
    for (const step of view.steps) {
      expect(step.label).toBe(TURN_PROCESS_STEP_LABELS[step.id]);
    }
  });

  it("folds expand_evidence and unknown-tool rows into agent-running", () => {
    const state = reduce([
      // expand_evidence projects as generic agent_running (no tool_name).
      progress(1, "agent_running", "正在扩展证据", { activity: "started", status: "running" }),
      progress(2, "agent_running", "已检查文章证据", {
        activity: "completed",
        status: "ok",
        duration_ms: 40,
      }),
    ]);
    const view = projectTurnProcess({ activity: state, isStreaming: true });
    expect(view.steps).toHaveLength(1);
    expect(view.steps[0]?.id).toBe("agent-running");
    // Fixed typed label — never intermediate or result summary copy.
    expect(view.steps[0]?.label).toBe("分析问题");
    expect(view.steps[0]?.status).toBe("complete");
  });
});

describe("explicit-result tool steps", () => {
  it("read_range started → composing started (no result): reading-context is interrupted", () => {
    const state = reduce([
      progress(1, "reading_context", "正在读取文章上下文", {
        activity: "started",
        tool_name: "read_range",
        status: "running",
      }),
      // No result — the agent proceeds straight to composing.
      progress(2, "composing_answer", "正在组织回答", { activity: "started", status: "running" }),
    ]);
    const view = projectTurnProcess({ activity: state, isStreaming: true });
    expect(stepById(view.steps, "reading-context").status).toBe("interrupted");
    expect(stepById(view.steps, "reading-context").label).toBe("读取文章");
    expect(stepById(view.steps, "composing-answer").status).toBe("active");
    expect(stepById(view.steps, "composing-answer").label).toBe("组织回答");
  });

  it("read_range started → completed/ok result: reading-context is complete", () => {
    const state = reduce([
      progress(1, "reading_context", "正在读取文章上下文", {
        activity: "started",
        tool_name: "read_range",
        status: "running",
      }),
      progress(2, "reading_context", "已读取相关上下文", {
        activity: "completed",
        tool_name: "read_range",
        status: "ok",
        duration_ms: 120,
      }),
    ]);
    const view = projectTurnProcess({ activity: state, isStreaming: true });
    const step = stepById(view.steps, "reading-context");
    expect(step.status).toBe("complete");
    expect(step.label).toBe("读取文章");
    expect(step.durationMs).toBe(120);
  });

  it("started-only tool step + message.completed must not infer complete", () => {
    const state = reduce([
      progress(1, "reading_context", "正在读取文章上下文", {
        activity: "started",
        tool_name: "read_range",
        status: "running",
      }),
    ]);
    const done = reduceAgenticActivityEvent(state, { type: "completed" });
    const view = projectTurnProcess({ activity: done });
    expect(stepById(view.steps, "reading-context").status).toBe("interrupted");
    expect(stepById(view.steps, "reading-context").label).toBe("读取文章");
    expect(view.header.state).toBe("settled");
  });

  it("search_current_article started-only is never complete on supersession or terminal", () => {
    const live = reduce([
      progress(1, "searching_article", "正在检索当前文章", {
        activity: "started",
        tool_name: "search_current_article",
        status: "running",
      }),
      progress(2, "composing_answer", "正在组织回答", { activity: "started", status: "running" }),
    ]);
    expect(
      stepById(projectTurnProcess({ activity: live, isStreaming: true }).steps, "searching-article")
        .status,
    ).toBe("interrupted");

    const done = reduceAgenticActivityEvent(live, { type: "completed" });
    expect(stepById(projectTurnProcess({ activity: done }).steps, "searching-article").status).toBe(
      "interrupted",
    );
  });

  it("web-search started-only is never complete without ok result", () => {
    const state = reduce([
      progress(1, "searching_web", "正在搜索网页", {
        activity: "started",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "running",
      }),
      progress(2, "composing_answer", "正在组织回答", { activity: "started", status: "running" }),
    ]);
    const view = projectTurnProcess({ activity: state, isStreaming: true });
    expect(stepById(view.steps, "web-search").status).toBe("interrupted");
    expect(stepById(view.steps, "web-search").label).toBe("网页查询");
  });

  it("last-wins status lets a retried tool end as complete (not failed)", () => {
    const state = reduce([
      progress(1, "reading_context", "正在读取文章上下文", {
        activity: "started",
        tool_name: "read_range",
        status: "running",
      }),
      progress(2, "reading_context", "读取文章上下文失败", {
        activity: "failed",
        tool_name: "read_range",
        status: "failed",
        duration_ms: 60,
      }),
      progress(3, "reading_context", "正在读取文章上下文", {
        activity: "started",
        tool_name: "read_range",
        status: "running",
      }),
      progress(4, "reading_context", "已读取相关上下文", {
        activity: "completed",
        tool_name: "read_range",
        status: "ok",
        duration_ms: 130,
      }),
    ]);
    const view = projectTurnProcess({ activity: state, isStreaming: true });
    const step = stepById(view.steps, "reading-context");
    expect(step.status).toBe("complete");
    expect(step.label).toBe("读取文章");
    expect(step.durationMs).toBe(130);
    assertNoInjectedErrorCopy(JSON.stringify(view));
  });

  it("drops duplicate and out-of-order progress via the reducer", () => {
    const state = reduce([
      progress(2, "reading_context", "正在读取文章上下文", {
        activity: "started",
        tool_name: "read_range",
        status: "running",
      }),
      // Duplicate sequence — dropped.
      progress(2, "reading_context", "重复事件", {
        activity: "started",
        tool_name: "read_range",
        status: "running",
      }),
      // Out-of-order — dropped.
      progress(1, "searching_article", "乱序事件", {
        activity: "started",
        tool_name: "search_current_article",
        status: "running",
      }),
    ]);
    const view = projectTurnProcess({ activity: state, isStreaming: true });
    expect(view.steps).toHaveLength(1);
    expect(view.steps[0]?.id).toBe("reading-context");
    expect(view.steps[0]?.label).toBe("读取文章");
  });

  it("reseeds the whole matrix on retry (run_started)", () => {
    let state = reduce([
      progress(1, "reading_context", "正在读取文章上下文", {
        activity: "started",
        tool_name: "read_range",
        status: "running",
      }),
    ]);
    state = reduceAgenticActivityEvent(state, {
      type: "run_started",
      messageId: "msg-1",
      turnRunId: "turn-2",
    });
    const view = projectTurnProcess({ activity: state, isStreaming: true });
    expect(view.steps).toHaveLength(0);
    // Fixed live label for agent_running phase after run_started.
    expect(view.header.liveSummary).toBe("分析问题");
  });

  it("freezes steps after a terminal — late progress never reactivates", () => {
    let state = reduce([
      progress(1, "reading_context", "正在读取文章上下文", {
        activity: "started",
        tool_name: "read_range",
        status: "running",
      }),
    ]);
    state = reduceAgenticActivityEvent(state, { type: "completed" });
    // Late progress after terminal — reducer drops it.
    state = reduceAgenticActivityEvent(
      state,
      progress(2, "searching_web", "正在搜索网页", {
        activity: "started",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "running",
      }),
    );
    const view = projectTurnProcess({ activity: state });
    expect(view.steps).toHaveLength(1);
    // Explicit-result tool without ok result → interrupted, not complete.
    expect(stepById(view.steps, "reading-context").status).toBe("interrupted");
    expect(view.header.state).toBe("settled");
  });
});

describe("inferred-result host stages", () => {
  it("composing completes when validating starts; validating only on turn completed", () => {
    const live = reduce([
      progress(1, "composing_answer", "正在组织回答", { activity: "started", status: "running" }),
      progress(2, "validating_evidence", "正在核对回答依据", { activity: "started", status: "running" }),
    ]);
    const liveView = projectTurnProcess({ activity: live, isStreaming: true });
    expect(stepById(liveView.steps, "composing-answer").status).toBe("complete");
    expect(stepById(liveView.steps, "validating-evidence").status).toBe("active");
    expect(stepById(liveView.steps, "composing-answer").label).toBe("组织回答");
    expect(stepById(liveView.steps, "validating-evidence").label).toBe("核对依据");

    const done = reduceAgenticActivityEvent(live, { type: "completed" });
    const doneView = projectTurnProcess({ activity: done });
    expect(stepById(doneView.steps, "validating-evidence").status).toBe("complete");
  });

  it("turn completed completes residual running host stages only", () => {
    const state = reduce([
      progress(1, "agent_running", "开始分析", { activity: "started", status: "running" }),
    ]);
    const done = reduceAgenticActivityEvent(state, { type: "completed" });
    const view = projectTurnProcess({ activity: done });
    expect(stepById(view.steps, "agent-running").status).toBe("complete");
    expect(stepById(view.steps, "agent-running").label).toBe("分析问题");
  });
});

describe("terminal semantics", () => {
  it("non-ok terminal marks running steps interrupted — never complete", () => {
    const base = reduce([
      progress(1, "reading_context", "已读取相关上下文", {
        activity: "completed",
        tool_name: "read_range",
        status: "ok",
        duration_ms: 100,
      }),
      progress(2, "composing_answer", "正在组织回答", { activity: "started", status: "running" }),
    ]);
    const failed = reduceAgenticActivityEvent(base, {
      type: "terminal",
      finalStatus: "failed",
    });
    const view = projectTurnProcess({ activity: failed });
    expect(stepById(view.steps, "reading-context").status).toBe("complete");
    expect(stepById(view.steps, "composing-answer").status).toBe("interrupted");
    expect(view.header.state).toBe("settled");
    expect(view.ariaLabel).toBe("本轮回答未能完成");

    const cancelled = reduceAgenticActivityEvent(base, {
      type: "terminal",
      finalStatus: "cancelled",
    });
    expect(projectTurnProcess({ activity: cancelled }).ariaLabel).toBe("本轮回答已取消");
  });

  it("tool-level failure keeps the turn live; label stays fixed (no error copy)", () => {
    const state = reduce([
      progress(1, "reading_context", "读取文章上下文失败", {
        activity: "failed",
        tool_name: "read_range",
        status: "failed",
        duration_ms: 55,
      }),
    ]);
    const view = projectTurnProcess({ activity: state, isStreaming: true });
    expect(view.header.state).toBe("running");
    expect(stepById(view.steps, "reading-context").status).toBe("failed");
    expect(stepById(view.steps, "reading-context").label).toBe("读取文章");
    // aria uses fixed phase label — never injected error summary.
    expect(view.ariaLabel).toBe("读取文章");
    assertNoInjectedErrorCopy(JSON.stringify(view));
  });

  it("unavailable renders degraded with fixed neutral label — no warning copy", () => {
    const state = reduce([
      progress(1, "searching_web", "网页搜索暂不可用", {
        activity: "unavailable",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "unavailable",
        duration_ms: 30,
      }),
    ]);
    const done = reduceAgenticActivityEvent(state, { type: "completed" });
    const view = projectTurnProcess({ activity: done });
    const step = stepById(view.steps, "web-search");
    expect(step.status).toBe("degraded");
    expect(step.label).toBe("网页查询");
    const serialized = JSON.stringify(view);
    assertNoInjectedErrorCopy(serialized);
    expect(serialized).not.toContain("警告");
    expect(serialized).not.toContain("warning");
  });

  it("failed tool with inject error summary never leaks into snapshot or view", () => {
    const state = reduce([
      progress(1, "reading_context", "读取文章上下文失败", {
        activity: "failed",
        tool_name: "read_range",
        status: "failed",
        duration_ms: 40,
      }),
      progress(2, "searching_web", "网页搜索暂不可用", {
        activity: "unavailable",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "unavailable",
        duration_ms: 20,
      }),
      progress(3, "agent_running", "证据扩展暂不可用", {
        activity: "unavailable",
        status: "unavailable",
        duration_ms: 10,
      }),
    ]);
    const done = reduceAgenticActivityEvent(state, { type: "completed" });
    const view = projectTurnProcess({ activity: done });
    const snapshot = buildAgenticProcessSnapshot(done);
    expect(stepById(view.steps, "reading-context").status).toBe("failed");
    expect(stepById(view.steps, "web-search").status).toBe("degraded");
    expect(stepById(view.steps, "agent-running").status).toBe("degraded");
    for (const step of view.steps) {
      expect(step.label).toBe(TURN_PROCESS_STEP_LABELS[step.id]);
    }
    assertNoInjectedErrorCopy(JSON.stringify(view));
    assertNoInjectedErrorCopy(JSON.stringify(snapshot));
    // Snapshot must not retain server summary field.
    expect(JSON.stringify(snapshot)).not.toContain('"summary"');
  });
});

describe("web search step", () => {
  it("upserts multiple attempts into one step with authoritative counts", () => {
    const state = reduce([
      progress(1, "searching_web", "正在搜索网页", {
        activity: "started",
        tool_name: "search_web",
        activity_id: "web_search",
        call_sequence: 1,
        attempt_count: null,
        status: "running",
      }),
      progress(2, "searching_web", "未找到相关网页结果", {
        activity: "completed",
        tool_name: "search_web",
        activity_id: "web_search",
        call_sequence: 1,
        attempt_count: 1,
        status: "ok",
        duration_ms: 900,
      }),
      progress(3, "searching_web", "正在搜索网页", {
        activity: "started",
        tool_name: "search_web",
        activity_id: "web_search",
        call_sequence: 2,
        status: "running",
      }),
      progress(4, "searching_web", "已完成网页搜索", {
        activity: "completed",
        tool_name: "search_web",
        activity_id: "web_search",
        call_sequence: 2,
        attempt_count: 2,
        status: "ok",
        duration_ms: 1100,
      }),
    ]);
    const view = projectTurnProcess({ activity: state, isStreaming: true });
    const webSteps = view.steps.filter((s) => s.id === "web-search");
    expect(webSteps).toHaveLength(1);
    expect(webSteps[0]?.attempts).toBe("已尝试 2 次");
    expect(webSteps[0]?.status).toBe("complete");
    expect(webSteps[0]?.label).toBe("网页查询");
  });

  it("falls back to call sequence when attempt count is 1", () => {
    const state = reduce([
      progress(1, "searching_web", "正在搜索网页", {
        activity: "started",
        tool_name: "search_web",
        activity_id: "web_search",
        call_sequence: 2,
        attempt_count: 1,
        status: "running",
      }),
    ]);
    const view = projectTurnProcess({ activity: state, isStreaming: true });
    expect(stepById(view.steps, "web-search").attempts).toBe("已调用 2 次");
  });

  it("domains appear only post-completed, hostname-only, deduped", () => {
    const citations = [
      webCitation("https://www.example.com/page?q=1", "c1"),
      webCitation("https://example.com/other", "c2"),
      webCitation("https://docs.site.org/guide", "c3"),
      { ...webCitation("https://ignored.article", "c4"), sourceKind: "article" as const, url: null },
      webCitation("not a url", "c5"),
    ];
    const state = reduce([
      progress(1, "searching_web", "已完成网页搜索", {
        activity: "completed",
        tool_name: "search_web",
        activity_id: "web_search",
        attempt_count: 1,
        status: "ok",
        duration_ms: 800,
      }),
    ]);
    const done = reduceAgenticActivityEvent(state, { type: "completed" });

    const completedView = projectTurnProcess({ activity: done, citations });
    expect(stepById(completedView.steps, "web-search").domains).toEqual([
      "example.com",
      "docs.site.org",
    ]);

    // Live turn with the same citations: no domains yet.
    const liveView = projectTurnProcess({ activity: state, citations, isStreaming: true });
    expect(stepById(liveView.steps, "web-search").domains).toEqual([]);

    // Non-ok terminal: citations are null server-side; no domains.
    const failed = reduceAgenticActivityEvent(state, { type: "terminal", finalStatus: "failed" });
    expect(stepById(projectTurnProcess({ activity: failed }).steps, "web-search").domains)
      .toEqual([]);
  });

  it("extractWebDomains caps with a +N overflow chip", () => {
    const many = Array.from({ length: 11 }, (_, i) =>
      webCitation(`https://site-${i}.example.net/`, `c${i}`),
    );
    const domains = extractWebDomains(many);
    expect(domains).toHaveLength(9);
    expect(domains[8]).toBe("+3");
  });
});

describe("visibility and reasoning", () => {
  it("renders nothing for empty state (no shell, no placeholder)", () => {
    const view = projectTurnProcess({ activity: createIdleAgenticActivityState() });
    expect(view.visible).toBe(false);
    expect(view.steps).toEqual([]);
    expect(view.reasoning).toBeNull();
  });

  it("is visible while streaming even before the first event", () => {
    const view = projectTurnProcess({
      activity: createIdleAgenticActivityState(),
      isStreaming: true,
    });
    expect(view.visible).toBe(true);
    expect(view.header.state).toBe("running");
    expect(view.header.liveSummary).toBeNull();
    expect(view.ariaLabel).toBe("Ask Claread 正在工作");
  });

  it("reasoning-only settled turn stays visible (snapshot-driven)", () => {
    const done = reduceAgenticActivityEvent(runningState(), { type: "completed" });
    const snapshot = buildAgenticProcessSnapshot(done);
    expect(snapshot).not.toBeNull();
    expect(snapshot?.status).toBe("completed");
    const view = projectTurnProcess({
      snapshot,
      reasoningMd: "已脱敏的思考文本",
      reasoningStatus: "completed",
      reasoningTruncated: false,
    });
    expect(view.visible).toBe(true);
    expect(view.steps).toEqual([]);
    expect(view.reasoning).toEqual({
      text: "已脱敏的思考文本",
      truncated: false,
      streaming: false,
    });
    expect(view.header.state).toBe("settled");
    expect(view.header.titleHint).toBe("thinking");
    expect(view.ariaLabel).toBe("本轮回答已完成");
  });

  it("a snapshot frozen mid-run (stale EOF) settles as interrupted, not live", () => {
    const midRun = reduce([
      progress(1, "reading_context", "正在读取文章上下文", {
        activity: "started",
        tool_name: "read_range",
        status: "running",
      }),
    ]);
    const snapshot = buildAgenticProcessSnapshot(midRun);
    const view = projectTurnProcess({ snapshot });
    expect(view.header.state).toBe("settled");
    expect(stepById(view.steps, "reading-context").status).toBe("interrupted");
    expect(stepById(view.steps, "reading-context").label).toBe("读取文章");
    expect(view.ariaLabel).toBe("本轮回答未能完成");
  });

  it("cold history without snapshot renders reasoning-only", () => {
    const view = projectTurnProcess({
      reasoningMd: "cold reasoning",
      reasoningStatus: "completed",
      reasoningTruncated: true,
    });
    expect(view.visible).toBe(true);
    expect(view.reasoning?.truncated).toBe(true);
    expect(view.header.titleHint).toBe("thinking");
    expect(view.header.state).toBe("settled");
    expect(view.steps).toEqual([]);
  });

  it("titleHint is processing when settled without reasoning", () => {
    const state = reduce([
      progress(1, "agent_running", "分析完成", { activity: "completed", status: "ok" }),
    ]);
    const done = reduceAgenticActivityEvent(state, { type: "completed" });
    const view = projectTurnProcess({ activity: done });
    expect(view.header.titleHint).toBe("processing");
    expect(view.header.durationS).toBe(1);
  });

  it("durationS is null when no elapsed time is known", () => {
    const snapshot = buildAgenticProcessSnapshot(runningState());
    const view = projectTurnProcess({
      snapshot,
      reasoningMd: "x",
      reasoningStatus: "completed",
    });
    expect(view.header.durationS).toBeNull();
  });
});

describe("leak allowlist and snapshot internals", () => {
  it("view model never carries ids, sequences, tool names, handles, or summaries", () => {
    const state = reduce([
      progress(1, "searching_web", "正在搜索网页", {
        activity: "started",
        tool_name: "search_web",
        activity_id: "web_search",
        call_sequence: 1,
        status: "running",
      }),
      progress(2, "searching_web", "已完成网页搜索", {
        activity: "completed",
        tool_name: "search_web",
        activity_id: "web_search",
        call_sequence: 1,
        attempt_count: 1,
        status: "ok",
        duration_ms: 700,
      }),
    ]);
    const done = reduceAgenticActivityEvent(state, { type: "completed" });
    const view = projectTurnProcess({
      activity: done,
      citations: [webCitation("https://example.com/")],
      reasoningMd: "safe text",
      reasoningStatus: "completed",
    });
    const serialized = JSON.stringify(view);
    for (const leaked of [
      "sequence",
      "toolName",
      "tool_name",
      "activityId",
      "activity_id",
      "turnRunId",
      "turn_run_id",
      "messageId",
      "message_id",
      "terminal_reason",
      "evh_",
      "fingerprint",
      "https://",
      "已完成网页搜索",
      "正在搜索网页",
    ]) {
      expect(serialized, `view model must not contain ${leaked}`).not.toContain(leaked);
    }
  });

  it("snapshot builder keeps internal control fields but drops summary and run ids", () => {
    expect(buildAgenticProcessSnapshot(createIdleAgenticActivityState())).toBeNull();
    const withSteps = reduce([
      progress(1, "reading_context", "读取文章上下文失败", {
        activity: "failed",
        tool_name: "read_range",
        status: "failed",
        duration_ms: 50,
      }),
    ]);
    const done = reduceAgenticActivityEvent(withSteps, { type: "completed" });
    const snapshot = buildAgenticProcessSnapshot(done);
    expect(snapshot?.execution_version).toBe("reader_record_ask_agentic_v2");
    expect(snapshot?.status).toBe("completed");
    expect(snapshot?.steps).toHaveLength(1);
    const step = snapshot?.steps[0];
    expect(step).toMatchObject({
      sequence: 1,
      phase: "reading_context",
      activity: "failed",
      toolName: "read_range",
      status: "failed",
      durationMs: 50,
    });
    // summary is intentionally absent (fixed labels at project time).
    expect(step).not.toHaveProperty("summary");
    const serialized = JSON.stringify(snapshot);
    expect(serialized).not.toContain("turnRunId");
    expect(serialized).not.toContain("turn-1");
    expect(serialized).not.toContain("messageId");
    expect(serialized).not.toContain("读取文章上下文失败");
    expect(serialized).not.toContain('"summary"');
    // Internal control fields may exist on the snapshot itself (not on the view).
    expect(serialized).toContain("toolName");
    expect(serialized).toContain("sequence");
  });
});
