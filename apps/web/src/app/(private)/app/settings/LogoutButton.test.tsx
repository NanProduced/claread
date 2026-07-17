/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LogoutButton } from "./LogoutButton";

const routerMock = vi.hoisted(() => ({
  refresh: vi.fn(),
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => routerMock,
}));

describe("LogoutButton", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: true })));
    routerMock.refresh.mockClear();
    routerMock.push.mockClear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders as a semantic button with type button", () => {
    render(<LogoutButton />);

    const button = screen.getByRole("button", { name: "退出登录" });
    expect(button).toBeTruthy();
    expect(button.getAttribute("type")).toBe("button");
  });

  it("has a minimum touch target of 44px (min-h-11)", () => {
    render(<LogoutButton />);

    const button = screen.getByRole("button", { name: "退出登录" });
    expect(button.className).toContain("min-h-11");
  });

  it("uses neutral surface hover instead of red or underline styling", () => {
    render(<LogoutButton />);

    const button = screen.getByRole("button", { name: "退出登录" });
    expect(button.className).toContain("hover:bg-surface-raised");
    expect(button.className).not.toContain("hover:underline");
    expect(button.className).not.toContain("hover:text-red-700");
  });

  it("posts to /api/web/auth/logout, refreshes, and redirects to login base on click", async () => {
    render(<LogoutButton />);

    fireEvent.click(screen.getByRole("button", { name: "退出登录" }));

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalledWith("/api/web/auth/logout", { method: "POST" });
      expect(routerMock.refresh).toHaveBeenCalled();
      expect(routerMock.push).toHaveBeenCalledWith("/login");
    });
  });

  it("shows pending state and disables the button while logging out", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>((resolve) => setTimeout(() => resolve({ ok: true } as Response), 50))),
    );

    render(<LogoutButton />);

    const button = screen.getByRole("button", { name: "退出登录" });
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "正在退出..." })).toBeTruthy();
    });

    expect(button.hasAttribute("disabled")).toBe(true);
  });
});
