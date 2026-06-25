/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ReadingRecordListItemVm } from "@/services/bff/reading-records";
import type { RecordListItemVm } from "@/types/view/RecordListItemVm";
import { LibraryClient } from "./LibraryClient";

const navigationMock = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => navigationMock,
  usePathname: () => "/app/library",
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("./DeleteRecordButton", () => ({
  DeleteRecordButton: () => <button type="button">删除</button>,
}));

vi.mock("./LibraryFavoriteButton", () => ({
  LibraryFavoriteButton: () => <button type="button">收藏</button>,
}));

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
    readerUrl: "/app/reader-record/reading_record_1",
    title: "New Reading Record",
    createdAt: "2026-06-22T00:00:00Z",
    sourceType: "text",
    sourceMetadata: {},
    productState: "readable_enhancing",
    readinessState: "article_ready",
    lastEventSequence: 2,
    ...overrides,
  };
}

function makeLegacyRecord(
  overrides: Partial<RecordListItemVm> = {},
): RecordListItemVm {
  return {
    id: "legacy_record_1",
    title: "Legacy Reading Record",
    sourceText: "Legacy source text",
    sourceTextExcerpt: "Legacy excerpt",
    sourceType: "article",
    readingGoal: "daily_reading",
    readingVariant: "intermediate_reading",
    analysisStatus: "ready",
    lastOpenedAt: "2026-06-23T00:00:00Z",
    createdAt: "2026-06-22T00:00:00Z",
    updatedAt: "2026-06-23T00:00:00Z",
    wordCount: 220,
    noteCount: 3,
    vocabularyCount: 4,
    isFavorited: false,
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
  it("renders the new Reading Record group above the legacy transition group", () => {
    render(
      <LibraryClient
        records={[makeLegacyRecord()]}
        status="ready"
        readingRecords={[makeReadingRecord()]}
        readingRecordsStatus="ready"
      />,
    );

    expect(screen.getByRole("heading", { name: "阅读记录" })).toBeTruthy();
    expect(screen.getByText("旧记录")).toBeTruthy();

    const newRecordLink = document.querySelector(
      'a[href="/app/reader-record/reading_record_1"]',
    );
    const legacyRecordLink = document.querySelector(
      'a[href="/app/reader/legacy_record_1"]',
    );
    expect(newRecordLink?.getAttribute("href")).toBe("/app/reader-record/reading_record_1");
    expect(legacyRecordLink?.getAttribute("href")).toBe("/app/reader/legacy_record_1");

    expect(screen.getByText("共 2 篇记录")).toBeTruthy();
  });

  it("keeps the legacy transition group visible when Reading Records fail to load", () => {
    render(
      <LibraryClient
        records={[]}
        status="ready"
        readingRecords={[]}
        readingRecordsStatus="upstream_unavailable"
        readingRecordsMessage="透读服务暂时不可用，请稍后重试。"
      />,
    );

    expect(screen.getByText("透读服务暂时不可用，请稍后重试。")).toBeTruthy();
    expect(screen.getByText("旧记录")).toBeTruthy();
    expect(screen.getByText("当前还没有可继续打开的旧记录。")).toBeTruthy();
  });
});
