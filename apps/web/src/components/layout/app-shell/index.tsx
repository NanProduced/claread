"use client";

import { Menu } from "lucide-react";
import { usePathname } from "next/navigation";
import { useCallback, useMemo, useState } from "react";
import { SidebarRail } from "../sidebar-rail";
import { CommandPaletteProvider } from "../command-palette";
import { ReadingRecordActivityIndicator } from "../reading-record-activity-indicator";
import { useRecentReading } from "../recent-reading-context";
import {
  AppShellLayoutContext,
  type AppShellVariant,
  type AppSidebarMode,
} from "./app-shell-context";

import { ScrollArea } from "@/components/primitives/scroll-area";

export interface AppShellProps {
  children: React.ReactNode;
  variant?: AppShellVariant;
  userName?: string;
  userContact?: string;
  userPlanLabel?: string;
}

export function AppShell({
  children,
  variant: variantProp,
  userName,
  userContact,
  userPlanLabel,
}: AppShellProps) {
  const pathname = usePathname();
  const { items: recentRecords } = useRecentReading();
  const variant = variantProp ?? "workspace";
  const isWorkspaceShell = variant === "workspace";
  const [sidebarMode, setSidebarMode] = useState<AppSidebarMode>("closed");
  const showSidebarOverlay = useCallback(() => {
    setSidebarMode((mode) => (mode === "locked" ? mode : "overlay"));
  }, []);
  const hideSidebarOverlay = useCallback(() => {
    setSidebarMode((mode) => (mode === "overlay" ? "closed" : mode));
  }, []);
  const lockSidebar = useCallback(() => {
    setSidebarMode("locked");
  }, []);
  const closeSidebar = useCallback(() => {
    setSidebarMode("closed");
  }, []);
  const releaseSidebarForReadingTool = useCallback(() => {
    setSidebarMode("closed");
  }, []);
  const appShellContext = useMemo(
    () => ({
      variant,
      sidebarMode,
      isWorkspaceShell,
      lockSidebar,
      closeSidebar,
      showSidebarOverlay,
      hideSidebarOverlay,
      releaseSidebarForReadingTool,
    }),
    [
      closeSidebar,
      hideSidebarOverlay,
      isWorkspaceShell,
      lockSidebar,
      releaseSidebarForReadingTool,
      showSidebarOverlay,
      sidebarMode,
      variant,
    ],
  );

  return (
    <AppShellLayoutContext.Provider value={appShellContext}>
      <div
        className="app-shell h-screen overflow-hidden bg-web-canvas text-ink"
        data-app-shell="true"
        data-app-shell-variant={variant}
        data-app-sidebar-state={sidebarMode}
      >
        <CommandPaletteProvider />
        {sidebarMode !== "locked" ? (
          <button
            type="button"
            className="app-sidebar-peek-button focus-ring fixed left-2 top-1.5 hidden h-8 w-8 items-center justify-center rounded-[6px] text-muted-foreground transition-[background-color,border-color,color] duration-150 ease-out hover:text-ink md:flex"
            style={{ zIndex: "var(--app-z-shell-navigation)" }}
            aria-label="打开导航"
            title="打开导航"
            data-app-sidebar-trigger-placement="topbar"
            data-app-sidebar-trigger="true"
            onClick={lockSidebar}
            onFocus={showSidebarOverlay}
            onPointerEnter={showSidebarOverlay}
          >
            <Menu aria-hidden="true" className="h-[18px] w-[18px]" />
          </button>
        ) : null}
        <SidebarRail
          pathname={pathname}
          variant={variant}
          recentRecords={recentRecords}
          sidebarMode={sidebarMode}
          onSidebarOverlayOpen={showSidebarOverlay}
          onSidebarOverlayClose={hideSidebarOverlay}
          onSidebarLock={lockSidebar}
          onSidebarClose={closeSidebar}
          userName={userName}
          userContact={userContact}
          userPlanLabel={userPlanLabel}
        />
        <ReadingRecordActivityIndicator pathname={pathname} />
        <ScrollArea
          className="h-full pb-20 md:pl-[var(--app-shell-sidebar-width)] md:pb-0"
          data-app-shell-content="true"
        >
          {children}
        </ScrollArea>
      </div>
    </AppShellLayoutContext.Provider>
  );
}

export { useAppShellLayout } from "./app-shell-context";
export type { AppShellVariant, AppSidebarMode } from "./app-shell-context";
