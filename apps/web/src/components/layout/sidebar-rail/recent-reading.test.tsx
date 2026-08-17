/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SidebarRail } from "./index";
import type { ReadingRecordListItemVm } from "@/services/bff/reading-records";
import {
  appLibraryRoute,
  appReaderRoute,
} from "@/lib/routes";

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

// SidebarRail now calls useSettingsDialog() at the top level so the user menu
// can openSettings(section) instead of linking to /app/settings. Provide a
// no-op mock here so the recent-reading tests can mount SidebarRail without
// pulling in the real SettingsDialogProvider. Assertions on openSettings
// behavior live in index.test.tsx.
vi.mock("@/components/settings/SettingsDialogProvider", () => ({
  useSettingsDialog: () => ({ openSettings: vi.fn() }),
}));

function makeRecord(
  overrides: Partial<ReadingRecordListItemVm> = {},
): ReadingRecordListItemVm {
  return {
    readingRecordId: "rr_1",
    readerUrl: appReaderRoute("rr_1"),
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
    expect(
      screen
        .getAllByRole("link")
        .filter((link) => link.getAttribute("href")?.startsWith("/app/reader/")),
    ).toHaveLength(10);
  });

  it("shows a short status only for priority product states", () => {
    render(
      <SidebarRail
        pathname="/app/library"
        recentRecords={[
          makeRecord({
            readingRecordId: "rr_p",
            readerUrl: appReaderRoute("rr_p"),
            title: "Parsing",
            productState: "processing",
          }),
          makeRecord({
            readingRecordId: "rr_a",
            readerUrl: appReaderRoute("rr_a"),
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
            readerUrl: appReaderRoute("rr_f"),
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

    for (const [title, recordId] of [
      ["Parsing", "rr_p"],
      ["Wait", "rr_a"],
      ["Failed", "rr_f"],
    ] as const) {
      expect(screen.getByText(title).closest("a")?.getAttribute("href")).toBe(
        appReaderRoute(recordId),
      );
    }
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
      readerUrl: appReaderRoute("rr_current"),
      title: "正在读的文章",
    });
    render(
      <SidebarRail
        pathname={appReaderRoute("rr_current")}
        recentRecords={[current]}
      />,
    );
    const link = screen.getByRole("link", { name: /正在读的文章/ });
    expect(link.getAttribute("aria-current")).toBe("page");
    expect(link.className).toContain("bg-[var(--app-control-current)]");
    expect(link.className).not.toContain("bg-[var(--app-control-quiet)]");
  });

  it("marks global destinations as the current page without conflating them with recent records", () => {
    render(<SidebarRail pathname={appLibraryRoute} recentRecords={[makeRecord()]} />);

    for (const link of screen.getAllByRole("link", { name: "全部阅读记录" })) {
      expect(link.getAttribute("aria-current")).toBe("page");
    }
    expect(screen.getByRole("link", { name: "Untitled 1" }).getAttribute("aria-current")).toBeNull();
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
        pathname={appReaderRoute("rr_current")}
        recentRecords={[]}
      />,
    );
    expect(screen.queryByText("当前解析页")).toBeNull();
  });

  it("needs_confirmation record links to resume confirmation and stays compact", () => {
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
            readerUrl: appReaderRoute("rr_ok"),
            title: "ReadToGo",
            productState: "readable_enhancing",
            readinessState: "article_ready",
          }),
        ]}
      />,
    );
    // needs_confirmation row: resume link, no CTA pill or arrow, shows "需要确认"
    const confirmRow = screen.getByText("NeedsConfirm");
    const confirmLi = confirmRow.closest("li");
    expect(confirmLi?.querySelector("a")?.getAttribute("href")).toBe(
      "/app/read?resume_candidate=rr_confirm",
    );
    expect(screen.getByText("需要确认")).toBeTruthy();
    expect(screen.queryByText("继续确认")).toBeNull();
    expect(confirmLi?.querySelector("svg.lucide-arrow-right")).toBeNull();
    expect(screen.queryByText("粘贴文本")).toBeNull();
    expect(screen.queryByText(/上次阅读/)).toBeNull();
    expect(screen.queryByText(/导入于/)).toBeNull();

    // Other states still use the new Reader route.
    const okRow = screen.getByText("ReadToGo");
    const okLi = okRow.closest("li");
    expect(okLi?.querySelector("a")?.getAttribute("href")).toBe(
      appReaderRoute("rr_ok"),
    );
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

