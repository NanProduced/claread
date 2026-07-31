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
      elapsed_ms: sequence * 2000,
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

/**
 * R0.4 — sentinel patterns that must NEVER appear in CoT DOM, aria-label,
 * or hidden nodes. Even if a malicious/buggy reducer let them through in
 * the `summary` field, the projection must not render them.
 */
const SENTINEL_PATTERNS = [
  "Let me search for more context",
  "[引用]",
  "evh_abc123def4567890123456789012ab",
  "search_web",
  "provider error: rate_limit_exceeded",
  "https://internal.provider.example.com/v1/chat",
  "terminal_reason: agent_run_failed",
  "tool_name: read_range",
  "run_id: run-abc-123",
  "message_id: msg-xyz-456",
] as const;

describe("TurnProcessDisclosure — R0 process content safety", () => {
  it("renders nothing when there is no activity and no snapshot", () => {
    const { container } = render(
      <TurnProcessDisclosure activity={createIdleAgenticActivityState()} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing for cold history with no snapshot and no reasoning", () => {
    const { container } = render(
      <TurnProcessDisclosure />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("R3: cold history WITH a safe reasoning projection renders a reasoning-only disclosure (no fabricated steps)", async () => {
    const user = userEvent.setup();
    render(
      <TurnProcessDisclosure
        reasoningMd={"先确认问题范围。\n\n再整理回答。"}
        reasoningStatus="completed"
        reasoningTruncated={false}
      />,
    );
    const root = screen.getByTestId("ask-turn-process");
    expect(root.getAttribute("data-turn-process-state")).toBe("settled");
    expect(screen.getByText("思考过程")).not.toBeNull();
    await user.click(screen.getByRole("button"));
    // Reasoning section renders; no process steps are fabricated.
    expect(screen.getByTestId("ask-turn-process-reasoning")).not.toBeNull();
    expect(root.textContent).toContain("思考要点");
    expect(root.textContent).toContain("先确认问题范围。");
    expect(root.querySelector("[data-step-status]")).toBeNull();
  });

  it("R3: pure-answer turn preserves 已整理回答 summary after success", async () => {
    // Only composing-answer happened (no reading-context, no web-search).
    // R1-rework: EVERY successful answer preserves a learner-facing summary.
    const done = reduceAgenticActivityEvent(
      liveTurn([
        progress(1, "composing_answer", "正在组织回答", {
          activity: "started",
          status: "running",
        }),
      ]),
      { type: "completed" },
    );
    const user = userEvent.setup();
    render(<TurnProcessDisclosure activity={done} />);
    const root = screen.getByTestId("ask-turn-process");
    expect(root.getAttribute("data-turn-process-state")).toBe("settled");
    // elapsed_ms = sequence * 2000 = 2000 → durationS = 2s
    expect(screen.getByText("已整理回答 · 2s")).not.toBeNull();
    // R3: 理解问题 → 整理回答 — the disclosure is a real collapse control
    // with the honest host-provable lifecycle pair.
    const trigger = screen.getByRole("button");
    await user.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(root.textContent).toContain("理解问题");
    expect(root.textContent).toContain("整理回答");
  });

  it("R1-rework P0: pure agent-running turn preserves 已整理回答 summary after success", () => {
    const done = reduceAgenticActivityEvent(
      liveTurn([
        progress(1, "agent_running", "分析完成", {
          activity: "completed",
          status: "ok",
        }),
      ]),
      { type: "completed" },
    );
    render(<TurnProcessDisclosure activity={done} />);
    const root = screen.getByTestId("ask-turn-process");
    expect(root.getAttribute("data-turn-process-state")).toBe("settled");
    expect(screen.getByText("已整理回答 · 2s")).not.toBeNull();
  });
});

describe("TurnProcessDisclosure — R0 running header", () => {
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
    // R0.3 — running header shows the user-facing label with 正在 prefix,
    // never the bare step label and never the server summary.
    expect(screen.getByText("正在查询网页")).not.toBeNull();
  });

  it("running header for reading-context shows 正在阅读本文", () => {
    const activity = liveTurn([
      progress(1, "reading_context", "正在读取文章上下文", {
        activity: "started",
        tool_name: "read_range",
        status: "running",
      }),
    ]);
    render(<TurnProcessDisclosure activity={activity} isStreaming />);
    expect(screen.getByText("正在阅读本文")).not.toBeNull();
  });

  it("running header for composing-answer shows 正在整理回答", () => {
    const activity = liveTurn([
      progress(1, "composing_answer", "正在组织回答", {
        activity: "started",
        status: "running",
      }),
    ]);
    render(<TurnProcessDisclosure activity={activity} isStreaming />);
    expect(screen.getByText("正在整理回答")).not.toBeNull();
  });

  it("running header for internal phase (agent-running) falls back to 正在整理回答", () => {
    const activity = liveTurn([
      progress(1, "agent_running", "开始分析", {
        activity: "started",
        status: "running",
      }),
    ]);
    render(<TurnProcessDisclosure activity={activity} isStreaming />);
    // Internal phase label 分析问题 must NEVER appear in the running header.
    expect(screen.queryByText("分析问题")).toBeNull();
    expect(screen.queryByText("开始分析")).toBeNull();
    expect(screen.getByText("正在整理回答")).not.toBeNull();
  });

  it("running header for internal phase (validating-evidence) falls back to 正在整理回答", () => {
    const activity = liveTurn([
      progress(1, "composing_answer", "正在组织回答", {
        activity: "started",
        status: "running",
      }),
      progress(2, "validating_evidence", "正在核对回答依据", {
        activity: "started",
        status: "running",
      }),
    ]);
    render(<TurnProcessDisclosure activity={activity} isStreaming />);
    expect(screen.queryByText("核对依据")).toBeNull();
    expect(screen.queryByText("正在核对回答依据")).toBeNull();
    // Falls back to the last visible step (composing) → 正在整理回答
    expect(screen.getByText("正在整理回答")).not.toBeNull();
  });
});

describe("TurnProcessDisclosure — R0 visible steps", () => {
  it("expanding reveals the learner steps with fixed labels (no internal stages)", async () => {
    const user = userEvent.setup();
    const activity = liveTurn([
      progress(1, "agent_running", "开始分析", { activity: "started", status: "running" }),
      progress(2, "agent_running", "分析完成", {
        activity: "completed",
        status: "ok",
        duration_ms: 100,
      }),
      progress(3, "reading_context", "已读取相关上下文", {
        activity: "completed",
        tool_name: "read_range",
        status: "ok",
        duration_ms: 1200,
      }),
      progress(4, "composing_answer", "正在组织回答", {
        activity: "started",
        status: "running",
      }),
    ]);
    render(<TurnProcessDisclosure activity={activity} isStreaming />);

    await user.click(screen.getByTestId("ask-agentic-activity"));
    // R3: 理解问题 (host) + 阅读本文 + 整理回答 are visible. 分析问题 is
    // internal and must NOT appear in the expanded content.
    expect(screen.getByText("理解问题")).not.toBeNull();
    expect(screen.getByText("阅读本文")).not.toBeNull();
    expect(screen.getByText("整理回答")).not.toBeNull();
    expect(screen.queryByText("分析问题")).toBeNull();
    expect(screen.queryByText("检索文章")).toBeNull();
    expect(screen.queryByText("核对依据")).toBeNull();
    // Server summaries must not leak into CoT DOM.
    const root = screen.getByTestId("ask-turn-process");
    expect(root.textContent).not.toContain("已读取相关上下文");
    expect(root.textContent).not.toContain("正在组织回答");
    expect(root.textContent).not.toContain("开始分析");
    expect(root.textContent).not.toContain("分析完成");
  });

  it("R3: after settle, composing-answer stays visible as complete (not hidden)", async () => {
    const user = userEvent.setup();
    const done = reduceAgenticActivityEvent(
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
      { type: "completed" },
    );
    render(<TurnProcessDisclosure activity={done} />);
    await user.click(screen.getByRole("button"));
    // Full learner narrative preserved after completion.
    expect(screen.getByText("理解问题")).not.toBeNull();
    expect(screen.getByText("阅读本文")).not.toBeNull();
    const composing = screen.getByText("整理回答").closest("[data-step-status]");
    expect(composing?.getAttribute("data-step-status")).toBe("complete");
  });

  it("R3: failed settled turn keeps 整理回答 as interrupted", async () => {
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
    await user.click(screen.getByRole("button"));
    const composing = screen.getByText("整理回答").closest("[data-step-status]");
    expect(composing?.getAttribute("data-step-status")).toBe("interrupted");
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
    expect(screen.getByText("阅读本文").closest("[data-step-status]")
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
      .getByText("阅读本文")
      .closest("[data-step-status]");
    expect(reading?.getAttribute("data-step-status")).toBe("interrupted");
  });
});

describe("TurnProcessDisclosure — R0 settled one-liner", () => {
  it("R1-rework: article-only success shows '已根据当前文章整理 · Ns'", () => {
    const done = reduceAgenticActivityEvent(
      liveTurn([
        progress(1, "reading_context", "已读取相关上下文", {
          activity: "completed",
          tool_name: "read_range",
          status: "ok",
          duration_ms: 1200,
        }),
      ]),
      { type: "completed" },
    );
    render(<TurnProcessDisclosure activity={done} />);
    expect(screen.getByText("已根据当前文章整理 · 2s")).not.toBeNull();
  });

  it("web success shows '已查询网页 · N 个来源 · Ns'", () => {
    const done = reduceAgenticActivityEvent(
      liveTurn([
        progress(1, "searching_web", "已完成网页搜索", {
          activity: "completed",
          tool_name: "search_web",
          activity_id: "web_search",
          status: "ok",
          duration_ms: 1500,
        }),
      ]),
      { type: "completed" },
    );
    render(
      <TurnProcessDisclosure
        activity={done}
        citations={[
          webCitation("https://www.example.com/a?q=1", "c1"),
          webCitation("https://docs.site.org/b", "c2"),
        ]}
      />,
    );
    expect(screen.getByText("已查询网页 · 2 个来源 · 2s")).not.toBeNull();
  });

  it("failed terminal shows '未完成' (no duration suffix)", () => {
    const failed = reduceAgenticActivityEvent(
      liveTurn([
        progress(1, "reading_context", "已读取相关上下文", {
          activity: "completed",
          tool_name: "read_range",
          status: "ok",
          duration_ms: 800,
        }),
      ]),
      { type: "terminal", finalStatus: "failed" },
    );
    render(<TurnProcessDisclosure activity={failed} />);
    expect(screen.getByText("未完成")).not.toBeNull();
  });

  it("cancelled terminal shows '已取消' (no duration suffix)", () => {
    const cancelled = reduceAgenticActivityEvent(
      liveTurn([
        progress(1, "reading_context", "已读取相关上下文", {
          activity: "completed",
          tool_name: "read_range",
          status: "ok",
          duration_ms: 800,
        }),
      ]),
      { type: "terminal", finalStatus: "cancelled" },
    );
    render(<TurnProcessDisclosure activity={cancelled} />);
    expect(screen.getByText("已取消")).not.toBeNull();
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
    // Stale-EOF snapshot with a reading-context step → "未完成" (no
    // business terminal ever arrived).
    expect(root.textContent).toContain("未完成");
  });
});

describe("TurnProcessDisclosure — R0 web process rules", () => {
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

  it("web step with no citations shows '已查询网页 · 0 个来源' (no fabricated sources)", () => {
    const done = reduceAgenticActivityEvent(
      liveTurn([
        progress(1, "searching_web", "已完成网页搜索", {
          activity: "completed",
          tool_name: "search_web",
          activity_id: "web_search",
          status: "ok",
          duration_ms: 1500,
        }),
      ]),
      { type: "completed" },
    );
    render(<TurnProcessDisclosure activity={done} citations={[]} />);
    // No fabricated sources: 0 个来源, and no hostname chips when expanded.
    expect(screen.getByText("已查询网页 · 0 个来源 · 2s")).not.toBeNull();
  });
});

describe("TurnProcessDisclosure — R0 sentinel leak guard", () => {
  it("sentinel patterns in server summaries never appear in DOM, aria, or hidden nodes", async () => {
    const user = userEvent.setup();
    // Inject every sentinel into the server `summary` field. The reducer
    // whitelist normally strips them, but the projection must also be
    // leak-proof by construction — it only renders fixed labels.
    const activity = liveTurn([
      progress(1, "agent_running", SENTINEL_PATTERNS[0], {
        activity: "started",
        status: "running",
      }),
      progress(2, "reading_context", SENTINEL_PATTERNS[1], {
        activity: "completed",
        tool_name: "read_range",
        status: "ok",
        duration_ms: 100,
      }),
      progress(3, "searching_web", SENTINEL_PATTERNS[2], {
        activity: "completed",
        tool_name: "search_web",
        activity_id: SENTINEL_PATTERNS[3],
        status: "ok",
        duration_ms: 200,
      }),
      progress(4, "composing_answer", SENTINEL_PATTERNS[4], {
        activity: "started",
        status: "running",
      }),
    ]);
    const done = reduceAgenticActivityEvent(activity, { type: "completed" });
    const { container } = render(
      <TurnProcessDisclosure
        activity={done}
        citations={[
          webCitation(SENTINEL_PATTERNS[5], "c1"),
        ]}
      />,
    );
    // Expand to render the full step content.
    await user.click(screen.getByRole("button"));
    const root = screen.getByTestId("ask-turn-process");
    const rootHtml = root.innerHTML;
    const rootText = root.textContent ?? "";
    const ariaLabel = screen.getByRole("button").getAttribute("aria-label") ?? "";
    const fullDom = container.innerHTML;

    for (const sentinel of SENTINEL_PATTERNS) {
      expect(rootText, `root.textContent must not contain ${sentinel}`).not.toContain(sentinel);
      expect(rootHtml, `root.innerHTML must not contain ${sentinel}`).not.toContain(sentinel);
      expect(ariaLabel, `aria-label must not contain ${sentinel}`).not.toContain(sentinel);
      expect(fullDom, `container.innerHTML must not contain ${sentinel}`).not.toContain(sentinel);
    }
  });

  it("renders no reasoning markup when no safe reasoning projection is provided", async () => {
    const user = userEvent.setup();
    const activity = liveTurn([
      progress(1, "reading_context", "已读取相关上下文", {
        activity: "completed",
        tool_name: "read_range",
        status: "ok",
        duration_ms: 100,
      }),
      progress(2, "composing_answer", "正在组织回答", {
        activity: "started",
        status: "running",
      }),
    ]);
    const { container } = render(
      <TurnProcessDisclosure activity={activity} isStreaming />,
    );
    await user.click(screen.getByTestId("ask-agentic-activity"));
    // No reasoning props ⇒ no reasoning section (never an empty shell).
    expect(container.querySelector(".ask-reasoning-response")).toBeNull();
    expect(container.querySelector('[data-testid="ask-turn-process-reasoning"]')).toBeNull();
    expect(container.querySelector('[data-testid="ask-reasoning-truncated"]')).toBeNull();
    expect(container.querySelector('[data-slot="reasoning-truncated"]')).toBeNull();
  });
});

describe("TurnProcessDisclosure — R3 safe reasoning (思考要点)", () => {
  it("renders the safe reasoning projection inside the same disclosure when expanded", async () => {
    const user = userEvent.setup();
    const done = reduceAgenticActivityEvent(
      liveTurn([
        progress(1, "composing_answer", "正在组织回答", {
          activity: "started",
          status: "running",
        }),
      ]),
      { type: "completed" },
    );
    const { container } = render(
      <TurnProcessDisclosure
        activity={done}
        reasoningMd={"先确认范围，再整理回答。"}
        reasoningStatus="completed"
        reasoningTruncated={false}
      />,
    );
    await user.click(screen.getByRole("button"));
    const reasoning = screen.getByTestId("ask-turn-process-reasoning");
    expect(reasoning).not.toBeNull();
    expect(reasoning.textContent).toContain("思考要点");
    expect(reasoning.textContent).toContain("先确认范围，再整理回答。");
    // Rendered through the shared Streamdown pipeline.
    expect(container.querySelector(".ask-reasoning-response")).not.toBeNull();
    // Steps and reasoning share ONE disclosure — no second process card.
    const root = screen.getByTestId("ask-turn-process");
    expect(root.querySelectorAll('[data-slot="chain-of-thought-content"]')).toHaveLength(1);
  });

  it("shows the truncation indicator when reasoning_truncated is set", async () => {
    const user = userEvent.setup();
    const done = reduceAgenticActivityEvent(
      liveTurn([
        progress(1, "composing_answer", "正在组织回答", {
          activity: "started",
          status: "running",
        }),
      ]),
      { type: "completed" },
    );
    render(
      <TurnProcessDisclosure
        activity={done}
        reasoningMd="部分推理内容。"
        reasoningStatus="completed"
        reasoningTruncated
      />,
    );
    await user.click(screen.getByRole("button"));
    const indicator = screen.getByTestId("ask-reasoning-truncated");
    expect(indicator).not.toBeNull();
    expect(indicator.getAttribute("data-slot")).toBe("reasoning-truncated");
    expect(indicator.textContent).toContain("已达到展示上限");
  });

  it("streaming reasoning with empty text shows the label indicator but no body (no empty shell)", async () => {
    const user = userEvent.setup();
    const activity = liveTurn([
      progress(1, "composing_answer", "正在组织回答", {
        activity: "started",
        status: "running",
      }),
    ]);
    const { container } = render(
      <TurnProcessDisclosure
        activity={activity}
        isStreaming
        reasoningMd=""
        reasoningStatus="streaming"
        reasoningTruncated={false}
      />,
    );
    await user.click(screen.getByTestId("ask-agentic-activity"));
    const reasoning = screen.getByTestId("ask-turn-process-reasoning");
    expect(reasoning.textContent).toContain("思考要点");
    // Empty text ⇒ no markdown body rendered.
    expect(container.querySelector(".ask-reasoning-response")).toBeNull();
  });

  it("reasoning re-expansion after the one-shot auto-close stays open", async () => {
    const user = userEvent.setup();
    const activity = liveTurn([
      progress(1, "composing_answer", "正在组织回答", {
        activity: "started",
        status: "running",
      }),
    ]);
    const { rerender } = render(
      <TurnProcessDisclosure
        activity={activity}
        isStreaming
        reasoningMd="推理内容。"
        reasoningStatus="streaming"
      />,
    );
    const trigger = screen.getByRole("button");
    // Expand during streaming, then settle: one-shot auto-close fires.
    await user.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    const done = reduceAgenticActivityEvent(activity, { type: "completed" });
    rerender(
      <TurnProcessDisclosure
        activity={done}
        reasoningMd="推理内容。"
        reasoningStatus="completed"
      />,
    );
    await new Promise((resolve) => setTimeout(resolve, 1200));
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    // User re-expands after settle — must stick.
    await user.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    await new Promise((resolve) => setTimeout(resolve, 1200));
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("ask-turn-process-reasoning")).not.toBeNull();
  });
});

describe("TurnProcessDisclosure — R0 auto-close contract", () => {
  it("manual expand after settle does NOT auto-close (one-shot only during stream)", async () => {
    const user = userEvent.setup();
    const done = reduceAgenticActivityEvent(
      liveTurn([
        progress(1, "reading_context", "已读取相关上下文", {
          activity: "completed",
          tool_name: "read_range",
          status: "ok",
          duration_ms: 800,
        }),
      ]),
      { type: "completed" },
    );
    render(<TurnProcessDisclosure activity={done} />);
    const trigger = screen.getByRole("button");
    // Settled state: expanding must persist (no auto-close on settle
    // because the stream never ran — hasEverStreamedRef stays false).
    await user.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    // Wait past the auto-close delay to confirm no timer fires.
    await new Promise((resolve) => setTimeout(resolve, 1200));
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
  });
});
