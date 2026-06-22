"use client";

import { ArrowRight, BookOpen, Check, ChevronDown, ClipboardPaste, FileUp, Link2, X, FileText, Target } from "lucide-react";
import Image from "next/image";
import type { Route } from "next";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Button } from "@/components/primitives/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/primitives/popover";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/primitives/tooltip";
import {
  fetchAnalysisTaskStatus,
  fetchCurrentAnalysisTask,
  isAnalysisTerminalStatus,
  type WebAnalysisTaskView,
} from "@/lib/analysis-task-client";
import { cn } from "@/lib/cn";
import {
  READING_GOAL_OPTIONS,
  READING_VARIANT_OPTIONS,
  DEFAULT_READING_VARIANT_BY_GOAL,
  type ReadingDefaultState,
  normalizeReadingDefaults,
} from "@/lib/reading-defaults";
import { appLibraryRoute, legacyAppReaderRoute } from "@/lib/routes";
import type { ReadingGoalDto, ReadingVariantDto, TaskStatusDto } from "@/types/api/tasks";

type SubmitState =
  | { kind: "idle" }
  | { kind: "pending"; message: string }
  | { kind: "success"; message: string }
  | { kind: "error"; message: string; recordId?: string };

interface AnalysisSubmitResponse {
  ok: boolean;
  message: string;
  taskId?: string;
  status?: TaskStatusDto;
  readerUrl?: string;
  recordId?: string;
}

const POLL_INTERVAL_MS = 2000;
const libraryRoute = appLibraryRoute;
const LOADING_MESSAGES = [
  "正在梳理文章结构",
  "正在识别关键表达",
  "正在整理语法线索",
  "正在生成精读批注",
  "正在准备阅读视图",
];
const intakeMethods = [
  { key: "paste", label: "贴入文本", icon: ClipboardPaste, available: true },
  { key: "link", label: "链接导入", icon: Link2, available: false },
  { key: "upload", label: "上传文档", icon: FileUp, available: false },
  { key: "sample", label: "示例文章", icon: BookOpen, available: false },
] as const;

const SHORT_DESC: Record<string, string> = {
  daily_reading: "自然读懂",
  academic: "术语与结构",
  exam: "长难句与考点",
};

const GOAL_ICONS: Record<string, React.ElementType> = {
  daily_reading: BookOpen,
  academic: FileText,
  exam: Target,
};

function GoalCard({
  goal,
  active,
  onSelect,
}: {
  goal: { value: string; label: string };
  active: boolean;
  onSelect: () => void;
}) {
  const Icon = GOAL_ICONS[goal.value] || BookOpen;
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onSelect}
      className={`group relative flex w-full flex-col items-center justify-center gap-2.5 rounded-[14px] border p-3 pt-4 text-center transition-all duration-300 ease-out focus-ring ${
        active
          ? "border-transparent bg-white shadow-[0_6px_20px_rgba(17,17,17,0.06)] ring-1 ring-ink/5"
          : "border-transparent bg-transparent hover:bg-ink/[0.03]"
      }`}
    >
      {active && (
        <div className="absolute right-2 top-2 flex h-[1.1rem] w-[1.1rem] items-center justify-center rounded-full bg-lens-blue text-white shadow-sm">
          <Check className="h-[0.7rem] w-[0.7rem]" strokeWidth={3} />
        </div>
      )}
      <div className="flex flex-col items-center gap-0.5">
        <span className={`font-sans text-[0.85rem] tracking-tight ${active ? "font-semibold text-ink" : "font-medium text-ink/80 group-hover:text-ink"}`}>
          {goal.label}
        </span>
        <span className="font-sans text-[0.72rem] tracking-wide text-muted">{SHORT_DESC[goal.value]}</span>
      </div>
      <div className={`mt-0.5 flex h-7 w-7 items-center justify-center transition-colors duration-300 ${active ? "text-ink/80" : "text-subtle group-hover:text-muted"}`}>
        <Icon className="h-[1.15rem] w-[1.15rem]" strokeWidth={1.5} />
      </div>
    </button>
  );
}

