"use client";

import { ArrowRight } from "lucide-react";
import type { Route } from "next";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  fetchAnalysisTaskStatus,
  fetchCurrentAnalysisTask,
  isAnalysisTerminalStatus,
  type WebAnalysisTaskView,
} from "@/lib/analysis-task-client";
import {
  appReadRoute,
  isAppReaderPlatePath,
  isAppReadingRecordPath,
  legacyAppReaderRoute,
  appLibraryRoute,
} from "@/lib/routes";

const ACTIVE_TASK_POLL_INTERVAL_MS = 8000;

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
    };
  }, [shouldShow, pathname]);

  useEffect(() => {
    if (
      !shouldShow ||
      !legacyTask ||
      isAnalysisTerminalStatus(legacyTask.status)
    ) {
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

  if (!shouldShow) {
    return null;
  }

  if (!legacyTask) {
    return null;
  }

  const actionHref = legacyTaskRoute(legacyTask);

  return (
    <div
      className="fixed bottom-[calc(9.25rem+env(safe-area-inset-bottom))] left-4 right-4 z-30 md:bottom-[6.25rem] md:left-auto md:right-5 md:w-[22rem] rounded-[10px] border border-hairline/70 bg-surface/94 px-4 py-3 shadow-[0_14px_34px_rgba(23,21,17,0.12)] backdrop-blur-sm"
      role="status"
      aria-live="polite"
    >
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-[0.8rem] font-semibold text-ink">旧任务处理中</p>
            <span className="rounded-full border border-hairline/70 bg-reader-paper/70 px-2 py-0.5 text-[0.64rem] font-semibold tracking-[0.08em] text-muted">
              Legacy
            </span>
          </div>
          <p className="mt-0.5 truncate text-[0.72rem] font-medium text-muted">
            旧 Reader 任务仍在运行
          </p>
          <p className="mt-1 text-[0.7rem] leading-5 text-muted/90">
            该任务仍使用旧入口，仅保留过渡查看能力。
          </p>
        </div>
        <button
          type="button"
          className="focus-ring inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-muted transition-colors hover:bg-reader-paper/70 hover:text-ink"
          onClick={() => router.push(actionHref as Route)}
          aria-label="打开旧任务"
        >
          <ArrowRight aria-hidden className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
