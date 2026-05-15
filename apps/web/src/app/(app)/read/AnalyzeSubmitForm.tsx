"use client";

import { Loader2, Send, Sparkles } from "lucide-react";
import type { Route } from "next";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ApertureWatermark, ClareadStamp } from "@/components/brand/BrandMarks";

const readingOptions = [
  { value: "daily_reading", label: "日常阅读" },
  { value: "academic", label: "学术摘要" },
  { value: "exam", label: "备考精读" },
] as const;

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
  const [readingGoal, setReadingGoal] = useState<(typeof readingOptions)[number]["value"]>(
    "daily_reading",
  );
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
        body: JSON.stringify({ text, readingGoal }),
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

  return (
    <section className="relative overflow-hidden rounded-[2rem] border border-hairline bg-reader-paper shadow-[0_24px_76px_rgba(35,28,18,0.11)]">
      <ApertureWatermark
        size={360}
        className="absolute -right-32 top-28 h-80 w-80 opacity-[0.045]"
      />

      <div className="relative border-b border-hairline bg-surface-warm/65 px-5 py-4 sm:px-7">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <ClareadStamp label="ARTICLE CANVAS" className="bg-reader-paper/80" />
            <p className="mt-3 max-w-2xl text-sm leading-6 text-muted">
              把英文文章放到纸面上。复杂选项收在后面，正文先成为中心。
            </p>
          </div>

          <fieldset className="min-w-0">
            <legend className="mb-2 text-xs font-semibold uppercase tracking-[0.14em] text-muted">
              阅读目标
            </legend>
            <div className="flex flex-wrap gap-2">
              {readingOptions.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={`focus-ring min-h-10 rounded-pill border px-3 text-xs font-semibold transition-colors ${
                    readingGoal === option.value
                      ? "border-lens-blue bg-lens-blue text-surface"
                      : "border-hairline bg-reader-paper text-ink hover:border-muted"
                  }`}
                  onClick={() => setReadingGoal(option.value)}
                  aria-pressed={readingGoal === option.value}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </fieldset>
        </div>
      </div>

      <div className="relative grid min-h-[590px] md:grid-cols-[68px_minmax(0,1fr)]">
        <div className="hidden border-r border-hairline bg-[linear-gradient(180deg,rgba(37,99,235,0.035),transparent_46%,rgba(17,17,17,0.025))] md:block">
          <div className="sticky top-8 flex justify-center pt-9 font-reading text-sm text-subtle">
            01
          </div>
        </div>

        <div className="px-5 py-7 sm:px-8 lg:px-12 lg:py-10">
          <div className="mb-7 flex flex-col gap-3 border-b border-hairline pb-5 sm:flex-row sm:items-end sm:justify-between">
            <label
              htmlFor="analysis-text"
              className="inline-flex items-center gap-2 text-sm font-semibold text-ink"
            >
              <Sparkles aria-hidden="true" className="h-4 w-4 text-lens-blue" />
              草稿正文
            </label>
            <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
              <span>{text.trim().length.toLocaleString("zh-CN")} 字符</span>
              <span aria-hidden="true" className="h-1 w-1 rounded-full bg-hairline" />
              <span>解析后写入阅读记录</span>
            </div>
          </div>

          <textarea
            id="analysis-text"
            className="min-h-[420px] w-full resize-none bg-transparent font-reading text-[1.18rem] leading-[1.95] text-ink outline-none placeholder:text-[#736f66] sm:text-[1.32rem]"
            placeholder={`Paste or write an English article here...

Cities are not only built to be crossed, but also to be read through signs, corners, and quiet habits.`}
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
        </div>

        <div
          aria-hidden="true"
          className="pointer-events-none absolute right-3 top-1/2 hidden -translate-y-1/2 rounded-pill border border-hairline bg-surface-warm/85 px-2.5 py-4 text-[0.6875rem] font-semibold uppercase tracking-[0.14em] text-muted shadow-surface-quiet xl:block [writing-mode:vertical-rl]"
        >
          Marginalia
        </div>
      </div>

      <div className="relative flex flex-col gap-4 border-t border-hairline bg-surface-warm/70 px-5 py-4 sm:px-7 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex flex-wrap items-center gap-2 text-xs font-semibold text-muted">
          <span className="rounded-pill border border-hairline bg-reader-paper px-3 py-2">
            原文 / 译文可切换
          </span>
          <span className="rounded-pill border border-hairline bg-reader-paper px-3 py-2">
            标注层默认开启
          </span>
          <span className="rounded-pill border border-hairline bg-reader-paper px-3 py-2">
            轻旁注按需唤起
          </span>
        </div>
        <button
          type="button"
          className="focus-ring inline-flex min-h-11 items-center justify-center gap-2 rounded-pill bg-lens-blue px-5 text-sm font-semibold text-surface transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
          disabled={isPending}
          onClick={handleSubmit}
        >
          {isPending ? (
            <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
          ) : (
            <Send aria-hidden className="h-4 w-4" />
          )}
          {isPending ? "透读中" : "开始透读"}
        </button>
      </div>

      {state.kind !== "idle" ? (
        <div
          className={`relative border-t border-hairline bg-surface-warm px-5 py-3 text-[0.8125rem] ${
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
    </section>
  );
}