function VariantPill({
  variant,
  active,
  onSelect,
}: {
  variant: { value: string; label: string };
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onSelect}
      className={`relative flex min-h-[2.25rem] w-full items-center justify-center gap-1.5 rounded-[8px] border px-1 transition-all duration-300 ease-out focus-ring ${
        active
          ? "border-lens-blue/30 bg-[rgba(37,99,235,0.06)] text-ink ring-1 ring-lens-blue/20"
          : "border-hairline/60 bg-transparent text-muted hover:border-hairline hover:bg-ink/[0.03] hover:text-ink"
      }`}
    >
      <span className={`text-[0.78rem] tracking-tight ${active ? "font-semibold" : "font-medium"}`}>{variant.label}</span>
      {active && <span className="h-[5px] w-[5px] shrink-0 rounded-full bg-lens-blue" />}
    </button>
  );
}

function ApertureCornerSubmitButton({
  isPending,
  isReady,
  onClick,
}: {
  isPending: boolean;
  isReady: boolean;
  onClick: () => void;
}) {
  return (
    <Button
      variant="primary-ink"
      className={cn("aperture-corner-cta group/aperture font-sans", isReady && "aperture-corner-cta--ready")}
      data-pending={isPending ? "true" : "false"}
      data-ready={isReady ? "true" : "false"}
      disabled={isPending}
      onClick={onClick}
    >
      <span className="aperture-corner-cta__mark" aria-hidden="true">
        <span className="aperture-corner-cta__asset aperture-corner-cta__asset--default" />
        <span className="aperture-corner-cta__asset aperture-corner-cta__asset--focus" />
      </span>
      <span className="aperture-corner-cta__content">
        <span className="aperture-corner-cta__label">
          {isPending ? "透读中..." : "开始透读"}
        </span>
        {!isPending ? (
          <ArrowRight aria-hidden className="aperture-corner-cta__arrow" />
        ) : null}
      </span>
    </Button>
  );
}

function MiniAperturePulse({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "relative inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-ink/10 bg-surface/70",
        className,
      )}
      aria-hidden="true"
    >
      <span className="absolute h-7 w-7 rounded-full border border-lens-blue/25 motion-safe:animate-ping motion-reduce:animate-none" />
      <span
        className="brand-aperture-mark h-[18px] w-[18px] bg-[url('/brand/claread-icon-fullcolor.png')] bg-contain bg-center bg-no-repeat"
      />
    </span>
  );
}

