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

describe("visible step filtering and labels", () => {
  it("live turn shows the host 理解问题 step plus user-visible wire steps in canonical order", () => {
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
    // R3: 理解问题 (host) + reading-context / web-search / composing-answer
    // (wire), canonical learner order; internals never appear.
    expect(view.steps.map((s) => s.id)).toEqual([
      "understanding-question",
      "reading-context",
      "web-search",
      "composing-answer",
    ]);
    // Host lifecycle step: wire phases arrived ⇒ understanding complete;
    // it carries no wire duration (never fabricated).
    expect(stepById(view.steps, "understanding-question").status).toBe("complete");
    expect(stepById(view.steps, "understanding-question").durationMs).toBeNull();
    for (const step of view.steps) {
      expect(step.label).toBe(TURN_PROCESS_STEP_LABELS[step.id]);
    }
  });

  it("settled turn keeps composing-answer (complete) — R3 no longer hides it", () => {
    const state = reduce([
      progress(1, "reading_context", "已读取相关上下文", {
        activity: "completed",
        tool_name: "read_range",
        status: "ok",
        duration_ms: 120,
      }),
      progress(2, "searching_web", "已完成网页搜索", {
        activity: "completed",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "ok",
        duration_ms: 200,
      }),
      progress(3, "composing_answer", "正在组织回答", { activity: "started", status: "running" }),
    ]);
    const done = reduceAgenticActivityEvent(state, { type: "completed" });
    const view = projectTurnProcess({ activity: done });
    // R3: 理解问题 → 阅读本文 → 网页查询 → 整理回答 after success.
    expect(view.steps.map((s) => s.id)).toEqual([
      "understanding-question",
      "reading-context",
      "web-search",
      "composing-answer",
    ]);
    expect(stepById(view.steps, "composing-answer").status).toBe("complete");
    expect(stepById(view.steps, "understanding-question").status).toBe("complete");
  });

  it("internal stages (agent-running, searching-article, validating-evidence) never appear in visible steps", () => {
    const state = reduce([
      progress(1, "agent_running", "开始分析", { activity: "started", status: "running" }),
      progress(2, "searching_article", "正在检索当前文章", {
        activity: "started",
        tool_name: "search_current_article",
        status: "running",
      }),
      progress(3, "validating_evidence", "正在核对回答依据", { activity: "started", status: "running" }),
    ]);
    const liveView = projectTurnProcess({ activity: state, isStreaming: true });
    // R3: internals fold but never render; the host lifecycle pair is the
    // learner narrative (understanding done — phases ran; composing active).
    expect(liveView.steps.map((s) => s.id)).toEqual([
      "understanding-question",
      "composing-answer",
    ]);
    expect(stepById(liveView.steps, "understanding-question").status).toBe("complete");
    expect(stepById(liveView.steps, "composing-answer").status).toBe("active");

    const done = reduceAgenticActivityEvent(state, { type: "completed" });
    const settledView = projectTurnProcess({ activity: done });
    expect(settledView.steps.map((s) => s.id)).toEqual([
      "understanding-question",
      "composing-answer",
    ]);
    expect(stepById(settledView.steps, "composing-answer").status).toBe("complete");
    expect(settledView.visible).toBe(true);
    // elapsed_ms = 300 (3 events × 100) → durationS = max(1, 0) = 1
    expect(settledView.header.settledCopy).toBe("已整理回答 · 1s");
  });

  it("fixed typed labels never carry server summary copy", () => {
    const state = reduce([
      progress(1, "reading_context", "正在读取文章上下文", {
        activity: "completed",
        tool_name: "read_range",
        status: "ok",
        duration_ms: 100,
      }),
    ]);
    const done = reduceAgenticActivityEvent(state, { type: "completed" });
    const view = projectTurnProcess({ activity: done });
    expect(stepById(view.steps, "reading-context").label).toBe("阅读本文");
    expect(JSON.stringify(view)).not.toContain("正在读取文章上下文");
    expect(JSON.stringify(view)).not.toContain("已读取相关上下文");
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
      progress(2, "composing_answer", "正在组织回答", { activity: "started", status: "running" }),
    ]);
    const view = projectTurnProcess({ activity: state, isStreaming: true });
    expect(stepById(view.steps, "reading-context").status).toBe("interrupted");
    expect(stepById(view.steps, "reading-context").label).toBe("阅读本文");
    expect(stepById(view.steps, "composing-answer").status).toBe("active");
    expect(stepById(view.steps, "composing-answer").label).toBe("整理回答");
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
    expect(step.label).toBe("阅读本文");
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
    expect(stepById(view.steps, "reading-context").label).toBe("阅读本文");
    expect(view.header.state).toBe("settled");
  });

  it("searching-article (internal) never appears in visible steps even when started-only", () => {
    const live = reduce([
      progress(1, "searching_article", "正在检索当前文章", {
        activity: "started",
        tool_name: "search_current_article",
        status: "running",
      }),
      progress(2, "composing_answer", "正在组织回答", { activity: "started", status: "running" }),
    ]);
    const liveView = projectTurnProcess({ activity: live, isStreaming: true });
    expect(liveView.steps.find((s) => s.id === "searching-article")).toBeUndefined();

    const done = reduceAgenticActivityEvent(live, { type: "completed" });
    const doneView = projectTurnProcess({ activity: done });
    expect(doneView.steps.find((s) => s.id === "searching-article")).toBeUndefined();
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
    expect(step.label).toBe("阅读本文");
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
      progress(2, "reading_context", "重复事件", {
        activity: "started",
        tool_name: "read_range",
        status: "running",
      }),
      progress(1, "searching_article", "乱序事件", {
        activity: "started",
        tool_name: "search_current_article",
        status: "running",
      }),
    ]);
    const view = projectTurnProcess({ activity: state, isStreaming: true });
    // R3: host 理解问题 + the single real wire step (searching-article is
    // internal; dup/out-of-order rows were dropped by the reducer).
    expect(view.steps.map((s) => s.id)).toEqual([
      "understanding-question",
      "reading-context",
    ]);
    expect(stepById(view.steps, "reading-context").label).toBe("阅读本文");
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
    // R3: after the retry reset no wire phase remains — only the host
    // 理解问题 step (active) is provable. 整理回答 starts only after a
    // composing phase / lifecycle signal, so it is NOT shown at T0.
    expect(view.steps.map((s) => s.id)).toEqual(["understanding-question"]);
    expect(stepById(view.steps, "understanding-question").status).toBe("active");
    // Header falls back to the only visible step's running label.
    expect(view.header.liveSummary).toBe("正在理解问题");
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
    // R3: host lifecycle pair — understanding complete (a phase ran),
    // reading interrupted (started-only at the terminal), and the
    // host-proved 整理回答 complete (success terminal).
    expect(stepById(view.steps, "understanding-question").status).toBe("complete");
    expect(stepById(view.steps, "reading-context").status).toBe("interrupted");
    expect(stepById(view.steps, "composing-answer").status).toBe("complete");
    expect(view.header.state).toBe("settled");
  });
});

