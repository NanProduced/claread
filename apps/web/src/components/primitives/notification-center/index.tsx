"use client";

import { Bell, CheckCheck, CircleAlert, CircleCheck, Info, TriangleAlert, X } from "lucide-react";
import { useSyncExternalStore } from "react";
import { cn } from "@/lib/cn";
import { Popover, PopoverContent, PopoverTrigger } from "../popover";
import { toast } from "../toast";

export type NotificationTone = "success" | "error" | "warning" | "info";

export interface NotificationAction {
  label: string;
  onClick: () => void;
}

export interface NotificationAlertInput {
  id: string;
  tone: NotificationTone;
  title: string;
  description?: string;
  action?: NotificationAction;
}

interface NotificationEntry extends NotificationAlertInput {
  createdAt: number;
  readAt: number | null;
}

const MAX_NOTIFICATION_ENTRIES = 20;

let notificationEntries: NotificationEntry[] = [];
const notificationListeners = new Set<() => void>();

function emitNotificationChange() {
  for (const listener of notificationListeners) {
    listener();
  }
}

function subscribeToNotifications(listener: () => void) {
  notificationListeners.add(listener);
  return () => {
    notificationListeners.delete(listener);
  };
}

function getNotificationSnapshot() {
  return notificationEntries;
}

function upsertNotification(input: NotificationAlertInput) {
  const current = notificationEntries.find((entry) => entry.id === input.id);
  const nextEntry: NotificationEntry = {
    ...input,
    createdAt: current?.createdAt ?? Date.now(),
    readAt: current?.readAt ?? null,
  };

  notificationEntries = [
    nextEntry,
    ...notificationEntries.filter((entry) => entry.id !== input.id),
  ].slice(0, MAX_NOTIFICATION_ENTRIES);
  emitNotificationChange();
}

function removeNotification(id: string) {
  const nextEntries = notificationEntries.filter((entry) => entry.id !== id);
  if (nextEntries.length === notificationEntries.length) {
    return;
  }
  notificationEntries = nextEntries;
  emitNotificationChange();
}

function markNotificationRead(id: string) {
  let changed = false;
  notificationEntries = notificationEntries.map((entry) => {
    if (entry.id !== id || entry.readAt !== null) {
      return entry;
    }
    changed = true;
    return { ...entry, readAt: Date.now() };
  });
  if (changed) {
    emitNotificationChange();
  }
}

function markAllNotificationsRead() {
  const now = Date.now();
  let changed = false;
  notificationEntries = notificationEntries.map((entry) => {
    if (entry.readAt !== null) {
      return entry;
    }
    changed = true;
    return { ...entry, readAt: now };
  });
  if (changed) {
    emitNotificationChange();
  }
}

function clearNotifications() {
  if (notificationEntries.length === 0) {
    return;
  }
  notificationEntries = [];
  emitNotificationChange();
}

const toastByTone = {
  success: toast.success,
  error: toast.error,
  warning: toast.warning,
  info: toast.info,
} as const;

/**
 * Single application notification boundary. Use the short-lived methods for
 * confirmation feedback; use alert for recoverable states that also belong in
 * the session notification center.
 */
export const notify = {
  success: (...args: Parameters<typeof toast.success>) => toast.success(...args),
  error: (...args: Parameters<typeof toast.error>) => toast.error(...args),
  warning: (...args: Parameters<typeof toast.warning>) => toast.warning(...args),
  info: (...args: Parameters<typeof toast.info>) => toast.info(...args),
  alert(input: NotificationAlertInput) {
    upsertNotification(input);
    return toastByTone[input.tone](input.title, {
      id: input.id,
      position: "top-center",
      description: input.description,
      duration: Infinity,
      closeButton: false,
      cancel: {
        label: "关闭提示",
        onClick: () => undefined,
      },
      action: input.action,
    });
  },
  resolveAlert(id: string) {
    toast.dismiss(id);
    removeNotification(id);
  },
  dismissAlert(id: string) {
    toast.dismiss(id);
    removeNotification(id);
  },
  markRead: markNotificationRead,
  markAllRead: markAllNotificationsRead,
  clear: clearNotifications,
};

const tonePresentation = {
  success: { icon: CircleCheck, className: "text-[var(--structure-green)]" },
  error: { icon: CircleAlert, className: "text-error-red" },
  warning: { icon: TriangleAlert, className: "text-[var(--vocab-amber)]" },
  info: { icon: Info, className: "text-[var(--context-blue)]" },
} as const;

export interface NotificationCenterTriggerProps {
  className?: string;
  showLabel?: boolean;
  side?: "top" | "right" | "bottom" | "left";
}

