/** @vitest-environment jsdom */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

// --- Mock useSettingsDialog so we can assert openSettings(section) calls ---
const openSettingsMock = vi.fn();
vi.mock("@/components/settings/SettingsDialogProvider", () => ({
  useSettingsDialog: () => ({ openSettings: openSettingsMock }),
}));

afterEach(() => {
  cleanup();
  openSettingsMock.mockClear();
});

beforeEach(() => {
  openSettingsMock.mockReset();
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

    const trigger = container.querySelector<HTMLElement>('[data-desktop-user-menu-trigger="true"]');
    expect(trigger).not.toBeNull();
    await user.click(trigger!);

    const menu = await screen.findByRole("menu");
    expect(sidebar?.contains(menu)).toBe(true);
  });
});

describe("User menu settings entries (desktop)", () => {
  it("reveals settings menuitems when the desktop user menu opens", async () => {
    const user = userEvent.setup();
    const { container } = render(<SidebarRail pathname="/app/library" sidebarMode="locked" />);
    const trigger = container.querySelector<HTMLElement>('[data-desktop-user-menu-trigger="true"]');
    expect(trigger).not.toBeNull();

    await user.click(trigger!);

    expect(screen.getByRole("menuitem", { name: "个人资料" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "偏好设置" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "用量与积分" })).toBeTruthy();
  });

  it("calls openSettings with the matching section when each settings entry is clicked", async () => {
    const user = userEvent.setup();
    const { container } = render(<SidebarRail pathname="/app/library" sidebarMode="locked" />);
    const trigger = container.querySelector<HTMLElement>('[data-desktop-user-menu-trigger="true"]');
    expect(trigger).not.toBeNull();

    // Radix DropdownMenu closes after each item click. Re-open the menu
    // before each click so we can exercise every entry in one test.

    // Account
    await user.click(trigger!);
    fireEvent.click(screen.getByRole("menuitem", { name: "个人资料" }));
    expect(openSettingsMock).toHaveBeenCalledWith("account");
    expect(openSettingsMock).toHaveBeenCalledTimes(1);
    openSettingsMock.mockClear();

    // Preferences
    await user.click(trigger!);
    fireEvent.click(screen.getByRole("menuitem", { name: "偏好设置" }));
    expect(openSettingsMock).toHaveBeenCalledWith("preferences");
    expect(openSettingsMock).toHaveBeenCalledTimes(1);
    openSettingsMock.mockClear();

    // Usage
    await user.click(trigger!);
    fireEvent.click(screen.getByRole("menuitem", { name: "用量与积分" }));
    expect(openSettingsMock).toHaveBeenCalledWith("usage");
    expect(openSettingsMock).toHaveBeenCalledTimes(1);
  });

  it("does not render any /app/settings or ?section= href in the user menu", async () => {
    const user = userEvent.setup();
    const { container } = render(<SidebarRail pathname="/app/library" sidebarMode="locked" />);
    const trigger = container.querySelector<HTMLElement>('[data-desktop-user-menu-trigger="true"]');
    expect(trigger).not.toBeNull();

    await user.click(trigger!);

    const menuItems = screen.getAllByRole("menuitem");
    for (const item of menuItems) {
      const href = item.getAttribute("href");
      if (href) {
        // Any href present must NOT point at the old settings routes.
        expect(href).not.toMatch(/\/app\/settings/);
        expect(href).not.toMatch(/[?&]section=/);
      }
    }

    // Settings entries should not be anchors at all.
    const accountItem = screen.getByRole("menuitem", { name: "个人资料" });
    const preferencesItem = screen.getByRole("menuitem", { name: "偏好设置" });
    const usageItem = screen.getByRole("menuitem", { name: "用量与积分" });

    expect(accountItem.tagName).not.toBe("A");
    expect(accountItem.getAttribute("href")).toBeNull();
    expect(preferencesItem.tagName).not.toBe("A");
    expect(preferencesItem.getAttribute("href")).toBeNull();
    expect(usageItem.tagName).not.toBe("A");
    expect(usageItem.getAttribute("href")).toBeNull();
  });

  it("does not link to /app/settings/ledger anymore", async () => {
    const user = userEvent.setup();
    const { container } = render(<SidebarRail pathname="/app/library" sidebarMode="locked" />);
    const trigger = container.querySelector<HTMLElement>('[data-desktop-user-menu-trigger="true"]');
    expect(trigger).not.toBeNull();

    await user.click(trigger!);

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
    const { container } = render(<SidebarRail pathname="/app/library" sidebarMode="locked" />);
    const trigger = container.querySelector<HTMLElement>('[data-desktop-user-menu-trigger="true"]');
    expect(trigger).not.toBeNull();

    await user.click(trigger!);

    const homeItem = screen.getByRole("menuitem", { name: "公共首页" });
    expect(homeItem.tagName).toBe("A");
    expect(homeItem.getAttribute("href")).toBe("/");

    expect(screen.getByRole("menuitem", { name: "退出登录" })).toBeTruthy();
  });
});

