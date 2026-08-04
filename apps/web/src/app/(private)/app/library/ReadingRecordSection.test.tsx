/** @vitest-environment jsdom */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { ReadingRecordListItemVm } from "@/services/bff/reading-records";
import { ReadingRecordSection } from "./ReadingRecordSection";

function makeReadingRecord(
  overrides: Partial<ReadingRecordListItemVm> = {},
): ReadingRecordListItemVm {
  return {
    readingRecordId: "reading_record_1",
    readerUrl: "/app/reader/reading_record_1",
    title: "First Reading",
    createdAt: "2026-06-22T00:00:00Z",
    sourceType: "text",
    productState: "readable_enhancing",
    readinessState: "article_ready",
    lastEventSequence: 3,
    lastOpenedAt: null,
    sourceLabel: "粘贴文本",
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

  it("renders items with title, date and a single user-language status", () => {
    render(
      <ReadingRecordSection
        readingRecords={[
          makeReadingRecord(),
          makeReadingRecord({
            readingRecordId: "reading_record_2",
            readerUrl: "/app/reader/reading_record_2",
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
    expect(firstLink?.getAttribute("href")).toBe("/app/reader/reading_record_1");
    expect(secondLink?.getAttribute("href")).toBe("/app/reader/reading_record_2");

    expect(screen.getByText("可以开始阅读")).toBeTruthy();
    expect(screen.getByText("解析中")).toBeTruthy();

    expect(screen.queryByText("文章就绪")).toBeNull();
    expect(screen.queryByText("已提交")).toBeNull();
    expect(screen.queryByText("可读·增强中")).toBeNull();
  });

  it("renders 去处理 CTA for action_required records (priority region only after dedup)", () => {
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

    // P2 dedup: 1 priority item 出现在顶部 region，主列表里没有重复行
    expect(screen.getAllByText("去处理").length).toBe(1);

    const link = screen.getByText("First Reading").closest("a");
    expect(link?.getAttribute("href")).toBe("/app/reader/reading_record_1");
  });

  it("renders needs_confirmation rows as resume links with approved copy", () => {
    render(
      <ReadingRecordSection
        readingRecords={[
          makeReadingRecord({
            productState: "needs_confirmation",
          }),
          makeReadingRecord({
            readingRecordId: "reading_record_2",
            readerUrl: "/app/reader/reading_record_2",
            title: "Second Reading",
            productState: "action_required",
          }),
        ]}
        status="ready"
      />,
    );

    // needs_confirmation 行在顶部 region 渲染 1 次；P2 dedup 后主列表中不再重复
    const ncRow = screen.getByText("First Reading").closest("li");
    expect(ncRow?.querySelector("a")?.getAttribute("href")).toBe(
      "/app/read?resume_candidate=reading_record_1",
    );
    expect(within(ncRow as HTMLElement).getByText("需要确认")).toBeTruthy();
    expect(
      within(ncRow as HTMLElement).getByText("请确认已准备好的内容后开始阅读"),
    ).toBeTruthy();
    expect(within(ncRow as HTMLElement).getByText("继续确认")).toBeTruthy();

    // 顶部 region = [nc, action_required]；主列表为空
    const region = screen.getByTestId("library-needs-attention");
    expect(region.querySelectorAll("li").length).toBe(2);
    // 顶部 region 内 nc 行保持恢复链接，且不影响其他 priority 行
    const ncInRegion = within(region).getByText("First Reading").closest("li");
    expect(ncInRegion?.querySelector("a")?.getAttribute("href")).toBe(
      "/app/read?resume_candidate=reading_record_1",
    );

    // action_required 行仍可点击 + 仍有 "去处理" CTA
    const arRow = within(region).getByText("Second Reading").closest("li");
    expect(arRow?.querySelector("a")?.getAttribute("href")).toBe(
      "/app/reader/reading_record_2",
    );
    expect(within(arRow as HTMLElement).getByText("去处理")).toBeTruthy();
  });

  it("renders 查看详情 CTA for failed records (priority region only after dedup)", () => {
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

    expect(screen.getAllByText("查看详情").length).toBe(1);

    const link = screen.getByText("First Reading").closest("a");
    expect(link?.getAttribute("href")).toBe("/app/reader/reading_record_1");
  });

  it("renders 可以开始阅读 (ready_to_read fallback) for unknown productState", () => {
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

    // Unknown productState falls into the default branch of readingRecordStatusKey,
    // which returns "ready_to_read" → "可以开始阅读".
    expect(screen.getByText("可以开始阅读")).toBeTruthy();
    expect(screen.queryByText("状态未知")).toBeNull();
  });

  it("does not surface readiness_state labels", () => {
    render(
      <ReadingRecordSection
        readingRecords={[
          makeReadingRecord({
            productState: "action_required",
            readinessState: "coverage_complete",
          }),
        ]}
        status="ready"
      />,
    );

    expect(screen.queryByText("覆盖完成")).toBeNull();
    expect(screen.queryByText("文章就绪")).toBeNull();
    expect(screen.queryByText("候选 Base 就绪")).toBeNull();
  });

  it("shows 需要处理 region only when priority items exist and caps to 3", () => {
    render(
      <ReadingRecordSection
        readingRecords={[
          makeReadingRecord({ readingRecordId: "a", productState: "action_required" }),
          makeReadingRecord({
            readingRecordId: "b",
            readerUrl: "/app/reader/b",
            title: "B",
            productState: "needs_confirmation",
          }),
          makeReadingRecord({
            readingRecordId: "c",
            readerUrl: "/app/reader/c",
            title: "C",
            productState: "failed",
          }),
          makeReadingRecord({
            readingRecordId: "d",
            readerUrl: "/app/reader/d",
            title: "D",
            productState: "readable_enhancing",
          }),
        ]}
        status="ready"
      />,
    );

    const region = screen.getByTestId("library-needs-attention");
    expect(region).toBeTruthy();
    expect(screen.getByText("需要处理")).toBeTruthy();

    const regionItems = region.querySelectorAll("li");
    expect(regionItems.length).toBe(3);
  });

  it("keeps the section source free of legacy reader route, legacy path and analysis-tasks wiring", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/app/(private)/app/library/ReadingRecordSection.tsx"),
      "utf-8",
    );

    expect(source).not.toContain("legacyAppReaderRoute");
    expect(source).not.toContain("/app/reader/");
    expect(source).not.toContain("analysis-tasks");

    for (const forbidden of [
      "处理中",
      "可读·增强中",
      "处理失败",
      "需处理",
      "文章就绪",
      "已提交",
      "候选 Base 就绪",
      "初始增强就绪",
      "覆盖完成",
    ]) {
      expect(source).not.toContain(forbidden);
    }
  });

  it("renders source label and correct time semantics (lastOpenedAt vs createdAt)", () => {
    render(
      <ReadingRecordSection
        readingRecords={[
          makeReadingRecord({
            readingRecordId: "rr_opened",
            readerUrl: "/app/reader/rr_opened",
            title: "Opened Record",
            sourceLabel: "上传文件 · report.pdf",
            lastOpenedAt: "2026-07-10T12:00:00Z",
          }),
          makeReadingRecord({
            readingRecordId: "rr_new",
            readerUrl: "/app/reader/rr_new",
            title: "New Record",
            sourceLabel: "粘贴文本",
            lastOpenedAt: null,
          }),
        ]}
        status="ready"
      />,
    );

    // Source labels are rendered
    expect(screen.getByText("上传文件 · report.pdf")).toBeTruthy();
    expect(screen.getByText("粘贴文本")).toBeTruthy();

    // Time semantics: lastOpenedAt → "上次阅读", null → "导入于"
    // The date part is formatted by toLocaleDateString("zh-CN")
    const openedDate = new Date("2026-07-10T12:00:00Z").toLocaleDateString("zh-CN");
    const newDate = new Date("2026-06-22T00:00:00Z").toLocaleDateString("zh-CN");
    expect(screen.getByText(`上次阅读 ${openedDate}`)).toBeTruthy();
    expect(screen.getByText(`导入于 ${newDate}`)).toBeTruthy();
  });
});
