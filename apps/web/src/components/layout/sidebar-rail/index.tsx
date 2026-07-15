"use client";

import {
  BookMarked,
  ExternalLink,
  FileText,
  Library,
  LogOut,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
  Settings,
  UserRound,
  WalletCards,
} from "lucide-react";
import type { Route } from "next";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/primitives/dropdown-menu";
import {
  appLibraryRoute,
  appReadRoute,
  appReadResumeCandidateRoute,
  appSettingsRoute,
  appVocabularyRoute,
  homeRoute,
  loginRouteBase,
} from "@/lib/routes";
import { formatShortcut } from "@/lib/shortcuts";
import { NotificationCenterTrigger } from "@/components/primitives/notification-center";
import { useCommandPalette } from "../command-palette";
import { cn } from "@/lib/cn";
import { readingRecordStatusKey, readingRecordStatusLabel, shouldShowStatusLine } from "@/lib/reader-record-status";
import type { AppShellVariant, AppSidebarMode } from "../app-shell/app-shell-context";
import type { ReadingRecordListItemVm } from "@/services/bff/reading-records";

const newReadItem = { href: appReadRoute, label: "新解读", icon: Plus } as const;

const workspaceItems = [
  { href: appLibraryRoute, label: "全部阅读记录", icon: Library },
  { href: appVocabularyRoute, label: "生词本", icon: BookMarked },
] as const;

const fallbackUserName = "Claread";
const fallbackUserPlan = "Free";

export function isSidebarActive(pathname: string, href: Route) {
  return pathname === href || (href !== appReadRoute && pathname.startsWith(String(href)));
}

function isReaderRecordPath(pathname: string) {
  return pathname.startsWith("/app/reader-record/");
}

export interface SidebarRailProps {
  pathname: string;
  variant?: AppShellVariant;
  sidebarMode?: AppSidebarMode;
  onSidebarOverlayOpen?: () => void;
  onSidebarOverlayClose?: () => void;
  onSidebarLock?: () => void;
  onSidebarClose?: () => void;
  userName?: string;
  userContact?: string;
  userPlanLabel?: string;
  recentRecords?: ReadingRecordListItemVm[];
}

