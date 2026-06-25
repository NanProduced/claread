"use client";

import { AlertTriangle, ArrowRight, BookOpenCheck } from "lucide-react";
import type { Route } from "next";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  fetchAnalysisTaskStatus,
  fetchCurrentAnalysisTask,
  isAnalysisTerminalStatus,
  type WebAnalysisTaskView,
} from "@/lib/analysis-task-client";
import { cn } from "@/lib/cn";
import {
  appReadRoute,
  appLibraryRoute,
  isAppReaderPlatePath,
  isAppReadingRecordPath,
  legacyAppReaderRoute,
} from "@/lib/routes";
import type {
  ReadingRecordListItemVm,
  ReadingRecordListResult,
} from "@/services/bff/reading-records";
import type { ReadingRecordProductState } from "@/types/api/reading-records";

const ACTIVE_PRODUCT_STATES: ReadingRecordProductState[] = [
  "processing",
  "readable_enhancing",
  "action_required",
  "failed",
];

const PRIORITY_PRODUCT_STATES: ReadingRecordProductState[] = [
  "action_required",
  "failed",
  "processing",
  "readable_enhancing",
];

const ACTIVE_TASK_POLL_INTERVAL_MS = 8000;

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
  const params = new URLSearchParams({
    limit: "8",
    productState: ACTIVE_PRODUCT_STATES.join(","),
  });

  return fetch(`/api/web/reading-records?${params.toString()}`, { signal })
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
  for (const state of PRIORITY_PRODUCT_STATES) {
    const match = items.find((item) => item.productState === state);
    if (match) {
      return match;
    }
  }

  return items[0] || null;
}

function shouldShowReadingRecordActivityIndicator(pathname: string): boolean {
  return (
    pathname !== appReadRoute &&
    !isAppReaderPlatePath(pathname) &&
    !isAppReadingRecordPath(pathname)
  );
}

function legacyTaskRoute(task: WebAnalysisTaskView | null): string {
  if (!task) {
    return appLibraryRoute;
  }

  return task.readerUrl || legacyAppReaderRoute(task.recordId);
}

export function ReadingRecordActivityIndicator({
  pathname,
}: {
  pathname: string;
}) {
  const router = useRouter();
  const [readingRecordState, setReadingRecordState] =
    useState<ReadingRecordActivityState>({
      status: "loading",
      items: [],
    });
  const [legacyTask, setLegacyTask] = useState<WebAnalysisTaskView | null>(null);
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
    queueMicrotask(() => {
      if (!cancelled) {
        setReadingRecordState({ status: "loading", items: [] });
        setLegacyTask(null);
      }
    });

    fetchReadingRecords(controller.signal).then((items) => {
      if (!cancelled) {
        setReadingRecordState({ status: "loaded", items });
      }
    });

    fetchCurrentAnalysisTask()
      .then((payload) => {
        if (cancelled || !payload.hasActive || !payload.task) {
          if (!cancelled) {
            setLegacyTask(null);
          }
          return;
        }

        if (isAnalysisTerminalStatus(payload.task.status)) {
          setLegacyTask(null);
          return;
        }

        setLegacyTask(payload.task);
      })
      .catch(() => {
        if (!cancelled) {
          setLegacyTask(null);
        }
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [shouldShow]);

  useEffect(() => {
    if (!shouldShow || !legacyTask || isAnalysisTerminalStatus(legacyTask.status)) {
      return;
    }

    const pollingTask = legacyTask;
    let cancelled = false;
    let timer: number | undefined;

    async function poll() {
      try {
        const payload = await fetchAnalysisTaskStatus(pollingTask.taskId);
        if (cancelled) {
          return;
        }

        if (
          payload.status === "succeeded" ||
          payload.status === "failed" ||
          payload.status === "cancelled" ||
          payload.status === "expired"
        ) {
          setLegacyTask(null);
          return;
        }

        if (payload.status && payload.recordId && payload.taskId) {
          setLegacyTask({
            taskId: payload.taskId,
            recordId: payload.recordId,
            status: payload.status,
            readerUrl: payload.readerUrl || legacyAppReaderRoute(payload.recordId),
            failureCode: payload.failureCode,
            failureMessage: payload.failureMessage,
          });
        }
      } catch {
        return;
      }

      timer = window.setTimeout(poll, ACTIVE_TASK_POLL_INTERVAL_MS);
    }

    timer = window.setTimeout(poll, ACTIVE_TASK_POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }
    };
  }, [legacyTask, shouldShow]);

  const activity = useMemo(
    () => selectReadingRecordActivity(readingRecordState.items),
    [readingRecordState.items],
  );

  if (
    !shouldShow ||
    (readingRecordState.status === "loading" && !legacyTask) ||
    (!activity && !legacyTask)
  ) {
    return null;
  }

  const isLegacyOnly = !activity && Boolean(legacyTask);
  const isAttention = activity
    ? activity.productState === "action_required" ||
      activity.productState === "failed"
    : false;
  const statusLabel = activity
    ? productStateLabel(activity.productState)
    : "旧任务处理中";
  const title = activity ? activity.title : "旧 Reader 任务仍在运行";
  const secondaryText = activity
    ? legacyTask
      ? "另有旧任务仍在透读，可通过旧入口继续查看。"
      : "打开当前阅读记录查看最新进展。"
    : "该任务仍使用旧入口，仅保留过渡查看能力。";
  const actionHref = activity ? activity.readerUrl : legacyTaskRoute(legacyTask);

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
      <div className="flex items-start gap-3">
        <span
          className={cn(
            "mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full border",
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
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-[0.8rem] font-semibold text-ink">{statusLabel}</p>
            {legacyTask && activity ? (
              <span className="rounded-full border border-hairline/70 bg-reader-paper/70 px-2 py-0.5 text-[0.64rem] font-semibold tracking-[0.08em] text-muted">
                旧任务
              </span>
            ) : null}
            {isLegacyOnly ? (
              <span className="rounded-full border border-hairline/70 bg-reader-paper/70 px-2 py-0.5 text-[0.64rem] font-semibold tracking-[0.08em] text-muted">
                Legacy
              </span>
            ) : null}
          </div>
          <p className="mt-0.5 truncate text-[0.72rem] font-medium text-muted">
            {title}
          </p>
          <p className="mt-1 text-[0.7rem] leading-5 text-muted/90">
            {secondaryText}
          </p>
        </div>
        <button
          type="button"
          className="focus-ring inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-muted transition-colors hover:bg-reader-paper/70 hover:text-ink"
          onClick={() => router.push(actionHref as Route)}
          aria-label={activity ? "打开阅读记录" : "打开旧任务"}
        >
          <ArrowRight aria-hidden className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
