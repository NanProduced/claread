/** @vitest-environment jsdom */

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CommandPaletteDialog } from "./CommandPaletteDialog";
import { useCommandPalette } from "./useCommandPalette";

const navigationMock = vi.hoisted(() => ({
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => navigationMock,
}));

const openSettingsMock = vi.hoisted(() => vi.fn());

vi.mock("@/components/settings/SettingsDialogProvider", () => ({
  useSettingsDialog: () => ({ openSettings: openSettingsMock }),
}));

function makeReadingRecord(overrides: Record<string, unknown> = {}) {
  return {
    readingRecordId: "reading_record_1",
    readerUrl: "/app/reader/reading_record_1",
    title: "New Reading Record",
    createdAt: "2026-06-23T08:00:00.000Z",
    sourceType: "text",
    productState: "readable_enhancing",
    readinessState: "article_ready",
    lastEventSequence: 1,
    sourceLabel: "粘贴文本",
    ...overrides,
  };
}

function stubReadingRecordFetch(
  responder: (url: string) => Array<Record<string, unknown>>,
) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const items = responder(url);

    return new Response(
      JSON.stringify({
        ok: true,
        items,
        total: items.length,
        limit: 8,
      }),
      { status: 200, headers: { "content-type": "application/json" } },
    );
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function openCommandPalette() {
  render(<CommandPaletteDialog />);
  act(() => {
    useCommandPalette.getState().setOpen(true);
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal(
    "ResizeObserver",
    class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  Object.defineProperty(window.HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
  useCommandPalette.setState({ open: false });
});

afterEach(() => {
  cleanup();
  useCommandPalette.setState({ open: false });
  vi.unstubAllGlobals();
});

describe("CommandPaletteDialog", () => {
  it("shows recent Reading Records from /api/web/reader/records and opens readerUrl", async () => {
    const fetchMock = stubReadingRecordFetch((url) => {
      expect(url).toBe("/api/web/reader/records?limit=8");
      return [makeReadingRecord()];
    });

    openCommandPalette();

    expect(await screen.findByText("最近阅读记录")).toBeTruthy();
    expect(await screen.findByText("New Reading Record")).toBeTruthy();

    fireEvent.click(screen.getByText("New Reading Record"));

    expect(navigationMock.push).toHaveBeenCalledWith(
      "/app/reader/reading_record_1",
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("searches Reading Records through /api/web/reader/records?query=...", async () => {
    const fetchMock = stubReadingRecordFetch((url) => {
      if (url === "/api/web/reader/records?limit=8") {
        return [makeReadingRecord({ title: "Recent Reading Record" })];
      }

      expect(url.startsWith("/api/web/reader/records?limit=8&query=")).toBe(true);
      return [
        makeReadingRecord({
          readingRecordId: "reading_record_focus",
          readerUrl: "/app/reader/reading_record_focus",
          title: "Focus Reading Record",
        }),
      ];
    });

    openCommandPalette();

    await userEvent.type(screen.getByRole("combobox"), "focus");

    await waitFor(() => {
      expect((screen.getByRole("combobox") as HTMLInputElement).value).toBe("focus");
    });

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).includes("query=focus"),
        ),
      ).toBe(true);
    });

    expect(
      await screen.findByText("Focus Reading Record", {}, { timeout: 3000 }),
    ).toBeTruthy();

    fireEvent.click(screen.getByText("Focus Reading Record"));

    expect(navigationMock.push).toHaveBeenCalledWith(
      "/app/reader/reading_record_focus",
    );
  });

  it("opens the newest Reading Record from the command item", async () => {
    stubReadingRecordFetch(() => [
      makeReadingRecord({
        readingRecordId: "reading_record_latest",
        readerUrl: "/app/reader/reading_record_latest",
        title: "Latest Reading Record",
      }),
    ]);

    openCommandPalette();

    expect(await screen.findByText("Latest Reading Record")).toBeTruthy();

    fireEvent.click(screen.getByText("打开最近文章"));

    expect(navigationMock.push).toHaveBeenCalledWith(
      "/app/reader/reading_record_latest",
    );
  });

  it("refetches Reading Records after closing while the first request is pending", async () => {
    const pendingResponses: Array<(response: Response) => void> = [];
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      expect(url).toBe("/api/web/reader/records?limit=8");

      return new Promise<Response>((resolve) => {
        pendingResponses.push(resolve);
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    openCommandPalette();

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    act(() => {
      useCommandPalette.getState().setOpen(false);
    });
    act(() => {
      useCommandPalette.getState().setOpen(true);
    });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });

    await act(async () => {
      for (const resolve of pendingResponses) {
        resolve(
          new Response(JSON.stringify({ ok: true, items: [], total: 0, limit: 8 }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      await Promise.resolve();
    });
  });

  it("opens the AppShell Settings Dialog (preferences section) when the 设置 command is selected without changing the URL", async () => {
    stubReadingRecordFetch(() => []);

    openCommandPalette();

    const settingsItem = await screen.findByText("设置");
    fireEvent.click(settingsItem);

    // The Settings command must route through openSettings, not router.push.
    expect(openSettingsMock).toHaveBeenCalledWith("preferences");
    expect(openSettingsMock).toHaveBeenCalledTimes(1);
    expect(navigationMock.push).not.toHaveBeenCalled();
  });
});