export function NotificationCenterTrigger({
  className,
  showLabel = false,
  side = "bottom",
}: NotificationCenterTriggerProps) {
  const entries = useSyncExternalStore(
    subscribeToNotifications,
    getNotificationSnapshot,
    getNotificationSnapshot,
  );
  const unreadCount = entries.filter((entry) => entry.readAt === null).length;
  const triggerLabel = unreadCount > 0 ? `打开通知中心，${unreadCount} 条未读` : "打开通知中心";

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={cn(
            "focus-ring relative inline-flex items-center justify-center text-muted transition-colors hover:bg-[var(--app-control-quiet)] hover:text-ink",
            showLabel ? "min-h-12 flex-col gap-1 rounded-note text-[0.6875rem] font-semibold" : "h-7 w-7 rounded-[6px]",
            className,
          )}
          aria-label={triggerLabel}
          data-notification-center-trigger="true"
        >
          <Bell aria-hidden="true" className="h-4 w-4" strokeWidth={2} />
          {showLabel ? <span>通知</span> : null}
          {unreadCount > 0 ? (
            <span
              className="absolute right-0.5 top-0.5 flex min-w-3.5 items-center justify-center rounded-full bg-ink px-1 text-[0.5625rem] font-bold leading-3 text-reader-paper"
              aria-hidden="true"
            >
              {unreadCount > 9 ? "9+" : unreadCount}
            </span>
          ) : null}
        </button>
      </PopoverTrigger>
      <PopoverContent
        side={side}
        align="end"
        sideOffset={8}
        className="!z-[var(--app-z-shell-overlay)] !w-[min(22rem,calc(100vw-1.5rem))] !p-0"
        style={{ zIndex: "var(--app-z-shell-overlay)" }}
        data-notification-center="true"
      >
        <div className="flex items-center gap-3 border-b border-hairline px-4 py-3">
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold text-ink">通知</h2>
            <p className="mt-0.5 text-xs text-subtle">
              {unreadCount > 0 ? `${unreadCount} 条需要留意` : "当前没有未读通知"}
            </p>
          </div>
          {unreadCount > 0 ? (
            <button
              type="button"
              className="focus-ring inline-flex min-h-8 items-center gap-1 rounded-[6px] px-2 text-xs font-semibold text-muted transition-colors hover:bg-[var(--app-control-quiet)] hover:text-ink"
              onClick={markAllNotificationsRead}
            >
              <CheckCheck aria-hidden="true" className="h-3.5 w-3.5" />
              全部已读
            </button>
          ) : null}
        </div>
        {entries.length === 0 ? (
          <div className="px-4 py-8 text-center">
            <p className="text-sm font-medium text-ink">暂时没有需要处理的通知。</p>
            <p className="mt-1 text-xs leading-5 text-subtle">解析、同步和跨页任务的提示会显示在这里。</p>
          </div>
        ) : (
          <ul className="max-h-[min(30rem,calc(100vh-7rem))] divide-y divide-hairline/70 overflow-y-auto" aria-label="通知列表">
            {entries.map((entry) => {
              const presentation = tonePresentation[entry.tone];
              const Icon = presentation.icon;

              return (
                <li
                  key={entry.id}
                  className={cn(
                    "relative flex gap-3 px-4 py-3.5",
                    entry.readAt === null ? "bg-[var(--app-control-quiet)]/55" : "bg-transparent",
                  )}
                >
                  <Icon aria-hidden="true" className={cn("mt-0.5 h-4 w-4 shrink-0", presentation.className)} />
                  <div className="min-w-0 flex-1 pr-7">
                    <p className="text-sm font-semibold leading-5 text-ink">{entry.title}</p>
                    {entry.description ? (
                      <p className="mt-1 text-xs leading-5 text-muted">{entry.description}</p>
                    ) : null}
                    {entry.action ? (
                      <button
                        type="button"
                        className="focus-ring mt-2 inline-flex min-h-8 items-center rounded-[6px] border border-hairline px-2.5 text-xs font-semibold text-ink transition-colors hover:bg-[var(--app-control-quiet)]"
                        onClick={() => {
                          markNotificationRead(entry.id);
                          entry.action?.onClick();
                        }}
                      >
                        {entry.action.label}
                      </button>
                    ) : null}
                  </div>
                  <button
                    type="button"
                    className="focus-ring absolute right-2 top-2 inline-flex h-8 w-8 items-center justify-center rounded-[6px] text-subtle transition-colors hover:bg-[var(--app-control-quiet)] hover:text-ink"
                    aria-label={`关闭通知：${entry.title}`}
                    onClick={() => notify.dismissAlert(entry.id)}
                  >
                    <X aria-hidden="true" className="h-3.5 w-3.5" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </PopoverContent>
    </Popover>
  );
}
