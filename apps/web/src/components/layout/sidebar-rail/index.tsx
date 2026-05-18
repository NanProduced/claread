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

const navigationItems = [
  { href: "/read" as Route, label: "新解读", icon: Plus },
  { href: "/library" as Route, label: "阅读记录", icon: Library },
  { href: "/vocabulary" as Route, label: "生词本", icon: BookMarked },
  { href: "/settings" as Route, label: "设置", icon: Settings },
] as const;

export function isSidebarActive(pathname: string, href: Route) {
  return pathname === href || (href !== "/read" && pathname.startsWith(String(href)));
}

export interface SidebarRailProps {
  pathname: string;
  collapsed: boolean;
  onToggle: () => void;
}

export function SidebarRail({ pathname, collapsed, onToggle }: SidebarRailProps) {
  return (
    <>
      <aside
        className={`fixed inset-y-0 left-0 z-30 hidden border-r border-hairline bg-[linear-gradient(180deg,rgba(255,255,255,0.88),rgba(247,246,242,0.96))] md:flex md:flex-col ${
          collapsed ? "w-[84px]" : "w-[232px]"
        }`}
        aria-label="Claread 产品导航"
      >
        <div className="flex h-full flex-col px-3 py-4 shadow-[inset_-1px_0_0_rgba(232,228,218,0.9)]">
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
                <div className="font-headline text-xl font-semibold leading-none tracking-normal">Claread</div>
                <div className="mt-1 text-[0.6875rem] font-semibold tracking-[0.22em] text-lens-blue">透读</div>
              </div>
            ) : null}
          </Link>

          <nav className="mt-8 flex flex-1 flex-col gap-1.5">
            {navigationItems.map((item) => {
              const Icon = item.icon;
              const active = isSidebarActive(pathname, item.href);

              return (
                <Link
                  key={item.href}
                  href={item.href}
                  title={collapsed ? item.label : undefined}
                  className={`focus-ring group flex min-h-11 items-center rounded-note text-sm font-semibold transition-colors ${
                    collapsed ? "justify-center px-0" : "gap-3 px-3"
                  } ${
                    active
                      ? "bg-[linear-gradient(180deg,rgba(255,255,255,0.95),rgba(251,250,246,0.98))] text-ink shadow-surface-quiet"
                      : "text-muted hover:bg-reader-paper/78 hover:text-ink"
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

          <div className="space-y-3 border-t border-hairline/90 pt-4">
            {!collapsed ? (
              <div className="rounded-note bg-reader-paper/65 px-3 py-3">
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
              onClick={onToggle}
              aria-label={collapsed ? "展开导航" : "折叠导航"}
            >
              {!collapsed ? <span>折叠导航</span> : null}
              {collapsed ? <ChevronsRight aria-hidden="true" className="h-4 w-4" /> : <ChevronsLeft aria-hidden="true" className="h-4 w-4" />}
            </button>
          </div>
        </div>
      </aside>

      <nav
        className="fixed inset-x-0 bottom-0 z-40 grid grid-cols-4 border-t border-hairline bg-surface-warm px-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] pt-2 shadow-[0_-10px_28px_rgba(17,17,17,0.06)] md:hidden"
        aria-label="移动端导航"
      >
        {navigationItems.map((item) => {
          const Icon = item.icon;
          const active = isSidebarActive(pathname, item.href);

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
    </>
  );
}
