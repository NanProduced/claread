/** @vitest-environment jsdom */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LearnerReasoningPanel } from "./LearnerReasoningPanel";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("LearnerReasoningPanel", () => {
  it("renders nothing without a valid snapshot", () => {
    const { container } = render(
      <LearnerReasoningPanel text={null} status={null} />
    );
    expect(container.innerHTML).toBe("");
  });

  it("mounts only after first snapshot with title 思路摘要", () => {
    render(
      <LearnerReasoningPanel text="正在梳理问题要点" status="streaming" />
    );
    expect(screen.getByTestId("ask-learner-reasoning")).toBeTruthy();
    expect(screen.getByText("思路摘要")).toBeTruthy();
    expect(screen.getByText("正在梳理问题要点")).toBeTruthy();
  });

  it("live streaming starts open; settle one-shot closes; user re-open stays", () => {
    vi.useFakeTimers();
    const { rerender } = render(
      <LearnerReasoningPanel text="正在梳理问题要点" status="streaming" />
    );
    const trigger = screen.getByTestId("ask-learner-reasoning-trigger");
    expect(trigger.getAttribute("aria-expanded")).toBe("true");

    rerender(
      <LearnerReasoningPanel text="正在梳理问题要点" status="completed" />
    );
    act(() => {
      vi.advanceTimersByTime(1100);
    });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");

    // User re-opens — must not be force-closed again after one-shot.
    fireEvent.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
  });

  it("does not introduce a scroll owner", () => {
    const { container } = render(
      <LearnerReasoningPanel text="结合证据核对结论" status="completed" />
    );
    const scrollable = container.querySelector(
      "[data-scroll-owner], .overflow-y-auto, .overflow-auto"
    );
    expect(scrollable).toBeNull();
  });

  it("renders pure text without markdown link affordance", () => {
    render(
      <LearnerReasoningPanel text="结合证据核对结论" status="completed" />
    );
    const root = screen.getByTestId("ask-learner-reasoning");
    expect(root.querySelector("a")).toBeNull();
    expect(root.textContent).not.toMatch(/deepseek|qwen|https?:/i);
  });
});
