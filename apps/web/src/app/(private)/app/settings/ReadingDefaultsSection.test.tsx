/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReadingDefaultsSection } from "./ReadingDefaultsSection";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function renderEditable() {
  return render(<ReadingDefaultsSection readingGoal="daily_reading" readingVariant="intermediate_reading" canEdit />);
}

describe("ReadingDefaultsSection", () => {
  it("renders current goal and variant without an inactive action bar", () => {
    renderEditable();
    expect(screen.getByText("日常阅读")).toBeTruthy();
    expect(screen.getByText("进阶")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "保存默认值" })).toBeNull();
  });

  it("enters dirty state after a change and can cancel back to the quiet state", () => {
    renderEditable();
    fireEvent.click(screen.getByRole("button", { name: "备考精读" }));
    expect((screen.getByRole("button", { name: "保存默认值" }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("button", { name: "保存默认值" })).toBeNull();
  });

  it("sends the preserved PATCH payload", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));
    renderEditable();
    fireEvent.click(screen.getByRole("button", { name: "备考精读" }));
    fireEvent.click(screen.getByRole("button", { name: "保存默认值" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/web/profile",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ settings: { default_reading_goal: "exam", default_reading_variant: "cet" } }) }),
    ));
  });

  it("shows success and error feedback after a save", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true }));
    renderEditable();
    fireEvent.click(screen.getByRole("button", { name: "备考精读" }));
    fireEvent.click(screen.getByRole("button", { name: "保存默认值" }));
    await waitFor(() => expect(screen.getByText("默认透读模式已保存。")).toBeTruthy());

    cleanup();
    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: false, message: "保存失败" }, 400));
    renderEditable();
    fireEvent.click(screen.getByRole("button", { name: "备考精读" }));
    fireEvent.click(screen.getByRole("button", { name: "保存默认值" }));
    await waitFor(() => expect(screen.getByText("保存失败")).toBeTruthy());
  });

  it("explains why shared defaults cannot be saved without an account", () => {
    render(<ReadingDefaultsSection readingGoal="daily_reading" readingVariant="intermediate_reading" canEdit={false} />);
    expect(screen.queryByRole("button", { name: "保存默认值" })).toBeNull();
    expect(screen.getByText("当前会话未连接真实账户，无法保存共享默认值。")).toBeTruthy();
  });
});