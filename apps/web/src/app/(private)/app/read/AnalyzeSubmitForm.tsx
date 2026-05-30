"use client";

import { ArrowRight, BookOpen, Check, ChevronDown, ClipboardPaste, FileUp, Link2, X, FileText, Target } from "lucide-react";
import type { Route } from "next";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { Button } from "@/components/primitives/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/primitives/popover";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/primitives/tooltip";
import { cn } from "@/lib/cn";
import {
  READING_GOAL_OPTIONS,
  READING_VARIANT_OPTIONS,
  DEFAULT_READING_VARIANT_BY_GOAL,
  type ReadingDefaultState,
  normalizeReadingDefaults,
} from "@/lib/reading-defaults";
import { appLibraryRoute, appReaderRoute } from "@/lib/routes";
import type { ReadingGoalDto, ReadingVariantDto } from "@/types/api/tasks";

type SubmitState =
  | { kind: "idle" }
  | { kind: "pending"; message: string }
  | { kind: "success"; message: string }
  | { kind: "error"; message: string; recordId?: string };

interface AnalysisSubmitResponse {
  ok: boolean;
  message: string;
  taskId?: string;
  status?: string;
  readerUrl?: string;
  recordId?: string;
}

interface AnalysisTaskStatusResponse {
  ok: boolean;
  message?: string;
  status?: string;
  readerUrl?: string;
  recordId?: string;
  failureMessage?: string | null;
}

const TERMINAL_STATUS = new Set(["succeeded", "failed", "cancelled", "expired"]);
const POLL_INTERVAL_MS = 2000;
const MAX_POLL_ATTEMPTS = 45;
const libraryRoute = appLibraryRoute;
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

type AnalyzeSubmitFormProps = ReadingDefaultState;

export function AnalyzeSubmitForm({ readingGoal: initialGoal, readingVariant: initialVariant }: AnalyzeSubmitFormProps) {
  const router = useRouter();
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [text, setText] = useState("");
  const defaults = normalizeReadingDefaults({ readingGoal: initialGoal, readingVariant: initialVariant });
  const [readingGoal, setReadingGoal] = useState<ReadingGoalDto>(defaults.readingGoal);
  const [readingVariant, setReadingVariant] = useState<ReadingVariantDto>(defaults.readingVariant);
  const [state, setState] = useState<SubmitState>({ kind: "idle" });

  async function pollTaskUntilReady(taskId: string): Promise<AnalysisTaskStatusResponse> {
    let latest: AnalysisTaskStatusResponse | null = null;

    for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS; attempt += 1) {
      await new Promise((resolve) => {
        window.setTimeout(resolve, POLL_INTERVAL_MS);
      });

      const response = await fetch(`/api/web/analysis/tasks/${encodeURIComponent(taskId)}`, {
        method: "GET",
      });
      const payload = (await response.json()) as AnalysisTaskStatusResponse;

      if (!response.ok || !payload.ok) {
        throw new Error(payload.message || "查询任务状态失败。");
      }

      latest = payload;

      if (payload.status && TERMINAL_STATUS.has(payload.status)) {
        return payload;
      }

      setState({ kind: "pending", message: "解析任务处理中..." });
    }

    return latest ?? { ok: false, message: "解析任务仍在处理中。" };
  }

  async function handleSubmit() {
    if (state.kind === "pending") {
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
        setState({ kind: "pending", message: "解析任务处理中..." });
        const latest = await pollTaskUntilReady(payload.taskId);

        if (latest.status === "succeeded") {
          router.push(
            (latest.readerUrl as Route | undefined) ||
              (latest.recordId ? appReaderRoute(latest.recordId) : libraryRoute),
          );
          return;
        }

        setState({
          kind: "error",
          message: latest.failureMessage || "解析任务尚未完成，请稍后打开当前任务。",
          recordId: latest.recordId || payload.recordId,
        });
        return;
      }

      setState({ kind: "success", message: payload.message });
      router.push(
        (payload.readerUrl as Route | undefined) ||
          (payload.recordId ? appReaderRoute(payload.recordId) : libraryRoute),
      );
    } catch (error) {
      setState({
        kind: "error",
        message: error instanceof Error ? error.message : "提交失败，请稍后重试。",
      });
    }
  }

  const isPending = state.kind === "pending";
  const errorRecordId = state.kind === "error" ? state.recordId : undefined;
  const selectedGoalLabel = READING_GOAL_OPTIONS.find((option) => option.value === readingGoal)?.label;
  const selectedVariantLabel = READING_VARIANT_OPTIONS[readingGoal].find(
    (option) => option.value === readingVariant,
  )?.label;

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

          {!text.trim() ? (
            <div className="pointer-events-none absolute left-16 top-9 z-10 max-w-[26rem] xl:left-24 xl:top-11">
              <p className="font-reading text-[1.16rem] leading-tight text-ink/78 xl:text-[1.28rem]">
                Paste an English article here
              </p>
              <p className="mt-2 max-w-[21rem] font-sans text-[0.78rem] leading-6 text-muted">
                粘贴英文文章，Claread 会带你进入透读。
              </p>
            </div>
          ) : null}

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

          {text.length > 0 && (
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
                isPending={isPending}
                isReady={text.trim().length > 0}
                onClick={handleSubmit}
              />
              </div>
            </div>
          </div>
        </div>
      </div>

      {state.kind !== "idle" && (
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
              onClick={() => router.push(appReaderRoute(errorRecordId))}
            >
              打开任务
            </button>
          )}
        </div>
      )}
    </div>
  );
}
