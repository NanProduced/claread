/** @vitest-environment jsdom */

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  readingRecordStatusKey,
  readingRecordStatusLabel,
} from "@/lib/reader-record-status";
import { appReaderRoute } from "@/lib/routes";
import type { ReadingRecordListItemVm } from "@/services/bff/reading-records";
import {
  NEEDS_ATTENTION_PRODUCT_STATES,
  ReadingRecordSection,
} from "./ReadingRecordSection";

function record(
  productState: ReadingRecordListItemVm["productState"],
  overrides: Partial<ReadingRecordListItemVm> = {},
): ReadingRecordListItemVm {
  return {
    readingRecordId: "rr",
    readerUrl: appReaderRoute("rr"),
    title: "t",
    createdAt: "2026-06-22T00:00:00Z",
    sourceType: "text",
    productState,
    readinessState: "article_ready",
    lastEventSequence: 1,
    lastOpenedAt: null,
    sourceLabel: "粘贴文本",
    ...overrides,
  };
}

function statusLabelFor(item: ReadingRecordListItemVm): string {
  return readingRecordStatusLabel(
    readingRecordStatusKey(item.productState, item.readinessState),
  );
}

afterEach(() => {
  cleanup();
});

describe("statusLabelFor (双键 mapping)", () => {
  it.each([
    ["processing", "article_ready", "解析中"],
    ["needs_confirmation", "submitted", "需要确认"],
    ["readable_enhancing", "article_ready", "可以开始阅读"],
    ["readable_enhancing", "coverage_complete", "解析完成"],
    ["action_required", "submitted", "等待继续"],
    ["failed", "submitted", "解析遇到问题"],
  ] as const)(
    "maps %s + %s → %s",
    (productState, readinessState, expected) => {
      expect(
        statusLabelFor(record(productState, { readinessState })),
      ).toBe(expected);
    },
  );

  it("does not leak readiness_state labels", () => {
    const labels = [
      "已提交",
      "候选 Base 就绪",
      "初始增强就绪",
      "覆盖完成",
      "文章就绪",
    ];
    for (const label of labels) {
      for (const ps of [
        "processing",
        "needs_confirmation",
        "readable_enhancing",
        "action_required",
        "failed",
      ] as const) {
        expect(
          statusLabelFor(record(ps, { readinessState: "submitted" })),
        ).not.toBe(label);
      }
    }
  });

  it("NEEDS_ATTENTION_PRODUCT_STATES excludes processing / readable_enhancing", () => {
    expect(NEEDS_ATTENTION_PRODUCT_STATES).toEqual([
      "needs_confirmation",
      "action_required",
      "failed",
    ]);
  });

  it("renders 解析完成 in DOM for readable_enhancing + coverage_complete", () => {
    render(
      <ReadingRecordSection
        readingRecords={[
          record("readable_enhancing", {
            readingRecordId: "done",
            readinessState: "coverage_complete",
            title: "Done",
          }),
        ]}
        status="ready"
      />,
    );
    expect(screen.getByText("解析完成")).toBeTruthy();
  });
});

describe("ReadingRecordSection priority region", () => {
  it("renders 需要处理 region only when priority items exist", () => {
    const { rerender } = render(
      <ReadingRecordSection
        readingRecords={[record("readable_enhancing"), record("processing")]}
        status="ready"
      />,
    );
    expect(screen.queryByTestId("library-needs-attention")).toBeNull();

    rerender(
      <ReadingRecordSection
        readingRecords={[
          record("action_required", { readingRecordId: "p1", title: "P1" }),
          record("failed", { readingRecordId: "p2", title: "P2" }),
          record("readable_enhancing", { readingRecordId: "n1", title: "N1" }),
        ]}
        status="ready"
      />,
    );
    expect(screen.getByTestId("library-needs-attention")).toBeTruthy();
  });
});

