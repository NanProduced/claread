/** @vitest-environment jsdom */
import { act, render, screen, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtHeader,
  ChainOfThoughtSearchResults,
  ChainOfThoughtStep,
  SearchResult,
  formatChainOfThoughtDuration,
} from "./chain-of-thought";

afterEach(cleanup);

function renderDisclosure({
  isStreaming = false,
  open,
  defaultOpen,
}: {
  isStreaming?: boolean;
  open?: boolean;
  defaultOpen?: boolean;
} = {}) {
  return render(
    <ChainOfThought
      isStreaming={isStreaming}
      open={open}
      defaultOpen={defaultOpen}
      data-testid="cot-root"
    >
      <ChainOfThoughtHeader glyph={<span data-testid="cot-glyph" />}>
        处理过程
      </ChainOfThoughtHeader>
      <ChainOfThoughtContent data-testid="cot-content">
        <ChainOfThoughtStep status="active" label="正在分析当前文章" />
        <ChainOfThoughtStep
          status="complete"
          label="已读取相关上下文"
          durationMs={1400}
        />
        <ChainOfThoughtStep
          status="degraded"
          label="网页搜索暂不可用"
          description="已尝试 2 次"
        >
          <ChainOfThoughtSearchResults>
            <SearchResult domain="example.com" />
          </ChainOfThoughtSearchResults>
        </ChainOfThoughtStep>
        <ChainOfThoughtStep status="failed" label="读取文章上下文失败" />
        <ChainOfThoughtStep status="interrupted" label="正在组织回答" />
      </ChainOfThoughtContent>
    </ChainOfThought>,
  );
}

function getTrigger(): HTMLElement {
  const trigger = screen.getByRole("button");
  return trigger;
}

/**
 * Radix Collapsible unmounts closed content, but the `animate-out` exit
 * animation keeps the node mounted in jsdom (no animationend there).
 * "Not visible" = absent OR Radix `data-state="closed"`.
 */
function expectContentCollapsed() {
  const content = screen.queryByTestId("cot-content");
  expect(
    content === null || content.getAttribute("data-state") === "closed",
  ).toBe(true);
}

function stepStatusOf(labelText: string): string | null {
  const label = screen.getByText(labelText);
  return label.closest("[data-step-status]")?.getAttribute("data-step-status") ?? null;
}

describe("ChainOfThought", () => {
  it("stays collapsed by default even while streaming (no auto-open)", () => {
    renderDisclosure({ isStreaming: true });
    expect(getTrigger().getAttribute("aria-expanded")).toBe("false");
    expectContentCollapsed();
  });

  it("expands and collapses on trigger click", async () => {
    const user = userEvent.setup();
    renderDisclosure();
    expect(getTrigger().getAttribute("aria-expanded")).toBe("false");

    await user.click(getTrigger());
    expect(getTrigger().getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("cot-content").getAttribute("data-state")).toBe(
      "open",
    );

    await user.click(getTrigger());
    expect(getTrigger().getAttribute("aria-expanded")).toBe("false");
    expectContentCollapsed();
  });

  it("respects controlled open prop", () => {
    renderDisclosure({ open: true });
    expect(getTrigger().getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("cot-content")).toBeTruthy();
  });

  it("honors defaultOpen=true", () => {
    renderDisclosure({ defaultOpen: true });
    expect(getTrigger().getAttribute("aria-expanded")).toBe("true");
  });

  it("keeps a disclosure open after the user expands it during streaming", () => {
    vi.useFakeTimers();
    try {
      const { rerender } = render(
        <ChainOfThought isStreaming data-testid="cot-root">
          <ChainOfThoughtHeader>处理过程</ChainOfThoughtHeader>
          <ChainOfThoughtContent data-testid="cot-content">内容</ChainOfThoughtContent>
        </ChainOfThought>,
      );
      // User opens it mid-stream.
      act(() => {
        fireEvent.click(screen.getByRole("button"));
      });
      expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("true");

      // Stream settles, but the user's explicit review state must win.
      rerender(
        <ChainOfThought isStreaming={false} data-testid="cot-root">
          <ChainOfThoughtHeader>处理过程</ChainOfThoughtHeader>
          <ChainOfThoughtContent data-testid="cot-content">内容</ChainOfThoughtContent>
        </ChainOfThought>,
      );
      act(() => {
        vi.advanceTimersByTime(1100);
      });
      expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("true");

      act(() => {
        vi.advanceTimersByTime(5000);
      });
      expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("true");
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("ChainOfThoughtStep", () => {
  it("exposes typed status via data-step-status for every status", () => {
    renderDisclosure({ open: true });
    expect(stepStatusOf("正在分析当前文章")).toBe("active");
    expect(stepStatusOf("已读取相关上下文")).toBe("complete");
    expect(stepStatusOf("网页搜索暂不可用")).toBe("degraded");
    expect(stepStatusOf("读取文章上下文失败")).toBe("failed");
    expect(stepStatusOf("正在组织回答")).toBe("interrupted");
  });

  it("renders description and formatted duration", () => {
    renderDisclosure({ open: true });
    expect(screen.getByText("已尝试 2 次")).toBeTruthy();
    expect(screen.getByText("1s")).toBeTruthy();
  });
});

describe("SearchResult chips", () => {
  it("are non-interactive spans without links or buttons", () => {
    renderDisclosure({ open: true });
    const chip = screen
      .getByText("example.com")
      .closest("[data-slot='chain-of-thought-search-result']");
    expect(chip).not.toBeNull();
    expect(chip?.tagName).toBe("SPAN");
    const content = screen.getByTestId("cot-content");
    expect(
      content.querySelector("[data-slot='chain-of-thought-search-results'] a"),
    ).toBeNull();
    expect(
      content.querySelector("[data-slot='chain-of-thought-search-results'] button"),
    ).toBeNull();
  });
});

describe("formatChainOfThoughtDuration", () => {
  it("formats without overstating precision", () => {
    expect(formatChainOfThoughtDuration(null)).toBeNull();
    expect(formatChainOfThoughtDuration(undefined)).toBeNull();
    expect(formatChainOfThoughtDuration(-5)).toBeNull();
    expect(formatChainOfThoughtDuration(Number.NaN)).toBeNull();
    expect(formatChainOfThoughtDuration(400)).toBe("<1s");
    expect(formatChainOfThoughtDuration(1400)).toBe("1s");
    expect(formatChainOfThoughtDuration(63_000)).toBe("63s");
  });
});
