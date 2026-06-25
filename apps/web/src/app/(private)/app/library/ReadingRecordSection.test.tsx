/** @vitest-environment jsdom */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { ReadingRecordListItemVm } from "@/services/bff/reading-records";
import { ReadingRecordSection } from "./ReadingRecordSection";

function makeReadingRecord(
  overrides: Partial<ReadingRecordListItemVm> = {},
): ReadingRecordListItemVm {
  return {
    readingRecordId: "reading_record_1",
    readerUrl: "/app/reader-record/reading_record_1",
    title: "First Reading",
    createdAt: "2026-06-22T00:00:00Z",
    sourceType: "text",
    sourceMetadata: {},
    productState: "readable_enhancing",
    readinessState: "article_ready",
    lastEventSequence: 3,
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
});

describe("ReadingRecordSection", () => {
  it("shows the default empty state", () => {
    render(
      <ReadingRecordSection
        readingRecords={[]}
        status="ready"
      />,
    );

    expect(
      screen.getByText("还没有阅读记录。提交一篇新解读后会在这里显示。"),
    ).toBeTruthy();
  });

  it("shows the query empty state when filtering", () => {
    render(
      <ReadingRecordSection
        readingRecords={[]}
        status="ready"
        hasQuery
      />,
    );

    expect(
      screen.getByText("当前检索条件下还没有匹配的阅读记录。"),
    ).toBeTruthy();
  });

  it("shows an error message from the parent BFF state", () => {
    render(
      <ReadingRecordSection
        readingRecords={[]}
        status="upstream_unavailable"
        message="透读服务暂时不可用，请稍后重试。"
      />,
    );

    expect(screen.getByText("透读服务暂时不可用，请稍后重试。")).toBeTruthy();
  });

  it("renders items with title, date, status and readerUrl", () => {
    render(
      <ReadingRecordSection
        readingRecords={[
          makeReadingRecord(),
          makeReadingRecord({
            readingRecordId: "reading_record_2",
            readerUrl: "/app/reader-record/reading_record_2",
            title: "Second Reading",
            createdAt: "2026-06-21T00:00:00Z",
            productState: "processing",
            readinessState: "submitted",
            lastEventSequence: 1,
          }),
        ]}
        status="ready"
      />,
    );

    expect(screen.getByText("First Reading")).toBeTruthy();
    expect(screen.getByText("Second Reading")).toBeTruthy();

    const firstLink = screen.getByText("First Reading").closest("a");
    const secondLink = screen.getByText("Second Reading").closest("a");
    expect(firstLink?.getAttribute("href")).toBe("/app/reader-record/reading_record_1");
    expect(secondLink?.getAttribute("href")).toBe("/app/reader-record/reading_record_2");

    expect(screen.getByText("可读·增强中")).toBeTruthy();
    expect(screen.getByText("文章就绪")).toBeTruthy();
    expect(screen.getByText("处理中")).toBeTruthy();
    expect(screen.getByText("已提交")).toBeTruthy();
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
