/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SidebarRail } from "./index";
import type { ReadingRecordListItemVm } from "@/services/bff/reading-records";
import { appLibraryRoute, appReadingRecordRoute } from "@/lib/routes";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    refresh: vi.fn(),
  }),
}));

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    className,
    ...rest
  }: {
    href: string;
    children: React.ReactNode;
    className?: string;
    [key: string]: unknown;
  }) => (
    <a href={href} className={className} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("next/image", () => ({
  default: (props: { alt?: string; className?: string }) => (
    // eslint-disable-next-line @next/next/no-img-element
    <img alt={props.alt ?? ""} className={props.className} />
  ),
}));

vi.mock("../command-palette", () => ({
  useCommandPalette: () => vi.fn(),
}));

vi.mock("@/lib/shortcuts", () => ({
  formatShortcut: () => "⌘K",
}));

function makeRecord(
  overrides: Partial<ReadingRecordListItemVm> = {},
): ReadingRecordListItemVm {
  return {
    readingRecordId: "rr_1",
    readerUrl: appReadingRecordRoute("rr_1"),
    title: "Untitled 1",
    createdAt: "2026-06-22T00:00:00Z",
    sourceType: "text",
    productState: "readable_enhancing",
    readinessState: "article_ready",
    lastEventSequence: 1,
    lastOpenedAt: "2026-06-22T10:00:00Z",
    sourceLabel: "粘贴文本",
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
});

describe("SidebarRail 最近阅读", () => {
  it("caps the list to 10 records", () => {
    const items = Array.from({ length: 15 }, (_, i) =>
      makeRecord({ readingRecordId: `rr_${i}`, title: `R${i}` }),
    );
    render(<SidebarRail pathname="/app/library" recentRecords={items} />);
    expect(screen.getAllByRole("link", { name: /^R\d+$/ })).toHaveLength(10);
  });

  it("shows a short status only for priority product states", () => {
    render(
      <SidebarRail
        pathname="/app/library"
        recentRecords={[
          makeRecord({
            readingRecordId: "rr_p",
            title: "Parsing",
            productState: "processing",
          }),
          makeRecord({
            readingRecordId: "rr_a",
            title: "Wait",
            productState: "action_required",
          }),
          makeRecord({
            readingRecordId: "rr_c",
            title: "Confirm",
            productState: "needs_confirmation",
          }),
          makeRecord({
            readingRecordId: "rr_f",
            title: "Failed",
            productState: "failed",
          }),
          makeRecord({
            readingRecordId: "rr_o",
            title: "Ok",
            productState: "readable_enhancing",
          }),
        ]}
      />,
    );
    expect(screen.getByText("解析中")).toBeTruthy();
    expect(screen.getByText("等待继续")).toBeTruthy();
    expect(screen.getByText("需要确认")).toBeTruthy();
    expect(screen.getByText("解析遇到问题")).toBeTruthy();
    // readable_enhancing must not show a status line.
    expect(screen.getByText("Ok").nextElementSibling?.textContent ?? "").toBe("");
  });

  it("does not show status line for readable_enhancing + coverage_complete (completed)", () => {
    render(
      <SidebarRail
        pathname="/app/library"
        recentRecords={[
          makeRecord({
            readingRecordId: "rr_cc",
            title: "Completed",
            productState: "readable_enhancing",
            readinessState: "coverage_complete",
          }),
        ]}
      />,
    );
    const titleEl = screen.getByText("Completed");
    expect(titleEl.nextElementSibling?.textContent ?? "").toBe("");
  });

  it("does not show status line for readable_enhancing + article_ready (ready_to_read)", () => {
    render(
      <SidebarRail
        pathname="/app/library"
        recentRecords={[
          makeRecord({
            readingRecordId: "rr_ar",
            title: "ReadyToRead",
            productState: "readable_enhancing",
            readinessState: "article_ready",
          }),
        ]}
      />,
    );
    const titleEl = screen.getByText("ReadyToRead");
    expect(titleEl.nextElementSibling?.textContent ?? "").toBe("");
  });

  it("renders the current Reading Record with real title + active state", () => {
    const current = makeRecord({
      readingRecordId: "rr_current",
      readerUrl: appReadingRecordRoute("rr_current"),
      title: "正在读的文章",
    });
    render(
      <SidebarRail
        pathname={appReadingRecordRoute("rr_current")}
        recentRecords={[current]}
      />,
    );
    const link = screen.getByRole("link", { name: /正在读的文章/ });
    expect(link.getAttribute("aria-current")).toBe("page");
  });

  it("shows 更多 pointing at appLibraryRoute", () => {
    render(
      <SidebarRail pathname="/app/library" recentRecords={[makeRecord()]} />,
    );
    const more = screen.getByRole("link", { name: "更多" });
    expect(more.getAttribute("href")).toBe(appLibraryRoute);
  });

  it("shows empty-state copy and a 阅读记录 CTA", () => {
    render(<SidebarRail pathname="/app/library" recentRecords={[]} />);
    expect(screen.getByText(/打开一篇文章后会显示在这里/)).toBeTruthy();
    const cta = screen.getByRole("link", { name: "阅读记录" });
    expect(cta.getAttribute("href")).toBe(appLibraryRoute);
  });

  it("never renders the legacy 当前解析页 generic label", () => {
    render(
      <SidebarRail
        pathname={appReadingRecordRoute("rr_current")}
        recentRecords={[]}
      />,
    );
    expect(screen.queryByText("当前解析页")).toBeNull();
  });

  it("needs_confirmation record is not clickable and shows status", () => {
    render(
      <SidebarRail
        pathname="/app/library"
        recentRecords={[
          makeRecord({
            readingRecordId: "rr_confirm",
            title: "NeedsConfirm",
            productState: "needs_confirmation",
            readinessState: "article_ready",
          }),
          makeRecord({
            readingRecordId: "rr_ok",
            title: "ReadToGo",
            productState: "readable_enhancing",
            readinessState: "article_ready",
          }),
        ]}
      />,
    );
    // needs_confirmation row: no <a href>, no arrow, shows "需要确认"
    const confirmRow = screen.getByText("NeedsConfirm");
    const confirmLi = confirmRow.closest("li");
    expect(confirmLi?.querySelector("a")).toBeNull();
    expect(screen.getByText("需要确认")).toBeTruthy();
    // readable_enhancing row: has <a href>
    const okRow = screen.getByText("ReadToGo");
    const okLi = okRow.closest("li");
    expect(okLi?.querySelector("a")).toBeTruthy();
  });

  it("does not show source label or date in the sidebar (compact display)", () => {
    render(
      <SidebarRail
        pathname="/app/library"
        recentRecords={[
          makeRecord({
            readingRecordId: "rr_compact",
            title: "Compact Title",
            sourceLabel: "上传文件 · report.pdf",
            lastOpenedAt: "2026-07-10T12:00:00Z",
            createdAt: "2026-06-22T00:00:00Z",
          }),
        ]}
      />,
    );

    // Title (display_title) is shown
    expect(screen.getByText("Compact Title")).toBeTruthy();

    // Source label must NOT appear in the sidebar
    expect(screen.queryByText("上传文件 · report.pdf")).toBeNull();
    // Date / time labels must NOT appear in the sidebar
    expect(screen.queryByText(/上次阅读/)).toBeNull();
    expect(screen.queryByText(/导入于/)).toBeNull();
  });
});