"use client";

import { usePathname } from "next/navigation";
import { useState } from "react";
import { SidebarRail } from "../sidebar-rail";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const standalone = pathname === "/login";
  const [manualCollapsed, setManualCollapsed] = useState<boolean | null>(null);
  const collapsed = manualCollapsed ?? pathname.startsWith("/reader/");

  if (standalone) {
    return <div className="min-h-screen bg-web-canvas text-ink">{children}</div>;
  }

  const railWidth = collapsed ? "md:pl-[84px]" : "md:pl-[232px]";

  return (
    <div className="min-h-screen bg-web-canvas text-ink">
      <SidebarRail pathname={pathname} collapsed={collapsed} onToggle={() => setManualCollapsed((value) => !(value ?? collapsed))} />
      <div className={`${railWidth} min-h-screen pb-20 md:pb-0`}>{children}</div>
    </div>
  );
}