describe("needs_confirmation 恢复入口", () => {
  it("renders aria-disabled=true and no anchor element in the priority region", () => {
    render(
      <ReadingRecordSection
        readingRecords={[
          record("action_required", {
            readingRecordId: "a1",
            title: "A1",
          }),
          record("needs_confirmation", {
            readingRecordId: "nc",
            title: "NC",
          }),
        ]}
        status="ready"
      />,
    );

    // 顶部 region: [a1, nc] — nc 进入新 Agentic 恢复确认流。
    const region = screen.getByTestId("library-needs-attention");
    const ncItem = within(region).getByText("NC").closest("li");
    expect(ncItem?.querySelector("a")?.getAttribute("href")).toBe(
      "/app/read?resume_candidate=nc",
    );
    expect(within(ncItem as HTMLElement).getByText("需要确认")).toBeTruthy();
    expect(within(ncItem as HTMLElement).getByText("请确认已准备好的内容后开始阅读")).toBeTruthy();
    expect(within(ncItem as HTMLElement).getByText("继续确认")).toBeTruthy();

    // a1 仍可点击
    const a1Item = within(region).getByText("A1").closest("li");
    expect(a1Item?.querySelector("a")?.getAttribute("href")).toBe(
      "/app/reader/rr",
    );
    // a1 仍渲染 "去处理" CTA + 右箭头
    expect(within(a1Item as HTMLElement).getByText("去处理")).toBeTruthy();
  });

  it("keeps needs_confirmation items in the main list when 4+ priority items push them down", () => {
    render(
      <ReadingRecordSection
        readingRecords={[
          record("action_required", {
            readingRecordId: "a1",
            title: "A1",
          }),
          record("action_required", {
            readingRecordId: "a2",
            title: "A2",
          }),
          record("action_required", {
            readingRecordId: "a3",
            title: "A3",
          }),
          record("needs_confirmation", {
            readingRecordId: "nc",
            title: "NC",
          }),
        ]}
        status="ready"
      />,
    );

    // 顶部 3 条：a1/a2/a3（全部可点击）
    const region = screen.getByTestId("library-needs-attention");
    const regionItems = region.querySelectorAll("li");
    expect(regionItems.length).toBe(3);

    // 主列表 = [nc]，仍进入新 Agentic 恢复确认流。
    const uls = document.querySelectorAll("section ul");
    const mainList = uls[uls.length - 1] as HTMLElement;
    const mainItems = mainList.querySelectorAll("li");
    expect(mainItems.length).toBe(1);
    const ncItem = mainItems[0];
    expect(ncItem.querySelector("a")?.getAttribute("href")).toBe(
      "/app/read?resume_candidate=nc",
    );
    expect(within(ncItem).getByText("请确认已准备好的内容后开始阅读")).toBeTruthy();
    expect(within(ncItem).getByText("继续确认")).toBeTruthy();
  });
});

