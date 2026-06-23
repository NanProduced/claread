/** @vitest-environment jsdom */

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CommandPaletteDialog } from "./CommandPaletteDialog";
import { useCommandPalette } from "./useCommandPalette";

const navigationMock = vi.hoisted(() => ({
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => navigationMock,
}));

function stubCommandPaletteFetch({
  legacyItems = [],
  readingRecordItems = [],
}: {
  legacyItems?: Array<{
    id: string;
    title: string;
    excerpt: string;
    createdAt: string;
  }>;
  readingRecordItems?: Array<Record<string, unknown>>;
}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);

    if (url.startsWith("/api/web/reading-records")) {
      return new Response(
        JSON.stringify({
          ok: true,
          items: readingRecordItems,
          total: readingRecordItems.length,
          limit: 6,
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }

    if (url.startsWith("/api/web/command-palette/records")) {
      return new Response(
        JSON.stringify({ items: legacyItems }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }

    return new Response("Not found", { status: 404 });
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

describe("CommandPaletteDialog Reading Record entries", () => {
  it("shows new Reading Records from the BFF and opens the returned readerUrl", async () => {
    const fetchMock = stubCommandPaletteFetch({
      readingRecordItems: [
        {
          readingRecordId: "reading_record_1",
          readerUrl: "/app/reader-record/reading_record_1",
          title: "New Reading Record",
          createdAt: "2026-06-23T08:00:00.000Z",
          sourceType: "text",
          sourceMetadata: {},
          productState: "readable_enhancing",
          readinessState: "article_ready",
          lastEventSequence: 1,
        },
      ],
    });

    openCommandPalette();

    expect(await screen.findByText("新阅读记录")).toBeTruthy();
    expect(await screen.findByText("New Reading Record")).toBeTruthy();

    fireEvent.click(screen.getByText("New Reading Record"));

    expect(navigationMock.push).toHaveBeenCalledWith(
      "/app/reader-record/reading_record_1",
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/web/reading-records?limit=6",
      expect.any(Object),
    );
  });

  it("keeps legacy command palette records opening the legacy ReaderWorkbench route", async () => {
    stubCommandPaletteFetch({
      legacyItems: [
        {
          id: "legacy-record-1",
          title: "Legacy Article",
          excerpt: "Legacy excerpt",
          createdAt: "2026-06-22T08:00:00.000Z",
        },
      ],
    });

    openCommandPalette();

    expect(await screen.findByText("最近文章")).toBeTruthy();
    expect(await screen.findByText("Legacy Article")).toBeTruthy();

    fireEvent.click(screen.getByText("Legacy Article"));

    expect(navigationMock.push).toHaveBeenCalledWith(
      "/app/reader/legacy-record-1",
    );
  });

  it("refetches new Reading Records after closing while the first request is pending", async () => {
    const pendingReadingRecordResponses: Array<(response: Response) => void> = [];
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);

      if (url.startsWith("/api/web/reading-records")) {
        return new Promise<Response>((resolve) => {
          pendingReadingRecordResponses.push(resolve);
        });
      }

      if (url.startsWith("/api/web/command-palette/records")) {
        return Promise.resolve(
          new Response(JSON.stringify({ items: [] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }

      return Promise.resolve(new Response("Not found", { status: 404 }));
    });
    vi.stubGlobal("fetch", fetchMock);

    openCommandPalette();

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.filter(([input]) =>
          String(input).startsWith("/api/web/reading-records"),
        ),
      ).toHaveLength(1);
    });

    act(() => {
      useCommandPalette.getState().setOpen(false);
    });
    act(() => {
      useCommandPalette.getState().setOpen(true);
    });

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.filter(([input]) =>
          String(input).startsWith("/api/web/reading-records"),
        ),
      ).toHaveLength(2);
    });

    await act(async () => {
      for (const resolve of pendingReadingRecordResponses) {
        resolve(
          new Response(JSON.stringify({ ok: true, items: [], total: 0, limit: 6 }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      await Promise.resolve();
    });
  });
});
