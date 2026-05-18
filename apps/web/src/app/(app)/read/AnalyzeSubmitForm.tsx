"use client";

import { Loader2, Send } from "lucide-react";
import type { Route } from "next";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/primitives/button";
import { SectionCard } from "@/components/composed/section-card";
import { SegmentedControl } from "@/components/composed/segmented-control";
import type { ReadingGoalDto, ReadingVariantDto } from "@/types/api/tasks";

const readingOptions = [
  { value: "daily_reading", label: "日常阅读" },
  { value: "academic", label: "学术摘要" },
  { value: "exam", label: "备考精读" },
] as const;

const readingVariantOptions: Record<
  ReadingGoalDto,
  Array<{ value: ReadingVariantDto; label: string; helper: string }>
> = {
  daily_reading: [
    { value: "beginner_reading", label: "入门", helper: "句意拆解更直白" },
    { value: "intermediate_reading", label: "中级", helper: "词句平衡" },
    { value: "intensive_reading", label: "精读", helper: "语法和表达更深入" },
  ],
  academic: [
    { value: "academic_general", label: "学术通用", helper: "术语、逻辑和摘要" },
  ],
  exam: [
    { value: "gaokao", label: "高考", helper: "中学语法与阅读题感" },
    { value: "cet", label: "四六级", helper: "快速定位主干信息" },
    { value: "kaoyan", label: "考研", helper: "长难句结构优先" },
    { value: "tem", label: "专四专八", helper: "修辞和文学语感" },
    { value: "ielts_toefl", label: "雅思托福", helper: "信息提取和题型判断" },
  ],
};

const defaultVariantByGoal: Record<ReadingGoalDto, ReadingVariantDto> = {
  daily_reading: "intermediate_reading",
  academic: "academic_general",
  exam: "cet",
};

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
const libraryRoute = "/library" as Route;

function readerRoute(recordId: string): Route {
  return `/reader/${recordId}` as Route;
}

export function AnalyzeSubmitForm() {
  const router = useRouter();
  const [text, setText] = useState("");
  const [readingGoal, setReadingGoal] = useState<ReadingGoalDto>("daily_reading");
  const [readingVariant, setReadingVariant] = useState<ReadingVariantDto>("intermediate_reading");
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
              (latest.recordId ? readerRoute(latest.recordId) : libraryRoute),
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
          (payload.recordId ? readerRoute(payload.recordId) : libraryRoute),
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
  const activeVariantOptions = readingVariantOptions[readingGoal];
  const showVariantOptions = activeVariantOptions.length > 1;

  return (
    <SectionCard
      className="overflow-hidden border-none bg-reader-paper p-0 shadow-[0_24px_76px_rgba(35,28,18,0.11)]"
      contentClassName="space-y-0"
    >
      <div className="border-b border-hairline bg-surface-warm/60 px-5 py-5 sm:px-7">
        <SegmentedControl
          label="透读模式"
          value={readingGoal}
          onValueChange={(nextGoal) => {
            setReadingGoal(nextGoal);
            setReadingVariant(defaultVariantByGoal[nextGoal]);
          }}
          options={readingOptions}
        />

        {showVariantOptions ? (
          <SegmentedControl
            className="mt-5 border-t border-hairline pt-4"
            label="细分场景"
            value={readingVariant}
            onValueChange={setReadingVariant}
            options={activeVariantOptions}
          />
        ) : null}
      </div>

      <div className="px-5 py-6 sm:px-8 lg:px-10 lg:py-8">
        <div className="mb-4 flex items-center justify-between gap-3">
          <label htmlFor="analysis-text" className="text-sm font-semibold text-ink">
            正文
          </label>
          <div className="flex items-center gap-3 text-xs text-muted">
            <span>{text.trim().length.toLocaleString("zh-CN")} 字符</span>
            {text.length > 0 ? (
              <button
                type="button"
                className="focus-ring rounded-pill transition-colors hover:text-ink"
                onClick={() => setText("")}
              >
                清空
              </button>
            ) : null}
          </div>
        </div>

        <div className="rounded-[1.35rem] border border-hairline bg-surface px-4 py-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.65)] sm:px-5 sm:py-5">
          <textarea
            id="analysis-text"
            className="min-h-[480px] w-full resize-none bg-transparent font-reading text-[1.16rem] leading-[1.95] text-ink outline-none placeholder:text-[#736f66] sm:text-[1.28rem]"
            placeholder={`在这里粘贴英文正文……

Cities are not only built to be crossed, but also to be read through signs, corners, and quiet habits.`}
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
        </div>
      </div>

      <div className="flex justify-end border-t border-hairline bg-surface-warm/72 px-5 py-4 sm:px-7">
        <Button variant="primary" size="lg" disabled={isPending} onClick={handleSubmit}>
          {isPending ? (
            <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
          ) : (
            <Send aria-hidden className="h-4 w-4" />
          )}
          {isPending ? "透读中" : "开始透读"}
        </Button>
      </div>

      {state.kind !== "idle" ? (
        <div
          className={`border-t border-hairline bg-surface-warm px-5 py-3 text-[0.8125rem] ${
            state.kind === "error" ? "text-red-700" : "text-muted"
          }`}
        >
          {state.message}
          {errorRecordId ? (
            <button
              type="button"
              className="ml-3 font-semibold text-ink underline decoration-hairline underline-offset-4"
              onClick={() => router.push(readerRoute(errorRecordId))}
            >
              打开当前任务
            </button>
          ) : null}
        </div>
      ) : null}
    </SectionCard>
  );
}
