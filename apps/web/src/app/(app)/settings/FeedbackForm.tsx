"use client";

import { useId, useState } from "react";

const FEEDBACK_TYPE_OPTIONS = [
  { value: "bug_report", label: "遇到问题" },
  { value: "feature_request", label: "功能建议" },
  { value: "quota_issue", label: "配额问题" },
  { value: "ux_issue", label: "体验不顺" },
  { value: "other", label: "其他" },
] as const;

type SubmitState =
  | { status: "idle"; message: string }
  | { status: "submitting"; message: string }
  | { status: "success"; message: string }
  | { status: "error"; message: string };

type FeedbackSubmitResponse =
  | {
      ok: true;
      message: string;
    }
  | {
      ok: false;
      message: string;
    };

export function FeedbackForm() {
  const contentId = useId();
  const [feedbackType, setFeedbackType] =
    useState<(typeof FEEDBACK_TYPE_OPTIONS)[number]["value"]>("bug_report");
  const [content, setContent] = useState("");
  const [state, setState] = useState<SubmitState>({
    status: "idle",
    message: "",
  });

  const canSubmit = content.trim().length > 0 && state.status !== "submitting";

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!canSubmit) {
      setState({ status: "error", message: "请先写下反馈内容。" });
      return;
    }

    setState({ status: "submitting", message: "正在提交..." });

    const response = await fetch("/api/web/feedback", {
      method: "POST",
      headers: {
        "content-type": "application/json",
      },
      body: JSON.stringify({
        feedbackScope: "app",
        targetId: "web-settings",
        sentiment: "neutral",
        feedbackType,
        content,
        contextJson: {
          entry: "settings",
          userAgent: navigator.userAgent,
        },
        appVersion: "web",
      }),
    });

    const result = (await response.json().catch(() => ({
      ok: false,
      message: "反馈提交失败，请稍后重试。",
    }))) as FeedbackSubmitResponse;

    if (!response.ok || !result.ok) {
      setState({
        status: "error",
        message: result.message || "反馈提交失败，请稍后重试。",
      });
      return;
    }

    setContent("");
    setFeedbackType("bug_report");
    setState({ status: "success", message: result.message || "反馈已提交。" });
  }

  return (
    <form
      className="space-y-4"
      onSubmit={handleSubmit}
    >
      <div className="flex flex-col gap-1">
        <label className="text-sm font-semibold text-ink" htmlFor={contentId}>
          应用反馈
        </label>
        <span className="text-xs leading-5 text-muted">
          问题、建议或体验不顺，都可以直接提交给 Claread 团队。
        </span>
      </div>

      <div className="flex flex-wrap gap-2">
        {FEEDBACK_TYPE_OPTIONS.map((option) => (
          <button
            className={`focus-ring min-h-9 rounded-pill border px-3 text-xs font-semibold transition-colors ${
              feedbackType === option.value
                ? "border-lens-blue bg-lens-blue-soft text-lens-blue"
                : "border-hairline bg-reader-paper text-muted hover:border-muted hover:text-ink"
            }`}
            key={option.value}
            onClick={() => setFeedbackType(option.value)}
            type="button"
            aria-pressed={feedbackType === option.value}
          >
            {option.label}
          </button>
        ))}
      </div>

      <textarea
        className="focus-ring min-h-32 w-full resize-y rounded-note border border-hairline bg-reader-paper px-4 py-3 text-sm leading-6 text-ink placeholder:text-subtle"
        id={contentId}
        maxLength={2000}
        onChange={(event) => setContent(event.target.value)}
        placeholder="请描述你遇到的问题或建议"
        value={content}
      />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <span
          className={`text-xs ${
            state.status === "error"
              ? "text-error-red"
              : state.status === "success"
                ? "text-structure-green"
                : "text-muted"
          }`}
          role={state.message ? "status" : undefined}
        >
          {state.message || `${content.length} / 2000`}
        </span>
        <button
          className="focus-ring rounded-pill bg-ink px-5 py-2 text-sm font-semibold text-surface disabled:cursor-not-allowed disabled:bg-muted"
          disabled={!canSubmit}
          type="submit"
        >
          {state.status === "submitting" ? "提交中" : "提交反馈"}
        </button>
      </div>
    </form>
  );
}
