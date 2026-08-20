/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DailyArticleSaveButton } from "./DailyArticleSaveButton";

const { toastError, toastSuccess } = vi.hoisted(() => ({
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
}));

vi.mock("@/components/primitives/toast", () => ({
  toast: { error: toastError, success: toastSuccess },
}));

beforeEach(() => vi.clearAllMocks());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

describe("Daily Reader save action", () => {
  it("sends signed-out readers to login and back to the same article", () => {
    render(
      <DailyArticleSaveButton
        articleId="daily-test"
        canFavorite={false}
        loginHref="/login?next=%2Fdaily%2Fdaily-test&intent=save"
      />,
    );

    const link = screen.getByRole("link", { name: "加入我的阅读记录" });
    expect(link.getAttribute("href")).toBe("/login?next=%2Fdaily%2Fdaily-test&intent=save");
  });

  it("loads the signed-in state and saves the article without leaving the page", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: true, favorited: false }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: true, favorited: true, message: "已加入阅读记录。" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <DailyArticleSaveButton
        articleId="daily-test"
        canFavorite
        loginHref="/login?next=%2Fdaily%2Fdaily-test&intent=save"
      />,
    );

    const button = await screen.findByRole("button", { name: "加入我的阅读记录" });
    expect(button.getAttribute("aria-pressed")).toBe("false");

    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "已加入阅读记录" }).getAttribute("aria-pressed")).toBe(
        "true",
      );
    });
    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/web/daily-reader/daily-test/favorite", {
      cache: "no-store",
    });
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/web/daily-reader/daily-test/favorite", {
      method: "POST",
    });
    expect(toastSuccess).toHaveBeenCalledWith("已加入阅读记录");
  });

  it("completes a login save intent without requiring a second click", async () => {
    window.history.replaceState({}, "", "/daily/daily-test?intent=save");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: true, favorited: false }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: true, favorited: true }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <DailyArticleSaveButton
        articleId="daily-test"
        autoSave
        canFavorite
        loginHref="/login?next=%2Fdaily%2Fdaily-test&intent=save"
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "已加入阅读记录" })).toBeTruthy();
    });
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/web/daily-reader/daily-test/favorite", {
      method: "POST",
    });
    expect(window.location.search).toBe("");
  });

  it("keeps a retry remove action when removal fails", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: true, favorited: true }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: false, message: "移除失败。" }), { status: 503 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: true, favorited: false }), { status: 200 }),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <DailyArticleSaveButton
        articleId="daily-test"
        canFavorite
        loginHref="/login?next=%2Fdaily%2Fdaily-test&intent=save"
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "已加入阅读记录" }));
    const retry = await screen.findByRole("button", { name: "重试移除" });
    expect(toastError).toHaveBeenCalledWith("移除失败，请重试");
    fireEvent.click(retry);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "加入我的阅读记录" })).toBeTruthy();
    });
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "DELETE")).toHaveLength(2);
  });

  it("offers login again when the server session has expired", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            ok: false,
            code: "upstream_auth_failed",
            message: "登录已失效，请重新登录。",
          }),
          { status: 401 },
        ),
      ),
    );

    render(
      <DailyArticleSaveButton
        articleId="daily-test"
        canFavorite
        loginHref="/login?next=%2Fdaily%2Fdaily-test&intent=save"
      />,
    );

    expect((await screen.findByRole("link", { name: "重新登录" })).getAttribute("href")).toBe(
      "/login?next=%2Fdaily%2Fdaily-test&intent=save",
    );
  });

  it("keeps a visible retry action when the initial state cannot be loaded", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: false, message: "收藏状态读取失败，请重试。" }), {
          status: 503,
          headers: { "content-type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: true, favorited: true }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <DailyArticleSaveButton
        articleId="daily-test"
        canFavorite
        loginHref="/login?next=%2Fdaily%2Fdaily-test&intent=save"
      />,
    );

    const retry = await screen.findByRole("button", { name: "重试收藏" });
    expect(screen.getByRole("status").textContent).toContain("收藏状态读取失败");

    fireEvent.click(retry);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "已加入阅读记录" })).toBeTruthy();
    });
  });
});