describe("去重 — 主列表排除已置顶的前 3 条", () => {
  it("4 priority + 1 non-priority → 顶部 3 + 主列表 2 (1 priority + 1 non-priority)", () => {
    render(
      <ReadingRecordSection
        readingRecords={[
          record("action_required", {
            readingRecordId: "p1",
            title: "P1",
            createdAt: "2026-06-22T00:00:00Z",
          }),
          record("needs_confirmation", {
            readingRecordId: "p2",
            title: "P2",
            createdAt: "2026-06-21T00:00:00Z",
          }),
          record("failed", {
            readingRecordId: "p3",
            title: "P3",
            createdAt: "2026-06-20T00:00:00Z",
          }),
          record("action_required", {
            readingRecordId: "p4",
            title: "P4",
            createdAt: "2026-06-19T00:00:00Z",
          }),
          record("readable_enhancing", {
            readingRecordId: "n1",
            title: "N1",
            createdAt: "2026-06-18T00:00:00Z",
          }),
        ]}
        status="ready"
      />,
    );

    const region = screen.getByTestId("library-needs-attention");
    const regionItems = region.querySelectorAll("li");
    expect(regionItems.length).toBe(3);
    // 顶部 3 条为置顶记录
    expect(within(region).getByText("P1")).toBeTruthy();
    expect(within(region).getByText("P2")).toBeTruthy();
    expect(within(region).getByText("P3")).toBeTruthy();
    expect(within(region).queryByText("P4")).toBeNull();
    expect(within(region).queryByText("N1")).toBeNull();

    // 主列表剩余 2 条
    const uls = document.querySelectorAll("section ul");
    const mainList = uls[uls.length - 1];
    const mainItems = mainList.querySelectorAll("li");
    expect(mainItems.length).toBe(2);
    expect(within(mainList as HTMLElement).getByText("P4")).toBeTruthy();
    expect(within(mainList as HTMLElement).getByText("N1")).toBeTruthy();
    // 顶部已有的置顶记录不应再出现在主列表
    expect(within(mainList as HTMLElement).queryByText("P1")).toBeNull();
    expect(within(mainList as HTMLElement).queryByText("P2")).toBeNull();
    expect(within(mainList as HTMLElement).queryByText("P3")).toBeNull();
  });

  it("主列表顺序与原 items 顺序一致（不把剩余 priority 提到前面）", () => {
    // 混排：non/priority 交替，末尾追加一条 priority
    // 顶部 cap 3 → 前 3 条 priority
    // 主列表应保留原顺序：剩余 non 在前，末尾的 priority 留在末尾而不是被提前
    render(
      <ReadingRecordSection
        readingRecords={[
          record("readable_enhancing", {
            readingRecordId: "n1",
            title: "N1",
            createdAt: "2026-06-22T00:00:00Z",
          }),
          record("action_required", {
            readingRecordId: "p1",
            title: "P1",
            createdAt: "2026-06-21T00:00:00Z",
          }),
          record("readable_enhancing", {
            readingRecordId: "n2",
            title: "N2",
            createdAt: "2026-06-20T00:00:00Z",
          }),
          record("needs_confirmation", {
            readingRecordId: "p2",
            title: "P2",
            createdAt: "2026-06-19T00:00:00Z",
          }),
          record("readable_enhancing", {
            readingRecordId: "n3",
            title: "N3",
            createdAt: "2026-06-18T00:00:00Z",
          }),
          record("failed", {
            readingRecordId: "p3",
            title: "P3",
            createdAt: "2026-06-17T00:00:00Z",
          }),
          record("action_required", {
            readingRecordId: "p4",
            title: "P4",
            createdAt: "2026-06-16T00:00:00Z",
          }),
        ]}
        status="ready"
      />,
    );

    // 顶部 3 条置顶记录（按 items 中出现顺序）
    const region = screen.getByTestId("library-needs-attention");
    const regionItems = region.querySelectorAll("li");
    expect(regionItems.length).toBe(3);
    expect(within(region).getByText("P1")).toBeTruthy();
    expect(within(region).getByText("P2")).toBeTruthy();
    expect(within(region).getByText("P3")).toBeTruthy();
    expect(within(region).queryByText("P4")).toBeNull();
    expect(within(region).queryByText("N1")).toBeNull();
    expect(within(region).queryByText("N2")).toBeNull();
    expect(within(region).queryByText("N3")).toBeNull();

    // 主列表顺序应与原 items 顺序一致
    const uls = document.querySelectorAll("section ul");
    const mainList = uls[uls.length - 1] as HTMLElement;
    const mainItems = Array.from(mainList.querySelectorAll("li"));
    expect(mainItems.length).toBe(4);
    const mainTitles = mainItems.map(
      (li) => li.querySelector("p")?.textContent ?? "",
    );
    expect(mainTitles).toEqual(["N1", "N2", "N3", "P4"]);

    // 顶部已有的置顶记录不应再出现在主列表
    expect(within(mainList).queryByText("P1")).toBeNull();
    expect(within(mainList).queryByText("P2")).toBeNull();
    expect(within(mainList).queryByText("P3")).toBeNull();
  });
});
