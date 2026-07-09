import type { Meta } from "@ladle/react";
import { useState } from "react";
import { appLibraryRoute } from "@/lib/routes";
import { SidebarRail } from ".";
import type { AppSidebarMode } from "../app-shell";

export default {
  title: "Layout/SidebarRail",
} satisfies Meta;

export const Default = () => {
  const [sidebarMode, setSidebarMode] = useState<AppSidebarMode>("locked");

  return (
    <div
      className="app-shell min-h-[740px] bg-web-canvas"
      data-app-shell-variant="workspace"
      data-app-sidebar-state={sidebarMode}
    >
      <SidebarRail
        pathname={appLibraryRoute}
        sidebarMode={sidebarMode}
        onSidebarOverlayOpen={() =>
          setSidebarMode((mode) => (mode === "locked" ? mode : "overlay"))
        }
        onSidebarOverlayClose={() =>
          setSidebarMode((mode) => (mode === "overlay" ? "closed" : mode))
        }
        onSidebarLock={() => setSidebarMode("locked")}
        onSidebarClose={() => setSidebarMode("closed")}
      />
      <div className="pl-[var(--app-shell-sidebar-width)]" />
    </div>
  );
};
