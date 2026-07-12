// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
  dismiss: vi.fn(),
}));

vi.mock("../toast", () => ({ toast: toastMock }));

import { NotificationCenterTrigger, notify } from ".";

afterEach(() => {
  cleanup();
  notify.clear();
  vi.clearAllMocks();
});

describe("NotificationCenterTrigger", () => {
  it("keeps a recoverable alert in the center while presenting a clear toast action pair", () => {
    render(<NotificationCenterTrigger />);

    act(() => {
      notify.alert({
        id: "reader-polling",
        tone: "warning",
        title: "自动刷新已暂停",
        description: "阅读服务暂时不可用，请稍后重试。",
        action: { label: "重试", onClick: vi.fn() },
      });
    });

    expect(toastMock.warning).toHaveBeenCalledWith(
      "自动刷新已暂停",
      expect.objectContaining({
        id: "reader-polling",
        position: "top-center",
        closeButton: false,
        cancel: expect.objectContaining({ label: "关闭提示" }),
        action: expect.objectContaining({ label: "重试" }),
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "打开通知中心，1 条未读" }));

    expect(screen.getByRole("heading", { name: "通知" })).toBeTruthy();
    expect(screen.getByRole("dialog").style.zIndex).toBe("var(--app-z-shell-overlay)");
    expect(screen.getByText("阅读服务暂时不可用，请稍后重试。")).toBeTruthy();
    expect(screen.getByRole("button", { name: "全部已读" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "全部已读" }));
    expect(screen.getByText("当前没有未读通知")).toBeTruthy();
  });

  it("runs an alert action, marks it as read, and allows the entry to be dismissed", () => {
    const retry = vi.fn();
    render(<NotificationCenterTrigger />);

    act(() => {
      notify.alert({
        id: "retryable-sync",
        tone: "error",
        title: "同步未完成",
        action: { label: "重试", onClick: retry },
      });
    });

    fireEvent.click(screen.getByRole("button", { name: "打开通知中心，1 条未读" }));
    fireEvent.click(screen.getByRole("button", { name: "重试" }));

    expect(retry).toHaveBeenCalledOnce();
    expect(screen.getByText("当前没有未读通知")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "关闭通知：同步未完成" }));
    expect(screen.getByText("暂时没有需要处理的通知。")).toBeTruthy();
    expect(toastMock.dismiss).toHaveBeenCalledWith("retryable-sync");
  });
});
