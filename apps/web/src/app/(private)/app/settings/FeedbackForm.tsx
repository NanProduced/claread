"use client";
import { useId, useState, useRef } from "react";
import { Pencil, Sparkles } from "lucide-react";
import { MyFeedbackList } from "./MyFeedbackList";

const FEEDBACK_TYPE_OPTIONS = [
  { value: "bug_report", label: "遇到问题" },
  { value: "feature_request", label: "功能建议" },
  { value: "quota_issue", label: "配额问题" },
  { value: "input_page_issue", label: "输入页问题" },
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
  const textareaRef = useRef<HTMLTextAreaElement>(null);
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
      textareaRef.current?.focus();
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
        contextSummary: "设置页应用反馈",
        clientPlatform: "web",
        clientSurface: "settings",
        entryPoint: "settings_form",
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
      textareaRef.current?.focus();
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
        <div className="flex items-center gap-2">
          <span className="inline-flex size-8 items-center justify-center rounded-full bg-[radial-gradient(circle_at_30%_25%,#FFF3D6_0%,#EAC87B_50%,#C9954D_100%)] text-stone-900 shadow-[0_8px_18px_rgba(147,111,57,0.18)]">
            <Sparkles className="size-4" strokeWidth={2.1} />
          </span>
          <label className="text-sm font-semibold text-ink" htmlFor={contentId}>
            应用反馈
          </label>
        </div>
        <span className="text-xs leading-5 text-muted">
          问题、建议或体验不顺，都可以直接提交给 Claread 团队。
        </span>
      </div>

      <div className="flex flex-wrap gap-2">
        {FEEDBACK_TYPE_OPTIONS.map((option) => (
          <button
            className={`focus-ring min-h-[44px] rounded-lg border px-4 text-sm font-medium transition-colors ${
              feedbackType === option.value
                ? "border-lens-blue bg-lens-blue-soft text-lens-blue"
                : "border-hairline bg-reader-paper text-muted hover:border-subtle hover:text-ink"
            }`}
            key={option.value}
            onClick={() => setFeedbackType(option.value)}
            type="button"
            aria-pressed={feedbackType === option.value}
          >
            <span className="mr-1.5 inline-flex size-5 items-center justify-center rounded-full bg-[radial-gradient(circle_at_30%_25%,#FFF9E7_0%,#E9D2A4_48%,#C4A06A_100%)] text-stone-900 shadow-[0_6px_12px_rgba(140,109,62,0.16)]">
              <Pencil className="size-2.5" strokeWidth={2.3} />
            </span>
            {option.label}
          </button>
        ))}
      </div>

      <textarea
        ref={textareaRef}
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
          className="focus-ring rounded-pill bg-ink px-6 py-2.5 text-sm font-semibold text-surface disabled:cursor-not-allowed disabled:bg-ink/50 transition-opacity"
          disabled={!canSubmit}
          type="submit"
        >
          {state.status === "submitting" ? "提交中..." : "提交反馈"}
        </button>
      </div>

      <div className="mt-12 border-t border-hairline pt-8">
        <h2 className="mb-6 text-sm font-semibold text-ink">我的反馈记录</h2>
        <MyFeedbackList />
      </div>
    </form>
  );
}
