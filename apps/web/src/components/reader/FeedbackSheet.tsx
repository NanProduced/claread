"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  Flag,
  Heart,
  Languages,
  Pencil,
  ScanSearch,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  X,
} from "lucide-react";

import type {
  FeedbackScopeDto,
  FeedbackSentimentDto,
  FeedbackTypeDto,
} from "@/types/api/feedback";
import { cn } from "@/lib/cn";
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
  clientSurface?: string;
  entryPoint?: string;
  onClose: () => void;
}

type SubmitState = "idle" | "submitting" | "success" | "error";

const TYPE_TOKEN_STYLES: Partial<Record<FeedbackTypeDto, string>> = {
  thumbs_up: "from-[#F9E4A6] via-[#F3C96A] to-[#D79C38] text-amber-950",
  helpful: "from-[#F9E4A6] via-[#F3C96A] to-[#D79C38] text-amber-950",
  translation_inaccurate: "from-[#D9EBFF] via-[#A9CCF4] to-[#7FA6D9] text-slate-900",
  too_few_annotations: "from-[#F2E8D4] via-[#E7D0B0] to-[#C7A67D] text-stone-900",
  too_many_annotations: "from-[#E6F0DD] via-[#BFD7AC] to-[#8DAA79] text-stone-900",
  wrong_difficulty: "from-[#EEE4D6] via-[#D8BFA8] to-[#B69276] text-stone-900",
  wrong_label: "from-[#FDE0D4] via-[#F3B6A7] to-[#D88773] text-stone-900",
  inaccurate: "from-[#FFD7D5] via-[#F1A9A4] to-[#D0736E] text-stone-900",
  wrong_boundary: "from-[#DDE7FF] via-[#BACAF3] to-[#8F9ED4] text-stone-900",
  should_not_annotate: "from-[#E9E4FF] via-[#C9C0F1] to-[#9D95CB] text-stone-900",
  sentence_analysis_wrong: "from-[#DEE8FF] via-[#B7C8F0] to-[#8EA5D3] text-stone-900",
  annotation_conflict: "from-[#F4E5D6] via-[#E2C4AB] to-[#BE9473] text-stone-900",
  selection_issue: "from-[#E2F3EF] via-[#B7DDD5] to-[#7FB5AA] text-stone-900",
  wrong_definition: "from-[#EDE7DD] via-[#D7C8B4] to-[#B49C80] text-stone-900",
  missing_definition: "from-[#FCEDD6] via-[#EFD3A2] to-[#D4A95A] text-stone-900",
  wrong_pos: "from-[#DDEBFF] via-[#B7D0F2] to-[#86A9D8] text-stone-900",
  wrong_phonetic: "from-[#E6F3EA] via-[#C4E0CB] to-[#95B79E] text-stone-900",
  bad_example: "from-[#F2E4F0] via-[#D6BFD0] to-[#B694AE] text-stone-900",
  bug_report: "from-[#FFDCD8] via-[#F0B0A8] to-[#D88579] text-stone-900",
  feature_request: "from-[#F7E5C7] via-[#E7C38A] to-[#C59553] text-stone-900",
  quota_issue: "from-[#E5F2F0] via-[#C7DFD8] to-[#93B7AD] text-stone-900",
  input_page_issue: "from-[#E5E9FA] via-[#C7D0F0] to-[#98A7D6] text-stone-900",
  ux_issue: "from-[#EFE5D6] via-[#DDC3A6] to-[#BD9974] text-stone-900",
  other: "from-[#EEEAE1] via-[#D8D0C4] to-[#B4AB9B] text-stone-900",
};

function feedbackTypeIcon(type: FeedbackTypeDto) {
  if (type === "thumbs_up" || type === "helpful") return Heart;
  if (type === "translation_inaccurate" || type === "wrong_definition") return Languages;
  if (type === "selection_issue" || type === "wrong_boundary") return ScanSearch;
  if (type === "feature_request" || type === "input_page_issue" || type === "other") return Pencil;
  return Sparkles;
}