describe("host stage inference", () => {
  it("composing completes when validating starts (supersession)", () => {
    const live = reduce([
      progress(1, "composing_answer", "正在组织回答", { activity: "started", status: "running" }),
      progress(2, "validating_evidence", "正在核对回答依据", { activity: "started", status: "running" }),
    ]);
    const liveView = projectTurnProcess({ activity: live, isStreaming: true });
    // composing-answer is visible while live; validating-evidence is internal.
    expect(stepById(liveView.steps, "composing-answer").status).toBe("complete");
    expect(stepById(liveView.steps, "composing-answer").label).toBe("整理回答");
    expect(liveView.steps.find((s) => s.id === "validating-evidence")).toBeUndefined();
  });

  it("composing is active when it is the current phase", () => {
    const live = reduce([
      progress(1, "composing_answer", "正在组织回答", { activity: "started", status: "running" }),
    ]);
    const view = projectTurnProcess({ activity: live, isStreaming: true });
    expect(stepById(view.steps, "composing-answer").status).toBe("active");
  });

  it("R1-rework P0: pure internal-only turn preserves 已整理回答 summary after settle", () => {
    const state = reduce([
      progress(1, "agent_running", "开始分析", { activity: "started", status: "running" }),
    ]);
    const done = reduceAgenticActivityEvent(state, { type: "completed" });
    const view = projectTurnProcess({ activity: done });
    expect(view.visible).toBe(true);
    // R3: host lifecycle pair — 理解问题 → 整理回答, both complete.
    expect(view.steps.map((s) => s.id)).toEqual([
      "understanding-question",
      "composing-answer",
    ]);
    expect(stepById(view.steps, "understanding-question").status).toBe("complete");
    expect(stepById(view.steps, "composing-answer").status).toBe("complete");
    // elapsed_ms = 100 → durationS = max(1, 0) = 1
    expect(view.header.settledCopy).toBe("已整理回答 · 1s");
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
    expect(stepById(view.steps, "understanding-question").status).toBe("complete");
    expect(stepById(view.steps, "reading-context").status).toBe("complete");
    // R3: composing-answer stays visible after settle — interrupted on a
    // non-ok terminal (a checkmark on an unfinished step would be a lie).
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
    expect(stepById(view.steps, "reading-context").label).toBe("阅读本文");
    // aria uses live summary — never injected error summary.
    expect(view.ariaLabel).toBe("正在阅读本文");
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
    // agent-running is internal → not in visible steps.
    expect(stepById(view.steps, "reading-context").status).toBe("failed");
    expect(stepById(view.steps, "web-search").status).toBe("degraded");
    expect(view.steps.find((s) => s.id === "agent-running")).toBeUndefined();
    for (const step of view.steps) {
      expect(step.label).toBe(TURN_PROCESS_STEP_LABELS[step.id]);
    }
    assertNoInjectedErrorCopy(JSON.stringify(view));
    const snapshot = buildAgenticProcessSnapshot(done);
    assertNoInjectedErrorCopy(JSON.stringify(snapshot));
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

describe("R0 visibility rules", () => {
  it("renders nothing for empty state (no shell, no placeholder)", () => {
    const view = projectTurnProcess({ activity: createIdleAgenticActivityState() });
    expect(view.visible).toBe(false);
    expect(view.steps).toEqual([]);
  });

  it("is visible while streaming even before the first event", () => {
    const view = projectTurnProcess({
      activity: createIdleAgenticActivityState(),
      isStreaming: true,
    });
    expect(view.visible).toBe(true);
    expect(view.header.state).toBe("running");
    // R3: at T0 only the host 理解问题 step is provable; header and aria
    // use its running label — never an internal-phase label.
    expect(view.steps.map((s) => s.id)).toEqual(["understanding-question"]);
    expect(stepById(view.steps, "understanding-question").status).toBe("active");
    expect(view.header.liveSummary).toBe("正在理解问题");
    expect(view.ariaLabel).toBe("正在理解问题");
  });

  it("R1-rework P0: pure-answer turn preserves a summary after settle", () => {
    const done = reduceAgenticActivityEvent(runningState(), { type: "completed" });
    const snapshot = buildAgenticProcessSnapshot(done);
    expect(snapshot).not.toBeNull();
    const view = projectTurnProcess({ snapshot });
    // R1-rework: EVERY successful answer preserves a learner-facing summary.
    expect(view.visible).toBe(true);
    expect(view.header.state).toBe("settled");
    expect(view.header.settledCopy).toBe("已整理回答");
    // R3: 理解问题 → 整理回答 — the minimal honest learner narrative for
    // a pure-answer turn (both host-proved, no fabricated tool steps).
    expect(view.steps.map((s) => s.id)).toEqual([
      "understanding-question",
      "composing-answer",
    ]);
    expect(stepById(view.steps, "understanding-question").status).toBe("complete");
    expect(stepById(view.steps, "composing-answer").status).toBe("complete");
    expect(stepById(view.steps, "composing-answer").label).toBe("整理回答");
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
    expect(stepById(view.steps, "reading-context").label).toBe("阅读本文");
    expect(view.ariaLabel).toBe("本轮回答未能完成");
    expect(view.header.settledCopy).toBe("未完成");
  });

  it("cold history without snapshot or reasoning renders nothing", () => {
    const view = projectTurnProcess({});
    expect(view.visible).toBe(false);
    expect(view.steps).toEqual([]);
    expect(view.reasoning).toBeNull();
  });

  it("R3: cold history WITH a safe reasoning projection renders reasoning-only (no fabricated steps)", () => {
    const view = projectTurnProcess({
      reasoningMd: "先确认问题范围，再整理回答。",
      reasoningStatus: "completed",
      reasoningTruncated: false,
    });
    expect(view.visible).toBe(true);
    expect(view.header.state).toBe("settled");
    // No run lifecycle is provable after a reload ⇒ zero steps, never a
    // fabricated pipeline.
    expect(view.steps).toEqual([]);
    expect(view.reasoning).toEqual({
      text: "先确认问题范围，再整理回答。",
      truncated: false,
      streaming: false,
    });
    expect(view.header.settledCopy).toBe("思考过程");
    expect(view.ariaLabel).toBe("思考过程");
  });

  it("R3: reasoning-only cold history with empty reasoning renders nothing (no empty shell)", () => {
    const view = projectTurnProcess({
      reasoningMd: "   ",
      reasoningStatus: "completed",
    });
    expect(view.visible).toBe(false);
    expect(view.reasoning).toBeNull();
  });

  it("R1-rework P0: agent-only turn (no visible tool) preserves 已整理回答 summary", () => {
    const state = reduce([
      progress(1, "agent_running", "分析完成", { activity: "completed", status: "ok" }),
    ]);
    const done = reduceAgenticActivityEvent(state, { type: "completed" });
    const view = projectTurnProcess({ activity: done });
    // agent-running is internal → no real tool steps, but R1-rework P0
    // requires a summary for every successful answer.
    expect(view.visible).toBe(true);
    // elapsed_ms = 100 → durationS = max(1, 0) = 1
    expect(view.header.settledCopy).toBe("已整理回答 · 1s");
    // R3: host lifecycle pair — 理解问题 → 整理回答.
    expect(view.steps.map((s) => s.id)).toEqual([
      "understanding-question",
      "composing-answer",
    ]);
    expect(stepById(view.steps, "understanding-question").status).toBe("complete");
    expect(stepById(view.steps, "composing-answer").status).toBe("complete");
  });

  it("durationS is null when no elapsed time is known", () => {
    const snapshot = buildAgenticProcessSnapshot(runningState());
    const view = projectTurnProcess({ snapshot });
    expect(view.header.durationS).toBeNull();
  });

  it("running live summary maps phases to user-facing labels", () => {
    const reading = reduce([
      progress(1, "reading_context", "正在读取", {
        activity: "started",
        tool_name: "read_range",
        status: "running",
      }),
    ]);
    expect(
      projectTurnProcess({ activity: reading, isStreaming: true }).header.liveSummary,
    ).toBe("正在阅读本文");

    const webing = reduce([
      progress(1, "searching_web", "正在搜索", {
        activity: "started",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "running",
      }),
    ]);
    expect(
      projectTurnProcess({ activity: webing, isStreaming: true }).header.liveSummary,
    ).toBe("正在查询网页");

    const composing = reduce([
      progress(1, "composing_answer", "正在组织", { activity: "started", status: "running" }),
    ]);
    expect(
      projectTurnProcess({ activity: composing, isStreaming: true }).header.liveSummary,
    ).toBe("正在整理回答");
  });

  it("internal phase (agent-running) falls back to 正在整理回答", () => {
    const state = reduce([
      progress(1, "agent_running", "开始分析", { activity: "started", status: "running" }),
    ]);
    const view = projectTurnProcess({ activity: state, isStreaming: true });
    expect(view.header.liveSummary).toBe("正在整理回答");
  });
});

describe("settled one-liner copy", () => {
  it("R1-rework: article-only success shows 已根据当前文章整理 · Ns", () => {
    const state = reduce([
      progress(1, "reading_context", "已读取", {
        activity: "completed",
        tool_name: "read_range",
        status: "ok",
        duration_ms: 120,
      }),
    ]);
    const done = reduceAgenticActivityEvent(state, { type: "completed" });
    const view = projectTurnProcess({ activity: done });
    expect(view.header.settledCopy).toBe("已根据当前文章整理 · 1s");
  });

  it("web success shows 已查询网页 · N 个来源 · Ns", () => {
    const state = reduce([
      progress(1, "searching_web", "已完成", {
        activity: "completed",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "ok",
        duration_ms: 200,
      }),
    ]);
    const done = reduceAgenticActivityEvent(state, { type: "completed" });
    const view = projectTurnProcess({
      activity: done,
      citations: [
        webCitation("https://example.com/a", "c1"),
        webCitation("https://example.com/b", "c2"),
      ],
    });
    expect(view.header.settledCopy).toBe("已查询网页 · 2 个来源 · 1s");
  });

  it("web step with 0 citations shows 已查询网页 · 0 个来源 (no fabricated sources)", () => {
    const state = reduce([
      progress(1, "searching_web", "已完成", {
        activity: "completed",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "ok",
        duration_ms: 200,
      }),
    ]);
    const done = reduceAgenticActivityEvent(state, { type: "completed" });
    const view = projectTurnProcess({ activity: done, citations: [] });
    expect(view.header.settledCopy).toBe("已查询网页 · 0 个来源 · 1s");
  });

  it("failed terminal shows 未完成 (no duration suffix)", () => {
    const state = reduce([
      progress(1, "reading_context", "已读取", {
        activity: "completed",
        tool_name: "read_range",
        status: "ok",
        duration_ms: 100,
      }),
    ]);
    const failed = reduceAgenticActivityEvent(state, {
      type: "terminal",
      finalStatus: "failed",
    });
    const view = projectTurnProcess({ activity: failed });
    expect(view.header.settledCopy).toBe("未完成");
  });

  it("cancelled terminal shows 已取消 (no duration suffix)", () => {
    const state = reduce([
      progress(1, "reading_context", "已读取", {
        activity: "completed",
        tool_name: "read_range",
        status: "ok",
        duration_ms: 100,
      }),
    ]);
    const cancelled = reduceAgenticActivityEvent(state, {
      type: "terminal",
      finalStatus: "cancelled",
    });
    const view = projectTurnProcess({ activity: cancelled });
    expect(view.header.settledCopy).toBe("已取消");
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

describe("ASK-UX-COT-COMPOSER-R3: host-provable lifecycle steps", () => {
  it("pure-answer streaming turn at T0 shows only 理解问题 (active) — no premature 整理回答", () => {
    const view = projectTurnProcess({
      activity: runningState(),
      isStreaming: true,
    });
    expect(view.visible).toBe(true);
    expect(view.header.state).toBe("running");
    // 整理回答 starts after a composing phase / lifecycle signal; at T0
    // only the accepted run itself is provable.
    expect(view.steps.map((s) => s.id)).toEqual(["understanding-question"]);
    const step = stepById(view.steps, "understanding-question");
    expect(step.status).toBe("active");
    expect(step.label).toBe("理解问题");
    expect(step.durationMs).toBeNull();
    expect(step.domains).toEqual([]);
  });

  it("pure-answer streaming turn injects 整理回答 (active) once a wire phase arrived", () => {
    const state = reduce([
      progress(1, "agent_running", "开始分析", { activity: "started", status: "running" }),
    ]);
    const view = projectTurnProcess({ activity: state, isStreaming: true });
    expect(view.steps.map((s) => s.id)).toEqual([
      "understanding-question",
      "composing-answer",
    ]);
    expect(stepById(view.steps, "understanding-question").status).toBe("complete");
    expect(stepById(view.steps, "composing-answer").status).toBe("active");
  });

  it("pure-answer completed turn shows 理解问题 → 整理回答 (both complete)", () => {
    const done = reduceAgenticActivityEvent(runningState(), { type: "completed" });
    const view = projectTurnProcess({ activity: done });
    expect(view.visible).toBe(true);
    expect(view.header.state).toBe("settled");
    expect(view.steps.map((s) => s.id)).toEqual([
      "understanding-question",
      "composing-answer",
    ]);
    expect(stepById(view.steps, "understanding-question").status).toBe("complete");
    expect(stepById(view.steps, "composing-answer").status).toBe("complete");
  });

  it("pure-answer failed turn marks both lifecycle steps interrupted (no phases ever ran)", () => {
    const failed = reduceAgenticActivityEvent(runningState(), {
      type: "terminal",
      finalStatus: "failed",
    });
    const view = projectTurnProcess({ activity: failed });
    expect(view.visible).toBe(true);
    expect(view.steps.map((s) => s.id)).toEqual([
      "understanding-question",
      "composing-answer",
    ]);
    expect(stepById(view.steps, "understanding-question").status).toBe("interrupted");
    expect(stepById(view.steps, "composing-answer").status).toBe("interrupted");
  });

  it("pure-answer cancelled turn marks both lifecycle steps interrupted", () => {
    const cancelled = reduceAgenticActivityEvent(runningState(), {
      type: "terminal",
      finalStatus: "cancelled",
    });
    const view = projectTurnProcess({ activity: cancelled });
    expect(view.visible).toBe(true);
    expect(view.steps.map((s) => s.id)).toEqual([
      "understanding-question",
      "composing-answer",
    ]);
    expect(stepById(view.steps, "understanding-question").status).toBe("interrupted");
    expect(stepById(view.steps, "composing-answer").status).toBe("interrupted");
  });

  it("R3: real-tool turns keep 整理回答 after settle (the R2 regression fix)", () => {
    const state = reduce([
      progress(1, "reading_context", "已读取", {
        activity: "completed",
        tool_name: "read_range",
        status: "ok",
        duration_ms: 120,
      }),
    ]);
    const done = reduceAgenticActivityEvent(state, { type: "completed" });
    const view = projectTurnProcess({ activity: done });
    // R2 hid composing-answer after settle for real-tool turns; R3 shows
    // the full honest narrative: 理解问题 → 阅读本文 → 整理回答.
    expect(view.steps.map((s) => s.id)).toEqual([
      "understanding-question",
      "reading-context",
      "composing-answer",
    ]);
    expect(stepById(view.steps, "composing-answer").status).toBe("complete");
    expect(stepById(view.steps, "reading-context").status).toBe("complete");
    expect(stepById(view.steps, "reading-context").durationMs).toBe(120);
    // Host steps carry no duration; wire steps keep theirs.
    expect(stepById(view.steps, "understanding-question").durationMs).toBeNull();
    expect(stepById(view.steps, "composing-answer").durationMs).toBeNull();
  });

  it("R3: web turn settled order is 理解问题 → 网页查询 → 整理回答", () => {
    const state = reduce([
      progress(1, "searching_web", "已完成网页搜索", {
        activity: "completed",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "ok",
        duration_ms: 900,
      }),
    ]);
    const done = reduceAgenticActivityEvent(state, { type: "completed" });
    const view = projectTurnProcess({ activity: done });
    expect(view.steps.map((s) => s.id)).toEqual([
      "understanding-question",
      "web-search",
      "composing-answer",
    ]);
    expect(stepById(view.steps, "composing-answer").status).toBe("complete");
  });

  it("R3: canonical order is stable even when web arrives before reading on the wire", () => {
    const state = reduce([
      progress(1, "searching_web", "已完成网页搜索", {
        activity: "completed",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "ok",
        duration_ms: 400,
      }),
      progress(2, "reading_context", "已读取", {
        activity: "completed",
        tool_name: "read_range",
        status: "ok",
        duration_ms: 120,
      }),
    ]);
    const done = reduceAgenticActivityEvent(state, { type: "completed" });
    const view = projectTurnProcess({ activity: done });
    expect(view.steps.map((s) => s.id)).toEqual([
      "understanding-question",
      "reading-context",
      "web-search",
      "composing-answer",
    ]);
  });

  it("injected steps carry no server data (leak-proof)", () => {
    const done = reduceAgenticActivityEvent(runningState(), { type: "completed" });
    const view = projectTurnProcess({ activity: done });
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
    ]) {
      expect(serialized, `injected step must not contain ${leaked}`).not.toContain(leaked);
    }
  });

  it("idle state (no run started) renders nothing — no injection", () => {
    const view = projectTurnProcess({ activity: createIdleAgenticActivityState() });
    expect(view.visible).toBe(false);
    expect(view.steps).toEqual([]);
  });

  it("cold history without snapshot renders nothing — no injection", () => {
    const view = projectTurnProcess({});
    expect(view.visible).toBe(false);
    expect(view.steps).toEqual([]);
  });
});

describe("ASK-UX-COT-COMPOSER-R3: safe reasoning projection", () => {
  it("projects settled reasoning text verbatim with truncated flag", () => {
    const done = reduceAgenticActivityEvent(runningState(), { type: "completed" });
    const view = projectTurnProcess({
      activity: done,
      reasoningMd: "先确认范围。\n再整理回答。",
      reasoningStatus: "completed",
      reasoningTruncated: true,
    });
    expect(view.reasoning).toEqual({
      text: "先确认范围。\n再整理回答。",
      truncated: true,
      streaming: false,
    });
    // Steps and reasoning coexist in one view (single disclosure).
    expect(view.steps.length).toBeGreaterThan(0);
  });

  it("streaming reasoning with empty text is still projected (live indicator, not an empty shell)", () => {
    const view = projectTurnProcess({
      activity: runningState(),
      isStreaming: true,
      reasoningMd: "",
      reasoningStatus: "streaming",
    });
    expect(view.reasoning).toEqual({
      text: "",
      truncated: false,
      streaming: true,
    });
  });

  it("empty reasoning with completed status projects nothing (no empty shell)", () => {
    const done = reduceAgenticActivityEvent(runningState(), { type: "completed" });
    const view = projectTurnProcess({
      activity: done,
      reasoningMd: "   ",
      reasoningStatus: "completed",
      reasoningTruncated: false,
    });
    expect(view.reasoning).toBeNull();
  });

  it("idle reasoning status with no text projects nothing", () => {
    const done = reduceAgenticActivityEvent(runningState(), { type: "completed" });
    const view = projectTurnProcess({
      activity: done,
      reasoningMd: null,
      reasoningStatus: "idle",
    });
    expect(view.reasoning).toBeNull();
  });

  it("reasoning fields never affect step projection or settled copy", () => {
    const state = reduce([
      progress(1, "reading_context", "已读取", {
        activity: "completed",
        tool_name: "read_range",
        status: "ok",
        duration_ms: 120,
      }),
    ]);
    const done = reduceAgenticActivityEvent(state, { type: "completed" });
    const withReasoning = projectTurnProcess({
      activity: done,
      reasoningMd: "思考内容",
      reasoningStatus: "completed",
    });
    const withoutReasoning = projectTurnProcess({ activity: done });
    expect(withReasoning.steps).toEqual(withoutReasoning.steps);
    expect(withReasoning.header.settledCopy).toBe(withoutReasoning.header.settledCopy);
  });

  it("reasoning view never carries leaked sentinel content from the projection layer", () => {
    const done = reduceAgenticActivityEvent(runningState(), { type: "completed" });
    // The projection forwards ONLY what the server safe fields carry. It
    // must never synthesize reasoning content of its own.
    const view = projectTurnProcess({ activity: done });
    expect(view.reasoning).toBeNull();
    const serialized = JSON.stringify(view);
    for (const leaked of ["evh_", "https://", "terminal_reason", "run_id", "message_id"]) {
      expect(serialized).not.toContain(leaked);
    }
  });
});

describe("ASK-CONTEXT-COMPACTION-R2: learner-visible lifecycle", () => {
  it("places an active compaction step before the normal learner process", () => {
    const view = projectTurnProcess({
      activity: runningState(),
      isStreaming: true,
      contextCompaction: {
        status: "running",
        elapsedMs: 0,
      },
    });

    expect(view.visible).toBe(true);
    expect(view.header.liveSummary).toBe("正在压缩上下文");
    expect(view.steps[0]).toMatchObject({
      id: "context-compaction",
      label: "正在压缩上下文",
      status: "active",
    });
    expect(view.steps[1]?.id).toBe("understanding-question");
  });

  it.each(["completed", "fallback"] as const)(
    "keeps a quiet completed compaction step for %s",
    (status) => {
      const done = reduceAgenticActivityEvent(runningState(), {
        type: "completed",
      });
      const view = projectTurnProcess({
        activity: done,
        contextCompaction: {
          status,
          elapsedMs: 840,
        },
      });

      expect(view.steps[0]).toMatchObject({
        id: "context-compaction",
        label: "上下文已压缩",
        status: status === "completed" ? "complete" : "degraded",
        durationMs: 840,
      });
      expect(JSON.stringify(view)).not.toContain("provider");
      expect(JSON.stringify(view)).not.toContain("detail_code");
    },
  );
});
