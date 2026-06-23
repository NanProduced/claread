"use client";

import { usePathname } from "next/navigation";
import { useState } from "react";
import { SidebarRail } from "../sidebar-rail";
import { CommandPaletteProvider } from "../command-palette";
import { ActiveAnalysisTaskIndicator } from "../active-analysis-task-indicator";
import { ReadingRecordActivityIndicator } from "../reading-record-activity-indicator";

import { ScrollArea } from "@/components/primitives/scroll-area";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [manualCollapsed, setManualCollapsed] = useState<boolean | null>(null);
  const collapsed =
    manualCollapsed ??
    (pathname.startsWith("/app/reader/") ||
      pathname.startsWith("/app/reader-record/") ||
      pathname === "/app/read");

  const railWidth = collapsed ? "md:pl-[84px]" : "md:pl-[232px]";

  return (
    <div className="h-screen overflow-hidden bg-web-canvas text-ink">
      <CommandPaletteProvider />
      <SidebarRail pathname={pathname} collapsed={collapsed} onToggle={() => setManualCollapsed((value) => !(value ?? collapsed))} />
      <ActiveAnalysisTaskIndicator pathname={pathname} />
      <ReadingRecordActivityIndicator />
      <ScrollArea className={`${railWidth} h-full pb-20 md:pb-0`}>
        {children}
      </ScrollArea>
    </div>
  );
}