function FeedbackToken({
  feedbackType,
  className,
}: {
  feedbackType: FeedbackTypeDto;
  className?: string;
}) {
  const Icon = feedbackTypeIcon(feedbackType);
  const gradient = TYPE_TOKEN_STYLES[feedbackType] ?? "from-[#F1E6D2] via-[#DDC7A7] to-[#B99774] text-stone-900";

  return (
    <span
      aria-hidden="true"
      className={cn(
        "relative inline-flex size-5 shrink-0 items-center justify-center rounded-full bg-gradient-to-br shadow-[inset_0_1px_0_rgba(255,255,255,0.7),0_6px_12px_rgba(120,98,70,0.16)] ring-1 ring-black/5",
        gradient,
        className,
      )}
    >
      <span className="absolute inset-[1px] rounded-full bg-white/12" />
      <Icon className="relative size-2.75" strokeWidth={2.2} />
    </span>
  );
}

function SuccessSeal() {
  return (
    <span className="relative flex size-14 items-center justify-center">
      <span className="absolute inset-0 rounded-full bg-[radial-gradient(circle_at_30%_25%,#FFF7DB_0%,#EAC56E_48%,#BD8D32_100%)] shadow-[0_12px_22px_rgba(120,88,30,0.22)]" />
      <span className="absolute inset-1 rounded-full border border-white/40" />
      <Sparkles className="relative z-10 size-5 text-amber-950" strokeWidth={2.1} />
    </span>
  );
}

