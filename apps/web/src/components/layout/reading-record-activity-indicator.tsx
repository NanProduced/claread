"use client";

import { AlertTriangle, ArrowRight, BookOpenCheck } from "lucide-react";
import type { Route } from "next";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { cn } from "@/lib/cn";
import {
  appReadRoute,
  isAppReaderPlatePath,
  isAppReadingRecordPath,
} from "@/lib/routes";
import type {
  ReadingRecordListItemVm,
  ReadingRecordListResult,
} from "@/services/bff/reading-records";
import type { ReadingRecordProductState } from "@/types/api/reading-records";

const PRIORITY_PRODUCT_STATES: ReadingRecordProductState[] = [
  "processing",
  "readable_enhancing",
  "action_required",
  "failed",
];

type ReadingRecordActivityState =
  | { status: "loading"; items: [] }
  | { status: "loaded"; items: ReadingRecordListItemVm[] };

function productStateLabel(state: ReadingRecordProductState): string {
  switch (state) {
    case "processing":
      return "处理中";
    case "readable_enhancing":
      return "可读·增强中";
    case "action_required":
      return "需要处理";
    case "failed":
      return "处理失败";
    case "needs_confirmation":
      return "待确认";
    case "deleted":
      return "已删除";
    default:
      return state;
  }
}

function fetchReadingRecords(
  signal?: AbortSignal,
): Promise<ReadingRecordListItemVm[]> {
  return fetch("/api/web/reading-records?limit=8", { signal })
    .then((response) => response.json())
    .then((result: ReadingRecordListResult) => (result.ok ? result.items : []))
    .catch((error: unknown) => {
      if (error instanceof DOMException && error.name === "AbortError") {
        return [];
      }
      return [];
    });
}

function selectReadingRecordActivity(
  items: ReadingRecordListItemVm[],
): ReadingRecordListItemVm | null {
  return (
    items.find((item) =>
      PRIORITY_PRODUCT_STATES.includes(item.productState),
    ) ||
    items[0] ||
    null
  );
}

function shouldShowReadingRecordActivityIndicator(pathname: string): boolean {
  return (
    pathname !== appReadRoute &&
    !isAppReaderPlatePath(pathname) &&
    !isAppReadingRecordPath(pathname)
  );
}

export function ReadingRecordActivityIndicator({
  pathname,
}: {
  pathname: string;
}) {
  const router = useRouter();
  const [state, setState] = useState<ReadingRecordActivityState>({
    status: "loading",
    items: [],
  });
  const shouldShow = useMemo(
    () => shouldShowReadingRecordActivityIndicator(pathname),
    [pathname],
  );

  useEffect(() => {
    if (!shouldShow) {
      return;
    }

    let cancelled = false;
    const controller = new AbortController();

    fetchReadingRecords(controller.signal).then((items) => {
      if (!cancelled) {
        setState({ status: "loaded", items });
      }
    });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [shouldShow]);

  const activity = useMemo(
    () => selectReadingRecordActivity(state.items),
    [state.items],
  );

  if (!shouldShow || state.status === "loading" || !activity) {
    return null;
  }

  const isAttention =
    activity.productState === "action_required" ||
    activity.productState === "failed";
  const statusLabel = productStateLabel(activity.productState);

  return (
    <div
      className={cn(
        "fixed bottom-[calc(9.25rem+env(safe-area-inset-bottom))] left-4 right-4 z-30 md:bottom-[6.25rem] md:left-auto md:right-5 md:w-[22rem]",
        "rounded-[10px] border border-hairline/70 bg-surface/94 px-4 py-3 shadow-[0_14px_34px_rgba(23,21,17,0.12)] backdrop-blur-sm",
        isAttention && "border-amber-300/70 bg-amber-50/95",
      )}
      role="status"
      aria-live="polite"
    >
      <div className="flex items-center gap-3">
        <span
          className={cn(
            "inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full border",
            isAttention
              ? "border-amber-300 bg-amber-100 text-amber-800"
              : "border-lens-blue/20 bg-lens-blue/[0.04] text-lens-blue",
          )}
        >
          {isAttention ? (
            <AlertTriangle aria-hidden className="h-4 w-4" />
          ) : (
            <BookOpenCheck aria-hidden className="h-4 w-4" />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[0.8rem] font-semibold text-ink">
            {statusLabel}
          </p>
          <p className="mt-0.5 truncate text-[0.72rem] font-medium text-muted">
            {activity.title}
          </p>
        </div>
        <button
          type="button"
          className="focus-ring inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-muted transition-colors hover:bg-reader-paper/70 hover:text-ink"
          onClick={() => router.push(activity.readerUrl as Route)}
          aria-label="打开新阅读记录"
        >
          <ArrowRight aria-hidden className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
