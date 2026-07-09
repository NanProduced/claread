"use client";

import { createContext, useContext } from "react";

export type AppShellVariant = "workspace";

export type AppSidebarMode = "closed" | "overlay" | "locked";

export interface AppShellLayoutContextValue {
  variant: AppShellVariant;
  sidebarMode: AppSidebarMode;
  isWorkspaceShell: boolean;
  lockSidebar: () => void;
  closeSidebar: () => void;
  showSidebarOverlay: () => void;
  hideSidebarOverlay: () => void;
  releaseSidebarForReadingTool: () => void;
}

export const AppShellLayoutContext = createContext<AppShellLayoutContextValue>({
  variant: "workspace",
  sidebarMode: "closed",
  isWorkspaceShell: false,
  lockSidebar: () => undefined,
  closeSidebar: () => undefined,
  showSidebarOverlay: () => undefined,
  hideSidebarOverlay: () => undefined,
  releaseSidebarForReadingTool: () => undefined,
});

export function useAppShellLayout() {
  return useContext(AppShellLayoutContext);
}