function AnalysisLoadingArtwork() {
  return (
    <div className="relative h-[18.5rem] w-full max-w-[34rem] sm:h-[20rem]">
      <div className="absolute inset-0 flex items-center justify-center">
        <div className="relative aspect-[16/11] w-full max-w-[29rem]">
          <Image
            src="/images/loading/analysis-loading-stage-idle.png"
            alt=""
            aria-hidden="true"
            fill
            sizes="(max-width: 640px) 80vw, 29rem"
            className="pointer-events-none absolute inset-0 h-full w-full select-none object-contain"
          />

          <div className="pointer-events-none absolute inset-0 motion-reduce:hidden" aria-hidden="true">
            <span className="loading-stage__pulse loading-stage__pulse--outer" />
            <span className="loading-stage__pulse loading-stage__pulse--inner" />
            <span className="loading-stage__scan-track" />
            <span className="loading-stage__scan-core" />
            <span className="loading-stage__glint loading-stage__glint--left" />
            <span className="loading-stage__glint loading-stage__glint--right" />
            <span className="loading-stage__highlight-shimmer" />
          </div>
        </div>
      </div>

      <style>{`
        .loading-stage__pulse {
          position: absolute;
          left: 57%;
          top: 46.2%;
          transform: translate(-50%, -50%) scale(0.92);
          border-radius: 999px;
          border: 1px solid rgba(31, 94, 255, 0.16);
          opacity: 0;
          animation: loading-stage-pulse 3.1s cubic-bezier(0.22, 1, 0.36, 1) infinite;
        }

        .loading-stage__pulse--outer {
          width: 26%;
          height: 26%;
        }

        .loading-stage__pulse--inner {
          width: 18%;
          height: 18%;
          animation-delay: 0.24s;
        }

        .loading-stage__scan-track {
          position: absolute;
          left: 28%;
          right: 19%;
          top: 49.2%;
          height: 1px;
          overflow: hidden;
        }

        .loading-stage__scan-core {
          position: absolute;
          left: 28%;
          top: calc(49.2% - 3px);
          width: 52%;
          height: 6px;
          border-radius: 999px;
          background: linear-gradient(
            90deg,
            rgba(31, 94, 255, 0) 0%,
            rgba(31, 94, 255, 0.08) 24%,
            rgba(140, 174, 255, 0.8) 50%,
            rgba(31, 94, 255, 0.08) 76%,
            rgba(31, 94, 255, 0) 100%
          );
          opacity: 0;
          filter: blur(0.4px);
          animation: loading-stage-scan 3.1s ease-in-out infinite;
        }

        .loading-stage__glint {
          position: absolute;
          width: 16px;
          height: 16px;
          opacity: 0;
        }

        .loading-stage__glint::before,
        .loading-stage__glint::after {
          content: "";
          position: absolute;
          left: 50%;
          top: 50%;
          transform: translate(-50%, -50%);
          border-radius: 999px;
          background: rgba(245, 186, 63, 0.92);
        }

        .loading-stage__glint::before {
          width: 16px;
          height: 2px;
        }

        .loading-stage__glint::after {
          width: 2px;
          height: 16px;
        }

        .loading-stage__glint--left {
          left: 19.2%;
          top: 60.8%;
          animation: loading-stage-glint-left 3.1s ease-in-out infinite;
        }

        .loading-stage__glint--right {
          left: 78.4%;
          top: 29.8%;
          width: 14px;
          height: 14px;
          animation: loading-stage-glint-right 3.1s ease-in-out infinite;
        }

        .loading-stage__highlight-shimmer {
          position: absolute;
          left: 36.2%;
          top: 63.6%;
          width: 18.5%;
          height: 4.2%;
          overflow: hidden;
          border-radius: 999px;
          opacity: 0;
          animation: loading-stage-shimmer 3.1s ease-in-out infinite;
        }

        .loading-stage__highlight-shimmer::before {
          content: "";
          position: absolute;
          inset: 0;
          background: linear-gradient(
            90deg,
            rgba(255, 255, 255, 0) 0%,
            rgba(255, 251, 239, 0.15) 32%,
            rgba(255, 255, 255, 0.82) 50%,
            rgba(255, 251, 239, 0.15) 68%,
            rgba(255, 255, 255, 0) 100%
          );
          transform: translateX(-115%);
          animation: loading-stage-shimmer-pass 3.1s ease-in-out infinite;
        }

        @keyframes loading-stage-pulse {
          0%,
          14%,
          100% {
            opacity: 0;
            transform: translate(-50%, -50%) scale(0.92);
          }

          30% {
            opacity: 0.42;
          }

          56% {
            opacity: 0;
            transform: translate(-50%, -50%) scale(1.18);
          }
        }

        @keyframes loading-stage-scan {
          0%,
          16%,
          100% {
            opacity: 0;
            transform: translateX(-18%);
          }

          28% {
            opacity: 0.88;
          }

          62% {
            opacity: 0.24;
            transform: translateX(18%);
          }
        }

        @keyframes loading-stage-glint-left {
          0%,
          38%,
          100% {
            opacity: 0;
            transform: scale(0.72);
          }

          46% {
            opacity: 0.88;
            transform: scale(1);
          }

          58% {
            opacity: 0;
            transform: scale(1.08);
          }
        }

        @keyframes loading-stage-glint-right {
          0%,
          60%,
          100% {
            opacity: 0;
            transform: scale(0.74);
          }

          68% {
            opacity: 0.72;
            transform: scale(1);
          }

          79% {
            opacity: 0;
            transform: scale(1.06);
          }
        }

        @keyframes loading-stage-shimmer {
          0%,
          56%,
          100% {
            opacity: 0;
          }

          68%,
          88% {
            opacity: 0.72;
          }
        }

        @keyframes loading-stage-shimmer-pass {
          0%,
          56% {
            transform: translateX(-115%);
          }

          86% {
            transform: translateX(118%);
          }

          100% {
            transform: translateX(118%);
          }
        }
      `}</style>
    </div>
  );
}

