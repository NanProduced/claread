"use client";

import {
  BookMarked,
  BookOpen,
  ChevronsLeft,
  ChevronsRight,
  Library,
  Plus,
  Settings,
} from "lucide-react";
import type { Route } from "next";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

const navigationItems = [
  { href: "/read" as Route, label: "新解读", icon: Plus },
  { href: "/library" as Route, label: "阅读记录", icon: Library },
  { href: "/vocabulary" as Route, label: "生词本", icon: BookMarked },
  { href: "/settings" as Route, label: "设置", icon: Settings },
] as const;

function isActive(pathname: string, href: Route) {
  return pathname === href || (href !== "/read" && pathname.startsWith(String(href)));
}

export function AppShellFrame({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const standalone = pathname === "/login";
  const [manualCollapsed, setManualCollapsed] = useState<boolean | null>(null);
  const collapsed = manualCollapsed ?? pathname.startsWith("/reader/");

  const railWidth = collapsed ? "md:pl-[84px]" : "md:pl-[232px]";

  if (standalone) {
    return <div className="min-h-screen bg-web-canvas text-ink">{children}</div>;
  }

  return (
    <div className="min-h-screen bg-web-canvas text-ink">
      <aside
        className={`fixed inset-y-0 left-0 z-30 hidden border-r border-hairline bg-surface-warm md:flex md:flex-col ${
          collapsed ? "w-[84px]" : "w-[232px]"
        }`}
        aria-label="Claread 产品导航"
      >
        <div className="flex h-full flex-col px-3 py-4">
          <Link
            href={"/read" as Route}
            className={`focus-ring flex min-h-12 items-center rounded-note px-2 transition-colors hover:bg-reader-paper ${
              collapsed ? "justify-center" : "gap-3"
            }`}
          >
            <Image
              src="/brand/claread-icon-fullcolor.png"
              alt="Claread"
              width={36}
              height={36}
              className="h-9 w-9 shrink-0 rounded-full"
            />
            {!collapsed ? (
              <div className="min-w-0">
                <div className="font-headline text-xl font-semibold leading-none tracking-normal">
                  Claread
                </div>
                <div className="mt-1 text-[0.6875rem] font-semibold tracking-[0.22em] text-lens-blue">
                  透读
                </div>
              </div>
            ) : null}
          </Link>

          <nav className="mt-8 flex flex-1 flex-col gap-1">
            {navigationItems.map((item) => {
              const Icon = item.icon;
              const active = isActive(pathname, item.href);

              return (
                <Link
                  key={item.href}
                  href={item.href}
                  title={collapsed ? item.label : undefined}
                  className={`focus-ring group flex min-h-11 items-center rounded-note text-sm font-semibold transition-colors ${
                    collapsed ? "justify-center px-0" : "gap-3 px-3"
                  } ${
                    active
                      ? "bg-reader-paper text-ink shadow-surface-quiet"
                      : "text-muted hover:bg-reader-paper/70 hover:text-ink"
                  }`}
                >
                  <Icon
                    aria-hidden="true"
                    className={`h-[18px] w-[18px] ${active ? "text-lens-blue" : "text-muted group-hover:text-ink"}`}
                  />
                  {!collapsed ? <span>{item.label}</span> : null}
                </Link>
              );
            })}
          </nav>

          <div className="space-y-3 border-t border-hairline pt-4">
            {!collapsed ? (
              <div className="px-2 py-2">
                <div className="flex items-center gap-2 text-xs font-semibold text-ink">
                  <BookOpen aria-hidden="true" className="h-3.5 w-3.5 text-lens-blue" />
                  阅读镜头
                </div>
                <p className="mt-2 text-xs leading-5 text-muted">
                  文章优先，工具退后。词典、笔记和设置围绕原文出现。
                </p>
              </div>
            ) : null}
            <button
              type="button"
              className={`focus-ring flex min-h-10 w-full items-center rounded-note text-sm font-semibold text-muted transition-colors hover:bg-reader-paper hover:text-ink ${
                collapsed ? "justify-center" : "justify-between px-3"
              }`}
              onClick={() => setManualCollapsed((value) => !(value ?? collapsed))}
              aria-label={collapsed ? "展开导航" : "折叠导航"}
            >
              {!collapsed ? <span>折叠导航</span> : null}
              {collapsed ? (
                <ChevronsRight aria-hidden="true" className="h-4 w-4" />
              ) : (
                <ChevronsLeft aria-hidden="true" className="h-4 w-4" />
              )}
            </button>
          </div>
        </div>
      </aside>

      <div className={`${railWidth} min-h-screen pb-20 md:pb-0`}>
        {children}
      </div>

      <nav
        className="fixed inset-x-0 bottom-0 z-40 grid grid-cols-4 border-t border-hairline bg-surface-warm px-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] pt-2 shadow-[0_-10px_28px_rgba(17,17,17,0.06)] md:hidden"
        aria-label="移动端导航"
      >
        {navigationItems.map((item) => {
          const Icon = item.icon;
          const active = isActive(pathname, item.href);

          return (
            <Link
              key={item.href}
              href={item.href}
              className={`focus-ring flex min-h-12 flex-col items-center justify-center gap-1 rounded-note text-[0.6875rem] font-semibold ${
                active ? "text-ink" : "text-muted"
              }`}
            >
              <Icon aria-hidden="true" className={`h-4 w-4 ${active ? "text-lens-blue" : ""}`} />
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
