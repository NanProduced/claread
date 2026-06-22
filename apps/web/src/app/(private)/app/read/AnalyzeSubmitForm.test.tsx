/** @vitest-environment jsdom */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchCurrentAnalysisTask } from "@/lib/analysis-task-client";

import { AnalysisLoadingStatusBar, AnalyzeSubmitForm } from "./AnalyzeSubmitForm";
import {
  READ_PAGE_SUBMIT_MODE,
  readPageSubmitEndpoint,
  readPageSubmitRequestBody,
} from "./submit-mode";

const navigationMock = vi.hoisted(() => ({
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => navigationMock,
}));

vi.mock("@/lib/analysis-task-client", () => ({
  fetchAnalysisTaskStatus: vi.fn(),
  fetchCurrentAnalysisTask: vi.fn(),
  isAnalysisTerminalStatus: (status: string) =>
    ["succeeded", "failed", "cancelled", "expired"].includes(status),
}));

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(fetchCurrentAnalysisTask).mockResolvedValue({
    ok: true,
    hasActive: false,
    task: null,
  });
});

afterEach(() => {
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
    expect(screen.queryByRole("button", { name: "去记录页" })).toBeNull();
    expect(screen.getByText("正在梳理文章结构")).toBeTruthy();

    const renderedText = document.body.textContent ?? "";
    expect(renderedText).not.toMatch(/\d{1,2}:\d{2}/);
    expect(renderedText).not.toContain("%");
    expect(renderedText).not.toContain("第");
    expect(renderedText).not.toContain("共");
  });
});

describe("AnalyzeSubmitForm submit cutover", () => {
  it("uses the new Reading Record submit mode by default while retaining explicit legacy mode", () => {
    expect(READ_PAGE_SUBMIT_MODE).toBe("reading-record");
    expect(readPageSubmitEndpoint()).toBe("/api/web/reading-record/submit");
    expect(
      readPageSubmitRequestBody({
        text: "This is a short English article.",
        readingGoal: "daily_reading",
        readingVariant: "intermediate_reading",
      }),
    ).toEqual({ plainText: "This is a short English article." });

    expect(readPageSubmitEndpoint("legacy")).toBe("/api/web/analysis/submit");
    expect(
      readPageSubmitRequestBody(
        {
          text: "This is a short English article.",
          readingGoal: "daily_reading",
          readingVariant: "intermediate_reading",
        },
        "legacy",
      ),
    ).toEqual({
      text: "This is a short English article.",
      readingGoal: "daily_reading",
      readingVariant: "intermediate_reading",
    });
  });

  it("submits /app/read to the new Reading Record endpoint and lands on reader-record", async () => {
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
