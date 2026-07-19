"use client";

import { useId, useRef, useState } from "react";
import { Loader2, SendHorizontal } from "lucide-react";

import { SettingsChoiceGroup } from "@/components/settings/SettingsChoiceGroup";
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

type FeedbackSubmitResponse = { ok: true; message: string } | { ok: false; message: string };

export function FeedbackForm() {
  const contentId = useId();
  const feedbackTypeId = useId();
  const feedbackTypeDescriptionId = useId();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [feedbackType, setFeedbackType] = useState<FeedbackType>("feature_request");
  const [sentiment, setSentiment] = useState<Sentiment>("neutral");
  const [content, setContent] = useState("");
  const [listRefreshKey, setListRefreshKey] = useState(0);
  const [state, setState] = useState<SubmitState>({ status: "idle", message: "" });

  const canSubmit = content.trim().length > 0 && state.status !== "submitting";
  const selectedType = FEEDBACK_TYPE_OPTIONS.find((option) => option.value === feedbackType)!;

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
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          feedbackScope: "app",
          targetId: "web-settings",
          sentiment,
          feedbackType,
          content,
          contextJson: { entry: "settings", userAgent: navigator.userAgent },
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
        setState({ status: "error", message: result.message || "反馈提交失败，内容已保留，请稍后重试。" });
        textareaRef.current?.focus();
        return;
      }
      setContent("");
      setFeedbackType("feature_request");
      setSentiment("neutral");
      setListRefreshKey((value) => value + 1);
      setState({ status: "success", message: result.message || "反馈已提交。" });
    } catch {
      setState({ status: "error", message: "网络异常，内容已保留，请稍后重试。" });
      textareaRef.current?.focus();
    }
  }

  return (
    <form className="space-y-0" onSubmit={handleSubmit}>
      <section className="pb-7" aria-labelledby="feedback-content-heading">
        <div className="flex items-center justify-between gap-3">
          <label id="feedback-content-heading" className="text-sm font-medium text-ink" htmlFor={contentId}>
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
          className="mt-2 min-h-36 w-full rounded-[var(--cl-radius-control-sm)] border border-hairline bg-surface px-3.5 py-3 text-sm leading-6 text-ink outline-none ring-offset-background placeholder:text-muted-foreground/70 focus-visible:ring-2 focus-visible:ring-lens-blue focus-visible:ring-offset-2"
        />
      </section>

      <section className="space-y-6 border-t border-hairline py-6" aria-label="反馈分类">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-8">
          <div className="max-w-sm">
            <label htmlFor={feedbackTypeId} className="text-sm font-medium text-ink">反馈类型</label>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">帮助我们更快找到负责的方向。</p>
          </div>
          <div className="w-full sm:max-w-64">
            <select
              id={feedbackTypeId}
              value={feedbackType}
              onChange={(event) => setFeedbackType(event.target.value as FeedbackType)}
              aria-describedby={feedbackTypeDescriptionId}
              className="min-h-11 w-full rounded-[var(--cl-radius-control-sm)] border border-hairline bg-surface px-3 text-sm text-ink outline-none transition-colors hover:bg-surface-raised focus-visible:ring-2 focus-visible:ring-lens-blue focus-visible:ring-offset-2"
            >
              {FEEDBACK_TYPE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
            <p id={feedbackTypeDescriptionId} className="mt-1.5 text-xs leading-5 text-muted-foreground">{selectedType.description}</p>
          </div>
        </div>
        <SettingsChoiceGroup
          name="feedback-sentiment"
          value={sentiment}
          options={SENTIMENT_OPTIONS}
          onValueChange={setSentiment}
          label="总体感受"
          description="这会帮助我们判断反馈的优先级。"
        />
      </section>

      <div className="flex flex-wrap items-center gap-3 border-t border-hairline py-5" aria-label="提交反馈">
        <Button
          variant="primary-ink"
          type="submit"
          disabled={!canSubmit}
          className="min-h-11 rounded-[var(--cl-radius-control-sm)] px-4 !shadow-none hover:!translate-y-0 hover:!shadow-none"
        >
          {state.status === "submitting" ? <><Loader2 className="size-4 animate-spin" aria-hidden="true" />提交中</> : <><SendHorizontal className="size-4" aria-hidden="true" />提交反馈</>}
        </Button>
        <span
          className={cn(
            "min-h-5 text-xs",
            state.status === "error" ? "text-destructive" : state.status === "success" ? "text-feedback-success" : "text-muted-foreground",
          )}
          role={state.message ? "status" : undefined}
          aria-live="polite"
        >
          {state.message || " "}
        </span>
      </div>

      <section className="border-t border-hairline pt-7 pb-8" aria-labelledby="my-feedback-heading">
        <div className="mb-4">
          <h2 id="my-feedback-heading" className="text-sm font-medium text-ink">我的反馈记录</h2>
          <p className="mt-1 text-xs text-muted-foreground">最近提交的反馈会显示在这里。</p>
        </div>
        <MyFeedbackList refreshKey={listRefreshKey} />
      </section>
    </form>
  );
}