"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  Loader2,
  MessageSquareText,
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
    placeholder: "可以补充哪里不准，或希望怎么改",
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
    placeholder: "可以补充这条标注哪里需要调整",
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
    placeholder: "可以补充句子解析哪里需要调整",
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
    placeholder: "可以补充释义、词性或例句的问题",
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
    placeholder: "写下建议，或描述你遇到的问题",
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

function SuccessSeal() {
  return (
    <span className="relative flex size-20 items-center justify-center animate-in zoom-in-95 duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]">
      <img
        src="/images/feedback/love.png"
        alt=""
        className="h-full w-full object-contain drop-shadow-[0_12px_24px_rgba(170,42,58,0.18)]"
      />
    </span>
  );
}

function sentimentCopy(sentiment: FeedbackSentimentDto) {
  if (sentiment === "positive") {
    return {
      label: "有帮助",
      Icon: ThumbsUp,
      activeClassName: "border-structure-green/35 bg-structure-green/10 text-structure-green shadow-[0_6px_14px_rgba(33,184,117,0.08)]",
      iconClassName: "border-structure-green/20 bg-structure-green/10",
    };
  }

  if (sentiment === "negative") {
    return {
      label: "有问题",
      Icon: ThumbsDown,
      activeClassName: "border-error-red/30 bg-error-red/10 text-error-red shadow-[0_6px_14px_rgba(190,18,60,0.08)]",
      iconClassName: "border-error-red/20 bg-error-red/10",
    };
  }

  return {
    label: "反馈",
    Icon: MessageSquareText,
    activeClassName: "border-lens-blue/30 bg-lens-blue-soft/60 text-lens-blue shadow-[0_6px_14px_rgba(28,95,190,0.08)]",
    iconClassName: "border-lens-blue/20 bg-lens-blue/10",
  };
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
    prefillSentiment ?? (scope === "app" ? "neutral" : null),
  );
  const [feedbackType, setFeedbackType] = useState<FeedbackTypeDto | null>(
    prefillType ?? null,
  );
  const [content, setContent] = useState("");
  const [submitState, setSubmitState] = useState<SubmitState>("idle");

  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Sync sentiment to prefillSentiment prop changes
  const [prevPrefillSentiment, setPrevPrefillSentiment] = useState(prefillSentiment);
  if (prefillSentiment !== prevPrefillSentiment) {
    setPrevPrefillSentiment(prefillSentiment);
    if (prefillSentiment) setSentiment(prefillSentiment);
  }

  // Sync feedbackType to prefillType prop changes
  const [prevPrefillType, setPrevPrefillType] = useState(prefillType);
  if (prefillType !== prevPrefillType) {
    setPrevPrefillType(prefillType);
    if (prefillType) setFeedbackType(prefillType);
  }

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

  // Recompute feedbackType when sentiment, prefillType, scope, or available options change
  const feedbackTypeSyncKey = `${scope}:${sentiment ?? ""}:${prefillType ?? ""}:${activeOptions
    .map((option) => option.value)
    .join(",")}`;
  const [prevFeedbackTypeSyncKey, setPrevFeedbackTypeSyncKey] = useState(feedbackTypeSyncKey);
  if (feedbackTypeSyncKey !== prevFeedbackTypeSyncKey) {
    setPrevFeedbackTypeSyncKey(feedbackTypeSyncKey);
    if (!sentiment) {
      setFeedbackType(null);
    } else {
      if (prefillType && activeOptions.some((option) => option.value === prefillType)) {
        setFeedbackType(prefillType);
      } else if (activeOptions.length === 1) {
        setFeedbackType(activeOptions[0].value);
      } else {
        setFeedbackType(null);
      }
    }
  }

  const requiresExplanation = config.requiresText || feedbackType === "other";
  const isImplicitPositiveType = sentiment === "positive" && activeOptions.length === 1;
  const showTypeOptions = activeOptions.length > 0 && !isImplicitPositiveType;
  const typeLegend = scope === "app" ? "反馈类型" : "问题类型";
  const textareaLegend = sentiment === "positive" ? "补充说明" : "详细描述";
  const textareaPlaceholder = feedbackType === "other"
    ? "请补充具体情况，帮助我们更快定位"
    : sentiment === "positive"
      ? "可选：告诉我们哪里有帮助"
      : config.placeholder;
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

  const initialSentiment = prefillSentiment ?? (scope === "app" ? "neutral" : null);
  const hasUnsavedInput = content.trim().length > 0
    || sentiment !== initialSentiment
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

  useEffect(() => {
    if (submitState !== "success") return;

    const timeoutId = window.setTimeout(() => {
      onClose();
    }, 2600);

    return () => window.clearTimeout(timeoutId);
  }, [onClose, submitState]);

  return (
    <div
      role="dialog"
      aria-modal="false"
      className="fixed bottom-0 left-0 right-0 z-50 overflow-hidden rounded-t-[20px] border border-hairline/70 bg-[color-mix(in_srgb,var(--surface)_88%,transparent)] p-0 text-ink shadow-[0_24px_70px_rgba(28,24,18,0.16)] backdrop-blur-2xl ring-1 ring-inset ring-white/20 duration-300 animate-in fade-in-0 zoom-in-95 slide-in-from-bottom-8 ease-out sm:bottom-6 sm:left-auto sm:right-6 sm:w-[430px] sm:rounded-[20px]"
      onKeyDown={handleKeyDown}
    >
      {submitState === "success" ? (
        <div className="relative flex flex-col items-center justify-center gap-3 px-6 py-10 text-center">
          <button
            type="button"
            onClick={onClose}
            className={cn(
              "absolute right-4 top-4 inline-flex size-8 items-center justify-center rounded-[10px] border border-hairline/75 bg-secondary/70 text-muted",
              readerInlineFocusRing,
              readerTransitionFast,
              "hover:border-muted hover:bg-[var(--app-control-quiet)] hover:text-ink",
            )}
            aria-label="关闭"
          >
            <X className="size-4" />
          </button>
          <SuccessSeal />
          <p className="text-base font-semibold text-ink">已收到反馈</p>
          <span className="inline-flex items-center gap-1.5 rounded-[10px] border border-hairline/70 bg-surface-warm/80 px-3 py-1.5 text-[12px] font-medium text-muted">
            <Check className="size-3 text-structure-green" />
            记录已写入
          </span>
        </div>
      ) : (
        <>
          <div className="px-5 pt-5 pb-0">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <h2 className="text-[17px] font-semibold leading-7 text-ink">
                  {config.title}
                </h2>
              </div>
              <button
                type="button"
                onClick={handleClose}
                className={cn(
                  "inline-flex size-9 items-center justify-center rounded-[12px] border border-hairline/75 bg-secondary/70 text-muted",
                  readerInlineFocusRing,
                  readerTransitionFast,
                  "hover:border-muted hover:bg-[var(--app-control-quiet)] hover:text-ink",
                )}
                aria-label="关闭"
              >
                <X className="size-3.5" />
              </button>
            </div>
          </div>

          <div className="flex flex-col gap-5 px-5 pt-5 pb-5">
            {(hasPositive || hasNegative || hasNeutral) && scope !== "app" ? (
              <fieldset className="flex flex-col gap-2.5">
                <legend className="text-[12px] font-semibold text-muted">
                  评价
                </legend>
                <div className="grid grid-cols-2 gap-2.5">
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

            {showTypeOptions ? (
              <fieldset className="flex flex-col gap-2.5">
                <legend className="text-[12px] font-semibold text-muted">
                  {typeLegend}
                </legend>
                <div className="grid grid-cols-2 gap-2">
                  {activeOptions.map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      aria-pressed={feedbackType === opt.value}
                      className={cn(
                        "group flex min-h-11 items-center justify-between rounded-[12px] border px-3 py-2 text-left text-[13px] font-semibold transition-all duration-200",
                        readerInlineFocusRing,
                        feedbackType === opt.value
                          ? "border-ink/70 bg-[linear-gradient(180deg,var(--ink),color-mix(in_srgb,var(--ink)_88%,var(--surface)))] text-background shadow-[0_7px_16px_rgba(30,25,18,0.13)]"
                          : "border-hairline/80 bg-secondary/58 text-ink-soft shadow-[inset_0_1px_0_rgba(255,255,255,0.34)] hover:border-muted hover:bg-[var(--app-control-quiet)] hover:text-ink",
                      )}
                      onClick={() => setFeedbackType(opt.value)}
                    >
                      <span>{opt.label}</span>
                      <Check
                        className={cn(
                          "size-3.5 transition-opacity",
                          feedbackType === opt.value ? "opacity-100" : "opacity-0 group-hover:opacity-35",
                        )}
                      />
                    </button>
                  ))}
                </div>
              </fieldset>
            ) : null}

            <fieldset className="flex flex-col gap-2.5">
              <legend className="text-[12px] font-semibold text-muted">
                {textareaLegend}
                {requiresExplanation ? (
                  <span className="ml-1 text-destructive/80">*</span>
                ) : null}
              </legend>
              <textarea
                ref={textareaRef}
                value={content}
                onChange={(e) => setContent(e.target.value)}
                placeholder={textareaPlaceholder}
                rows={3}
                className={cn(
                  "min-h-[112px] w-full resize-none rounded-[14px] border border-hairline/85 bg-[color-mix(in_srgb,var(--surface-warm)_58%,transparent)] px-3.5 py-3 text-sm leading-6 text-ink placeholder:text-muted/65 shadow-[inset_0_2px_6px_rgba(30,25,18,0.025)] transition-all",
                  readerInlineFocusRing,
                  "hover:border-[var(--app-control-border-hover)] hover:bg-surface-warm/80",
                  "focus:border-lens-blue/40 focus:bg-surface focus:ring-4 focus:ring-lens-blue/10",
                )}
              />
            </fieldset>

            {submitState === "error" ? (
              <p className="text-xs text-destructive">提交失败，请重试</p>
            ) : null}

            <div className="mt-1 flex items-center justify-end gap-2 border-t border-hairline/60 pt-4">
                <button
                  type="button"
                  onClick={handleClose}
                  className={cn(
                    "inline-flex min-h-10 items-center justify-center rounded-[12px] border border-hairline/80 bg-secondary/70 px-4 text-sm font-medium text-ink",
                    readerInlineFocusRing,
                    readerTransitionFast,
                    "hover:border-muted hover:bg-[var(--app-control-quiet)]",
                  )}
                >
                  取消
                </button>
                <button
                  type="button"
                  disabled={!canSubmit}
                  onClick={handleSubmit}
                  className={cn(
                    "inline-flex min-h-10 items-center justify-center gap-1.5 rounded-[12px] bg-ink px-5 text-sm font-semibold text-background shadow-[0_8px_18px_rgba(30,25,18,0.16)] ring-1 ring-inset ring-white/10 transition-all",
                    readerInlineFocusRing,
                    "disabled:pointer-events-none disabled:bg-muted/55 disabled:text-background/75 disabled:shadow-none",
                    "hover:bg-ink-soft hover:shadow-[0_8px_18px_rgba(30,25,18,0.18)] active:scale-[0.98]",
                  )}
                >
                  {submitState === "submitting" ? (
                    <>
                      <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
                      提交中
                    </>
                  ) : (
                    "提交反馈"
                  )}
                </button>
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
  const meta = sentimentCopy(sentiment);
  const Icon = meta.Icon;

  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "group flex min-h-[58px] items-center gap-3 rounded-[14px] border px-3.5 py-2.5 text-left transition-all duration-200 ease-[cubic-bezier(0.25,1,0.5,1)]",
        readerInlineFocusRing,
        active
          ? meta.activeClassName
          : "border-hairline/80 bg-secondary/58 text-ink-soft shadow-[inset_0_1px_0_rgba(255,255,255,0.34)] hover:border-muted hover:bg-[var(--app-control-quiet)] hover:text-ink",
      )}
    >
      <span
        className={cn(
          "inline-flex size-9 shrink-0 items-center justify-center rounded-[10px] border bg-background/72 transition-colors",
          active ? meta.iconClassName : "border-hairline/70",
        )}
      >
        <Icon className="size-[18px]" aria-hidden="true" />
      </span>
      <span className="min-w-0 flex-1 text-sm font-semibold leading-5">{meta.label}</span>
      <Check
        className={cn(
          "size-4 shrink-0 transition-opacity",
          active ? "opacity-100" : "opacity-0 group-hover:opacity-35",
        )}
      />
    </button>
  );
}
