"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Flag, ThumbsDown, ThumbsUp, X } from "lucide-react";

import type {
  FeedbackScopeDto,
  FeedbackSentimentDto,
  FeedbackTypeDto,
  FeedbackCreateRequestDto,
} from "@/types/api/feedback";
import { cn } from "@/lib/cn";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/primitives";
import { readerInlineFocusRing, readerTransitionFast } from "./interaction";

export type { FeedbackScopeDto as FeedbackScope, FeedbackSentimentDto as FeedbackSentiment };

const FEEDBACK_CONFIG_BY_SCOPE: Record<
  FeedbackScopeDto,
  {
    title: string;
    placeholder: string;
    requiresText: boolean;
    positiveOptions?: { value: FeedbackTypeDto; label: string }[];
    negativeOptions?: { value: FeedbackTypeDto; label: string }[];
    neutralOptions?: { value: FeedbackTypeDto; label: string }[];
  }
> = {
  analysis_result: {
    title: "解读结果反馈",
    placeholder: "请描述具体问题或建议",
    requiresText: false,
    positiveOptions: [{ value: "thumbs_up", label: "有帮助" }],
    negativeOptions: [
      { value: "translation_inaccurate", label: "翻译不准" },
      { value: "too_few_annotations", label: "标注太少" },
      { value: "too_many_annotations", label: "标注太多" },
      { value: "wrong_difficulty", label: "难度不合适" },
      { value: "other", label: "其他" },
    ],
  },
  annotation: {
    title: "标注反馈",
    placeholder: "请描述标注的问题",
    requiresText: false,
    positiveOptions: [{ value: "helpful", label: "有帮助" }],
    negativeOptions: [
      { value: "wrong_label", label: "标签错误" },
      { value: "inaccurate", label: "内容不准" },
      { value: "wrong_boundary", label: "边界不对" },
      { value: "should_not_annotate", label: "不该标注" },
      { value: "other", label: "其他" },
    ],
  },
  sentence: {
    title: "句子反馈",
    placeholder: "请描述句子的问题",
    requiresText: false,
    negativeOptions: [
      { value: "translation_inaccurate", label: "翻译不准" },
      { value: "sentence_analysis_wrong", label: "解析有误" },
      { value: "annotation_conflict", label: "标注冲突" },
      { value: "selection_issue", label: "选区问题" },
      { value: "other", label: "其他" },
    ],
  },
  dictionary: {
    title: "词典反馈",
    placeholder: "请描述词典的问题",
    requiresText: false,
    negativeOptions: [
      { value: "wrong_definition", label: "释义错误" },
      { value: "missing_definition", label: "释义缺失" },
      { value: "wrong_pos", label: "词性错误" },
      { value: "wrong_phonetic", label: "音标错误" },
      { value: "bad_example", label: "例句不好" },
      { value: "other", label: "其他" },
    ],
  },
  app: {
    title: "应用反馈",
    placeholder: "请描述你遇到的问题或建议",
    requiresText: true,
    neutralOptions: [
      { value: "bug_report", label: "遇到问题" },
      { value: "feature_request", label: "功能建议" },
      { value: "quota_issue", label: "配额问题" },
      { value: "input_page_issue", label: "输入页问题" },
      { value: "ux_issue", label: "体验不顺" },
      { value: "other", label: "其他" },
    ],
  },
};

export { FEEDBACK_CONFIG_BY_SCOPE };

export interface FeedbackSheetProps {
  scope: FeedbackScopeDto;
  prefillSentiment?: FeedbackSentimentDto;
  prefillType?: FeedbackTypeDto;
  analysisRecordId?: string;
  targetId: string;
  annotationType?: string;
  contextJson?: Record<string, unknown>;
  contextSummary?: string;
  onClose: () => void;
}

type SubmitState = "idle" | "submitting" | "success" | "error";

