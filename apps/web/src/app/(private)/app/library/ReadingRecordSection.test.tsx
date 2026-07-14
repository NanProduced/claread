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
  it("shows the default empty state with submit CTA", () => {
    render(
      <ReadingRecordSection
        readingRecords={[]}
        status="ready"
      />,
    );

    expect(
      screen.getByText("还没有阅读记录。提交一篇新解读后会在这里显示。"),
    ).toBeTruthy();

    const ctaLink = screen.getByText("提交一篇新解读").closest("a");
    expect(ctaLink?.getAttribute("href")).toBe("/app/read");
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

  it("shows a generic error message from the parent BFF state", () => {
    render(
      <ReadingRecordSection
        readingRecords={[]}
        status="upstream_unavailable"
        message="透读服务暂时不可用，请稍后重试。"
      />,
    );

    expect(screen.getByText("透读服务暂时不可用，请稍后重试。")).toBeTruthy();
    expect(screen.queryByText("去登录")).toBeNull();
  });

  it("renders login CTA when status is auth_required", () => {
    render(
      <ReadingRecordSection
        readingRecords={[]}
        status="auth_required"
      />,
    );

    expect(screen.getByText("请先登录后查看阅读记录。")).toBeTruthy();

    const loginLink = screen.getByText("去登录").closest("a");
    expect(loginLink?.getAttribute("href")).toBe("/login");
  });

  it("renders login CTA when status is upstream_auth_failed", () => {
    render(
      <ReadingRecordSection
        readingRecords={[]}
        status="upstream_auth_failed"
        message="登录态已失效，请重新登录后再试。"
      />,
    );

    expect(screen.getByText("登录态已失效，请重新登录后再试。")).toBeTruthy();

    const loginLink = screen.getByText("去登录").closest("a");
    expect(loginLink?.getAttribute("href")).toBe("/login");
  });

  it("renders limited_debug state without login CTA", () => {
    render(
      <ReadingRecordSection
        readingRecords={[]}
        status="limited_debug"
        message="当前登录态无法访问阅读记录，请使用完整登录会话。"
      />,
    );

    expect(
      screen.getByText("当前登录态无法访问阅读记录，请使用完整登录会话。"),
    ).toBeTruthy();
    expect(screen.queryByText("去登录")).toBeNull();
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

  it("renders 去处理 CTA for action_required records", () => {
    render(
      <ReadingRecordSection
        readingRecords={[
          makeReadingRecord({
            productState: "action_required",
          }),
        ]}
        status="ready"
      />,
    );

    expect(screen.getByText("去处理")).toBeTruthy();

    const link = screen.getByText("First Reading").closest("a");
    expect(link?.getAttribute("href")).toBe("/app/reader-record/reading_record_1");
  });

  it("renders 去处理 CTA for needs_confirmation records", () => {
    render(
      <ReadingRecordSection
        readingRecords={[
          makeReadingRecord({
            productState: "needs_confirmation",
          }),
        ]}
        status="ready"
      />,
    );

    expect(screen.getByText("去处理")).toBeTruthy();
  });

  it("renders 查看详情 CTA for failed records", () => {
    render(
      <ReadingRecordSection
        readingRecords={[
          makeReadingRecord({
            productState: "failed",
          }),
        ]}
        status="ready"
      />,
    );

    expect(screen.getByText("查看详情")).toBeTruthy();

    const link = screen.getByText("First Reading").closest("a");
    expect(link?.getAttribute("href")).toBe("/app/reader-record/reading_record_1");
  });

  it("renders 状态未知 for unknown productState", () => {
    render(
      <ReadingRecordSection
        readingRecords={[
          makeReadingRecord({
            productState: "unknown_future_state" as never,
          }),
        ]}
        status="ready"
      />,
    );

    expect(screen.getByText("状态未知")).toBeTruthy();
  });

  it("renders 状态未知 for unknown readinessState", () => {
    render(
      <ReadingRecordSection
        readingRecords={[
          makeReadingRecord({
            readinessState: "unknown_future_state" as never,
          }),
        ]}
        status="ready"
      />,
    );

    expect(screen.getByText("状态未知")).toBeTruthy();
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