describe("SidebarRail 最近阅读行菜单", () => {
  it("renders an accessible action trigger per row, as a Link sibling", () => {
    render(
      <SidebarRail
        pathname="/app/library"
        recentRecords={[makeRecord({ title: "菜单文章" })]}
      />,
    );
    const trigger = screen.getByRole("button", { name: '打开“菜单文章”的操作菜单' });
    expect(trigger.tagName).toBe("BUTTON");
    const link = screen.getByRole("link", { name: /菜单文章/ });
    // Sibling, not nested: the trigger must not be inside the link.
    expect(link.contains(trigger)).toBe(false);
    expect(link.parentElement?.contains(trigger)).toBe(true);
  });

  it("hides the trigger by default and reveals it on row hover/focus", () => {
    render(
      <SidebarRail
        pathname="/app/library"
        recentRecords={[makeRecord({ title: "可见性文章" })]}
      />,
    );
    const trigger = screen.getByRole("button", { name: '打开“可见性文章”的操作菜单' });
    expect(trigger.className).toContain("opacity-0");
    expect(trigger.className).toContain("group-hover:opacity-100");
    expect(trigger.className).toContain("group-focus-within:opacity-100");
    // Per-trigger Radix data-state, not a shared sidebar boolean.
    expect(trigger.className).toContain("data-[state=open]:opacity-100");
    // Tab-focusable: must never be display:none.
    expect(trigger.className).not.toContain("hidden");
  });

  it("marks only the open row's trigger with data-state=open", async () => {
    render(
      <SidebarRail
        pathname="/app/library"
        recentRecords={[
          makeRecord({ title: "侧栏甲" }),
          makeRecord({ readingRecordId: "rr_2", title: "侧栏乙" }),
        ]}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: '打开“侧栏甲”的操作菜单' }));
    await screen.findByRole("menu");

    const triggers = screen
      .getAllByRole("button", { hidden: true })
      .filter((b) => (b.getAttribute("aria-label") ?? "").includes("的操作菜单"));
    const opened = triggers.find((b) => b.getAttribute("aria-label")?.includes("侧栏甲"));
    const other = triggers.find((b) => b.getAttribute("aria-label")?.includes("侧栏乙"));
    expect(opened?.getAttribute("data-state")).toBe("open");
    expect(other?.getAttribute("data-state")).not.toBe("open");
  });

  it("keeps the sidebar overlay open while a row menu is open (portalled)", async () => {
    const onSidebarOverlayClose = vi.fn();
    const { container } = render(
      <SidebarRail
        pathname="/app/library"
        recentRecords={[makeRecord({ title: "菜单打开" })]}
        onSidebarOverlayClose={onSidebarOverlayClose}
      />,
    );
    const trigger = screen.getByRole("button", { name: '打开“菜单打开”的操作菜单' });
    await userEvent.click(trigger);
    await screen.findByRole("menu");
    // Pointer leaves the rail while the portalled menu is open.
    fireEvent.mouseLeave(container.querySelector("aside") as HTMLElement);
    expect(onSidebarOverlayClose).not.toHaveBeenCalled();
  });

  it("restores auto-collapse after the menu closes with pointer outside", async () => {
    const onSidebarOverlayClose = vi.fn();
    const { container } = render(
      <SidebarRail
        pathname="/app/library"
        recentRecords={[makeRecord({ title: "菜单关闭" })]}
        onSidebarOverlayClose={onSidebarOverlayClose}
      />,
    );
    const trigger = screen.getByRole("button", { name: '打开“菜单关闭”的操作菜单' });
    await userEvent.click(trigger);
    await screen.findByRole("menu");
    fireEvent.mouseLeave(container.querySelector("aside") as HTMLElement);
    expect(onSidebarOverlayClose).not.toHaveBeenCalled();

    // Close the menu with Escape.
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
    expect(onSidebarOverlayClose).toHaveBeenCalled();
  });

  it("keeps aria-current on the current record row", () => {
    render(
      <SidebarRail
        pathname={appReaderRoute("rr_1")}
        recentRecords={[makeRecord()]}
      />,
    );
    const link = screen.getByRole("link", { name: /Untitled 1/ });
    expect(link.getAttribute("aria-current")).toBe("page");
  });
});