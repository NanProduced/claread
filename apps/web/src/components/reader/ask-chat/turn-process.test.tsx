/** @vitest-environment jsdom */
import {
  cleanup,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
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

function activity(
  events: AgenticActivityEvent[],
  ids: { messageId?: string; turnRunId?: string } = {},
): AgenticActivityState {
  const started = reduceAgenticActivityEvent(createIdleAgenticActivityState(), {
    type: "run_started",
    messageId: ids.messageId ?? "msg-1",
    turnRunId: ids.turnRunId ?? "turn-1",
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

const twoSettledSteps = activity([
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
]);

describe("TurnProcessDisclosure — unified process disclosure", () => {
  it("renders nothing before reasoning or real process steps exist", () => {
    expect(render(<TurnProcessDisclosure />).container.firstChild).toBeNull();
    cleanup();
    expect(
      render(<TurnProcessDisclosure activity={createIdleAgenticActivityState()} />)
        .container.firstChild,
    ).toBeNull();
    cleanup();
    // A live run that has produced neither reasoning nor a typed step must
    // not render an empty container.
    expect(
      render(<TurnProcessDisclosure activity={activity([])} isStreaming />)
        .container.firstChild,
    ).toBeNull();
  });

  it("auto-expands when provider reasoning starts and streams without entering the live region", async () => {
    const view = render(
      <TurnProcessDisclosure activity={activity([])} isStreaming />,
    );
    expect(view.container.firstChild).toBeNull();

    view.rerender(
      <TurnProcessDisclosure
        activity={activity([])}
        isStreaming
        reasoningMd=""
        reasoningStatus="streaming"
      />,
    );

    const trigger = await screen.findByRole("button");
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(trigger.textContent).toContain("正在思考");
    expect(screen.getByTestId("ask-turn-process-announcement").textContent).toBe(
      "开始思考",
    );

    view.rerender(
      <TurnProcessDisclosure
        activity={activity([])}
        isStreaming
        reasoningMd="先判断句子主干。"
        reasoningStatus="streaming"
      />,
    );
    await screen.findByText(/先判断句子主干/);
    // Reasoning deltas themselves never enter the polite live region.
    expect(screen.getByTestId("ask-turn-process-announcement").textContent).toBe(
      "开始思考",
    );
  });

  it("collapses automatically at the first formal answer delta", async () => {
    const view = render(
      <TurnProcessDisclosure
        activity={activity([])}
        isStreaming
        reasoningMd="推理进行中"
        reasoningStatus="streaming"
      />,
    );
    expect((await screen.findByRole("button")).getAttribute("aria-expanded")).toBe(
      "true",
    );

    // Providers may begin the answer before the reasoning lane emits its
    // terminal event. The first answer delta must still win and collapse.
    view.rerender(
      <TurnProcessDisclosure
        activity={twoSettledSteps}
        isStreaming
        reasoningMd="推理进行中"
        reasoningStatus="streaming"
        answerStarted
        turnStatus="streaming"
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe(
        "false",
      );
    });
    expect(screen.getByRole("button").textContent).toContain("思考完毕");
    expect(screen.getByTestId("ask-turn-process-announcement").textContent).toBe(
      "开始生成回答",
    );
  });

  it("keeps a manually chosen expansion across the answer transition", async () => {
    const user = userEvent.setup();
    const view = render(
      <TurnProcessDisclosure
        activity={activity([])}
        isStreaming
        reasoningMd="推理进行中"
        reasoningStatus="streaming"
      />,
    );
    expect((await screen.findByRole("button")).getAttribute("aria-expanded")).toBe(
      "true",
    );

    // The user closes and re-opens: this attempt now follows the manual pick.
    await user.click(screen.getByRole("button"));
    expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("false");
    await user.click(screen.getByRole("button"));
    expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("true");

    view.rerender(
      <TurnProcessDisclosure
        activity={twoSettledSteps}
        isStreaming
        reasoningMd="推理进行中"
        reasoningStatus="completed"
        answerStarted
        turnStatus="streaming"
      />,
    );

    expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("true");
  });

  it("resets to automatic behavior on a retry attempt", async () => {
    const user = userEvent.setup();
    const view = render(
      <TurnProcessDisclosure
        activity={activity([], { turnRunId: "run-1" })}
        reasoningMd="第一轮推理"
        reasoningStatus="completed"
        answerStarted
        turnStatus="completed"
      />,
    );
    const firstTrigger = await screen.findByRole("button");
    expect(firstTrigger.getAttribute("aria-expanded")).toBe("false");
    // Manual re-open at the end of attempt one pins the choice…
    await user.click(firstTrigger);
    expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("true");

    // Completed messages temporarily detach live activity before retry binds the
    // next run id. That idle gap must not erase the previous attempt identity.
    view.rerender(
      <TurnProcessDisclosure
        activity={null}
        reasoningMd="第一轮推理"
        reasoningStatus="completed"
        answerStarted
        turnStatus="completed"
      />,
    );

    // …but attempt two starts a fresh automatic lifecycle: the next answer
    // transition collapses on its own instead of honoring attempt-one's
    // manual open.
    view.rerender(
      <TurnProcessDisclosure
        activity={activity([], { turnRunId: "run-2" })}
        isStreaming
        reasoningMd=""
        reasoningStatus="streaming"
        turnStatus="streaming"
      />,
    );
    expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("true");

    view.rerender(
      <TurnProcessDisclosure
        activity={{ ...twoSettledSteps, turnRunId: "run-2" }}
        isStreaming
        reasoningMd="第二轮推理"
        reasoningStatus="completed"
        answerStarted
        turnStatus="streaming"
      />,
    );
    await waitFor(() => {
      expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe(
        "false",
      );
    });
  });

  it("keeps rendering when only the reasoning lane is live without any activity binding", async () => {
    render(
      <TurnProcessDisclosure
        activity={null}
        isStreaming
        reasoningMd="仅推理在线。"
        reasoningStatus="streaming"
        turnStatus="streaming"
      />,
    );
    const trigger = await screen.findByRole("button");
    expect(trigger.textContent).toContain("正在思考");
  });

  it("restores a cold completed turn collapsed once, without persisting expansion", async () => {
    const user = userEvent.setup();
    render(
      <TurnProcessDisclosure
        reasoningMd="历史推理文本。"
        reasoningStatus="completed"
        reasoningVisibilityStatus="complete"
        turnStatus="completed"
      />,
    );

    const trigger = screen.getByRole("button");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(trigger.textContent).toContain("思考完毕");
    await user.click(trigger);
    expect(screen.getByText("历史推理文本。")).not.toBeNull();
  });

  it("owns reasoning and steps inside exactly one disclosure and drops the old process label", async () => {
    const user = userEvent.setup();
    render(
      <TurnProcessDisclosure
        activity={twoSettledSteps}
        reasoningMd="推理与步骤同属一个容器。"
        reasoningStatus="completed"
        turnStatus="completed"
      />,
    );

    const root = screen.getByTestId("ask-turn-process");
    expect(document.querySelectorAll('[data-testid="ask-turn-process"]')).toHaveLength(1);
    expect(root.querySelectorAll("button")).toHaveLength(1);
    expect(root.textContent).toContain("思考完毕");
    expect(root.textContent).not.toContain("回答过程");

    await user.click(screen.getByRole("button"));
    expect(screen.getByText("推理与步骤同属一个容器。")).not.toBeNull();
    expect(screen.getByText("分析问题")).not.toBeNull();
    expect(screen.getByText("查找文章依据")).not.toBeNull();
  });

  it("titles settled turns by reasoning outcome with an optional step count", () => {
    const completed = render(
      <TurnProcessDisclosure
        activity={twoSettledSteps}
        reasoningMd="完成推理。"
        reasoningStatus="completed"
        turnStatus="completed"
      />,
    );
    const completedTrigger = screen.getByRole("button");
    expect(completedTrigger.textContent).toMatch(/^思考完毕/);
    expect(completedTrigger.textContent).toContain("个步骤");
    expect(completedTrigger.textContent).not.toContain("来源");
    completed.unmount();

    const stopped = render(
      <TurnProcessDisclosure
        activity={reduceAgenticActivityEvent(twoSettledSteps, {
          type: "terminal",
          finalStatus: "cancelled",
        })}
        reasoningMd="部分推理。"
        reasoningStatus="interrupted"
        turnStatus="interrupted"
      />,
    );
    expect(screen.getByRole("button").textContent).toContain("思考已停止");
    stopped.unmount();

    const failed = render(
      <TurnProcessDisclosure
        activity={reduceAgenticActivityEvent(twoSettledSteps, {
          type: "terminal",
          finalStatus: "failed",
        })}
        reasoningMd="部分推理。"
        reasoningStatus="interrupted"
        turnStatus="failed"
      />,
    );
    expect(screen.getByRole("button").textContent).toContain("思考未完成");
    failed.unmount();
  });

  it("caps long reasoning with its own scroll region while steps stay outside it", async () => {
    const user = userEvent.setup();
    render(
      <TurnProcessDisclosure
        activity={twoSettledSteps}
        reasoningMd={"很长的推理".repeat(400)}
        reasoningStatus="completed"
        turnStatus="completed"
      />,
    );

    await user.click(screen.getByRole("button"));
    const reasoning = screen.getByTestId("ask-turn-process-reasoning");
    expect(reasoning.className).toContain("overflow-y-auto");
    expect(reasoning.className).toContain("overflow-x-hidden");
    expect(reasoning.className).toContain("max-h-[min(176px,28dvh)]");
    expect(reasoning.className).not.toContain("border-l");
    expect(reasoning.getAttribute("role")).toBe("region");
    expect(reasoning.getAttribute("tabindex")).toBe("0");
    // Long URLs and unbroken strings wrap instead of scrolling sideways.
    expect(reasoning.className).toContain("whitespace-pre-wrap");
    expect(reasoning.className).toContain("break-words");
    // The step rail lives outside the reasoning scroll region.
    const rail = document.querySelector('[data-testid="ask-turn-process-rail"]');
    if (rail) {
      expect(reasoning.contains(rail)).toBe(false);
    }
    // The disclosure content itself never becomes a second scroll owner.
    const content = document.querySelector('[data-slot="chain-of-thought-content"]');
    expect(content?.className).not.toContain("overflow-y");
  });

  it("renders truncation and safety notes inside the single container", async () => {
    const user = userEvent.setup();

    // Length truncation keeps the visible prefix plus a quiet in-container note.
    const truncated = render(
      <TurnProcessDisclosure
        reasoningMd="被截断的推理前缀。"
        reasoningStatus="completed"
        reasoningTruncated
        reasoningVisibilityStatus="truncated"
        turnStatus="completed"
      />,
    );
    await user.click(screen.getByRole("button"));
    const truncatedRoot = screen.getByTestId("ask-turn-process");
    expect(truncatedRoot.textContent).toContain("被截断的推理前缀。");
    expect(screen.getByTestId("ask-turn-process-truncated-note").textContent).toBe(
      "内容较长，仅展示部分",
    );
    expect(truncatedRoot.querySelectorAll('[role="status"]')).toHaveLength(1);
    truncated.unmount();
    cleanup();

    // Partial safety filtering keeps the shown part plus a neutral note whose
    // accessible description explains the hiding without alarmist copy.
    const blocked = render(
      <TurnProcessDisclosure
        reasoningMd="可见的思考片段。"
        reasoningStatus="completed"
        reasoningVisibilityStatus="blocked"
        turnStatus="completed"
      />,
    );
    await user.click(screen.getByRole("button"));
    const blockedNote = screen.getByTestId("ask-turn-process-blocked-note");
    expect(blockedNote.textContent).toBe("部分思考未展示");
    expect(
      blockedNote.getAttribute("title") ?? blockedNote.getAttribute("aria-label"),
    ).toContain("为避免展示可能包含敏感信息的内容，部分思考已隐藏。");
    expect(
      screen.getByTestId("ask-turn-process").querySelectorAll('[role="status"]'),
    ).toHaveLength(1);
    blocked.unmount();
    cleanup();

    // Fully filtered reasoning still shows a titled, explained disclosure —
    // never an empty shell.
    const fullyBlocked = render(
      <TurnProcessDisclosure
        reasoningMd=""
        reasoningStatus="completed"
        reasoningVisibilityStatus="blocked"
        turnStatus="completed"
      />,
    );
    expect(screen.getByRole("button").textContent).toContain(
      "部分内容未展示",
    );
    await user.click(screen.getByRole("button"));
    expect(
      screen.getByText("为避免展示可能包含敏感信息的内容，部分思考已隐藏。"),
    ).not.toBeNull();
    expect(screen.queryByTestId("ask-turn-process-reasoning")).toBeNull();
    fullyBlocked.unmount();
    cleanup();

    const alarmist = document.body.textContent ?? "";
    expect(alarmist).not.toContain("违反安全规则");
    expect(alarmist).not.toContain("内容不安全");
  });

  it("keeps a stop in place and announces it once", async () => {
    const runningAnalysis = activity([
      progress(1, "analysis", "PRIVATE", {
        activity: "started",
        status: "running",
      }),
    ]);
    const view = render(
      <TurnProcessDisclosure
        activity={runningAnalysis}
        isStreaming
        reasoningMd="停止前的推理。"
        reasoningStatus="streaming"
        turnStatus="streaming"
      />,
    );
    expect((await screen.findByRole("button")).getAttribute("aria-expanded")).toBe(
      "true",
    );

    view.rerender(
      <TurnProcessDisclosure
        activity={reduceAgenticActivityEvent(runningAnalysis, {
          type: "terminal",
          finalStatus: "cancelled",
        })}
        reasoningMd="停止前的推理。"
        reasoningStatus="interrupted"
        turnStatus="interrupted"
      />,
    );

    // Stop preserves whatever expansion state the viewer had.
    expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("button").textContent).toContain("思考已停止");
    expect(screen.getByTestId("ask-turn-process-announcement").textContent).toBe(
      "思考已停止",
    );
  });

  it("keeps failed-turn reasoning viewable under the failure title", async () => {
    const view = render(
      <TurnProcessDisclosure
        activity={activity([])}
        isStreaming
        reasoningMd="失败前的推理。"
        reasoningStatus="streaming"
        turnStatus="streaming"
      />,
    );
    expect((await screen.findByRole("button")).getAttribute("aria-expanded")).toBe(
      "true",
    );

    view.rerender(
      <TurnProcessDisclosure
        activity={reduceAgenticActivityEvent(activity([]), {
          type: "terminal",
          finalStatus: "failed",
        })}
        reasoningMd="失败前的推理。"
        reasoningStatus="interrupted"
        turnStatus="failed"
      />,
    );

    expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("button").textContent).toContain("思考未完成");
    await waitFor(() => {
      expect(screen.queryByText("失败前的推理。")).not.toBeNull();
    });
  });

  it("uses fixed step labels and semantic icons", async () => {
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
    await user.click(screen.getByRole("button"));
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

  it("renders only permitted dynamic step metadata and never full URLs", async () => {
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

  it("exposes a native keyboard-operable trigger with one scroll owner", async () => {
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
    const trigger = screen.getByRole("button");
    expect(trigger.tagName).toBe("BUTTON");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(trigger.getAttribute("aria-controls")).toBeTruthy();
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
      await user.click(screen.getByRole("button"));
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
    await user.click(screen.getByRole("button"));
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

  it("shows the typed citation-check failure explanation", async () => {
    const failed = reduceAgenticActivityEvent(
      activity([
        progress(1, "validating_evidence", "PRIVATE", {
          activity: "failed",
          status: "failed",
          outcome: "failed",
        }),
      ]),
      { type: "terminal", finalStatus: "failed" },
    );
    const user = userEvent.setup();
    render(<TurnProcessDisclosure activity={failed} />);
    await user.click(screen.getByRole("button"));
    expect(screen.getByText("引用检查未通过，本轮回答未完成。")).not.toBeNull();
  });
});