export function FeedbackSheet({
  scope,
  prefillSentiment,
  prefillType,
  analysisRecordId,
  targetId,
  annotationType,
  contextJson,
  contextSummary,
  onClose,
}: FeedbackSheetProps) {
  const config = FEEDBACK_CONFIG_BY_SCOPE[scope];
  const hasPositive = Boolean(config.positiveOptions?.length);
  const hasNegative = Boolean(config.negativeOptions?.length);
  const hasNeutral = Boolean(config.neutralOptions?.length);

  const [sentiment, setSentiment] = useState<FeedbackSentimentDto | null>(
    prefillSentiment ?? null,
  );
  const [feedbackType, setFeedbackType] = useState<FeedbackTypeDto | null>(
    prefillType ?? null,
  );
  const [content, setContent] = useState("");
  const [submitState, setSubmitState] = useState<SubmitState>("idle");

  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (prefillSentiment) setSentiment(prefillSentiment);
  }, [prefillSentiment]);

  useEffect(() => {
    if (prefillType) setFeedbackType(prefillType);
  }, [prefillType]);

  useEffect(() => {
    if (sentiment && !prefillType) {
      setFeedbackType(null);
    }
  }, [sentiment, prefillType]);

  const activeOptions = sentiment === "positive"
    ? config.positiveOptions ?? []
    : sentiment === "negative"
      ? config.negativeOptions ?? []
      : sentiment === "neutral"
        ? config.neutralOptions ?? []
        : [];

  const canSubmit =
    submitState === "idle" &&
    sentiment !== null &&
    feedbackType !== null &&
    (!config.requiresText || content.trim().length > 0);

  const handleSubmit = useCallback(async () => {
    if (!sentiment || !feedbackType || !canSubmit) return;

    setSubmitState("submitting");

    const body = {
      feedbackScope: scope,
      targetId,
      sentiment,
      feedbackType,
      content: content.trim() || null,
      contextJson: contextJson ?? {},
      ...(analysisRecordId ? { analysisRecordId } : {}),
      ...(annotationType ? { annotationType } : {}),
    };

    try {
      const res = await fetch("/api/web/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }

      setSubmitState("success");
    } catch {
      setSubmitState("error");
    }
  }, [canSubmit, sentiment, feedbackType, scope, targetId, content, contextJson, analysisRecordId, annotationType]);

  useEffect(() => {
    if (submitState === "success") {
      const timer = setTimeout(onClose, 1600);
      return () => clearTimeout(timer);
    }
  }, [submitState, onClose]);

  const hasUnsavedInput = content.trim().length > 0 || sentiment !== (prefillSentiment ?? null) || feedbackType !== (prefillType ?? null);

  const handleClose = useCallback(() => {
    if (submitState === "success" || !hasUnsavedInput) {
      onClose();
      return;
    }
    const confirmed = window.confirm("关闭将丢失已填写的内容，确定要关闭吗？");
    if (confirmed) onClose();
  }, [submitState, hasUnsavedInput, onClose]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        handleClose();
      }
    },
    [handleClose],
  );

  return (
    <Dialog open onOpenChange={(open) => { if (!open) handleClose(); }}>
      <DialogContent
        size="sm"
        showCloseButton={false}
        className="p-0 overflow-hidden"
        onKeyDown={handleKeyDown}
      >
        {submitState === "success" ? (
          <div className="flex flex-col items-center justify-center gap-3 py-12">
            <span className="flex size-10 items-center justify-center rounded-full bg-structure-green/15 text-structure-green">
              <Check className="size-5" strokeWidth={2.5} />
            </span>
            <p className="text-sm font-semibold text-ink">已记下，感谢帮 Claread 更准</p>
          </div>
        ) : (
          <>
            <DialogHeader className="px-5 pt-5 pb-0">
              <div className="flex items-center justify-between">
                <DialogTitle className="text-base font-semibold text-ink">
                  {config.title}
                </DialogTitle>
                <button
                  type="button"
                  onClick={handleClose}
                  className={cn(
                    "inline-flex size-7 items-center justify-center rounded-md border border-hairline bg-secondary text-muted",
                    readerInlineFocusRing,
                    readerTransitionFast,
                    "hover:bg-[var(--app-control-quiet)] hover:text-ink",
                  )}
                  aria-label="关闭"
                >
                  <X className="size-3.5" />
                </button>
              </div>
              {contextSummary ? (
                <DialogDescription className="mt-1 line-clamp-2 text-xs text-muted">
                  {contextSummary}
                </DialogDescription>
              ) : null}
            </DialogHeader>

            <div className="flex flex-col gap-4 px-5 pt-4 pb-5">
              {(hasPositive || hasNegative || hasNeutral) && scope !== "app" ? (
                <fieldset className="flex flex-col gap-2">
                  <legend className="text-xs font-semibold tracking-[0.02em] text-muted/80">
                    评价
                  </legend>
                  <div className="flex gap-2">
                    {hasPositive ? (
                      <SentimentButton
                        sentiment="positive"
                        active={sentiment === "positive"}
                        onClick={() => setSentiment("positive")}
                      />
                    ) : null}
                    {hasNegative ? (
                      <SentimentButton
                        sentiment="negative"
                        active={sentiment === "negative"}
                        onClick={() => setSentiment("negative")}
                      />
                    ) : null}
                  </div>
                </fieldset>
              ) : null}

              {scope === "app" && !sentiment ? (
                <fieldset className="flex flex-col gap-2">
                  <div className="flex gap-2">
                    <SentimentButton
                      sentiment="neutral"
                      active={false}
                      onClick={() => setSentiment("neutral")}
                    />
                  </div>
                </fieldset>
              ) : null}

              {activeOptions.length > 0 ? (
                <fieldset className="flex flex-col gap-2">
                  <legend className="text-xs font-semibold tracking-[0.02em] text-muted/80">
                    问题类型
                  </legend>
                  <div className="flex flex-wrap gap-1.5">
                    {activeOptions.map((opt) => (
                      <button
                        key={opt.value}
                        type="button"
                        className={cn(
                          "inline-flex items-center rounded-lg border px-3 py-1.5 text-xs font-medium",
                          readerInlineFocusRing,
                          readerTransitionFast,
                          feedbackType === opt.value
                            ? "border-lens-blue/30 bg-lens-blue-soft/60 text-lens-blue"
                            : "border-hairline bg-secondary text-ink hover:border-[var(--app-control-border-hover)] hover:bg-[var(--app-control-quiet)]",
                        )}
                        onClick={() => setFeedbackType(opt.value)}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                </fieldset>
              ) : null}

              <fieldset className="flex flex-col gap-2">
                <legend className="text-xs font-semibold tracking-[0.02em] text-muted/80">
                  详细描述
                  {config.requiresText ? (
                    <span className="ml-1 text-destructive/80">*</span>
                  ) : null}
                </legend>
                <textarea
                  ref={textareaRef}
                  value={content}
                  onChange={(e) => setContent(e.target.value)}
                  placeholder={config.placeholder}
                  rows={3}
                  className={cn(
                    "w-full resize-none rounded-lg border border-hairline bg-surface-warm px-3 py-2.5 text-sm text-ink placeholder:text-muted/60",
                    readerInlineFocusRing,
                    readerTransitionFast,
                    "hover:border-[var(--app-control-border-hover)]",
                    "focus:border-lens-blue/30 focus:ring-2 focus:ring-lens-blue/10",
                  )}
                />
              </fieldset>

              {submitState === "error" ? (
                <p className="text-xs text-destructive">提交失败，请重试</p>
              ) : null}

              <div className="flex items-center justify-end gap-2 pt-1">
                <button
                  type="button"
                  onClick={handleClose}
                  className={cn(
                    "inline-flex min-h-9 items-center justify-center rounded-lg border border-hairline bg-secondary px-4 text-sm font-medium text-ink",
                    readerInlineFocusRing,
                    readerTransitionFast,
                    "hover:bg-[var(--app-control-quiet)]",
                  )}
                >
                  取消
                </button>
                <button
                  type="button"
                  disabled={!canSubmit}
                  onClick={handleSubmit}
                  className={cn(
                    "inline-flex min-h-9 items-center justify-center rounded-lg bg-lens-blue px-4 text-sm font-semibold text-white",
                    readerInlineFocusRing,
                    readerTransitionFast,
                    "disabled:pointer-events-none disabled:opacity-40",
                    "hover:bg-lens-blue/90 active:bg-lens-blue/80",
                  )}
                >
                  {submitState === "submitting" ? "提交中…" : "提交反馈"}
                </button>
              </div>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

interface SentimentButtonProps {
  sentiment: FeedbackSentimentDto;
  active: boolean;
  onClick: () => void;
}

function SentimentButton({ sentiment, active, onClick }: SentimentButtonProps) {
  const icon =
    sentiment === "positive" ? (
      <ThumbsUp className="size-3.5" />
    ) : sentiment === "negative" ? (
      <ThumbsDown className="size-3.5" />
    ) : (
      <Flag className="size-3.5" />
    );

  const label =
    sentiment === "positive"
      ? "有帮助"
      : sentiment === "negative"
        ? "有问题"
        : "反馈";

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium",
        readerInlineFocusRing,
        readerTransitionFast,
        active
          ? sentiment === "positive"
            ? "border-structure-green/30 bg-structure-green/10 text-structure-green"
            : sentiment === "negative"
              ? "border-destructive/25 bg-destructive/10 text-destructive"
              : "border-lens-blue/30 bg-lens-blue-soft/60 text-lens-blue"
          : "border-hairline bg-secondary text-ink hover:border-[var(--app-control-border-hover)] hover:bg-[var(--app-control-quiet)]",
      )}
    >
      {icon}
      {label}
    </button>
  );
}
