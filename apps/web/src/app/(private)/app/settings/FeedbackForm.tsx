"use client";

import { useId, useRef, useState } from "react";
import { Loader2, SendHorizontal } from "lucide-react";

import { cn } from "@/lib/cn";
import { Button } from "@/components/primitives/button";
import { MyFeedbackList } from "./MyFeedbackList";

const FEEDBACK_TYPE_OPTIONS = [
  { value: "bug_report", label: "遇到问题", description: "出错、异常或结果不对" },
  { value: "feature_request", label: "功能建议", description: "希望 Claread 增加什么" },
  { value: "quota_issue", label: "配额问题", description: "积分、次数或扣减疑问" },
  { value: "input_page_issue", label: "输入页问题", description: "粘贴、导入或提交体验" },
  { value: "ux_issue", label: "体验不顺", description: "流程、按钮或阅读感受" },
  { value: "other", label: "其他", description: "不属于以上分类" },
] as const;

const SENTIMENT_OPTIONS = [
  { value: "positive", label: "喜欢" },
  { value: "neutral", label: "建议" },
  { value: "negative", label: "遇阻" },
] as const;

type FeedbackType = (typeof FEEDBACK_TYPE_OPTIONS)[number]["value"];
type Sentiment = (typeof SENTIMENT_OPTIONS)[number]["value"];

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
  const [feedbackType, setFeedbackType] = useState<FeedbackType>("feature_request");
  const [sentiment, setSentiment] = useState<Sentiment>("neutral");
  const [content, setContent] = useState("");
  const [listRefreshKey, setListRefreshKey] = useState(0);
  const [state, setState] = useState<SubmitState>({
    status: "idle",
    message: "",
  });

  const canSubmit = content.trim().length > 0 && state.status !== "submitting";

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!canSubmit) {
      setState({ status: "error", message: "先写下你想反馈的内容。" });
      textareaRef.current?.focus();
      return;
    }

    setState({ status: "submitting", message: "正在提交..." });

    try {
      const response = await fetch("/api/web/feedback", {
        method: "POST",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({
          feedbackScope: "app",
          targetId: "web-settings",
          sentiment,
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
          message: result.message || "反馈提交失败，内容已保留，请稍后重试。",
        });
        textareaRef.current?.focus();
        return;
      }

      setContent("");
      setFeedbackType("feature_request");
      setSentiment("neutral");
      setListRefreshKey((value) => value + 1);
      setState({ status: "success", message: result.message || "反馈已提交。" });
    } catch {
      setState({
        status: "error",
        message: "网络异常，内容已保留，请稍后重试。",
      });
      textareaRef.current?.focus();
    }
  }

  return (
    <form className="space-y-8" onSubmit={handleSubmit}>
      <fieldset className="space-y-3">
        <legend className="text-sm font-medium text-ink">总体感受</legend>
        <div className="flex flex-wrap gap-2">
          {SENTIMENT_OPTIONS.map((option) => {
            const active = sentiment === option.value;
            return (
              <label
                key={option.value}
                className={cn(
                  "flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 transition-colors focus-within:ring-2 focus-within:ring-lens-blue focus-within:ring-offset-2 focus-within:ring-offset-background",
                  active
                    ? "border-lens-blue bg-surface-canvas text-ink"
                    : "border-hairline/60 bg-background text-muted-foreground hover:border-hairline hover:text-ink",
                )}
              >
                <input
                  type="radio"
                  name="feedback-sentiment"
                  value={option.value}
                  checked={active}
                  onChange={() => setSentiment(option.value)}
                  className="sr-only"
                />
                <span
                  className={cn(
                    "flex size-4 shrink-0 items-center justify-center rounded-full border",
                    active ? "border-lens-blue" : "border-muted-foreground",
                  )}
                  aria-hidden="true"
                >
                  {active && <span className="size-2 rounded-full bg-lens-blue" />}
                </span>
                <span className="text-sm font-medium">{option.label}</span>
              </label>
            );
          })}
        </div>
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="text-sm font-medium text-ink">反馈类型</legend>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {FEEDBACK_TYPE_OPTIONS.map((option) => {
            const active = feedbackType === option.value;
            return (
              <label
                key={option.value}
                className={cn(
                  "flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 transition-colors focus-within:ring-2 focus-within:ring-lens-blue focus-within:ring-offset-2 focus-within:ring-offset-background",
                  active
                    ? "border-lens-blue bg-surface-canvas text-ink"
                    : "border-hairline/60 bg-background text-muted-foreground hover:border-hairline hover:text-ink",
                )}
              >
                <input
                  type="radio"
                  name="feedback-type"
                  value={option.value}
                  checked={active}
                  onChange={() => setFeedbackType(option.value)}
                  className="sr-only"
                />
                <span
                  className={cn(
                    "flex size-4 shrink-0 items-center justify-center rounded-full border",
                    active ? "border-lens-blue" : "border-muted-foreground",
                  )}
                  aria-hidden="true"
                >
                  {active && <span className="size-2 rounded-full bg-lens-blue" />}
                </span>
                <span className="text-sm font-medium">{option.label}</span>
              </label>
            );
          })}
        </div>
      </fieldset>

      <div className="space-y-2">
        <div className="flex items-center justify-between gap-3">
          <label className="text-sm font-medium text-ink" htmlFor={contentId}>
            反馈内容
          </label>
          <span className="text-xs text-muted-foreground">{content.length} / 2000</span>
        </div>
        <textarea
          ref={textareaRef}
          id={contentId}
          maxLength={2000}
          value={content}
          onChange={(event) => setContent(event.target.value)}
          placeholder="写下具体问题、建议，或你希望 Claread 改进的地方。"
          className="min-h-32 w-full rounded-lg border border-hairline/80 bg-surface px-3.5 py-3 text-sm leading-6 text-ink outline-none ring-offset-background placeholder:text-muted-foreground/70 focus-visible:ring-2 focus-visible:ring-lens-blue focus-visible:ring-offset-2"
        />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
        <span
          className={cn(
            "min-h-5 text-xs",
            state.status === "error"
              ? "text-destructive"
              : state.status === "success"
                ? "text-structure-green"
                : "text-muted-foreground",
          )}
          role={state.message ? "status" : undefined}
        >
          {state.message || " "}
        </span>
        <Button
          variant="primary-ink"
          type="submit"
          disabled={!canSubmit}
          className="min-h-11 justify-center px-5"
        >
          {state.status === "submitting" ? (
            <>
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              提交中
            </>
          ) : (
            <>
              <SendHorizontal className="size-4" aria-hidden="true" />
              提交反馈
            </>
          )}
        </Button>
      </div>

      <section className="border-t border-hairline pt-8">
        <div className="mb-4">
          <h2 className="text-sm font-medium text-ink">我的反馈记录</h2>
          <p className="mt-1 text-xs text-muted-foreground">最近提交的反馈会显示在这里。</p>
        </div>
        <MyFeedbackList refreshKey={listRefreshKey} />
      </section>
    </form>
  );
}
