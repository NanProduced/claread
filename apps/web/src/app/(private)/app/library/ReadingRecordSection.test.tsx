/** @vitest-environment jsdom */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { ReadingRecordSection } from "./ReadingRecordSection";

function mockFetchOnce(body: unknown) {
  vi.mocked(globalThis.fetch).mockResolvedValueOnce({
    json: async () => body,
  } as Response);
}

function mockFetchError() {
  vi.mocked(globalThis.fetch).mockRejectedValueOnce(new Error("network error"));
}

describe("ReadingRecordSection", () => {
  beforeEach(() => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      json: async () => ({ ok: false, status: 0, code: "upstream_unavailable", message: "" }),
    } as Response);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows loading state before fetch resolves", () => {
    vi.mocked(globalThis.fetch).mockReturnValue(
      new Promise(() => {
        /* never resolves */
      }),
    );

    render(<ReadingRecordSection />);

    expect(screen.getByText("加载新阅读记录中…")).toBeTruthy();
  });

  it("shows empty state when BFF returns no items", async () => {
    mockFetchOnce({
      ok: true,
      items: [],
      total: 0,
      limit: 20,
    });

    render(<ReadingRecordSection />);

    await waitFor(() => {
      expect(
        screen.getByText("还没有新阅读记录。提交一篇新解读后会在这里显示。"),
      ).toBeTruthy();
    });
  });

  it("shows error message when BFF returns an error", async () => {
    mockFetchOnce({
      ok: false,
      status: 503,
      code: "upstream_unavailable",
      message: "透读服务暂时不可用，请稍后重试。",
    });

    render(<ReadingRecordSection />);

    await waitFor(() => {
      expect(
        screen.getByText("透读服务暂时不可用，请稍后重试。"),
      ).toBeTruthy();
    });
  });

  it("shows error message when fetch throws", async () => {
    vi.mocked(globalThis.fetch).mockReset();
    mockFetchError();

    render(<ReadingRecordSection />);

    await waitFor(() => {
      expect(
        screen.getByText("无法加载新阅读记录，请稍后重试。"),
      ).toBeTruthy();
    });
  });

  it("renders items with title, date, status and readerUrl from BFF", async () => {
    mockFetchOnce({
      ok: true,
      items: [
        {
          readingRecordId: "reading_record_1",
          readerUrl: "/app/reader-record/reading_record_1",
          title: "First Reading",
          createdAt: "2026-06-22T00:00:00Z",
          sourceType: "text",
          sourceMetadata: {},
          productState: "readable_enhancing",
          readinessState: "article_ready",
          lastEventSequence: 3,
        },
        {
          readingRecordId: "reading_record_2",
          readerUrl: "/app/reader-record/reading_record_2",
          title: "Second Reading",
          createdAt: "2026-06-21T00:00:00Z",
          sourceType: "text",
          sourceMetadata: {},
          productState: "processing",
          readinessState: "submitted",
          lastEventSequence: 1,
        },
      ],
      total: 2,
      limit: 20,
    });

    render(<ReadingRecordSection />);

    await waitFor(() => {
      expect(screen.getByText("First Reading")).toBeTruthy();
    });

    expect(screen.getByText("Second Reading")).toBeTruthy();

    const firstLink = screen.getByText("First Reading").closest("a");
    expect(firstLink).not.toBeNull();
    expect(firstLink?.getAttribute("href")).toBe("/app/reader-record/reading_record_1");

    const secondLink = screen.getByText("Second Reading").closest("a");
    expect(secondLink?.getAttribute("href")).toBe("/app/reader-record/reading_record_2");

    const firstItem = firstLink!.parentElement!;
    expect(firstItem.textContent).toContain("可读·增强中");
    expect(firstItem.textContent).toContain("文章就绪");

    const secondItem = secondLink!.parentElement!;
    expect(secondItem.textContent).toContain("处理中");
    expect(secondItem.textContent).toContain("已提交");
  });

  it("uses readerUrl from BFF response, not a hardcoded route helper", async () => {
    mockFetchOnce({
      ok: true,
      items: [
        {
          readingRecordId: "reading_record_custom",
          readerUrl: "/app/reader-record/reading_record_custom",
          title: "Custom URL Reading",
          createdAt: "2026-06-22T00:00:00Z",
          sourceType: "text",
          sourceMetadata: {},
          productState: "readable_enhancing",
          readinessState: "article_ready",
          lastEventSequence: 1,
        },
      ],
      total: 1,
      limit: 20,
    });

    render(<ReadingRecordSection />);

    await waitFor(() => {
      expect(screen.getByText("Custom URL Reading")).toBeTruthy();
    });

    const link = screen.getByText("Custom URL Reading").closest("a");
    expect(link?.getAttribute("href")).toBe("/app/reader-record/reading_record_custom");
  });

  it("keeps the section source free of legacy reader route, legacy path and analysis-tasks wiring", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/app/(private)/app/library/ReadingRecordSection.tsx"),
      "utf-8",
    );

    expect(source).not.toContain("legacyAppReaderRoute");
    expect(source).not.toContain("/app/reader/");
    expect(source).not.toContain("analysis-tasks");
  });
});
