/** @vitest-environment jsdom */
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import {
  createIdleAgenticActivityState,
  reduceAgenticActivityEvent,
  type AgenticActivityEvent,
  type AgenticActivityState,
} from "../ask/agentic-activity";
import type { AgenticCitationDisplayItem } from "../ask/agentic-evidence";
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
      elapsed_ms: sequence * 100,
      ...extras,
    },
  };
}

function activity(events: AgenticActivityEvent[]): AgenticActivityState {
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

function webCitation(url: string, citationId = "c1"): AgenticCitationDisplayItem {
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

describe("TurnProcessDisclosure — Answer Process surface", () => {
  it("renders nothing without a live activity or same-session snapshot", () => {
    expect(render(<TurnProcessDisclosure />).container.firstChild).toBeNull();
    cleanup();
    expect(
      render(<TurnProcessDisclosure activity={createIdleAgenticActivityState()} />)
        .container.firstChild,
    ).toBeNull();
  });

  it("does not render legacy reasoning fields or fabricate a step at T0", () => {
    render(
      <TurnProcessDisclosure
        activity={activity([])}
        isStreaming
        reasoningMd="RAW_REASONING"
        reasoningStatus="completed"
        reasoningTruncated
      />,
    );
    const root = screen.getByTestId("ask-turn-process");
    expect(root.textContent).toContain("回答过程");
    expect(root.textContent).not.toContain("RAW_REASONING");
    expect(root.querySelector("[data-step-status]")).toBeNull();
    expect(root.querySelector('[data-testid="ask-turn-process-reasoning"]')).toBeNull();
    expect(screen.getByTestId("ask-agentic-activity").getAttribute("aria-expanded")).toBe(
      "false",
    );
  });

  it("uses one ChainOfThought with fixed labels and semantic active icons", async () => {
    const state = activity([
      progress(1, "analysis", "PRIVATE ANALYSIS", {
        activity: "completed",
        status: "ok",
      }),
      progress(2, "searching_article", "PRIVATE ARTICLE", {
        activity: "completed",
        tool_name: "search_current_article",
        activity_id: "article_evidence",
        status: "ok",
      }),
      progress(3, "searching_web", "PRIVATE WEB", {
        activity: "completed",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "ok",
      }),
      { type: "answer_started", generationId: 0 },
    ]);
    const user = userEvent.setup();
    render(<TurnProcessDisclosure activity={state} isStreaming />);

    expect(screen.getAllByTestId("ask-turn-process")).toHaveLength(1);
    await user.click(screen.getByTestId("ask-agentic-activity"));
    expect(screen.getByText("分析问题")).not.toBeNull();
    expect(screen.getByText("查找文章依据")).not.toBeNull();
    expect(screen.getByText("查询网页")).not.toBeNull();
    expect(screen.getByText("生成回答")).not.toBeNull();
    expect(screen.queryByText("理解问题")).toBeNull();
    expect(screen.queryByText("阅读本文")).toBeNull();
    expect(screen.queryByText("整理回答")).toBeNull();
    expect(screen.queryByText("正在确认问题意图")).toBeNull();

    expect(
      screen
        .getByTestId("ask-turn-process")
        .querySelector('[data-step-icon="analysis"]')?.getAttribute("class"),
    ).toContain("lucide-brain");
    expect(
      screen
        .getByTestId("ask-turn-process")
        .querySelector('[data-step-icon="article-evidence"]')?.getAttribute("class"),
    ).toContain("lucide-file-search");
    expect(
      screen
        .getByTestId("ask-turn-process")
        .querySelector('[data-step-icon="web-evidence"]')?.getAttribute("class"),
    ).toContain("lucide-earth");
    expect(
      screen
        .getByTestId("ask-turn-process")
        .querySelector('[data-step-icon="answering"]')?.getAttribute("class"),
    ).toContain("lucide-pencil-line");
  });

  it("renders only permitted dynamic metadata and never full URLs", async () => {
    const state = activity([
      progress(1, "searching_web", "PRIVATE WEB", {
        activity: "completed",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "ok",
        attempt_count: 2,
        call_sequence: 2,
      }),
    ]);
    const user = userEvent.setup();
    render(
      <TurnProcessDisclosure
        activity={state}
        webSearchSummary={{ outcome: "no_results", cited_source_count: 0 }}
        citations={[webCitation("https://example.com/private?token=secret")]}
      />,
    );
    await user.click(screen.getByRole("button"));
    const root = screen.getByTestId("ask-turn-process");
    expect(root.textContent).toContain("未找到相关网页结果");
    expect(root.textContent).toContain("已尝试 2 次");
    expect(root.textContent).not.toContain("https://");
    expect(root.textContent).not.toContain("token=secret");
    expect(root.textContent).not.toContain("PRIVATE WEB");
    expect(
      root.querySelector('[data-step-icon="web-evidence"]')?.getAttribute("class"),
    ).toContain(
      "lucide-search-x",
    );
    expect(root.querySelector(".lucide-check")).toBeNull();
  });

  it("uses degraded and interrupted semantic icons without warning prose", async () => {
    const degraded = activity([
      progress(1, "searching_web", "PRIVATE DOWN", {
        activity: "unavailable",
        tool_name: "search_web",
        activity_id: "web_search",
        status: "unavailable",
      }),
    ]);
    const user = userEvent.setup();
    render(<TurnProcessDisclosure activity={degraded} isStreaming />);
    await user.click(screen.getByRole("button"));
    const root = screen.getByTestId("ask-turn-process");
    expect(
      root.querySelector('[data-step-icon="web-evidence"]')?.getAttribute("class"),
    ).toContain(
      "lucide-triangle-alert",
    );
    expect(root.textContent).not.toContain("PRIVATE DOWN");
    expect(root.textContent).not.toContain("provider");

    cleanup();
    const interrupted = reduceAgenticActivityEvent(
      activity([
        progress(1, "searching_article", "PRIVATE ARTICLE", {
          activity: "started",
          tool_name: "search_current_article",
          activity_id: "article_evidence",
          status: "running",
        }),
      ]),
      { type: "terminal", finalStatus: "cancelled" },
    );
    render(<TurnProcessDisclosure activity={interrupted} />);
    await user.click(screen.getByRole("button"));
    expect(
      screen
        .getByTestId("ask-turn-process")
        .querySelector('[data-step-icon="article-evidence"]')?.getAttribute("class"),
    ).toContain("lucide-circle-slash");
  });

  it("keeps one scroll owner and exposes a native accessible trigger", async () => {
    const state = activity([
      progress(1, "searching_article", "PRIVATE ARTICLE", {
        activity: "completed",
        tool_name: "search_current_article",
        activity_id: "article_evidence",
        status: "ok",
      }),
    ]);
    const user = userEvent.setup();
    const { container } = render(<TurnProcessDisclosure activity={state} />);
    const trigger = screen.getByRole("button", { name: /回答过程/ });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    trigger.focus();
    await user.keyboard("{Enter}");
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    await user.keyboard(" ");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    const content = container.querySelector('[data-slot="chain-of-thought-content"]');
    expect(content?.className).not.toContain("overflow-y");
    expect(container.querySelectorAll('[data-slot="chain-of-thought"]')).toHaveLength(1);
  });

  it("announces every step outcome without relying on icon or color", async () => {
    const cases: Array<{
      event: AgenticActivityEvent;
      expected: string;
    }> = [
      {
        event: progress(1, "analysis", "PRIVATE", {
          activity: "completed",
          status: "ok",
          outcome: "success",
        }),
        expected: "已完成",
      },
      {
        event: progress(1, "searching_web", "PRIVATE", {
          activity: "completed",
          tool_name: "search_web",
          activity_id: "web_search",
          status: "ok",
          outcome: "empty",
        }),
        expected: "未找到结果",
      },
      {
        event: progress(1, "searching_web", "PRIVATE", {
          activity: "unavailable",
          tool_name: "search_web",
          activity_id: "web_search",
          status: "unavailable",
          outcome: "degraded",
        }),
        expected: "部分不可用",
      },
      {
        event: progress(1, "searching_web", "PRIVATE", {
          activity: "failed",
          tool_name: "search_web",
          activity_id: "web_search",
          status: "failed",
          outcome: "failed",
        }),
        expected: "失败",
      },
    ];

    const user = userEvent.setup();
    for (const testCase of cases) {
      render(<TurnProcessDisclosure activity={activity([testCase.event])} />);
      await user.click(screen.getByRole("button", { name: /回答过程/ }));
      expect(
        screen.getByTestId("ask-turn-process").querySelector(
          "[data-step-accessible-status]",
        ),
      ).not.toBeNull();
      expect(
        screen
          .getByTestId("ask-turn-process")
          .querySelector("[data-step-accessible-status]")?.textContent,
      ).toBe(testCase.expected);
      cleanup();
    }

    const interrupted = activity([
      progress(1, "searching_article", "PRIVATE", {
        activity: "started",
        tool_name: "search_current_article",
        activity_id: "article_evidence",
        status: "running",
        outcome: null,
      }),
      { type: "terminal", finalStatus: "cancelled" },
    ]);
    render(<TurnProcessDisclosure activity={interrupted} />);
    await user.click(screen.getByRole("button", { name: /回答过程/ }));
    expect(
      screen.getByTestId("ask-turn-process").querySelector(
        "[data-step-accessible-status]",
      ),
    ).not.toBeNull();
    expect(
      screen
        .getByTestId("ask-turn-process")
        .querySelector("[data-step-accessible-status]")?.textContent,
    ).toBe("已中断");
  });
});
