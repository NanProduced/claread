/** @vitest-environment jsdom */

import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ReadingRecordListItemVm } from "@/services/bff/reading-records";
import { LibraryClient } from "./LibraryClient";

const navigationMock = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => {
  const stableSearchParams = new URLSearchParams();
  return {
    useRouter: () => navigationMock,
    usePathname: () => "/app/library",
    useSearchParams: () => stableSearchParams,
  };
});

function createMemoryStorage(): Storage {
  const store = new Map<string, string>();

  return {
    get length() {
      return store.size;
    },
    clear() {
      store.clear();
    },
    getItem(key: string) {
      return store.get(key) ?? null;
    },
    key(index: number) {
      return Array.from(store.keys())[index] ?? null;
    },
    removeItem(key: string) {
      store.delete(key);
    },
    setItem(key: string, value: string) {
      store.set(key, value);
    },
  };
}

function makeReadingRecord(
  overrides: Partial<ReadingRecordListItemVm> = {},
): ReadingRecordListItemVm {
  return {
    readingRecordId: "reading_record_1",
    readerUrl: "/app/reader/reading_record_1",
    title: "New Reading Record",
    createdAt: "2026-06-22T00:00:00Z",
    sourceType: "text",
    productState: "readable_enhancing",
    readinessState: "article_ready",
    lastEventSequence: 2,
    lastOpenedAt: null,
    sourceLabel: "粘贴文本",
    ...overrides,
  };
}

beforeEach(() => {
  vi.resetAllMocks();
  Object.defineProperty(window, "sessionStorage", {
    configurable: true,
    value: createMemoryStorage(),
  });
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    callback(0);
    return 1;
  });
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  vi.stubGlobal(
    "ResizeObserver",
    class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  Object.defineProperty(window.HTMLElement.prototype, "scrollTo", {
    configurable: true,
    value: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("LibraryClient", () => {
  it("renders only the Reading Record group without legacy reader links", () => {
    render(
      <LibraryClient
        readingRecords={[makeReadingRecord()]}
        readingRecordsStatus="ready"
      />,
    );

    expect(screen.getByRole("heading", { name: "阅读记录" })).toBeTruthy();

    const newRecordLink = document.querySelector(
      'a[href="/app/reader/reading_record_1"]',
    );
    expect(newRecordLink?.getAttribute("href")).toBe(
      "/app/reader/reading_record_1",
    );

    const legacyLinks = document.querySelectorAll('a[href^="/app/reader/"]');
    expect(legacyLinks).toHaveLength(0);

    expect(screen.getByText("共 1 篇记录")).toBeTruthy();
  });

  it("renders multiple reading records with correct links", () => {
    render(
      <LibraryClient
        readingRecords={[
          makeReadingRecord(),
          makeReadingRecord({
            readingRecordId: "reading_record_2",
            readerUrl: "/app/reader/reading_record_2",
            title: "Second Reading",
          }),
        ]}
        readingRecordsStatus="ready"
      />,
    );

    expect(screen.getByText("New Reading Record")).toBeTruthy();
    expect(screen.getByText("Second Reading")).toBeTruthy();
    expect(screen.getByText("共 2 篇记录")).toBeTruthy();
  });

  it("searches by title only (display_title), not by sourceLabel", () => {
    render(
      <LibraryClient
        readingRecords={[
          makeReadingRecord({ title: "Climate Notes" }),
          makeReadingRecord({
            readingRecordId: "reading_record_2",
            readerUrl: "/app/reader/reading_record_2",
            title: "Exam Strategy",
            sourceLabel: "climate keyword buried in source label",
          }),
        ]}
        readingRecordsStatus="ready"
      />,
    );

    const input = screen.getByLabelText("搜索阅读记录标题");
    fireEvent.change(input, { target: { value: "climate" } });

    expect(screen.getByText("Climate Notes")).toBeTruthy();
    expect(screen.queryByText("Exam Strategy")).toBeNull();
    expect(screen.getByText("找到 1 篇记录")).toBeTruthy();
  });

  it("shows empty state CTA when there are no records", () => {
    render(
      <LibraryClient
        readingRecords={[]}
        readingRecordsStatus="ready"
      />,
    );

    expect(
      screen.getByText("还没有阅读记录。提交一篇新解读后会在这里显示。"),
    ).toBeTruthy();

    const ctaLink = screen.getByText("提交一篇新解读").closest("a");
    expect(ctaLink?.getAttribute("href")).toBe("/app/read");
  });

  it("shows login CTA when BFF returns auth_required", () => {
    render(
      <LibraryClient
        readingRecords={[]}
        readingRecordsStatus="auth_required"
        readingRecordsMessage="请先登录后查看阅读记录。"
      />,
    );

    expect(screen.getByText("请先登录后查看阅读记录。")).toBeTruthy();

    const loginLink = screen.getByText("去登录").closest("a");
    expect(loginLink?.getAttribute("href")).toBe("/login");
  });

  it("shows login CTA when BFF returns upstream_auth_failed", () => {
    render(
      <LibraryClient
        readingRecords={[]}
        readingRecordsStatus="upstream_auth_failed"
        readingRecordsMessage="登录态已失效，请重新登录后再试。"
      />,
    );

    expect(screen.getByText("登录态已失效，请重新登录后再试。")).toBeTruthy();

    const loginLink = screen.getByText("去登录").closest("a");
    expect(loginLink?.getAttribute("href")).toBe("/login");
  });

  it("shows debug state without login CTA when BFF returns limited_debug", () => {
    render(
      <LibraryClient
        readingRecords={[]}
        readingRecordsStatus="limited_debug"
        readingRecordsMessage="当前登录态无法访问阅读记录，请使用完整登录会话。"
      />,
    );

    expect(
      screen.getByText("当前登录态无法访问阅读记录，请使用完整登录会话。"),
    ).toBeTruthy();
    expect(screen.queryByText("去登录")).toBeNull();
  });

  it("shows generic error state without raw code for upstream_unavailable", () => {
    render(
      <LibraryClient
        readingRecords={[]}
        readingRecordsStatus="upstream_unavailable"
        readingRecordsMessage="透读服务暂时不可用，请稍后重试。"
      />,
    );

    expect(screen.getByText("透读服务暂时不可用，请稍后重试。")).toBeTruthy();
    expect(screen.queryByText("upstream_unavailable")).toBeNull();
    expect(screen.queryByText("去登录")).toBeNull();
  });

  it("does not render legacy section, archive copy, or favorite/delete controls", () => {
    render(
      <LibraryClient
        readingRecords={[makeReadingRecord()]}
        readingRecordsStatus="ready"
      />,
    );

    expect(screen.queryByText("Legacy Records")).toBeNull();
    expect(screen.queryByText("旧记录")).toBeNull();
    expect(screen.queryByText("过渡入口")).toBeNull();
    expect(screen.queryByText("Reading Archive.")).toBeNull();
    expect(screen.queryByText("收藏")).toBeNull();
    expect(screen.queryByText("删除")).toBeNull();
    expect(screen.queryByText("按阅读目标浏览")).toBeNull();
    expect(screen.queryByText("最近重读")).toBeNull();
  });
});
