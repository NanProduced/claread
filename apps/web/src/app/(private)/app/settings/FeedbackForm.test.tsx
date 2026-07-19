/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FeedbackForm } from "./FeedbackForm";

const MyFeedbackListMock = vi.hoisted(() => vi.fn<(props: { refreshKey?: number }) => null>(() => null));

vi.mock("./MyFeedbackList", () => ({
  MyFeedbackList: MyFeedbackListMock,
}));

describe("FeedbackForm", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ ok: true, message: "反馈已提交。" }),
    })));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    MyFeedbackListMock.mockClear();
  });

  it("renders three sentiment radios with exactly one selected", () => {
    render(<FeedbackForm />);
    const group = screen.getByRole("group", { name: "总体感受" });
    const radios = group.querySelectorAll('input[type="radio"]');
    expect(radios).toHaveLength(3);
    expect(Array.from(radios).filter((radio) => (radio as HTMLInputElement).checked)).toHaveLength(1);
  });

  it("renders one feedback-type select with all supported options and a description", () => {
    render(<FeedbackForm />);
    const select = screen.getByRole("combobox", { name: "反馈类型" }) as HTMLSelectElement;
    expect(select.value).toBe("feature_request");
    expect(select.querySelectorAll("option")).toHaveLength(6);
    expect(screen.getByText("希望 Claread 增加什么")).toBeTruthy();
    fireEvent.change(select, { target: { value: "bug_report" } });
    expect(screen.getByText("出错、异常或结果不对")).toBeTruthy();
  });

  it("keeps submit disabled until content is entered", () => {
    render(<FeedbackForm />);
    const submitButton = screen.getByRole("button", { name: "提交反馈" });
    expect((submitButton as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByPlaceholderText(/写下具体问题/), { target: { value: "something" } });
    expect((submitButton as HTMLButtonElement).disabled).toBe(false);
  });

  it("submits the preserved API payload and resets compact controls on success", async () => {
    render(<FeedbackForm />);
    fireEvent.click(screen.getByRole("radio", { name: "喜欢" }));
    fireEvent.change(screen.getByRole("combobox", { name: "反馈类型" }), { target: { value: "bug_report" } });
    fireEvent.change(screen.getByPlaceholderText(/写下具体问题/), { target: { value: "test content" } });
    fireEvent.click(screen.getByRole("button", { name: "提交反馈" }));

    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/web/feedback",
      expect.objectContaining({ method: "POST", body: expect.stringContaining('"sentiment":"positive"') }),
    ));
    const body = JSON.parse((globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][1].body);
    expect(body).toMatchObject({
      feedbackScope: "app", targetId: "web-settings", sentiment: "positive", feedbackType: "bug_report",
      content: "test content", contextSummary: "设置页应用反馈", clientPlatform: "web",
      clientSurface: "settings", entryPoint: "settings_form", appVersion: "web",
    });
    expect((screen.getByPlaceholderText(/写下具体问题/) as HTMLTextAreaElement).value).toBe("");
    expect((screen.getByRole("combobox", { name: "反馈类型" }) as HTMLSelectElement).value).toBe("feature_request");
  });

  it("shows submitting and error states without discarding content", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({
      ok: false,
      json: () => Promise.resolve({ ok: false, message: "server error" }),
    })));
    render(<FeedbackForm />);
    fireEvent.change(screen.getByPlaceholderText(/写下具体问题/), { target: { value: "kept content" } });
    fireEvent.click(screen.getByRole("button", { name: "提交反馈" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("server error"));
    expect((screen.getByPlaceholderText(/写下具体问题/) as HTMLTextAreaElement).value).toBe("kept content");
  });

  it("refreshes the feedback list after successful submit", async () => {
    render(<FeedbackForm />);
    fireEvent.change(screen.getByPlaceholderText(/写下具体问题/), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "提交反馈" }));
    await waitFor(() => expect(MyFeedbackListMock.mock.calls.at(-1)?.[0].refreshKey).toBeGreaterThan(0));
  });

  it("uses one compact focusable choice grammar without decorative media", () => {
    const { container } = render(<FeedbackForm />);
    expect(container.querySelectorAll("img")).toHaveLength(0);
    expect(container.querySelector("style")?.textContent ?? "").not.toContain("@keyframes");
    const labels = container.querySelectorAll('input[type="radio"]');
    expect(labels).toHaveLength(3);
    labels.forEach((input) => expect(input.closest("label")?.className).toContain("focus-within:ring-2"));
    expect(container.querySelectorAll('input[name="feedback-type"]')).toHaveLength(0);
  });
});