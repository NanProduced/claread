/** @vitest-environment jsdom */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/primitives/tooltip";
import { SettingsDialogProvider } from "@/components/settings/SettingsDialogProvider";
import { AppShell } from ".";

const navigationMock = vi.hoisted(() => ({
  pathname: "/app/reader/record_1",
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => navigationMock.pathname,
  useRouter: () => ({
    push: navigationMock.push,
  }),
}));

vi.mock("../reading-record-activity-indicator", () => ({
  ReadingRecordActivityIndicator: () => null,
}));

vi.mock("../recent-reading-context", () => ({
  useRecentReading: () => ({
    items: [
      {
        readingRecordId: "record_1",
        title: "文章标题一",
        productState: "needs_confirmation",
        readerUrl: "/app/reader/record_1",
        createdAt: "2024-01-01T00:00:00.000Z",
        updatedAt: "2024-01-01T00:00:00.000Z",
      },
      {
        readingRecordId: "record_2",
        title: "文章标题二",
        productState: "completed",
        readerUrl: "/app/reader/record_2",
        createdAt: "2024-01-02T00:00:00.000Z",
        updatedAt: "2024-01-02T00:00:00.000Z",
      },
    ],
    refetch: vi.fn(),
  }),
}));

afterEach(() => {
  cleanup();
  navigationMock.push.mockReset();
  navigationMock.pathname = "/app/reader/record_1";
});

describe("AppShell", () => {
  it("uses a Notion-style closed/overlay/locked sidebar contract on reader pages", () => {
    const { container } = render(
      <SettingsDialogProvider>
        <TooltipProvider>
          <AppShell>
            <main>Reader surface</main>
          </AppShell>
        </TooltipProvider>
      </SettingsDialogProvider>,
    );

    const shell = container.querySelector<HTMLElement>('[data-app-shell="true"]');
    expect(shell).not.toBeNull();
    expect(shell?.dataset.appShellVariant).toBe("workspace");
    expect(shell?.dataset.appSidebarState).toBe("closed");

    const content = container.querySelector<HTMLElement>('[data-app-shell-content="true"]');
    expect(content?.className).toContain("md:pl-[var(--app-shell-sidebar-width)]");

    const trigger = screen.getByRole("button", { name: "打开导航" });
    expect(trigger.className).toContain("app-sidebar-peek-button");
    expect(trigger.getAttribute("data-app-sidebar-trigger-placement")).toBe("topbar");
    expect(trigger.className).toContain("top-1.5");
    expect(trigger.className).not.toContain("shadow");
    expect(trigger.style.zIndex).toBe("var(--app-z-shell-navigation)");

    const sidebar = container.querySelector<HTMLElement>('[data-app-sidebar="rail"]');
    expect(sidebar).not.toBeNull();
    expect(sidebar?.dataset.appSidebarState).toBe("closed");
    expect(sidebar?.dataset.appSidebarVariant).toBe("workspace");
    expect(sidebar?.className).toContain("w-[var(--app-shell-sidebar-width-locked)]");
    expect(sidebar?.className).toContain("pointer-events-none");
    expect(sidebar?.style.zIndex).toBe("var(--app-z-shell-navigation)");
    expect(screen.getByText("最近阅读")).not.toBeNull();
    expect(screen.getByText("文章标题一")).not.toBeNull();
    expect(screen.getByText("文章标题二")).not.toBeNull();
    // Status label for the first item (needs_confirmation)
    expect(screen.getByText("需要确认")).not.toBeNull();
    expect(screen.queryByText("打开一篇文章后会显示在这里。")).toBeNull();
    expect(screen.getAllByText("Claread")).toHaveLength(2);
    expect(screen.getAllByText("新解读")).toHaveLength(2);
    expect(screen.getByText("阅读资产")).not.toBeNull();
    expect(screen.getAllByText("全部阅读记录")).toHaveLength(2);
    expect(screen.getAllByText("生词本")).toHaveLength(2);
    expect(
      document.querySelector<HTMLElement>('[data-desktop-user-menu-trigger="true"]'),
    ).not.toBeNull();
    expect(screen.getByText("Free")).not.toBeNull();
    expect(screen.queryByText("阅读工作区")).toBeNull();

    fireEvent.pointerEnter(trigger);

    expect(shell?.dataset.appSidebarState).toBe("overlay");
    expect(sidebar?.dataset.appSidebarState).toBe("overlay");
    expect(sidebar?.className).not.toContain("pointer-events-none");
    expect(sidebar?.className).toContain("top-12");

    fireEvent.click(screen.getByRole("button", { name: "固定导航" }));

    expect(shell?.dataset.appSidebarState).toBe("locked");
    expect(sidebar?.dataset.appSidebarState).toBe("locked");
    expect(sidebar?.className).toContain("inset-y-0");

    fireEvent.click(screen.getByRole("button", { name: "隐藏导航" }));

    expect(shell?.dataset.appSidebarState).toBe("closed");
    expect(sidebar?.dataset.appSidebarState).toBe("closed");
  });

  it("keeps the shell-navigation layer in both CSS and the runtime component contract", () => {
    const globalsSource = readFileSync(
      resolve(process.cwd(), "src/app/globals.css"),
      "utf8",
    );

    expect(globalsSource).toMatch(
      /\.app-sidebar-peek-button\s*\{[\s\S]*?z-index:\s*var\(--app-z-shell-navigation\);/,
    );
    expect(globalsSource).toMatch(
      /\.app-workspace-sidebar\s*\{[\s\S]*?z-index:\s*var\(--app-z-shell-navigation\);/,
    );
    expect(globalsSource).toContain("--app-z-shell-navigation: 70;");
  });
  it("uses the same workspace sidebar contract on non-reader app routes", () => {
    navigationMock.pathname = "/app/library";

    const { container } = render(
      <SettingsDialogProvider>
        <TooltipProvider>
          <AppShell>
            <main>Library</main>
          </AppShell>
        </TooltipProvider>
      </SettingsDialogProvider>,
    );

    const shell = container.querySelector<HTMLElement>('[data-app-shell="true"]');
    const sidebar = container.querySelector<HTMLElement>('[data-app-sidebar="rail"]');

    expect(shell?.dataset.appShellVariant).toBe("workspace");
    expect(shell?.dataset.appSidebarState).toBe("closed");
    expect(sidebar?.dataset.appSidebarState).toBe("closed");
    expect(sidebar?.className).toContain(
      "w-[var(--app-shell-sidebar-width-locked)]",
    );
    expect(screen.getByText("阅读资产")).not.toBeNull();
    expect(screen.getByText("文章标题一")).not.toBeNull();
    expect(screen.getByText("文章标题二")).not.toBeNull();

    fireEvent.pointerEnter(screen.getByRole("button", { name: "打开导航" }));

    expect(shell?.dataset.appSidebarState).toBe("overlay");
    expect(sidebar?.dataset.appSidebarState).toBe("overlay");

    expect(
      document.querySelector<HTMLElement>('[data-desktop-user-menu-trigger="true"]'),
    ).not.toBeNull();
  });
});
