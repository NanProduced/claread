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

function reduce(events: AgenticActivityEvent[]): AgenticActivityState {
  return events.reduce(
    (state, event) => reduceAgenticActivityEvent(state, event),
    runningState(),
  );
}

function stepById(
  view: ReturnType<typeof projectTurnProcess>,
  id: string,
) {
  const step = view.steps.find((candidate) => candidate.id === id);
  if (!step) {
    throw new Error(`missing step ${id} in ${view.steps.map((item) => item.id)}`);
  }
  return step;
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

describe("Answer Process projection", () => {
  it("does not pre-render future steps at run_started", () => {
    const view = projectTurnProcess({
      activity: runningState(),
      isStreaming: true,
    });

    expect(view.visible).toBe(true);
    expect(view.steps).toEqual([]);
    expect(view.header.liveSummary).not.toContain("确认问题意图");
    expect(JSON.stringify(view)).not.toContain("understanding-question");
  });

  it("renders analysis only from the real Analysis lifecycle", () => {
    const state = reduce([
      progress(1, "analysis", "PRIVATE ANALYSIS SUMMARY", {
        activity: "started",
        status: "running",
      }),
      progress(2, "analysis", "PRIVATE ANALYSIS DONE", {
        activity: "completed",
        status: "ok",
        duration_ms: 120,
      }),
    ]);
    const view = projectTurnProcess({ activity: state });
    const step = stepById(view, "analysis");

    expect(view.steps.map((item) => item.id)).toEqual(["analysis"]);
    expect(step).toMatchObject({
      label: "分析问题",
      status: "complete",
      lifecycle: "settled",
      outcome: "success",
      durationMs: 120,
    });
    expect(JSON.stringify(view)).not.toContain("PRIVATE ANALYSIS");
  });

  it("keeps article production tools in one first-seen stable step", () => {
    const state = reduce([
      progress(1, "searching_article", "PRIVATE EXPAND", {
        activity: "started",
        tool_name: "expand_evidence",
        activity_id: "article_evidence",
        status: "running",
      }),
      progress(2, "searching_article", "PRIVATE EXPAND DONE", {
        activity: "completed",
        tool_name: "expand_evidence",
        activity_id: "article_evidence",
        status: "ok",
        duration_ms: 80,
      }),
      progress(3, "searching_article", "PRIVATE SEARCH", {
        activity: "started",
        tool_name: "search_current_article",
        activity_id: "article_evidence",
        status: "running",
      }),
      progress(4, "searching_article", "PRIVATE SEARCH DONE", {
        activity: "completed",
        tool_name: "search_current_article",
        activity_id: "article_evidence",
        status: "ok",
        duration_ms: 140,
      }),
    ]);
    const view = projectTurnProcess({ activity: state });

    expect(view.steps.map((item) => item.id)).toEqual(["article-evidence"]);
    expect(stepById(view, "article-evidence")).toMatchObject({
      label: "查找文章依据",
      status: "complete",
      outcome: "success",
      durationMs: 140,
    });
    expect(JSON.stringify(view)).not.toContain("PRIVATE");
  });

  it("keeps legacy read_range compatible but presents the same article label", () => {
    const state = reduce([
      progress(1, "reading_context", "PRIVATE LEGACY", {
        activity: "completed",
        tool_name: "read_range",
        activity_id: "article_evidence",
        status: "ok",
        duration_ms: 90,
      }),
    ]);
    const view = projectTurnProcess({ activity: state });
    expect(view.steps.map((item) => item.id)).toEqual(["article-evidence"]);
    expect(stepById(view, "article-evidence").label).toBe("查找文章依据");
  });

  it("uses first-seen event order instead of canonical reordering", () => {
    const state = reduce([
      progress(1, "searching_web", "PRIVATE WEB", {
        activity: "completed",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "ok",
        call_sequence: 1,
      }),
      progress(2, "searching_article", "PRIVATE ARTICLE", {
        activity: "completed",
        tool_name: "search_current_article",
        activity_id: "article_evidence",
        status: "ok",
      }),
      {
        type: "answer_started",
        generationId: 0,
      },
    ]);
    const view = projectTurnProcess({ activity: state, isStreaming: true });
    expect(view.steps.map((item) => item.id)).toEqual([
      "web-evidence",
      "article-evidence",
      "answering",
    ]);
  });

  it("orders a preview-reset answering replacement by local ordinal", () => {
    const state = reduce([
      { type: "answer_started", generationId: 0 },
      progress(1, "searching_article", "PRIVATE ARTICLE", {
        activity: "started",
        tool_name: "search_current_article",
        activity_id: "article_evidence",
        status: "running",
      }),
      { type: "answer_completed" },
      { type: "answer_started", generationId: 1 },
      progress(2, "searching_web", "PRIVATE WEB", {
        activity: "started",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "running",
      }),
    ]);
    const view = projectTurnProcess({ activity: state, isStreaming: true });
    expect(view.steps.map((item) => item.id)).toEqual([
      "article-evidence",
      "answering",
      "web-evidence",
    ]);
  });

  it("requires an explicit result and interrupts an unmatched article call", () => {
    const state = reduce([
      progress(1, "searching_article", "PRIVATE ARTICLE", {
        activity: "started",
        tool_name: "search_current_article",
        activity_id: "article_evidence",
        status: "running",
      }),
      { type: "answer_started", generationId: 0 },
      { type: "answer_completed" },
    ]);
    const done = reduceAgenticActivityEvent(state, { type: "completed" });
    const view = projectTurnProcess({ activity: done });

    expect(stepById(view, "article-evidence")).toMatchObject({
      status: "interrupted",
      lifecycle: "settled",
      outcome: "interrupted",
    });
    expect(stepById(view, "answering")).toMatchObject({
      status: "complete",
      outcome: "success",
    });
  });

  it("does not turn a late composing event into the answering step", () => {
    const state = reduce([
      progress(1, "composing_answer", "PRIVATE LATE COMPOSING", {
        activity: "started",
        status: "running",
      }),
    ]);
    expect(state.steps).toEqual([]);
    expect(projectTurnProcess({ activity: state }).steps).toEqual([]);
  });

  it("starts and completes answering from the accepted delta lifecycle", () => {
    let state = reduce([{ type: "answer_started", generationId: 0 }]);
    let view = projectTurnProcess({ activity: state, isStreaming: true });
    expect(stepById(view, "answering")).toMatchObject({
      label: "生成回答",
      status: "active",
      lifecycle: "active",
      outcome: null,
    });

    state = reduceAgenticActivityEvent(state, { type: "answer_completed" });
    state = reduceAgenticActivityEvent(state, { type: "completed" });
    view = projectTurnProcess({ activity: state });
    expect(stepById(view, "answering")).toMatchObject({
      status: "complete",
      lifecycle: "settled",
      outcome: "success",
    });
  });

  it("maps web no_results independently from success/checkmark", () => {
    const state = reduce([
      progress(1, "searching_web", "PRIVATE WEB START", {
        activity: "started",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "running",
        call_sequence: 1,
      }),
      progress(2, "searching_web", "PRIVATE WEB RESULT", {
        activity: "completed",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "ok",
        attempt_count: 1,
        call_sequence: 1,
      }),
    ]);
    const done = reduceAgenticActivityEvent(state, { type: "completed" });
    const view = projectTurnProcess({
      activity: done,
      webSearchSummary: { outcome: "no_results", cited_source_count: 0 },
    });
    const step = stepById(view, "web-evidence");

    expect(step).toMatchObject({
      status: "complete",
      outcome: "empty",
      detail: "no_results",
      attempts: null,
    });
    expect(view.header.settledCopy).toBe("已完成");
  });

  it("maps unavailable web search to degraded and preserves attempts", () => {
    const state = reduce([
      progress(1, "searching_web", "PRIVATE WEB START", {
        activity: "started",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "running",
        call_sequence: 1,
      }),
      progress(2, "searching_web", "PRIVATE WEB DOWN", {
        activity: "unavailable",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "unavailable",
        attempt_count: 2,
        call_sequence: 2,
      }),
    ]);
    const view = projectTurnProcess({ activity: state, isStreaming: true });
    expect(stepById(view, "web-evidence")).toMatchObject({
      status: "degraded",
      outcome: "degraded",
      detail: "degraded",
      attempts: "已尝试 2 次",
    });
  });

  it("hides citation-check when the backend only starts validation", () => {
    const state = reduce([
      progress(1, "validating_evidence", "PRIVATE VALIDATION", {
        activity: "started",
        status: "running",
      }),
    ]);
    expect(
      projectTurnProcess({
        activity: state,
        citations: [webCitation("https://example.com")],
      }).steps,
    ).toEqual([]);
  });

  it("shows citation-check only after a real completed validation result", () => {
    const state = reduce([
      progress(1, "validating_evidence", "PRIVATE VALIDATION", {
        activity: "completed",
        status: "ok",
        duration_ms: 40,
      }),
    ]);
    const view = projectTurnProcess({
      activity: state,
      citations: [webCitation("https://example.com")],
    });
    expect(stepById(view, "citation-check")).toMatchObject({
      label: "检查引用",
      status: "complete",
      outcome: "success",
    });
  });

  it("keeps context compaction independent and only announces a perceptible wait", () => {
    const state = runningState();
    const short = projectTurnProcess({
      activity: state,
      isStreaming: true,
      contextCompaction: { status: "running", elapsedMs: 100 },
    });
    const long = projectTurnProcess({
      activity: state,
      isStreaming: true,
      contextCompaction: { status: "running", elapsedMs: 600 },
    });

    expect(short.steps).toEqual([]);
    expect(short.header.liveSummary).not.toBe("正在整理较早对话");
    expect(long.steps).toEqual([]);
    expect(long.header.liveSummary).toBe("正在整理较早对话");
  });

  it("never carries raw process data or reasoning in the view", () => {
    const state = reduce([
      progress(1, "searching_web", "PRIVATE SERVER SUMMARY", {
        activity: "completed",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "ok",
      }),
    ]);
    const view = projectTurnProcess({
      activity: state,
      citations: [webCitation("https://example.com/path?secret=1")],
      reasoningMd: "RAW_REASONING_SHOULD_BE_IGNORED",
      reasoningStatus: "completed",
    });
    const serialized = JSON.stringify(view);
    for (const forbidden of [
      "PRIVATE SERVER SUMMARY",
      "RAW_REASONING_SHOULD_BE_IGNORED",
      "search_web",
      "activity_id",
      "turn-1",
      "https://",
      "secret=1",
    ]) {
      expect(serialized).not.toContain(forbidden);
    }
    expect(view).not.toHaveProperty("reasoning");
  });

  it("keeps hostname-only domains and caps overflow", () => {
    const urls = Array.from({ length: 10 }, (_, index) => `https://www.example-${index}.com/a`);
    expect(extractWebDomains(urls.map((url, index) => webCitation(url, `c${index}`)))).toEqual([
      "example-0.com",
      "example-1.com",
      "example-2.com",
      "example-3.com",
      "example-4.com",
      "example-5.com",
      "example-6.com",
      "example-7.com",
      "+2",
    ]);
  });

  it("does not render cold process history without a snapshot", () => {
    expect(projectTurnProcess({})).toMatchObject({
      visible: false,
      steps: [],
    });
  });

  it("snapshot is UI memory only and contains no summary or reasoning", () => {
    const state = reduce([
      progress(1, "analysis", "PRIVATE SUMMARY", {
        activity: "completed",
        status: "ok",
      }),
    ]);
    const snapshot = buildAgenticProcessSnapshot(state);
    expect(snapshot).not.toBeNull();
    expect(JSON.stringify(snapshot)).not.toContain("PRIVATE SUMMARY");
    expect(JSON.stringify(snapshot)).not.toContain("reasoning");
    expect(snapshot?.steps[0]).toMatchObject({
      sequence: 1,
      phase: "analysis",
      status: "ok",
    });
  });

  it("projects live explicit empty outcome before message.completed", () => {
    const state = reduce([
      progress(1, "searching_web", "PRIVATE STATUS SUMMARY", {
        activity: "completed",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "ok",
        outcome: "empty",
      }),
    ]);
    const view = projectTurnProcess({
      activity: state,
      isStreaming: true,
      // A settled summary must not override the live progress outcome.
      webSearchSummary: { outcome: "completed", cited_source_count: 0 },
    });
    expect(stepById(view, "web-evidence")).toMatchObject({
      status: "complete",
      outcome: "empty",
      detail: "no_results",
    });

    const snapshot = buildAgenticProcessSnapshot(state);
    expect(snapshot?.steps[0]?.outcome).toBe("empty");
  });

  it("lets the settled Host web summary correct a stale live fold", () => {
    let state = reduce([
      progress(1, "searching_web", "PRIVATE LIVE EMPTY", {
        activity: "completed",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "ok",
        outcome: "empty",
      }),
    ]);
    state = reduceAgenticActivityEvent(state, { type: "completed" });
    const snapshot = buildAgenticProcessSnapshot(state);
    expect(snapshot?.steps[0]?.outcome).toBe("empty");

    const view = projectTurnProcess({
      snapshot,
      webSearchSummary: { outcome: "completed", cited_source_count: 1 },
    });
    expect(stepById(view, "web-evidence")).toMatchObject({
      status: "complete",
      outcome: "success",
      detail: null,
    });
  });
});

describe("fixed public labels", () => {
  it("uses the Answer Process vocabulary", () => {
    expect(TURN_PROCESS_STEP_LABELS).toEqual({
      analysis: "分析问题",
      "article-evidence": "查找文章依据",
      "web-evidence": "查询网页",
      answering: "生成回答",
      "citation-check": "检查引用",
    });
  });
});
