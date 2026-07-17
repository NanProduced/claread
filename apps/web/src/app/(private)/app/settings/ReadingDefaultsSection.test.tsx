/** @vitest-environment jsdom */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
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
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("ReadingDefaultsSection", () => {
  it("renders the current reading goal and variant", () => {
    render(
      <ReadingDefaultsSection
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
        canEdit
      />,
    );

    expect(screen.getByText("日常阅读")).toBeTruthy();
    expect(screen.getByText("进阶")).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "保存默认值" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("enters dirty state after a change and enables save", () => {
    render(
      <ReadingDefaultsSection
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
        canEdit
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "备考精读" }));
    expect(
      (screen.getByRole("button", { name: "保存默认值" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(
      (screen.getByRole("button", { name: "保存默认值" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("sends PATCH payload with settings.default_reading_goal and settings.default_reading_variant", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));

    render(
      <ReadingDefaultsSection
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
        canEdit
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "备考精读" }));
    fireEvent.click(screen.getByRole("button", { name: "保存默认值" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/web/profile",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({
            settings: {
              default_reading_goal: "exam",
              default_reading_variant: "cet",
            },
          }),
        }),
      );
    });
  });

  it("shows success state after saving", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));

    render(
      <ReadingDefaultsSection
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
        canEdit
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "备考精读" }));
    fireEvent.click(screen.getByRole("button", { name: "保存默认值" }));

    await waitFor(() => {
      expect(screen.getByText("默认透读模式已保存。")).toBeTruthy();
    });
  });

  it("shows error state when saving fails", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ ok: false, message: "保存失败" }, 400),
    );

    render(
      <ReadingDefaultsSection
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
        canEdit
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "备考精读" }));
    fireEvent.click(screen.getByRole("button", { name: "保存默认值" }));

    await waitFor(() => {
      expect(screen.getByText("保存失败")).toBeTruthy();
    });
  });

  it("is not editable when canEdit is false", () => {
    render(
      <ReadingDefaultsSection
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
        canEdit={false}
      />,
    );

    expect(
      (screen.getByRole("button", { name: "保存默认值" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      screen.getByText("当前会话未连接真实账户，无法保存共享默认值。"),
    ).toBeTruthy();
  });
});