function AnalysisLoadingStage({
  title,
  animationSlot,
}: {
  title: string;
  animationSlot?: ReactNode;
}) {
  return (
    <div
      className="relative z-10 flex min-h-0 flex-1 items-center justify-center overflow-hidden px-8 py-10 sm:px-16 xl:px-24"
      aria-live="polite"
    >
      <div className="pointer-events-none absolute inset-x-16 top-10 hidden max-w-[42rem] space-y-4 opacity-[0.16] sm:block xl:left-24">
        {[0.82, 0.64, 0.76, 0.52, 0.7].map((width, index) => (
          <span
            key={index}
            className="block h-px rounded-full bg-ink/35"
            style={{ width: `${width * 100}%` }}
          />
        ))}
      </div>

      <div className="relative flex w-full max-w-[36rem] flex-col items-center text-center">
        {animationSlot ?? <AnalysisLoadingArtwork />}

        <p className="mt-1 font-sans text-[0.72rem] font-bold tracking-[0.16em] text-lens-blue/88">
          Claread Reading Desk
        </p>
        <h2 className="mt-2.5 font-headline text-[1.5rem] font-semibold leading-tight text-ink sm:text-[1.78rem]">
          {title}
        </h2>
      </div>
    </div>
  );
}

export function AnalysisLoadingStatusBar({
  messagePrefix,
}: {
  messagePrefix: string;
}) {
  const [messageIndex, setMessageIndex] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setMessageIndex((current) => (current + 1) % LOADING_MESSAGES.length);
    }, 2600);

    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className="flex min-h-12 max-w-[38rem] items-center gap-3 font-sans text-[0.78rem]">
      <MiniAperturePulse className="h-8 w-8 bg-surface/76" />
      <div className="min-w-0">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <p className="shrink-0 font-semibold text-ink">{messagePrefix}</p>
          <span className="hidden h-1 w-1 shrink-0 rounded-full bg-hairline sm:inline-flex" aria-hidden="true" />
          <p
            key={messageIndex}
            className="min-w-0 text-[0.75rem] font-semibold text-ink/74 motion-safe:animate-in motion-safe:fade-in motion-reduce:animate-none"
            aria-live="polite"
          >
            {LOADING_MESSAGES[messageIndex]}
          </p>
        </div>
        <p className="mt-1 min-w-0 text-[0.72rem] font-medium leading-5 text-muted">
          离开本页不会影响透读，完成后会保存到阅读记录
        </p>
      </div>
    </div>
  );
}

type AnalyzeSubmitFormProps = ReadingDefaultState;

