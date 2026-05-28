"use client";

import {
  BookMarked,
  BookOpen,
  Compass,
  ChevronsLeft,
  ChevronsRight,
  Library,
  Plus,
  Search,
  Settings,
} from "lucide-react";
import type { Route } from "next";
import Image from "next/image";
import Link from "next/link";
import {
  appLibraryRoute,
  appReadRoute,
  appSettingsRoute,
  appVocabularyRoute,
  homeRoute,
} from "@/lib/routes";
import { formatShortcut } from "@/lib/shortcuts";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/primitives/tooltip";
import { useCommandPalette } from "../command-palette";
import { cn } from "@/lib/cn";

const navigationItems = [
  { href: appReadRoute, label: "新解读", icon: Plus },
  { href: appLibraryRoute, label: "阅读记录", icon: Library },
  { href: appVocabularyRoute, label: "生词本", icon: BookMarked },
  { href: appSettingsRoute, label: "设置", icon: Settings },
] as const;

export function isSidebarActive(pathname: string, href: Route) {
  return pathname === href || (href !== appReadRoute && pathname.startsWith(String(href)));
}

export interface SidebarRailProps {
  pathname: string;
  collapsed: boolean;
  onToggle: () => void;
}

export function SidebarRail({ pathname, collapsed, onToggle }: SidebarRailProps) {
  const togglePalette = useCommandPalette((s) => s.toggle);
  const shortcutLabel = formatShortcut("Primary+K");

  return (
    <>
      <aside
        className={`app-nav-surface fixed inset-y-0 left-0 z-30 hidden border-r border-hairline md:flex md:flex-col ${
          collapsed ? "w-[84px]" : "w-[232px]"
        }`}
        aria-label="Claread 产品导航"
      >
        <div className="app-nav-surface-shadow flex h-full flex-col px-3 py-5">
          {/* ── Logo Area ── */}
          <Link
            href={appReadRoute}
            className={`focus-ring flex min-h-12 items-center rounded-note px-2 transition-colors hover:bg-[var(--app-control-quiet)] ${
              collapsed ? "justify-center" : "gap-3"
            }`}
          >
            <Image
              src="/brand/claread-icon-fullcolor.png"
              alt="Claread"
              width={36}
              height={36}
              className="brand-aperture-shell brand-aperture-mark h-9 w-9 shrink-0 rounded-full border"
            />
            {!collapsed ? (
              <div className="min-w-0">
                <div className="font-headline text-xl font-semibold leading-none tracking-normal">Claread</div>
                <div className="mt-1 text-[0.65rem] font-bold tracking-[0.22em] text-muted/80">透读</div>
              </div>
            ) : null}
          </Link>

          {/* ── Search (Moved to Top) ── */}
          <div className="mt-6 flex flex-col px-1">
            {collapsed ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    className="focus-ring flex min-h-[38px] w-full items-center justify-center rounded-[8px] text-sm font-semibold text-muted transition-colors hover:bg-[var(--app-control-quiet)] hover:text-ink"
                    onClick={togglePalette}
                    aria-label="搜索或跳转"
                  >
                    <Search aria-hidden="true" className="h-[18px] w-[18px]" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="right">
                  搜索或跳转 ({shortcutLabel})
                </TooltipContent>
              </Tooltip>
            ) : (
              <button
                type="button"
                className="focus-ring group flex min-h-[38px] w-full items-center gap-3 rounded-[8px] px-2 text-[0.85rem] font-semibold text-muted transition-colors hover:bg-[var(--app-control-quiet)] hover:text-ink"
                onClick={togglePalette}
              >
                <div className="flex w-5 shrink-0 justify-center">
                  <Search aria-hidden="true" className="h-[18px] w-[18px]" />
                </div>
                <span>搜索或跳转</span>
                <span className="ml-auto text-[0.65rem] font-bold tracking-[0.08em] text-subtle/80">{shortcutLabel}</span>
              </button>
            )}
          </div>

          {/* ── Main Navigation ── */}
          <nav className="mt-2 flex flex-1 flex-col gap-0.5 px-1">
            {navigationItems.map((item) => {
              const Icon = item.icon;
              const active = isSidebarActive(pathname, item.href);

              return (
                <Link
                  key={item.href}
                  href={item.href}
                  title={collapsed ? item.label : undefined}
                  className={cn(
                    "relative focus-ring group flex min-h-[38px] items-center rounded-[8px] text-[0.85rem] transition-colors",
                    collapsed ? "justify-center px-0" : "gap-3 px-2",
                    active
                      ? "text-ink font-bold bg-transparent"
                      : "text-muted font-semibold hover:bg-[var(--app-control-quiet)] hover:text-ink"
                  )}
                >
                  {/* Left Indicator for Active State */}
                  {active && (
                    <div className={cn(
                      "absolute top-1.5 bottom-1.5 w-[3px] rounded-r-full bg-ink",
                      collapsed ? "left-[-12px]" : "left-[-12px]"
                    )} />
                  )}

                  <div className="flex w-5 shrink-0 justify-center">
                    <Icon
                      aria-hidden="true"
                      className={cn(
                        "h-[18px] w-[18px] transition-colors",
                        active ? "text-ink" : "text-muted group-hover:text-ink"
                      )}
                      strokeWidth={active ? 2.5 : 2}
                    />
                  </div>
                  {!collapsed ? <span>{item.label}</span> : null}
                </Link>
              );
            })}
          </nav>

          {/* ── Bottom Actions ── */}
          <div className="mt-auto flex flex-col gap-0.5 px-1 pb-2">
            <Link
              href={homeRoute}
              title={collapsed ? "公共首页" : undefined}
              className={cn(
                "focus-ring flex min-h-[38px] items-center rounded-[8px] text-[0.85rem] font-semibold text-muted transition-colors hover:bg-[var(--app-control-quiet)] hover:text-ink",
                collapsed ? "justify-center px-0" : "gap-3 px-2"
              )}
            >
              <div className="flex w-5 shrink-0 justify-center">
                <Compass aria-hidden="true" className="h-[18px] w-[18px]" />
              </div>
              {!collapsed ? <span>公共首页</span> : null}
            </Link>
            
            <button
              type="button"
              title={collapsed ? "展开导航" : "折叠导航"}
              className={cn(
                "focus-ring flex min-h-[38px] items-center rounded-[8px] text-[0.85rem] font-semibold text-muted transition-colors hover:bg-[var(--app-control-quiet)] hover:text-ink",
                collapsed ? "justify-center w-full px-0" : "gap-3 px-2"
              )}
              onClick={onToggle}
              aria-label={collapsed ? "展开导航" : "折叠导航"}
            >
              <div className="flex w-5 shrink-0 justify-center">
                {collapsed ? (
                  <ChevronsRight aria-hidden="true" className="h-[18px] w-[18px]" />
                ) : (
                  <ChevronsLeft aria-hidden="true" className="h-[18px] w-[18px]" />
                )}
              </div>
              {!collapsed ? <span>折叠导航</span> : null}
            </button>
          </div>
        </div>
      </aside>

      {/* ── Mobile Nav ── */}
      <nav
        className="app-nav-surface fixed inset-x-0 bottom-0 z-40 grid grid-cols-5 border-t border-hairline px-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] pt-2 shadow-[var(--app-mobile-nav-shadow)] md:hidden"
        aria-label="移动端导航"
      >
        <button
          type="button"
          className="focus-ring flex min-h-12 flex-col items-center justify-center gap-1 rounded-note text-[0.6875rem] font-semibold text-muted"
          onClick={togglePalette}
        >
          <Search aria-hidden="true" className="h-4 w-4" />
          <span>搜索</span>
        </button>
        {navigationItems.map((item) => {
          const Icon = item.icon;
          const active = isSidebarActive(pathname, item.href);

          return (
            <Link
              key={item.href}
              href={item.href}
              className={`focus-ring flex min-h-12 flex-col items-center justify-center gap-1 rounded-note text-[0.6875rem] ${
                active ? "font-bold text-ink" : "font-semibold text-muted"
              }`}
            >
              <Icon aria-hidden="true" className="h-4 w-4" strokeWidth={active ? 2.5 : 2} />
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>
    </>
  );
}
