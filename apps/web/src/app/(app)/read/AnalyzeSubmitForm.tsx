"use client";

import { Loader2, Send } from "lucide-react";
import type { Route } from "next";
import { useRouter } from "next/navigation";
import { useState } from "react";

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
    <section className="bg-surface shadow-surface-quiet rounded-note border border-hairline overflow-hidden focus-within:border-muted transition-colors flex flex-col">
      <textarea
        className="w-full h-64 p-5 text-[1.125rem] font-reading leading-[1.8] text-ink placeholder:text-subtle resize-none outline-none bg-transparent"
        placeholder="在这里粘贴英文文章..."
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      <div className="px-5 py-4 bg-surface-warm border-t border-hairline flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <select
            className="bg-transparent text-[0.8125rem] font-semibold text-ink-soft outline-none cursor-pointer"
            value={readingGoal}
            onChange={(event) =>
              setReadingGoal(event.target.value as (typeof readingOptions)[number]["value"])
            }
          >
            {readingOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        <button
          type="button"
          className="inline-flex items-center justify-center gap-2 bg-ink text-surface rounded-pill px-[18px] py-[11px] text-[0.8125rem] font-semibold hover:opacity-90 transition-opacity disabled:cursor-not-allowed disabled:opacity-60"
          disabled={isPending}
          onClick={handleSubmit}
        >
          {isPending ? (
            <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
          ) : (
            <Send aria-hidden className="h-4 w-4" />
          )}
          {isPending ? "解析中" : "开始解读"}
        </button>
      </div>
      {state.kind !== "idle" && (
        <div
          className={`border-t border-hairline px-5 py-3 text-[0.8125rem] ${
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
      )}
    </section>
  );
}
