/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AccountSection } from "./AccountSection";

afterEach(cleanup);

vi.mock("../NicknameEditor", () => ({
  NicknameEditor: ({ initialNickname, displayFallback }: { initialNickname: string; displayFallback?: string }) => (
    <div
      data-testid="nickname-editor"
      data-nickname={initialNickname}
      data-fallback={displayFallback}
    />
  ),
}));

vi.mock("../LogoutButton", () => ({
  LogoutButton: () => <button type="button" data-testid="logout-button">退出登录</button>,
}));

describe("AccountSection", () => {
  const baseProps = {
    nickname: "Alice",
    displayFallback: "Alice Display",
    avatarText: "A",
  };

  it("renders the compact avatar text in a quiet raised surface", () => {
    const { container } = render(<AccountSection {...baseProps} status="ready" />);

    expect(screen.getByText("A")).toBeTruthy();
    const avatar = container.querySelector(".bg-surface-raised");
    expect(avatar).not.toBeNull();
    expect(avatar?.textContent).toBe("A");
  });

  it("renders NicknameEditor with nickname and displayFallback props", () => {
    render(<AccountSection {...baseProps} status="ready" />);

    const editor = screen.getByTestId("nickname-editor");
    expect(editor).toBeTruthy();
    expect(editor.getAttribute("data-nickname")).toBe("Alice");
    expect(editor.getAttribute("data-fallback")).toBe("Alice Display");
  });

  it("renders the three section group labels", () => {
    render(<AccountSection {...baseProps} status="ready" />);

    expect(screen.getByText("账户")).toBeTruthy();
    expect(screen.getByText("登录信息")).toBeTruthy();
    expect(screen.getByText("会话")).toBeTruthy();
  });

  it("renders the ready status label", () => {
    render(<AccountSection {...baseProps} status="ready" />);

    expect(screen.getByText("已连接")).toBeTruthy();
  });

  it("renders accurate status labels for non-ready states", () => {
    render(<AccountSection {...baseProps} status="upstream_unavailable" />);

    expect(screen.getByText("服务不可用")).toBeTruthy();
  });

  it("renders LogoutButton when status is ready", () => {
    render(<AccountSection {...baseProps} status="ready" />);

    expect(screen.getByTestId("logout-button")).toBeTruthy();
    expect(screen.queryByText("重新登录")).toBeNull();
  });

  it("renders the re-login link when status is unauthenticated", () => {
    render(<AccountSection {...baseProps} status="unauthenticated" />);

    const link = screen.getByText("重新登录");
    expect(link).toBeTruthy();
    expect(link.closest("a")?.getAttribute("href")).toContain("/login");
    expect(link.closest("a")?.getAttribute("href")).toContain("next=");
    expect(screen.queryByTestId("logout-button")).toBeNull();
  });
});
