/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConversationShell } from "./ConversationShell";

const scrollHarness = vi.hoisted(() => ({
  targetScrollTop: null as null | ((
    defaultTarget: number,
    elements: { scrollElement: HTMLElement; contentElement: HTMLElement },
  ) => number),
  scrollToBottom: vi.fn(() => Promise.resolve(true)),
  scrollElement: null as HTMLElement | null,
  contentElement: null as HTMLElement | null,
}));

vi.mock("@/components/ai-elements/conversation", () => ({
  Conversation: ({
    children,
    targetScrollTop,
    className,
  }: {
    children: ReactNode;
    targetScrollTop?: typeof scrollHarness.targetScrollTop;
    className?: string;
  }) => {
    scrollHarness.targetScrollTop = targetScrollTop ?? null;
    return (
      <div data-testid="conversation-root" className={className}>
        {children}
      </div>
    );
  },
  ConversationContent: ({ children }: { children: ReactNode }) => (
    <div data-testid="conversation-content">{children}</div>
  ),
}));

vi.mock("use-stick-to-bottom", () => ({
  useStickToBottomContext: () => ({
    scrollToBottom: scrollHarness.scrollToBottom,
    scrollRef: { current: scrollHarness.scrollElement },
    contentRef: { current: scrollHarness.contentElement },
  }),
}));

function rect(top: number, height = 40): DOMRect {
  return {
    x: 0,
    y: top,
    top,
    bottom: top + height,
    left: 0,
    right: 100,
    width: 100,
    height,
    toJSON: () => ({}),
  };
}

function targetElements() {
  const user = document.createElement("div");
  user.dataset.testid = "ask-user-message";
  user.getBoundingClientRect = () => rect(400);
  const contentElement = document.createElement("div");
  contentElement.append(user);
  const scrollElement = document.createElement("div");
  Object.defineProperty(scrollElement, "scrollTop", {
    configurable: true,
    value: 0,
    writable: true,
  });
  scrollElement.getBoundingClientRect = () => rect(0, 500);
  return { scrollElement, contentElement };
}

describe("ConversationShell", () => {
  beforeEach(() => {
    scrollHarness.targetScrollTop = null;
    scrollHarness.scrollToBottom.mockClear();
    scrollHarness.scrollElement = document.createElement("div");
    scrollHarness.contentElement = document.createElement("div");
    Object.defineProperties(scrollHarness.scrollElement, {
      scrollTop: { configurable: true, value: 300, writable: true },
      scrollHeight: { configurable: true, value: 1000 },
      clientHeight: { configurable: true, value: 500 },
    });
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders children when hasMessages is true", () => {
    render(
      <ConversationShell hasMessages latestUserMessageId="user-1">
        <div data-testid="message">hello</div>
      </ConversationShell>,
    );

    expect(screen.getByTestId("message").textContent).toBe("hello");
  });

  it("renders emptyState when hasMessages is false", () => {
    render(
      <ConversationShell
        hasMessages={false}
        latestUserMessageId={null}
        emptyState={<div data-testid="empty">empty</div>}
      >
        <div data-testid="message">hello</div>
      </ConversationShell>,
    );

    expect(screen.getByTestId("empty").textContent).toBe("empty");
    expect(screen.queryByTestId("message")).toBeNull();
  });

  it("forwards className and contentClassName to the conversation wrapper", () => {
    render(
      <ConversationShell
        hasMessages
        latestUserMessageId="user-1"
        className="outer-shell"
        contentClassName="inner-shell"
      >
        <div>content</div>
      </ConversationShell>,
    );

    expect(screen.getByText("content").closest("[class*=outer-shell]")).not.toBeNull();
  });

  it("switches from question anchor to persistent natural-bottom follow until the next user turn", () => {
    const { rerender } = render(
      <ConversationShell hasMessages latestUserMessageId="user-1">
        <div>answer</div>
      </ConversationShell>,
    );

    const elements = targetElements();
    expect(scrollHarness.targetScrollTop?.(900, elements)).toBe(384);
    expect(screen.getByTestId("ask-jump-to-latest")).toBeTruthy();

    fireEvent.click(screen.getByTestId("ask-jump-to-latest"));
    expect(scrollHarness.scrollToBottom).toHaveBeenCalledTimes(1);
    expect(scrollHarness.targetScrollTop?.(900, elements)).toBe(900);
    expect(scrollHarness.targetScrollTop?.(1200, elements)).toBe(1200);

    rerender(
      <ConversationShell hasMessages latestUserMessageId="user-2">
        <div>next answer</div>
      </ConversationShell>,
    );
    expect(scrollHarness.targetScrollTop?.(900, elements)).toBe(384);
  });
});
