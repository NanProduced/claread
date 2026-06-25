/** @vitest-environment jsdom */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AnalysisLoadingStatusBar, AnalyzeSubmitForm } from "./AnalyzeSubmitForm";
import {
  READ_PAGE_SUBMIT_MODE,
  readPageSubmitEndpoint,
  readPageSubmitRequestBody,
} from "./submit-mode";
import { RECENT_READING_RECORD_STORAGE_KEY } from "./recent-reading-record";

const navigationMock = vi.hoisted(() => ({
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => navigationMock,
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

beforeEach(() => {
  vi.resetAllMocks();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: createMemoryStorage(),
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AnalysisLoadingStatusBar", () => {
  it("uses reassurance copy without fake progress or timers", () => {
    render(
      <AnalysisLoadingStatusBar
        messagePrefix="正在透读"
      />,
    );

    expect(screen.getByText("正在透读")).toBeTruthy();
    expect(screen.getByText("离开本页不会影响透读，完成后会保存到阅读记录")).toBeTruthy();
    expect(screen.getByText("正在梳理文章结构")).toBeTruthy();

    const renderedText = document.body.textContent ?? "";
    expect(renderedText).not.toMatch(/\d{1,2}:\d{2}/);
    expect(renderedText).not.toContain("%");
    expect(renderedText).not.toContain("第");
    expect(renderedText).not.toContain("共");
  });
});

describe("AnalyzeSubmitForm submit cutover", () => {
  it("uses Reading Record submit mode only", () => {
    expect(READ_PAGE_SUBMIT_MODE).toBe("reading-record");
    expect(readPageSubmitEndpoint()).toBe("/api/web/reading-record/submit");
    expect(
      readPageSubmitRequestBody({
        text: "This is a short English article.",
        readingGoal: "daily_reading",
        readingVariant: "intermediate_reading",
      }),
    ).toEqual({ plainText: "This is a short English article." });
  });

  it("submits /app/read to the Reading Record endpoint and lands on reader-record", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/web/reading-record/submit");
      expect(init).toEqual(
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ plainText: "This is a short English article." }),
        }),
      );

      return new Response(
        JSON.stringify({
          ok: true,
          readingRecordId: "reading_record_1",
          readerUrl: "/app/reader-record/reading_record_1",
          snapshot: {
            record: {
              title: "Snapshot title from Reading Record",
            },
          },
          message: "阅读记录已创建，正在打开 Reader。",
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("Paste an English article here"), {
      target: { value: "This is a short English article." },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始透读" }));

    await waitFor(() => {
      expect(navigationMock.push).toHaveBeenCalledWith(
        "/app/reader-record/reading_record_1",
      );
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const saved = JSON.parse(
      window.localStorage.getItem(RECENT_READING_RECORD_STORAGE_KEY) ?? "null",
    ) as Record<string, unknown>;
    expect(saved).toMatchObject({
      readingRecordId: "reading_record_1",
      readerUrl: "/app/reader-record/reading_record_1",
      title: "Snapshot title from Reading Record",
      createdAt: expect.any(String),
    });
    expect(saved.snapshot).toBeUndefined();
  });

  it("loads a valid recent Reading Record from localStorage and continues reading", async () => {
    window.localStorage.setItem(
      RECENT_READING_RECORD_STORAGE_KEY,
      JSON.stringify({
        readingRecordId: "reading_record_recent",
        readerUrl: "/app/reader-record/reading_record_recent",
        title: "Saved recent article",
        createdAt: "2026-06-22T12:00:00.000Z",
      }),
    );

    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Saved recent article")).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: /继续阅读/ }));

    expect(navigationMock.push).toHaveBeenCalledWith(
      "/app/reader-record/reading_record_recent",
    );
  });

  it("ignores invalid recent Reading Record localStorage payloads", async () => {
    window.localStorage.setItem(
      RECENT_READING_RECORD_STORAGE_KEY,
      JSON.stringify({
        readingRecordId: "legacy_record",
        readerUrl: "/app/reader/legacy_record",
        title: "Legacy record should not render",
        createdAt: "2026-06-22T12:00:00.000Z",
      }),
    );

    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    await waitFor(() => {
      expect(screen.queryByText("Legacy record should not render")).toBeNull();
    });
    expect(screen.queryByRole("button", { name: /继续阅读/ })).toBeNull();
  });

  it("removes legacy analysis-task polling from AnalyzeSubmitForm", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/app/(private)/app/read/AnalyzeSubmitForm.tsx"),
      "utf-8",
    );

    expect(source).not.toContain("fetchCurrentAnalysisTask");
    expect(source).not.toContain("fetchAnalysisTaskStatus");
    expect(source).not.toContain("saveRecentReadingRecordForSubmitMode");
    expect(source).not.toContain("legacyAppReaderRoute");
  });

  it("keeps the recent Reading Record helper free of legacy analysis wiring", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/app/(private)/app/read/recent-reading-record.ts"),
      "utf-8",
    );

    expect(source).not.toContain("legacyAppReaderRoute");
    expect(source).not.toContain("/app/reader/");
    expect(source).not.toContain("analysis-tasks");
    expect(source).not.toContain("saveRecentReadingRecordForSubmitMode");
  });

  it("keeps the new Reading Record API route free of legacy analysis submit wiring", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/app/api/web/reading-record/submit/route.ts"),
      "utf-8",
    );

    expect(source).toContain("submitReadingRecordPlainTextFromWeb");
    expect(source).not.toContain("submitAnalysisFromWeb");
    expect(source).not.toContain("legacyAppReaderRoute");
    expect(source).not.toContain("analysis-tasks");
  });
});