export function AnalyzeSubmitForm({ readingGoal: initialGoal, readingVariant: initialVariant }: AnalyzeSubmitFormProps) {
  const router = useRouter();
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [text, setText] = useState("");
  const defaults = normalizeReadingDefaults({ readingGoal: initialGoal, readingVariant: initialVariant });
  const [readingGoal, setReadingGoal] = useState<ReadingGoalDto>(defaults.readingGoal);
  const [readingVariant, setReadingVariant] = useState<ReadingVariantDto>(defaults.readingVariant);
  const [state, setState] = useState<SubmitState>({ kind: "idle" });
  const [activeTask, setActiveTask] = useState<WebAnalysisTaskView | null>(null);
  const isWaiting = Boolean(activeTask) || state.kind === "pending";

  useEffect(() => {
    let cancelled = false;

    async function restoreActiveTask() {
      try {
        const payload = await fetchCurrentAnalysisTask();
        if (cancelled || !payload.hasActive || !payload.task) {
          return;
        }

        if (isAnalysisTerminalStatus(payload.task.status)) {
          return;
        }

        setActiveTask(payload.task);
        setState({ kind: "pending", message: "有一篇文章正在透读。" });
      } catch {
        // Active task recovery is a convenience signal; avoid blocking fresh input.
      }
    }

    void restoreActiveTask();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!activeTask || isAnalysisTerminalStatus(activeTask.status)) {
      return;
    }

    const pollingTask = activeTask;
    let cancelled = false;
    let timer: number | undefined;

    async function poll() {
      try {
        const payload = await fetchAnalysisTaskStatus(pollingTask.taskId);
        if (cancelled) {
          return;
        }

        if (payload.status === "succeeded") {
          setActiveTask(null);
          setState({ kind: "success", message: "解析完成，正在打开 Reader。" });
          router.push(
            (payload.readerUrl as Route | undefined) ||
              (payload.recordId ? legacyAppReaderRoute(payload.recordId) : libraryRoute),
          );
          return;
        }

        if (payload.status === "failed" || payload.status === "cancelled" || payload.status === "expired") {
          setActiveTask(null);
          setState({
            kind: "error",
            message: payload.failureMessage || "解析任务未能完成，请稍后重试。",
            recordId: payload.recordId || pollingTask.recordId,
          });
          return;
        }

        if (payload.status && payload.recordId && payload.taskId) {
          setActiveTask({
            taskId: payload.taskId,
            recordId: payload.recordId,
            status: payload.status,
            readerUrl: payload.readerUrl || legacyAppReaderRoute(payload.recordId),
            failureCode: payload.failureCode,
            failureMessage: payload.failureMessage,
          });
        }

        setState({ kind: "pending", message: "正在透读。" });
      } catch (error) {
        if (!cancelled) {
          setState({
            kind: "error",
            message: error instanceof Error ? error.message : "查询任务状态失败。",
            recordId: pollingTask.recordId,
          });
          setActiveTask(null);
        }
        return;
      }

      timer = window.setTimeout(poll, POLL_INTERVAL_MS);
    }

    timer = window.setTimeout(poll, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }
    };
  }, [activeTask, router]);

  async function handleSubmit() {
    if (isWaiting) {
      return;
    }

    if (text.trim().length === 0) {
      setState({ kind: "error", message: "请先粘贴一段需要透读的英文内容。" });
      return;
    }

    setState({ kind: "pending", message: "正在提交解析任务..." });

    try {
      const response = await fetch("/api/web/analysis/submit", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text, readingGoal, readingVariant }),
      });
      const payload = (await response.json()) as AnalysisSubmitResponse;

      if (!response.ok || !payload.ok) {
        setState({
          kind: "error",
          message: payload.message || "提交失败，请稍后重试。",
          recordId: payload.recordId,
        });
        return;
      }

      if (payload.taskId && payload.status && payload.status !== "succeeded") {
        setActiveTask({
          taskId: payload.taskId,
          recordId: payload.recordId || "",
          status: payload.status,
          readerUrl:
            payload.readerUrl ||
            (payload.recordId ? legacyAppReaderRoute(payload.recordId) : libraryRoute),
        });
        setState({ kind: "pending", message: "正在透读。" });
        return;
      }

      setState({ kind: "success", message: payload.message });
      router.push(
        (payload.readerUrl as Route | undefined) ||
          (payload.recordId ? legacyAppReaderRoute(payload.recordId) : libraryRoute),
      );
    } catch (error) {
      setState({
        kind: "error",
        message: error instanceof Error ? error.message : "提交失败，请稍后重试。",
      });
    }
  }

  const errorRecordId = state.kind === "error" ? state.recordId : undefined;
  const selectedGoalLabel = READING_GOAL_OPTIONS.find((option) => option.value === readingGoal)?.label;
  const selectedVariantLabel = READING_VARIANT_OPTIONS[readingGoal].find(
    (option) => option.value === readingVariant,
  )?.label;
  const loadingStageTitle = activeTask ? "有一篇文章正在透读" : "正在透读这篇文章";

  return (
    <div className="flex min-h-0 flex-1 w-full flex-col">
      <div className="flex min-h-0 flex-1 flex-col">
        <label htmlFor="analysis-text" className="sr-only">
          在此贴入或导入英文文章
        </label>

        <div className="group/manuscript relative flex min-h-[22rem] flex-1 w-full shrink-0 flex-col overflow-hidden rounded-[10px] bg-[linear-gradient(180deg,rgba(251,247,238,0.62),rgba(251,247,238,0.18)_48%,rgba(251,247,238,0)_100%)] ring-1 ring-hairline/35 transition-[box-shadow,background-color] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] focus-within:shadow-[0_18px_44px_rgba(23,21,17,0.055)] lg:min-h-[31rem] lg:shrink 2xl:min-h-[34rem]">
          <div
            aria-hidden="true"
            className="absolute inset-0 z-0 cursor-text"
            onClick={() => textareaRef.current?.focus()}
          />
          <div className="pointer-events-none absolute left-4 top-5 h-[calc(100%-2.5rem)] w-px bg-hairline/75 transition-colors duration-300 group-focus-within/manuscript:bg-lens-blue/28 xl:left-5" />
          <div className="pointer-events-none absolute left-12 top-9 h-[3.4rem] w-[2px] bg-ink/22 transition-all duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] group-focus-within/manuscript:h-[4.4rem] group-focus-within/manuscript:bg-lens-blue/58 xl:left-16" />

          {!isWaiting && !text.trim() ? (
            <div className="pointer-events-none absolute left-16 top-9 z-10 max-w-[26rem] xl:left-24 xl:top-11">
              <p className="font-reading text-[1.16rem] leading-tight text-ink/78 xl:text-[1.28rem]">
                Paste an English article here
              </p>
              <p className="mt-2 max-w-[21rem] font-sans text-[0.78rem] leading-6 text-muted">
                粘贴英文文章，Claread 会带你进入透读。
              </p>
            </div>
          ) : null}

          {isWaiting ? (
            <AnalysisLoadingStage title={loadingStageTitle} />
          ) : (
            <textarea
              ref={textareaRef}
              id="analysis-text"
              className="relative z-10 min-h-0 flex-1 resize-none overflow-y-auto bg-transparent px-16 py-10 font-reading text-[1.08rem] leading-[2.08] text-ink outline-none placeholder:text-transparent sm:text-[1.18rem] xl:px-24 xl:py-12 xl:text-[1.24rem] selection:bg-lens-blue/15 selection:text-ink"
              placeholder="Paste an English article here"
              value={text}
              onChange={(event) => setText(event.target.value)}
              onKeyDown={(event) => {
                if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                  event.preventDefault();
                  void handleSubmit();
                }
              }}
            />
          )}

          {!isWaiting && text.length > 0 && (
            <button
              type="button"
              className="absolute right-3 top-3 z-20 inline-flex h-9 w-9 items-center justify-center rounded-full text-subtle transition-colors hover:bg-surface/70 hover:text-ink focus-ring"
              onClick={() => {
                setText("");
                textareaRef.current?.focus();
              }}
              title="清空"
            >
              <X aria-hidden className="h-4 w-4" />
            </button>
          )}

          <div className="relative z-20 mx-5 mb-4 shrink-0 border-t border-hairline/68 px-0 pt-3 sm:mx-10 xl:mx-14">
            {isWaiting ? (
              <AnalysisLoadingStatusBar
                messagePrefix={activeTask ? "有一篇文章正在透读" : "正在透读"}
              />
            ) : (
              <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
                <TooltipProvider delayDuration={180}>
                  <div className="flex min-w-0 flex-wrap items-center gap-x-5 gap-y-2 font-sans">
                    {intakeMethods.map((method) => {
                      const Icon = method.icon;
                      const content = (
                        <button
                          type="button"
                          aria-disabled={!method.available}
                          className={`focus-ring group/source inline-flex min-h-9 items-center gap-2 px-0 text-[0.78rem] font-medium leading-none transition-colors duration-200 ${
                            method.available
                              ? "text-ink hover:text-lens-blue"
                              : "cursor-default text-subtle/62 hover:text-muted"
                          }`}
                          onClick={() => {
                            if (method.available) {
                              textareaRef.current?.focus();
                            }
                          }}
                        >
                          <span
                            className={`inline-flex h-6 w-6 items-center justify-center rounded-[7px] border transition-colors duration-200 ${
                              method.available
                                ? "border-ink/12 bg-reader-paper/54 text-ink group-hover/source:border-lens-blue/34 group-hover/source:text-lens-blue"
                                : "border-transparent bg-transparent text-subtle/62"
                            }`}
                          >
                            <Icon aria-hidden className="h-3.5 w-3.5" />
                          </span>
                          <span>{method.label}</span>
                        </button>
                      );
                      const node = method.available ? (
                        content
                      ) : (
                        <Tooltip>
                          <TooltipTrigger asChild>{content}</TooltipTrigger>
                          <TooltipContent side="top">即将支持</TooltipContent>
                        </Tooltip>
                      );

                      return (
                        <span key={method.key} className="inline-flex items-center">
                          {node}
                        </span>
                      );
                    })}
                  </div>
                </TooltipProvider>

                <div className="flex flex-col-reverse items-stretch gap-3 sm:flex-row sm:items-center sm:justify-end">
                  <Popover>
                    <PopoverTrigger asChild>
                      <button
                        type="button"
                        className="focus-ring inline-flex min-h-10 items-center justify-center gap-2 whitespace-nowrap rounded-[10px] border border-transparent px-3 font-sans text-[0.8rem] font-medium leading-none text-muted transition-colors duration-200 hover:bg-reader-paper/46 hover:text-ink data-[state=open]:bg-reader-paper/60 data-[state=open]:text-ink"
                      >
                        <span>
                          模式：{selectedGoalLabel}
                          {selectedVariantLabel && selectedVariantLabel !== "学术通用" ? ` · ${selectedVariantLabel}` : ""}
                        </span>
                        <ChevronDown aria-hidden className="h-3.5 w-3.5" />
                      </button>
                    </PopoverTrigger>
                    <PopoverContent
                      align="end"
                      side="top"
                      sideOffset={14}
                      className="w-[min(420px,calc(100vw-2rem))] rounded-[20px] border border-hairline/78 bg-[color-mix(in_srgb,var(--surface)_96%,var(--reader-paper)_4%)] p-4 shadow-[0_24px_48px_rgba(23,21,17,0.14)] data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[side=top]:slide-in-from-bottom-2"
                    >
                      <div className="flex items-center justify-between gap-4 px-1.5 pb-2 font-sans">
                        <p className="text-[0.85rem] font-semibold tracking-tight text-ink">透读模式</p>
                        <span className="max-w-[14rem] truncate text-right text-[0.72rem] font-medium tracking-tight text-muted">
                          当前：{selectedGoalLabel}
                          {selectedVariantLabel && selectedVariantLabel !== "学术通用" ? ` · ${selectedVariantLabel}` : ""}
                        </span>
                      </div>

                      <div className="mt-1 flex gap-2">
                        {READING_GOAL_OPTIONS.map((goal) => (
                          <div key={goal.value} className="flex-1">
                            <GoalCard
                              goal={goal}
                              active={goal.value === readingGoal}
                              onSelect={() => {
                                setReadingGoal(goal.value);
                                const variants = READING_VARIANT_OPTIONS[goal.value];
                                if (!variants.find((v) => v.value === readingVariant)) {
                                  setReadingVariant(DEFAULT_READING_VARIANT_BY_GOAL[goal.value] || variants[0].value);
                                }
                              }}
                            />
                          </div>
                        ))}
                      </div>

                      <div className="mt-4 min-h-[7rem] px-1 pb-0.5">
                        <div className="mb-3 flex items-center gap-3">
                          <span className="shrink-0 text-[0.72rem] font-semibold tracking-tight text-muted/90">细分方式</span>
                          <div className="h-px flex-1 bg-hairline/60" />
                        </div>
                        <div className="grid grid-cols-3 gap-2">
                          {READING_VARIANT_OPTIONS[readingGoal].map((variant) => (
                            <VariantPill
                              key={variant.value}
                              variant={variant}
                              active={variant.value === readingVariant}
                              onSelect={() => setReadingVariant(variant.value)}
                            />
                          ))}
                        </div>
                      </div>
                    </PopoverContent>
                  </Popover>

                  {text.length > 0 ? (
                    <span className="self-center font-sans text-[0.72rem] font-medium text-subtle">
                      {text.trim().length.toLocaleString("en-US")} chars
                    </span>
                  ) : null}

                  <ApertureCornerSubmitButton
                    isPending={false}
                    isReady={text.trim().length > 0}
                    onClick={handleSubmit}
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {state.kind !== "idle" && !isWaiting && (
        <div
          className={`mt-4 shrink-0 rounded-[14px] border border-hairline/70 bg-surface/42 px-4 py-3 text-[0.82rem] font-medium lg:mx-12 ${
            state.kind === "error" ? "text-red-700" : "text-lens-blue"
          }`}
        >
          {state.message}
          {errorRecordId && (
            <button
              type="button"
              className="ml-4 text-[0.72rem] font-semibold underline decoration-hairline underline-offset-4 transition-colors hover:text-ink"
              onClick={() => router.push(legacyAppReaderRoute(errorRecordId))}
            >
              打开任务
            </button>
          )}
        </div>
      )}
    </div>
  );
}
