/** @vitest-environment jsdom */
import { render, screen, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import {
  createIdleAgenticActivityState,
  reduceAgenticActivityEvent,
  type AgenticActivityEvent,
  type AgenticActivityState,
} from "../ask/agentic-activity";
import type { AgenticCitationDisplayItem } from "../ask/agentic-evidence";
import { buildAgenticProcessSnapshot } from "../ask/agentic-process-projection";
import { TurnProcessDisclosure } from "./turn-process";

afterEach(cleanup);

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
      elapsed_ms: sequence * 1000,
      ...extras,
    },
  };
}

function liveTurn(events: AgenticActivityEvent[]): AgenticActivityState {
  const started = reduceAgenticActivityEvent(createIdleAgenticActivityState(), {
    type: "run_started",
    messageId: "msg-1",
    turnRunId: "turn-1",
  });
  return events.reduce(
    (state, event) => reduceAgenticActivityEvent(state, event),
    started,
  );
}

function webCitation(url: string, citationId: string): AgenticCitationDisplayItem {
  return {
    citationId,
    sourceKind: "web",
    title: "网页来源",
    snippet: "",
    url,
    sourceTitle: "示例",
    description: null,
    publishedAt: null,
    retrievedAt: null,
  };
}

describe("TurnProcessDisclosure", () => {
  it("renders nothing when there is no reasoning and no activity", () => {
    const { container } = render(
      <TurnProcessDisclosure activity={createIdleAgenticActivityState()} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("stays collapsed by default while running and keeps the r2 activity hooks", () => {
    const activity = liveTurn([
      progress(1, "searching_web", "正在搜索网页", {
        activity: "started",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "running",
      }),
    ]);
    render(<TurnProcessDisclosure activity={activity} isStreaming />);

    const activityRow = screen.getByTestId("ask-agentic-activity");
    expect(activityRow.getAttribute("data-activity-status")).toBe("running");
    expect(activityRow.getAttribute("data-activity-phase")).toBe("searching_web");
    expect(activityRow.getAttribute("data-activity-sequence")).toBe("1");
    expect(activityRow.getAttribute("aria-expanded")).toBe("false");
    // Fixed typed live label — never server summary.
    expect(screen.getByText("网页查询")).not.toBeNull();
  });

  it("expanding reveals typed steps with fixed labels and live reasoning text", async () => {
    const user = userEvent.setup();
    const activity = liveTurn([
      progress(1, "reading_context", "已读取相关上下文", {
        activity: "completed",
        tool_name: "read_range",
        status: "ok",
        duration_ms: 1200,
      }),
      progress(2, "composing_answer", "正在组织回答", {
        activity: "started",
        status: "running",
      }),
    ]);
    render(
      <TurnProcessDisclosure
        activity={activity}
        isStreaming
        reasoningMd={"先确认文章段落。\n再组织回答。"}
        reasoningStatus="streaming"
      />,
    );

    await user.click(screen.getByTestId("ask-agentic-activity"));
    const content = screen.getByTestId("ask-turn-process-reasoning")
      .parentElement;
    expect(screen.getByText("读取文章")).not.toBeNull();
    const activeStep = screen
      .getByText("组织回答", { selector: "[data-slot='chain-of-thought-step'] span" })
      .closest("[data-step-status]");
    expect(activeStep?.getAttribute("data-step-status")).toBe("active");
    expect(content?.textContent).toContain("先确认文章段落。");
    // Server summaries must not leak into CoT DOM.
    expect(content?.textContent).not.toContain("已读取相关上下文");
    expect(content?.textContent).not.toContain("正在组织回答");
  });

  it("settled completed copy splits 思考过程 / 处理过程 with duration", () => {
    const withReasoning = liveTurn([
      progress(1, "agent_running", "分析完成", { activity: "completed", status: "ok" }),
    ]);
    const done = reduceAgenticActivityEvent(withReasoning, { type: "completed" });
    const { unmount } = render(
      <TurnProcessDisclosure
        activity={done}
        reasoningMd="思考文本"
        reasoningStatus="completed"
      />,
    );
    expect(screen.getByText("思考过程 · 1s")).not.toBeNull();
    unmount();

    const stepsOnly = liveTurn([
      progress(1, "agent_running", "分析完成", { activity: "completed", status: "ok" }),
    ]);
    const doneNoReasoning = reduceAgenticActivityEvent(stepsOnly, {
      type: "completed",
    });
    render(<TurnProcessDisclosure activity={doneNoReasoning} />);
    expect(screen.getByText("处理过程 · 1s")).not.toBeNull();
  });

  it("renders the truncation notice with the fixed copy", async () => {
    const user = userEvent.setup();
    const done = reduceAgenticActivityEvent(
      liveTurn([
        progress(1, "agent_running", "分析完成", { activity: "completed", status: "ok" }),
      ]),
      { type: "completed" },
    );
    render(
      <TurnProcessDisclosure
        activity={done}
        reasoningMd="部分推理内容"
        reasoningStatus="completed"
        reasoningTruncated
      />,
    );
    await user.click(screen.getByRole("button"));
    const notice = screen.getByTestId("ask-reasoning-truncated");
    expect(notice.textContent).toBe("已达到展示上限，仅显示部分推理内容。");
  });

  it("non-ok terminal freezes running steps as interrupted (never a checkmark)", async () => {
    const user = userEvent.setup();
    const failed = reduceAgenticActivityEvent(
      liveTurn([
        progress(1, "reading_context", "已读取相关上下文", {
          activity: "completed",
          tool_name: "read_range",
          status: "ok",
          duration_ms: 800,
        }),
        progress(2, "composing_answer", "正在组织回答", {
          activity: "started",
          status: "running",
        }),
      ]),
      { type: "terminal", finalStatus: "failed" },
    );
    render(<TurnProcessDisclosure activity={failed} />);
    const root = screen.getByTestId("ask-turn-process");
    expect(root.getAttribute("data-turn-process-state")).toBe("settled");
    await user.click(screen.getByRole("button"));
    const composing = screen
      .getByText("组织回答")
      .closest("[data-step-status]");
    expect(composing?.getAttribute("data-step-status")).toBe("interrupted");
    const reading = screen
      .getByText("读取文章")
      .closest("[data-step-status]");
    expect(reading?.getAttribute("data-step-status")).toBe("complete");
  });

  it("unavailable/failed summaries never appear in CoT DOM or aria-label", async () => {
    const user = userEvent.setup();
    const activity = liveTurn([
      progress(1, "searching_web", "网页搜索暂不可用", {
        activity: "unavailable",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "unavailable",
        duration_ms: 30,
      }),
      progress(2, "reading_context", "读取文章上下文失败", {
        activity: "failed",
        tool_name: "read_range",
        status: "failed",
        duration_ms: 40,
      }),
    ]);
    const done = reduceAgenticActivityEvent(activity, { type: "completed" });
    render(<TurnProcessDisclosure activity={done} />);
    const trigger = screen.getByRole("button");
    await user.click(trigger);
    const root = screen.getByTestId("ask-turn-process");
    expect(root.textContent).not.toContain("网页搜索暂不可用");
    expect(root.textContent).not.toContain("读取文章上下文失败");
    expect(trigger.getAttribute("aria-label")).not.toContain("网页搜索暂不可用");
    expect(trigger.getAttribute("aria-label")).not.toContain("读取文章上下文失败");
    expect(screen.getByText("网页查询").closest("[data-step-status]")
      ?.getAttribute("data-step-status")).toBe("degraded");
    expect(screen.getByText("读取文章").closest("[data-step-status]")
      ?.getAttribute("data-step-status")).toBe("failed");
  });

  it("started-only tool step is interrupted (not complete) after success terminal", async () => {
    const user = userEvent.setup();
    const done = reduceAgenticActivityEvent(
      liveTurn([
        progress(1, "reading_context", "正在读取文章上下文", {
          activity: "started",
          tool_name: "read_range",
          status: "running",
        }),
      ]),
      { type: "completed" },
    );
    render(<TurnProcessDisclosure activity={done} />);
    await user.click(screen.getByRole("button"));
    const reading = screen
      .getByText("读取文章")
      .closest("[data-step-status]");
    expect(reading?.getAttribute("data-step-status")).toBe("interrupted");
  });

  it("web step shows non-interactive domain chips only post-completed", async () => {
    const user = userEvent.setup();
    const webDone = reduceAgenticActivityEvent(
      liveTurn([
        progress(1, "searching_web", "已完成网页搜索", {
          activity: "completed",
          tool_name: "search_web",
          activity_id: "web_search",
          attempt_count: 2,
          status: "ok",
          duration_ms: 1500,
        }),
      ]),
      { type: "completed" },
    );
    render(
      <TurnProcessDisclosure
        activity={webDone}
        citations={[
          webCitation("https://www.example.com/a?q=1", "c1"),
          webCitation("https://docs.site.org/b", "c2"),
        ]}
      />,
    );
    await user.click(screen.getByRole("button"));
    expect(screen.getByText("已尝试 2 次")).not.toBeNull();
    expect(screen.getByText("example.com")).not.toBeNull();
    expect(screen.getByText("docs.site.org")).not.toBeNull();
    // Non-interactive: no anchors inside the steps region.
    const root = screen.getByTestId("ask-turn-process");
    expect(root.querySelector("a")).toBeNull();
    expect(root.textContent).not.toContain("已完成网页搜索");
  });

  it("cold history (snapshot null, reasoning only) renders a reasoning-only disclosure", async () => {
    const user = userEvent.setup();
    render(
      <TurnProcessDisclosure
        reasoningMd="冷启动的推理文本"
        reasoningStatus="completed"
        reasoningTruncated={false}
      />,
    );
    const trigger = screen.getByRole("button");
    expect(screen.getByText("思考过程")).not.toBeNull();
    await user.click(trigger);
    expect(screen.getByTestId("ask-turn-process-reasoning").textContent)
      .toContain("冷启动的推理文本");
    expect(screen.queryByTestId("ask-agentic-activity")).toBeNull();
  });

  it("a stale-EOF snapshot settles without a permanent shimmer", () => {
    const midRun = liveTurn([
      progress(1, "reading_context", "正在读取文章上下文", {
        activity: "started",
        tool_name: "read_range",
        status: "running",
      }),
    ]);
    const snapshot = buildAgenticProcessSnapshot(midRun);
    render(<TurnProcessDisclosure snapshot={snapshot} />);
    const root = screen.getByTestId("ask-turn-process");
    expect(root.getAttribute("data-turn-process-state")).toBe("settled");
    expect(root.textContent).toContain("处理过程");
  });
});