export function FeedbackSheet({
  scope,
  prefillSentiment,
  prefillType,
  analysisRecordId,
  targetId,
  annotationType,
  contextJson,
  contextSummary,
  clientSurface,
  entryPoint,
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

  useEffect(() => {
    if (feedbackType === "other") {
      textareaRef.current?.focus();
    }
  }, [feedbackType]);

  const activeOptions = sentiment === "positive"
    ? config.positiveOptions ?? []
    : sentiment === "negative"
      ? config.negativeOptions ?? []
      : sentiment === "neutral"
        ? config.neutralOptions ?? []
        : [];

  const requiresExplanation = config.requiresText || feedbackType === "other";
  const canSubmit =
    submitState === "idle" &&
    sentiment !== null &&
    feedbackType !== null &&
    (!requiresExplanation || content.trim().length > 0);

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
      contextSummary: contextSummary ?? null,
      clientPlatform: "web",
      clientSurface: clientSurface ?? null,
      entryPoint: entryPoint ?? null,
      appVersion: "web",
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
  }, [
    analysisRecordId,
    annotationType,
    canSubmit,
    clientSurface,
    content,
    contextJson,
    contextSummary,
    entryPoint,
    feedbackType,
    scope,
    sentiment,
    targetId,
  ]);

  const hasUnsavedInput = content.trim().length > 0
    || sentiment !== (prefillSentiment ?? null)
    || feedbackType !== (prefillType ?? null);

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
    <div
      role="dialog"
      aria-modal="false"
      className="fixed bottom-0 left-0 right-0 z-50 overflow-hidden rounded-t-2xl border border-hairline/60 bg-surface/80 p-0 text-ink shadow-2xl backdrop-blur-2xl ring-1 ring-inset ring-white/20 duration-300 animate-in fade-in-0 zoom-in-95 slide-in-from-bottom-8 ease-out sm:bottom-6 sm:left-auto sm:right-6 sm:w-[410px] sm:rounded-2xl"
      onKeyDown={handleKeyDown}
    >
      {submitState === "success" ? (
        <div className="flex flex-col items-center justify-center gap-3 px-6 py-11 text-center">
          <SuccessSeal />
          <div className="space-y-1">
            <p className="text-sm font-semibold text-ink">已收到反馈</p>
            <p className="text-xs leading-5 text-muted">你的反馈会帮助 Claread 持续修正这类问题。</p>
          </div>
          <span className="inline-flex items-center gap-1 rounded-full border border-hairline/70 bg-surface-warm/80 px-2.5 py-1 text-[0.68rem] text-muted">
            <Check className="size-3 text-structure-green" />
            记录已写入
          </span>
        </div>
      ) : (
        <>
          <div className="px-5 pt-5 pb-0">
            <div className="flex items-center justify-between">
              <div className="space-y-1">
                <h2 className="text-base font-semibold text-ink">
                  {config.title}
                </h2>
                {contextSummary ? (
                  <div className="rounded-xl border border-hairline/70 bg-surface-warm/65 px-3 py-2">
                    <p className="line-clamp-2 text-xs leading-5 text-muted">
                      {contextSummary}
                    </p>
                  </div>
                ) : null}
              </div>
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
          </div>

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
                        "inline-flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs font-medium shadow-[0_1px_0_rgba(255,255,255,0.35)]",
                        readerInlineFocusRing,
                        readerTransitionFast,
                        feedbackType === opt.value
                          ? "border-lens-blue/30 bg-lens-blue-soft/60 text-lens-blue"
                          : "border-hairline bg-secondary text-ink hover:border-[var(--app-control-border-hover)] hover:bg-[var(--app-control-quiet)]",
                      )}
                      onClick={() => setFeedbackType(opt.value)}
                    >
                      <FeedbackToken feedbackType={opt.value} />
                      {opt.label}
                    </button>
                  ))}
                </div>
              </fieldset>
            ) : null}

            <fieldset className="flex flex-col gap-2">
              <legend className="text-xs font-semibold tracking-[0.02em] text-muted/80">
                详细描述
                {requiresExplanation ? (
                  <span className="ml-1 text-destructive/80">*</span>
                ) : null}
              </legend>
              <textarea
                ref={textareaRef}
                value={content}
                onChange={(e) => setContent(e.target.value)}
                placeholder={feedbackType === "other" ? "请补充具体情况，帮助我们更快定位问题" : config.placeholder}
                rows={3}
                className={cn(
                  "w-full resize-none rounded-xl border border-hairline bg-surface-warm/50 px-3.5 py-3 text-sm text-ink placeholder:text-muted/50 shadow-[inset_0_2px_4px_rgba(0,0,0,0.02)] transition-all",
                  readerInlineFocusRing,
                  "hover:border-[var(--app-control-border-hover)] hover:bg-surface-warm",
                  "focus:border-lens-blue/40 focus:bg-surface focus:ring-4 focus:ring-lens-blue/10",
                )}
              />
              <p className="text-[0.7rem] leading-5 text-muted">
                {feedbackType === "other"
                  ? "“其他” 需要补充说明，避免这条反馈失去可处理性。"
                  : "你的反馈会帮助 Claread 持续修正这类问题。"}
              </p>
            </fieldset>

            {submitState === "error" ? (
              <p className="text-xs text-destructive">提交失败，请重试</p>
            ) : null}

            <div className="flex items-center justify-between gap-3 rounded-xl border border-hairline/60 bg-surface-warm/55 px-3.5 py-3">
              <div className="flex items-center gap-2">
                <FeedbackToken
                  feedbackType={feedbackType ?? "other"}
                  className="size-6"
                />
                <p className="text-[0.72rem] leading-5 text-muted">
                  提交后可在“我的反馈”里查看处理状态。
                </p>
              </div>
              <div className="flex items-center gap-2">
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
                    "inline-flex min-h-9 items-center justify-center rounded-lg bg-lens-blue px-5 text-sm font-semibold text-white shadow-sm ring-1 ring-inset ring-black/10 transition-all",
                    readerInlineFocusRing,
                    "disabled:pointer-events-none disabled:opacity-40",
                    "hover:bg-lens-blue/90 hover:shadow-md active:scale-[0.98]",
                  )}
                >
                  {submitState === "submitting" ? "提交中…" : "提交反馈"}
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
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

  const tokenType: FeedbackTypeDto =
    sentiment === "positive"
      ? "thumbs_up"
      : sentiment === "negative"
        ? "inaccurate"
        : "feature_request";

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
      <FeedbackToken feedbackType={tokenType} />
      {icon}
      {label}
    </button>
  );
}
