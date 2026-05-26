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
        <div className="app-nav-surface-shadow flex h-full flex-col px-3 py-4">
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
                      ? "app-nav-item--active text-ink"
                      : "text-muted hover:bg-[var(--app-control-quiet)] hover:text-ink"
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

          <div className="mt-auto flex flex-col gap-1.5">
            {collapsed ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    className="focus-ring flex min-h-11 w-full items-center justify-center rounded-note text-sm font-semibold text-muted transition-colors hover:bg-[var(--app-control-quiet)] hover:text-ink"
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
                className="focus-ring group flex min-h-11 w-full items-center gap-3 rounded-note px-3 text-sm font-semibold text-muted transition-colors hover:bg-[var(--app-control-quiet)] hover:text-ink"
                onClick={togglePalette}
              >
                <Search aria-hidden="true" className="h-[18px] w-[18px]" />
                <span>搜索或跳转</span>
                <span className="ml-auto text-xs tracking-[0.08em] text-subtle">{shortcutLabel}</span>
              </button>
            )}
          </div>

          <div className="space-y-3 border-t border-hairline/90 pt-4">
            {!collapsed ? (
              <div className="rounded-note bg-[var(--app-control-quiet)] px-3 py-3">
                <div className="flex items-center gap-2 text-xs font-semibold text-ink">
                  <BookOpen aria-hidden="true" className="h-3.5 w-3.5 text-lens-blue" />
                  阅读镜头
                </div>
                <p className="mt-2 text-xs leading-5 text-muted">
                  文章优先，工具退后。词典、笔记和设置围绕原文出现。
                </p>
              </div>
            ) : null}
            <Link
              href={homeRoute}
              className={`focus-ring flex min-h-10 items-center rounded-note text-sm font-semibold text-muted transition-colors hover:bg-[var(--app-control-quiet)] hover:text-ink ${
                collapsed ? "justify-center px-0" : "gap-3 px-3"
              }`}
            >
              <Compass aria-hidden="true" className="h-4 w-4" />
              {!collapsed ? <span>返回公共首页</span> : null}
            </Link>
            <button
              type="button"
              className={`focus-ring flex min-h-10 w-full items-center rounded-note text-sm font-semibold text-muted transition-colors hover:bg-[var(--app-control-quiet)] hover:text-ink ${
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
