/** @vitest-environment jsdom */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConversationShell } from "./ConversationShell";

describe("ConversationShell", () => {
  beforeEach(() => {
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
      <ConversationShell hasMessages>
        <div data-testid="message">hello</div>
      </ConversationShell>,
    );

    expect(screen.getByTestId("message").textContent).toBe("hello");
  });

  it("renders emptyState when hasMessages is false", () => {
    render(
      <ConversationShell
        hasMessages={false}
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
        className="outer-shell"
        contentClassName="inner-shell"
      >
        <div>content</div>
      </ConversationShell>,
    );

    expect(screen.getByText("content").closest("[class*=outer-shell]")).not.toBeNull();
  });
});