export function SidebarRail({
  pathname,
  variant = "workspace",
  sidebarMode = "closed",
  onSidebarOverlayOpen,
  onSidebarOverlayClose,
  onSidebarLock,
  onSidebarClose,
  userName: userNameProp,
  userContact: userContactProp,
  userPlanLabel: userPlanLabelProp,
  recentRecords = [],
}: SidebarRailProps) {
  const router = useRouter();
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const [sidebarPointerInside, setSidebarPointerInside] = useState(false);
  const [logoutPending, setLogoutPending] = useState(false);
  const togglePalette = useCommandPalette((s) => s.toggle);
  const shortcutLabel = formatShortcut("Primary+K");
  const sidebarVisible = sidebarMode !== "closed";
  const sidebarLocked = sidebarMode === "locked";
  const toggleLabel = sidebarLocked ? "隐藏导航" : "固定导航";
  const handleToggle = sidebarLocked ? onSidebarClose : onSidebarLock;
  const readerRecordActive = isReaderRecordPath(pathname);
  const newReadActive = isSidebarActive(pathname, newReadItem.href);
  const NewReadIcon = newReadItem.icon;
  const userName = userNameProp?.trim() || fallbackUserName;
  const userContact = userContactProp?.trim();
  const userPlanLabel = userPlanLabelProp?.trim() || fallbackUserPlan;
  const userInitial = userName.trim().slice(0, 1).toUpperCase() || "C";
  const recentList = recentRecords.slice(0, 10);
  const currentRecordId = readerRecordActive
    ? (pathname.split("/").pop() ?? null)
    : null;

  async function handleLogout() {
    if (logoutPending) {
      return;
    }

    setLogoutPending(true);
    try {
      await fetch("/api/web/auth/logout", { method: "POST" });
      router.refresh();
      router.push(loginRouteBase);
    } finally {
      setLogoutPending(false);
    }
  }

  const navItemClassName = (active: boolean) =>
    cn(
      "focus-ring group flex min-h-8 items-center gap-2.5 rounded-[6px] px-2 text-[0.8125rem] transition-[background-color,color] duration-150",
      active
        ? "bg-[var(--app-control-quiet)] font-semibold text-ink"
        : "font-medium text-muted hover:bg-[var(--app-control-quiet)] hover:text-ink",
    );

  function handleSidebarMouseEnter() {
    setSidebarPointerInside(true);
    onSidebarOverlayOpen?.();
  }

  function handleSidebarMouseLeave() {
    setSidebarPointerInside(false);
    if (!sidebarLocked && !userMenuOpen) {
      onSidebarOverlayClose?.();
    }
  }

  function handleUserMenuOpenChange(open: boolean) {
    setUserMenuOpen(open);
    if (!open && !sidebarLocked && !sidebarPointerInside) {
      onSidebarOverlayClose?.();
    }
  }

  return (
    <>
      <aside
        className={cn(
          "app-nav-surface app-workspace-sidebar fixed left-0 hidden border-hairline md:flex md:flex-col",
          "w-[var(--app-shell-sidebar-width-locked)] transition-[transform,opacity] duration-150 ease-out",
          sidebarLocked
            ? "inset-y-0 border-r shadow-none"
            : "bottom-2 left-2 top-12 rounded-[10px] border shadow-[var(--app-sidebar-overlay-shadow)]",
          !sidebarVisible
            ? "pointer-events-none -translate-x-[calc(var(--app-shell-sidebar-width-locked)+1rem)] opacity-0"
            : "translate-x-0 opacity-100",
        )}
        style={{ zIndex: "var(--app-z-shell-navigation)" }}
        aria-label="Claread 产品导航"
        data-app-sidebar="rail"
        data-app-sidebar-state={sidebarMode}
        data-app-sidebar-variant={variant}
        onMouseEnter={handleSidebarMouseEnter}
        onMouseLeave={handleSidebarMouseLeave}
      >
        <div className="app-nav-surface-shadow flex h-full flex-col px-2.5 py-3">
          <div className="flex min-h-9 items-center gap-2 px-1">
            <div className="flex min-w-0 flex-1 items-center gap-2 px-1.5 py-1">
              <Image
                src="/brand/claread-icon-fullcolor.png"
                alt=""
                aria-hidden="true"
                width={22}
                height={22}
                className="brand-aperture-mark h-[22px] w-[22px] shrink-0 object-contain"
              />
              <div className="min-w-0" aria-label="Claread">
                <div className="truncate text-[0.875rem] font-semibold leading-4 text-ink">Claread</div>
              </div>
            </div>
            <NotificationCenterTrigger />
            <button
              type="button"
              title={toggleLabel}
              className="focus-ring flex h-7 w-7 shrink-0 items-center justify-center rounded-[6px] text-muted transition-colors hover:bg-[var(--app-control-quiet)] hover:text-ink"
              onClick={handleToggle}
              aria-label={toggleLabel}
            >
              {sidebarLocked ? (
                <PanelLeftClose aria-hidden="true" className="h-4 w-4" />
              ) : (
                <PanelLeftOpen aria-hidden="true" className="h-4 w-4" />
              )}
            </button>
          </div>

          <div className="mt-3 px-1">
            <button
              type="button"
              className="focus-ring group flex min-h-8 w-full items-center gap-2 rounded-[7px] px-2 text-[0.8125rem] font-medium text-muted transition-colors hover:bg-[var(--app-control-quiet)] hover:text-ink"
              onClick={togglePalette}
              aria-label={`搜索或跳转，快捷键 ${shortcutLabel}`}
            >
              <Search aria-hidden="true" className="h-4 w-4 shrink-0" />
              <span>搜索或跳转</span>
              <span className="ml-auto text-[0.6875rem] font-medium text-subtle/80">
                {shortcutLabel}
              </span>
            </button>
          </div>

          <div className="mt-3 px-1">
            <Link
              href={newReadItem.href}
              className={cn(
                "focus-ring group flex min-h-8 items-center gap-2 rounded-[7px] px-2 text-[0.8125rem] transition-[background-color,color] duration-150",
                newReadActive
                  ? "bg-[var(--app-control-quiet)] font-semibold text-ink"
                  : "bg-transparent font-semibold text-ink hover:bg-[var(--app-control-quiet)]",
              )}
            >
              <NewReadIcon
                aria-hidden="true"
                className="h-4 w-4 shrink-0"
                strokeWidth={newReadActive ? 2.35 : 2.15}
              />
              <span className="truncate">{newReadItem.label}</span>
            </Link>
          </div>

          <nav className="mt-4 flex min-h-0 flex-1 flex-col overflow-hidden" aria-label="阅读导航">
            <div className="px-2 text-[0.6875rem] font-medium leading-5 text-subtle">最近阅读</div>
            <div className="mt-1 min-h-0 flex-1 overflow-y-auto pr-0.5 [scrollbar-color:color-mix(in_srgb,var(--muted)_34%,transparent)_transparent] [scrollbar-width:thin]">
              {recentList.length === 0 ? (
                <div className="flex flex-col gap-2 px-1 py-3 text-[0.78rem] leading-5 text-subtle">
                  <span>打开一篇文章后会显示在这里。</span>
                  <Link
                    href={appLibraryRoute}
                    className="focus-ring inline-flex items-center gap-1 self-start rounded-[6px] px-2 py-1 text-[0.78rem] font-semibold text-muted hover:bg-[var(--app-control-quiet)] hover:text-ink"
                  >
                    阅读记录
                  </Link>
                </div>
              ) : (
                <ul className="flex flex-col gap-0.5">
                  {recentList.map((record) => {
                    const isCurrent = record.readingRecordId === currentRecordId;
                    const statusKey = readingRecordStatusKey(record.productState, record.readinessState);
                    const status = shouldShowStatusLine(statusKey) ? readingRecordStatusLabel(statusKey) : null;
                    const needsConfirmation = record.productState === 'needs_confirmation';
                    return (
                      <li key={record.readingRecordId}>
                        <Link
                          href={
                            needsConfirmation
                              ? appReadResumeCandidateRoute(record.readingRecordId)
                              : (record.readerUrl as Route)
                          }
                          className={cn(navItemClassName(isCurrent), "items-start py-1.5")}
                          aria-current={isCurrent ? "page" : undefined}
                        >
                          <FileText
                            aria-hidden="true"
                            className={cn("mt-0.5 h-4 w-4 shrink-0", isCurrent ? "text-ink" : "text-muted group-hover:text-ink")}
                            strokeWidth={isCurrent ? 2.25 : 2}
                          />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate font-semibold">{record.title}</span>
                            {status ? (
                              <span className="block truncate text-[0.6875rem] font-normal text-subtle">
                                {status}
                              </span>
                            ) : null}
                          </span>
                        </Link>
                      </li>
                    );
                  })}
                  <li>
                    <Link
                      href={appLibraryRoute}
                      className="focus-ring flex min-h-8 items-center gap-2 rounded-[6px] px-2 text-[0.78rem] font-medium text-muted transition-colors hover:bg-[var(--app-control-quiet)] hover:text-ink"
                    >
                      更多
                    </Link>
                  </li>
                </ul>
              )}
            </div>

            <div className="mt-4 border-t border-hairline/70 pt-3">
              <div className="px-2 text-[0.6875rem] font-medium leading-5 text-subtle">阅读资产</div>
              <div className="mt-1 flex flex-col gap-0.5">
                {workspaceItems.map((item) => {
                  const Icon = item.icon;
                  const active = isSidebarActive(pathname, item.href);

                  return (
                    <Link key={item.href} href={item.href} className={navItemClassName(active)}>
                      <Icon
                        aria-hidden="true"
                        className={cn(
                          "h-4 w-4 shrink-0",
                          active ? "text-ink" : "text-muted group-hover:text-ink",
                        )}
                        strokeWidth={active ? 2.25 : 2}
                      />
                      <span className="truncate">{item.label}</span>
                    </Link>
                  );
                })}
              </div>
            </div>
          </nav>

          <div className="mt-3 border-t border-hairline/70 px-1 pt-3">
            <DropdownMenu open={userMenuOpen} onOpenChange={handleUserMenuOpenChange}>
              <DropdownMenuTrigger asChild>
                <button
                  type="button"
                  className="focus-ring flex min-h-10 w-full items-center gap-2.5 rounded-[8px] px-2 text-left transition-colors hover:bg-[var(--app-control-quiet)]"
                  aria-label="打开用户菜单"
                >
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-ink text-[0.75rem] font-semibold text-reader-paper">
                    {userInitial}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[0.875rem] font-semibold leading-4 text-ink">
                      {userName}
                    </span>
                    <span className="block truncate text-[0.75rem] leading-4 text-subtle">
                      {userPlanLabel}
                    </span>
                  </span>
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent
                portalled={false}
                side="top"
                align="start"
                className="!z-[var(--app-z-shell-overlay)] w-60"
                style={{ zIndex: "var(--app-z-shell-overlay)" }}
              >
                <div className="px-3 py-2">
                  <div className="truncate text-[0.8125rem] font-semibold leading-5 text-ink">
                    {userName}
                  </div>
                  <div className="truncate text-[0.75rem] leading-5 text-subtle">
                    {userContact ?? "未绑定联系方式"}
                  </div>
                </div>
                <DropdownMenuSeparator />
                <DropdownMenuItem asChild>
                  <Link href={appSettingsRoute} className="gap-2">
                    <UserRound aria-hidden="true" className="h-4 w-4" />
                    <span>个人资料</span>
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href={appSettingsRoute} className="gap-2">
                    <Settings aria-hidden="true" className="h-4 w-4" />
                    <span>偏好设置</span>
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href={`${appSettingsRoute}/ledger` as Route} className="gap-2">
                    <WalletCards aria-hidden="true" className="h-4 w-4" />
                    <span>用量与订阅</span>
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem asChild>
                  <Link href={homeRoute} className="gap-2">
                    <ExternalLink aria-hidden="true" className="h-4 w-4" />
                    <span>公共首页</span>
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  className="gap-2 text-red-700 focus:text-red-700"
                  disabled={logoutPending}
                  onSelect={() => {
                    void handleLogout();
                  }}
                >
                  <LogOut aria-hidden="true" className="h-4 w-4" />
                  <span>{logoutPending ? "正在退出..." : "退出登录"}</span>
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
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
        {[newReadItem, ...workspaceItems].map((item) => {
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
        <NotificationCenterTrigger
          showLabel
          side="top"
          className="text-muted"
        />
      </nav>
    </>
  );
}
