"use client";

import { Loader2, X } from "lucide-react";
import type { Route } from "next";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/primitives/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/primitives/popover";
import { SegmentedControl } from "@/components/composed/segmented-control";
import {
  DEFAULT_READING_VARIANT_BY_GOAL,
  READING_GOAL_OPTIONS,
  READING_VARIANT_OPTIONS,
  type ReadingDefaultState,
  normalizeReadingDefaults,
} from "@/lib/reading-defaults";
import { appLibraryRoute, appReaderRoute } from "@/lib/routes";
import { formatShortcut } from "@/lib/shortcuts";
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
const intakeCues = ["贴入文本", "链接导入", "上传文档", "示例文章"] as const;

interface AnalyzeSubmitFormProps extends ReadingDefaultState {}

export function AnalyzeSubmitForm({ readingGoal: initialGoal, readingVariant: initialVariant }: AnalyzeSubmitFormProps) {
  const router = useRouter();
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
  const activeVariantOptions = READING_VARIANT_OPTIONS[readingGoal];
  const showVariantOptions = activeVariantOptions.length > 1;
  const submitShortcutLabel = formatShortcut("Primary+Enter");

  return (
    <div className="flex min-h-0 flex-1 w-full flex-col">
      <div className="flex flex-1 flex-col">
        <label htmlFor="analysis-text" className="sr-only">
          在此贴入或导入英文文章
        </label>

        <div className="group relative flex min-h-[12rem] flex-1 w-full shrink-0 lg:shrink">
          <div className="pointer-events-none absolute left-4 top-7 h-full max-h-[24rem] w-px bg-hairline/60 xl:left-5" />
          <div className="pointer-events-none absolute left-12 top-10 h-[2.6rem] w-[2px] bg-ink/30 transition-colors duration-500 group-focus-within:bg-ink/72" />
          
          <textarea
            id="analysis-text"
            className="relative z-10 h-full w-full resize-none overflow-y-auto bg-transparent px-14 py-10 font-reading text-[1.08rem] leading-[2.16] text-ink outline-none placeholder:text-muted/78 sm:text-[1.18rem] xl:px-20 xl:py-12 xl:text-[1.24rem] selection:bg-lens-blue/15 selection:text-ink"
            placeholder="在此粘贴文章、链接或导入文档……"
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
              className="absolute right-4 top-4 z-20 p-2 text-subtle transition-colors hover:text-ink focus-ring"
              onClick={() => setText("")}
              title="清空"
            >
              <X aria-hidden className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>

      <div className="mt-5 flex flex-col gap-4 border-t border-hairline/70 pt-5 pb-6 lg:flex-row lg:items-center lg:justify-between pl-4 lg:pl-12 md:pb-4 shrink-0">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 font-sans text-[0.65rem] font-bold tracking-[0.15em] text-subtle">
          <span>{intakeCues.join(" · ")}</span>
          <span className="hidden text-hairline/80 sm:inline">|</span>
          <Popover>
            <PopoverTrigger asChild>
              <button
                type="button"
                className="font-sans text-[0.65rem] font-bold tracking-[0.15em] text-muted transition-colors hover:text-ink focus-ring"
              >
                模式：{READING_GOAL_OPTIONS.find((option) => option.value === readingGoal)?.label}
              </button>
            </PopoverTrigger>
            <PopoverContent
              align="start"
              className="w-[300px] rounded-none border border-hairline bg-surface p-5 shadow-sm"
            >
            <SegmentedControl
              label="Goal"
              value={readingGoal}
              onValueChange={(nextGoal) => {
                setReadingGoal(nextGoal);
                setReadingVariant(DEFAULT_READING_VARIANT_BY_GOAL[nextGoal]);
              }}
              options={READING_GOAL_OPTIONS}
            />
            {showVariantOptions ? (
              <SegmentedControl
                className="mt-5 border-t border-hairline pt-4"
                label="Variant"
                value={readingVariant}
                onValueChange={setReadingVariant}
                options={activeVariantOptions}
              />
            ) : null}
            </PopoverContent>
          </Popover>
          {text.length > 0 ? (
            <>
              <span className="hidden text-hairline/80 sm:inline">|</span>
              <span className="text-[0.65rem] font-bold tracking-[0.15em]">{text.trim().length.toLocaleString("en-US")} chars</span>
            </>
          ) : null}
        </div>

        <div className="flex items-center gap-6 lg:gap-8 self-end lg:self-auto">
          <div className="hidden font-sans text-[0.65rem] font-bold tracking-[0.15em] text-subtle/90 lg:block">
            提交 {submitShortcutLabel}
          </div>
          <Button
            variant="primary-ink"
            className="group min-w-[150px] px-8 py-3.5 font-sans text-[0.82rem] font-semibold tracking-[0.08em] transition-all duration-300 focus-ring"
            disabled={isPending}
            onClick={handleSubmit}
          >
            {isPending ? <Loader2 aria-hidden className="mr-2 h-3.5 w-3.5 animate-spin" /> : null}
            {isPending ? "透读中..." : "开始透读"}
          </Button>
        </div>
      </div>

      {state.kind !== "idle" && (
        <div
          className={`mt-5 shrink-0 border-t border-hairline/60 bg-surface/35 px-4 py-3 text-[0.8rem] font-medium lg:px-12 ${
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