describe("Mobile settings entry", () => {
  it("renders a user menu trigger in the mobile bottom nav", () => {
    const { container } = render(<SidebarRail pathname="/app/library" sidebarMode="closed" />);
    const mobileTrigger = container.querySelector<HTMLElement>('[data-mobile-user-menu-trigger="true"]');
    expect(mobileTrigger).not.toBeNull();
    expect(mobileTrigger?.getAttribute("aria-label")).toBe("打开用户菜单");
    expect(mobileTrigger?.textContent).toContain("我的");
  });

  it("exposes the same three settings entries from the mobile user menu and routes them through openSettings", async () => {
    const user = userEvent.setup();
    const { container } = render(<SidebarRail pathname="/app/library" sidebarMode="closed" />);
    const mobileTrigger = container.querySelector<HTMLElement>('[data-mobile-user-menu-trigger="true"]');
    expect(mobileTrigger).not.toBeNull();

    await user.click(mobileTrigger!);

    const accountItem = screen.getByRole("menuitem", { name: "个人资料" });
    const preferencesItem = screen.getByRole("menuitem", { name: "偏好设置" });
    const usageItem = screen.getByRole("menuitem", { name: "用量与积分" });

    // Settings entries must not be anchors — they open the AppShell Dialog.
    expect(accountItem.tagName).not.toBe("A");
    expect(accountItem.getAttribute("href")).toBeNull();
    expect(preferencesItem.tagName).not.toBe("A");
    expect(preferencesItem.getAttribute("href")).toBeNull();
    expect(usageItem.tagName).not.toBe("A");
    expect(usageItem.getAttribute("href")).toBeNull();

    // Click → openSettings(section). Radix closes after each click, so
    // re-open the menu between clicks to exercise every entry.
    fireEvent.click(accountItem);
    expect(openSettingsMock).toHaveBeenCalledWith("account");
    openSettingsMock.mockClear();

    await user.click(mobileTrigger!);
    fireEvent.click(screen.getByRole("menuitem", { name: "偏好设置" }));
    expect(openSettingsMock).toHaveBeenCalledWith("preferences");
    openSettingsMock.mockClear();

    await user.click(mobileTrigger!);
    fireEvent.click(screen.getByRole("menuitem", { name: "用量与积分" }));
    expect(openSettingsMock).toHaveBeenCalledWith("usage");
  });

  it("keeps the mobile trigger touch target at least 44x44", () => {
    const sidebarSource = readFileSync(
      resolve(process.cwd(), "src/components/layout/sidebar-rail/index.tsx"),
      "utf8",
    );
    expect(sidebarSource).toMatch(
      /min-h-12[\s\S]*?data-mobile-user-menu-trigger="true"/,
    );
  });
});
