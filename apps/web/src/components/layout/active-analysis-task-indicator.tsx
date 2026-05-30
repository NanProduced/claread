"use client";

import { ExternalLink } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  fetchAnalysisTaskStatus,
  fetchCurrentAnalysisTask,
  isAnalysisTerminalStatus,
  type WebAnalysisTaskView,
} from "@/lib/analysis-task-client";
import { appLibraryRoute, appReaderRoute } from "@/lib/routes";
import { toast } from "@/components/primitives/toast";
import { cn } from "@/lib/cn";

const ACTIVE_TASK_POLL_INTERVAL_MS = 8000;

export function ActiveAnalysisTaskIndicator({
  pathname,
}: {
  pathname: string;
}) {
  const router = useRouter();
  const [task, setTask] = useState<WebAnalysisTaskView | null>(null);
  const hiddenOnReadPage = pathname === "/app/read";

  useEffect(() => {
    let cancelled = false;

    async function restore() {
      try {
        const payload = await fetchCurrentAnalysisTask();
        if (cancelled || !payload.hasActive || !payload.task || isAnalysisTerminalStatus(payload.task.status)) {
          return;
        }
        setTask(payload.task);
      } catch {
        // The indicator is supplemental; avoid surfacing session/upstream noise globally.
      }
    }

    void restore();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!task || isAnalysisTerminalStatus(task.status)) {
      return;
    }

    const pollingTask = task;
    let cancelled = false;
    let timer: number | undefined;

    async function poll() {
      try {
        const payload = await fetchAnalysisTaskStatus(pollingTask.taskId);
        if (cancelled) {
          return;
        }

        if (payload.status === "succeeded") {
          setTask(null);
          const recordId = payload.recordId || pollingTask.recordId;
          toast.message("透读完成，打开阅读页", {
            action: {
              label: "打开阅读页",
              onClick: () => router.push(appReaderRoute(recordId)),
            },
          });
          return;
        }

        if (payload.status === "failed" || payload.status === "cancelled" || payload.status === "expired") {
          setTask(null);
          toast.error("透读未完成", {
            description: payload.failureMessage || "可以稍后在记录页查看或重新提交。",
          });
          return;
        }

        if (payload.status && payload.taskId && payload.recordId) {
          setTask({
            taskId: payload.taskId,
            recordId: payload.recordId,
            status: payload.status,
            readerUrl: payload.readerUrl || appReaderRoute(payload.recordId),
            failureCode: payload.failureCode,
            failureMessage: payload.failureMessage,
          });
        }
      } catch {
        // Keep the last known task visible; the next poll may recover.
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
  }, [router, task]);

  if (!task || hiddenOnReadPage) {
    return null;
  }

  return (
    <div
      className={cn(
        "fixed bottom-[calc(5rem+env(safe-area-inset-bottom))] left-4 right-4 z-30 md:bottom-5 md:left-auto md:right-5 md:w-[22rem]",
        "rounded-[10px] border border-hairline/70 bg-surface/94 px-4 py-3 shadow-[0_14px_34px_rgba(23,21,17,0.12)] backdrop-blur-sm",
      )}
      role="status"
      aria-live="polite"
    >
      <div className="flex items-center gap-3">
        <span className="relative inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-lens-blue/20 bg-lens-blue/[0.04]">
          <span className="absolute h-7 w-7 rounded-full border border-lens-blue/25 motion-safe:animate-ping motion-reduce:animate-none" />
          <span
            className="brand-aperture-mark h-[18px] w-[18px] bg-[url('/brand/claread-icon-fullcolor.png')] bg-contain bg-center bg-no-repeat"
          />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[0.8rem] font-semibold text-ink">有一篇文章正在透读</p>
          <p className="mt-0.5 truncate text-[0.72rem] font-medium text-muted">
            稍后可在记录页看到
          </p>
        </div>
        <button
          type="button"
          className="focus-ring inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-muted transition-colors hover:bg-reader-paper/70 hover:text-ink"
          onClick={() => router.push(appLibraryRoute)}
          aria-label="去记录页"
        >
          <ExternalLink aria-hidden className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
