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
    phone: "13800000000",
    avatarText: "A",
  };

  it("renders the avatar text", () => {
    render(<AccountSection {...baseProps} status="ready" />);

    expect(screen.getByText("A")).toBeTruthy();
  });

  it("renders NicknameEditor with nickname and displayFallback props", () => {
    render(<AccountSection {...baseProps} status="ready" />);

    const editor = screen.getByTestId("nickname-editor");
    expect(editor).toBeTruthy();
    expect(editor.getAttribute("data-nickname")).toBe("Alice");
    expect(editor.getAttribute("data-fallback")).toBe("Alice Display");
  });

  it("renders the phone number with status label for ready status", () => {
    render(<AccountSection {...baseProps} status="ready" />);

    expect(screen.getByText("13800000000")).toBeTruthy();
    expect(screen.getByText("已连接")).toBeTruthy();
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

  it("renders the re-login link when status is limited_debug", () => {
    render(<AccountSection {...baseProps} status="limited_debug" />);

    expect(screen.getByText("重新登录")).toBeTruthy();
    expect(screen.queryByTestId("logout-button")).toBeNull();
  });

  it("falls back to 'Web User' label when phone is missing", () => {
    render(<AccountSection {...baseProps} phone={undefined} status="ready" />);

    expect(screen.getByText("Web User")).toBeTruthy();
  });

  it("shows status label as amber when status is not ready", () => {
    const { container } = render(
      <AccountSection {...baseProps} status="upstream_unavailable" />,
    );

    const statusSpan = container.querySelector(".text-amber-600");
    expect(statusSpan).not.toBeNull();
    expect(statusSpan?.textContent).toBe("服务不可用");
  });
});
