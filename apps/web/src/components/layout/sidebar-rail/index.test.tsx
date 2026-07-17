/** @vitest-environment jsdom */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SidebarRail } from ".";

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

afterEach(() => {
  cleanup();
});

describe("SidebarRail z-index contract", () => {
  it("links needs_confirmation recent reading to the candidate resume route", () => {
    render(
      <SidebarRail
        pathname="/app/library"
        sidebarMode="locked"
        recentRecords={[
          {
            readingRecordId: "record/a?b",
            readerUrl: "/app/reader-record/record%2Fa%3Fb",
            title: "待确认文章",
            createdAt: "2026-07-14T00:00:00Z",
            sourceType: "text",
            productState: "needs_confirmation",
            readinessState: "candidate_base_ready",
            lastEventSequence: 1,
            lastOpenedAt: null,
            sourceLabel: "粘贴文本",
          },
        ]}
      />,
    );

    const link = screen.getByText("待确认文章").closest("a");
    expect(link?.getAttribute("href")).toBe(
      "/app/read?resume_candidate=record%2Fa%3Fb",
    );
    expect(screen.getByText("需要确认")).toBeTruthy();
    expect(screen.queryByText("继续确认")).toBeNull();
    expect(screen.queryByText("粘贴文本")).toBeNull();
    expect(screen.queryByText(/2026/)).toBeNull();
    expect(screen.getByText("更多").closest("a")?.getAttribute("href")).toBe(
      "/app/library",
    );
  });

  it("keeps recent reading capped at ten items", () => {
    render(
      <SidebarRail
        pathname="/app/library"
        sidebarMode="locked"
        recentRecords={Array.from({ length: 11 }, (_, index) => ({
          readingRecordId: `record_${index}`,
          readerUrl: `/app/reader-record/record_${index}`,
          title: `文章 ${index}`,
          createdAt: "2026-07-14T00:00:00Z",
          sourceType: "text" as const,
          productState: "readable_enhancing" as const,
          readinessState: "article_ready" as const,
          lastEventSequence: index,
          lastOpenedAt: null,
          sourceLabel: "粘贴文本",
        }))}
      />,
    );

    expect(screen.getByText("文章 9")).toBeTruthy();
    expect(screen.queryByText("文章 10")).toBeNull();
  });

  it("uses the semantic shell-navigation z-index for the overlay surface in all sidebar states", () => {
    const { container, rerender } = render(
      <SidebarRail pathname="/app/reader-record/record_1" sidebarMode="closed" />,
    );

    const sidebar = container.querySelector<HTMLElement>('[data-app-sidebar="rail"]');
    expect(sidebar).not.toBeNull();
    expect(sidebar?.style.zIndex).toBe("var(--app-z-shell-navigation)");

    // Overlay state.
    rerender(
      <SidebarRail pathname="/app/reader-record/record_1" sidebarMode="overlay" />,
    );
    const overlaySidebar = container.querySelector<HTMLElement>(
      '[data-app-sidebar="rail"]',
    );
    expect(overlaySidebar?.style.zIndex).toBe("var(--app-z-shell-navigation)");

    // Locked state.
    rerender(
      <SidebarRail pathname="/app/reader-record/record_1" sidebarMode="locked" />,
    );
    const lockedSidebar = container.querySelector<HTMLElement>(
      '[data-app-sidebar="rail"]',
    );
    expect(lockedSidebar?.style.zIndex).toBe("var(--app-z-shell-navigation)");
  });

  it("places the portal user menu above the shell navigation layer", () => {
    const sidebarSource = readFileSync(
      resolve(process.cwd(), "src/components/layout/sidebar-rail/index.tsx"),
      "utf8",
    );
    const globalsSource = readFileSync(
      resolve(process.cwd(), "src/app/globals.css"),
      "utf8",
    );

    expect(globalsSource).toContain("--app-z-shell-navigation: 70;");
    expect(globalsSource).toContain("--app-z-shell-overlay: 80;");
    expect(sidebarSource).toMatch(
      /<DropdownMenuContent[\s\S]*?className="!z-\[var\(--app-z-shell-overlay\)\] w-60"[\s\S]*?style=\{\{ zIndex: "var\(--app-z-shell-overlay\)" \}\}/,
    );
  });
  it("opens the user menu inside the sidebar interaction boundary", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <SidebarRail pathname="/app/reader-record/record_1" sidebarMode="locked" />,
    );
    const sidebar = container.querySelector<HTMLElement>('[data-app-sidebar="rail"]');
    expect(sidebar).not.toBeNull();

    await user.click(screen.getByRole("button", { name: "打开用户菜单" }));

    const menu = await screen.findByRole("menu");
    expect(sidebar?.contains(menu)).toBe(true);
  });
});

describe("User menu settings links", () => {
  it("opens the user menu to reveal settings menuitems", async () => {
    const user = userEvent.setup();
    render(<SidebarRail pathname="/app/library" sidebarMode="locked" />);

    await user.click(screen.getByRole("button", { name: "打开用户菜单" }));

    expect(screen.getByRole("menuitem", { name: "个人资料" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "偏好设置" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "用量与积分" })).toBeTruthy();
  });

  it("points each settings entry to the matching ?section= URL as a Link", async () => {
    const user = userEvent.setup();
    render(<SidebarRail pathname="/app/library" sidebarMode="locked" />);

    await user.click(screen.getByRole("button", { name: "打开用户菜单" }));

    const accountItem = screen.getByRole("menuitem", { name: "个人资料" });
    const preferencesItem = screen.getByRole("menuitem", { name: "偏好设置" });
    const usageItem = screen.getByRole("menuitem", { name: "用量与积分" });

    expect(accountItem.tagName).toBe("A");
    expect(accountItem.getAttribute("href")).toBe("/app/settings?section=account");

    expect(preferencesItem.tagName).toBe("A");
    expect(preferencesItem.getAttribute("href")).toBe("/app/settings?section=preferences");

    expect(usageItem.tagName).toBe("A");
    expect(usageItem.getAttribute("href")).toBe("/app/settings?section=usage");
  });

  it("does not link to /app/settings/ledger anymore", async () => {
    const user = userEvent.setup();
    render(<SidebarRail pathname="/app/library" sidebarMode="locked" />);

    await user.click(screen.getByRole("button", { name: "打开用户菜单" }));

    const menuItems = screen.getAllByRole("menuitem");
    const ledgerLinks = menuItems.filter(
      (item) =>
        item.tagName === "A" &&
        (item.getAttribute("href") ?? "").includes("/ledger"),
    );
    expect(ledgerLinks).toHaveLength(0);
    expect(screen.queryByText("用量与订阅")).toBeNull();
  });

  it("keeps non-settings menu items unchanged", async () => {
    const user = userEvent.setup();
    render(<SidebarRail pathname="/app/library" sidebarMode="locked" />);

    await user.click(screen.getByRole("button", { name: "打开用户菜单" }));

    const homeItem = screen.getByRole("menuitem", { name: "公共首页" });
    expect(homeItem.tagName).toBe("A");
    expect(homeItem.getAttribute("href")).toBe("/");

    expect(screen.getByRole("menuitem", { name: "退出登录" })).toBeTruthy();
  });
});
