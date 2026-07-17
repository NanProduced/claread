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
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ ok: true, message: "反馈已提交。" }),
        }),
      ),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    MyFeedbackListMock.mockClear();
  });

  it("renders three sentiment radios with exactly one selected", () => {
    render(<FeedbackForm />);

    const sentimentGroup = screen.getByRole("group", { name: "总体感受" });
    const radios = sentimentGroup.querySelectorAll('input[type="radio"]');
    expect(radios).toHaveLength(3);
    expect(Array.from(radios).filter((radio) => (radio as HTMLInputElement).checked)).toHaveLength(1);
    expect((Array.from(radios).find((radio) => (radio as HTMLInputElement).value === "neutral") as HTMLInputElement).checked).toBe(true);
  });

  it("renders six feedback type radios", () => {
    render(<FeedbackForm />);
    expect(screen.getAllByRole("radio", { name: /遇到问题|功能建议|配额问题|输入页问题|体验不顺|其他/ })).toHaveLength(6);
  });

  it("keeps submit disabled until content is entered", () => {
    render(<FeedbackForm />);

    const submitButton = screen.getByRole("button", { name: "提交反馈" });
    expect(submitButton.hasAttribute("disabled")).toBe(true);

    fireEvent.change(screen.getByPlaceholderText(/写下具体问题/), {
      target: { value: "something" },
    });

    expect(submitButton.hasAttribute("disabled")).toBe(false);
  });

  it("submits the preserved API payload and clears the form on success", async () => {
    render(<FeedbackForm />);

    fireEvent.click(screen.getByRole("radio", { name: "喜欢" }));
    fireEvent.click(screen.getByRole("radio", { name: "遇到问题" }));
    fireEvent.change(screen.getByPlaceholderText(/写下具体问题/), {
      target: { value: "test content" },
    });

    fireEvent.click(screen.getByRole("button", { name: "提交反馈" }));

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/api/web/feedback",
        expect.objectContaining({
          method: "POST",
          body: expect.stringContaining("\"sentiment\":\"positive\""),
        }),
      );
    });

    const bodyArg = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][1];
    const body = JSON.parse(bodyArg.body);
    expect(body).toMatchObject({
      feedbackScope: "app",
      targetId: "web-settings",
      sentiment: "positive",
      feedbackType: "bug_report",
      content: "test content",
      contextSummary: "设置页应用反馈",
      clientPlatform: "web",
      clientSurface: "settings",
      entryPoint: "settings_form",
      appVersion: "web",
    });

    expect(screen.getByRole("button", { name: "提交反馈" })).toBeTruthy();
    expect((screen.getByPlaceholderText(/写下具体问题/) as HTMLTextAreaElement).value).toBe("");
    expect((screen.getByRole("radio", { name: "功能建议" }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("radio", { name: "建议" }) as HTMLInputElement).checked).toBe(true);
  });

  it("shows submitting state with disabled button", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>((resolve) => setTimeout(() => resolve({ ok: true, json: () => Promise.resolve({ ok: true }) } as Response), 100))),
    );

    render(<FeedbackForm />);

    fireEvent.change(screen.getByPlaceholderText(/写下具体问题/), {
      target: { value: "x" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交反馈" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "提交中" })).toBeTruthy();
    });

    expect(screen.getByRole("button", { name: "提交中" }).hasAttribute("disabled")).toBe(true);
  });

  it("displays error message and keeps content on failed submission", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: false,
          json: () => Promise.resolve({ ok: false, message: "server error" }),
        }),
      ),
    );

    render(<FeedbackForm />);

    fireEvent.change(screen.getByPlaceholderText(/写下具体问题/), {
      target: { value: "kept content" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交反馈" }));

    await waitFor(() => {
      expect(screen.getByRole("status").textContent).toBe("server error");
    });

    expect((screen.getByPlaceholderText(/写下具体问题/) as HTMLTextAreaElement).value).toBe("kept content");
  });

  it("refreshes the feedback list after successful submit", async () => {
    render(<FeedbackForm />);

    fireEvent.change(screen.getByPlaceholderText(/写下具体问题/), {
      target: { value: "x" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交反馈" }));

    await waitFor(() => {
      const calls = MyFeedbackListMock.mock.calls;
      const lastCall = calls[calls.length - 1][0];
      expect(lastCall.refreshKey).toBeGreaterThan(0);
    });
  });

  it("does not contain decorative images or keyframe animations", () => {
    const { container } = render(<FeedbackForm />);

    expect(container.querySelectorAll("img")).toHaveLength(0);
    expect(container.querySelector("style")?.textContent ?? "").not.toContain("@keyframes");
  });

  it("uses a single visible focus ring contract on radio labels", () => {
    const { container } = render(<FeedbackForm />);

    const radioInputs = container.querySelectorAll('input[type="radio"]');
    expect(radioInputs.length).toBeGreaterThan(0);
    Array.from(radioInputs).forEach((input) => {
      const label = input.closest("label");
      expect(label).not.toBeNull();
      expect(label!.className).toContain("focus-within:ring-2");
      expect(label!.className).toContain("focus-within:ring-lens-blue");
    });
  });

  it("uses the same active visual contract for sentiment and type radios", () => {
    render(<FeedbackForm />);

    fireEvent.click(screen.getByRole("radio", { name: "喜欢" }));
    fireEvent.click(screen.getByRole("radio", { name: "遇到问题" }));

    const checkedInputs = document.querySelectorAll('input[type="radio"]:checked');
    expect(checkedInputs.length).toBe(2);

    Array.from(checkedInputs).forEach((input) => {
      const label = input.closest("label");
      expect(label).not.toBeNull();
      expect(label!.className).toContain("border-lens-blue");
      expect(label!.className).toContain("bg-surface-canvas");
      expect(label!.className).not.toContain("border-ink");
      expect(label!.className).not.toContain("bg-ink");
    });
  });
});
