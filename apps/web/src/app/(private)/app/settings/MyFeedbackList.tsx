"use client";

import { useEffect, useRef, useState } from "react";
import {
  Award,
  Clock,
  RotateCcw,
} from "lucide-react";

import { FEEDBACK_CONFIG_BY_SCOPE } from "@/components/reader/FeedbackSheet";
import type {
  FeedbackClientPlatformDto,
  FeedbackScopeDto,
  FeedbackTypeDto,
} from "@/types/api/feedback";
import { cn } from "@/lib/cn";

type FeedbackItem = {
  id: string;
  feedbackScope: FeedbackScopeDto;
  feedbackType: FeedbackTypeDto;
  sentiment: string;
  content: string | null;
  contextSummary: string | null;
  clientPlatform: FeedbackClientPlatformDto;
  clientSurface: string | null;
  entryPoint: string | null;
  resolutionNote: string | null;
  status: string;
  rewardPoints: number;
  createdAt: string;
};

type ListState =
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | { phase: "loaded"; items: FeedbackItem[]; cursor: string | null; hasMore: boolean; loadingMore: boolean };

interface MyFeedbackListProps {
  refreshKey?: number;
}

const SCOPE_LABELS: Record<string, string> = {
  analysis_result: "结果反馈",
  annotation: "标注反馈",
  sentence: "句子反馈",
  dictionary: "词典反馈",
  app: "应用反馈",
};

const PLATFORM_LABELS: Record<FeedbackClientPlatformDto, string> = {
  web: "Web",
  wechat_miniprogram: "小程序",
};

const SURFACE_LABELS: Record<string, string> = {
  reader: "Reader",
  dictionary: "词典",
  settings: "设置页",
  result_page: "结果页",
  profile: "个人页",
};

const STATUS_LABELS: Record<string, { label: string; className: string }> = {
  pending: { label: "待处理", className: "border-vocab-amber/20 bg-vocab-amber/10 text-vocab-amber" },
  adopted: { label: "已采纳", className: "border-structure-green/20 bg-structure-green/10 text-structure-green" },
  resolved: { label: "已解决", className: "border-structure-green/20 bg-structure-green/10 text-structure-green" },
  dismissed: { label: "已关闭", className: "border-muted/15 bg-muted/10 text-muted" },
};

function getFeedbackTypeLabel(scope: FeedbackScopeDto, feedbackType: FeedbackTypeDto): string {
  const config = FEEDBACK_CONFIG_BY_SCOPE[scope];
  if (!config) return feedbackType;
  const allOptions = [
    ...(config.positiveOptions ?? []),
    ...(config.negativeOptions ?? []),
    ...(config.neutralOptions ?? []),
  ];
  const match = allOptions.find((o) => o.value === feedbackType);
  return match?.label ?? feedbackType;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

function iconForSentiment(sentiment: string) {
  if (sentiment === "positive") return "/images/feedback/thumbs-up.png";
  if (sentiment === "negative") return "/images/feedback/thumbs-down.png";
  return "/images/feedback/comment.png";
}

function normalizeDisplayText(value: string | null): string | null {
  const normalized = value?.trim().replace(/\s+/g, " ");
  return normalized ? normalized : null;
}

function getFeedbackDisplay(item: FeedbackItem) {
  const content = normalizeDisplayText(item.content);
  const contextSummary = normalizeDisplayText(item.contextSummary);
  const hasDistinctContext = Boolean(contextSummary && contextSummary !== content);

  return {
    note: content,
    quote: hasDistinctContext ? contextSummary : null,
  };
}

function SkeletonRow() {
  return (
    <div className="flex animate-pulse items-start gap-3 rounded-[18px] border border-hairline/75 bg-surface/62 px-4 py-4">
      <div className="size-10 shrink-0 rounded-[12px] bg-hairline/70" />
      <div className="flex-1 space-y-3">
        <div className="h-3.5 w-32 rounded bg-hairline/70" />
        <div className="h-3 w-64 max-w-full rounded bg-hairline/60" />
      </div>
      <div className="h-7 w-16 rounded-[10px] bg-hairline/60" />
    </div>
  );
}

export function MyFeedbackList({ refreshKey = 0 }: MyFeedbackListProps) {
  const [state, setState] = useState<ListState>({ phase: "loading" });
  const [revokingId, setRevokingId] = useState<string | null>(null);
  const activeRef = useRef(true);

  useEffect(() => {
    activeRef.current = true;

    async function loadInitial() {
      setState({ phase: "loading" });
      try {
        const res = await fetch("/api/web/feedback?limit=6");
        const data = await res.json();

        if (!activeRef.current) return;

        if (!res.ok || !data.ok) {
          setState({ phase: "error", message: data.message || "加载失败" });
          return;
        }

        setState({
          phase: "loaded",
          items: data.items,
          cursor: data.cursor,
          hasMore: data.hasMore,
          loadingMore: false,
        });
      } catch {
        if (!activeRef.current) return;
        setState({ phase: "error", message: "网络异常，请稍后重试。" });
      }
    }

    void loadInitial();

    return () => {
      activeRef.current = false;
    };
  }, [refreshKey]);

  async function handleRevoke(id: string) {
    setRevokingId(id);
    try {
      const res = await fetch(`/api/web/feedback/${id}`, { method: "DELETE" });
      if (res.ok) {
        setState((prev) => {
          if (prev.phase !== "loaded") return prev;
          return { ...prev, items: prev.items.filter((i) => i.id !== id) };
        });
      } else {
        alert("撤回失败，该反馈可能已不在待处理状态。");
      }
    } catch {
      alert("网络异常，请稍后重试。");
    } finally {
      setRevokingId(null);
    }
  }

  async function handleLoadMore() {
    if (state.phase !== "loaded" || !state.cursor || state.loadingMore) return;

    setState((prev) => (prev.phase === "loaded" ? { ...prev, loadingMore: true } : prev));

    try {
      const params = new URLSearchParams();
      params.set("limit", "6");
      params.set("cursor", state.cursor);
      const res = await fetch(`/api/web/feedback?${params.toString()}`);
      const data = await res.json();

      if (!res.ok || !data.ok) return;

      setState((prev) => {
        if (prev.phase !== "loaded") return prev;
        return {
          ...prev,
          items: [...prev.items, ...data.items],
          cursor: data.cursor,
          hasMore: data.hasMore,
          loadingMore: false,
        };
      });
    } catch {
      setState((prev) => (prev.phase === "loaded" ? { ...prev, loadingMore: false } : prev));
    }
  }

  if (state.phase === "loading") {
    return (
      <div className="space-y-2.5">
        <SkeletonRow />
        <SkeletonRow />
        <SkeletonRow />
      </div>
    );
  }

  if (state.phase === "error") {
    return (
      <div className="rounded-[18px] border border-hairline/75 bg-surface/62 px-4 py-8 text-center">
        <p className="text-sm text-muted">{state.message}</p>
      </div>
    );
  }

  const { items, hasMore, loadingMore } = state;

  return (
    <div className="space-y-2.5">
      {items.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-4 rounded-[20px] border border-hairline/75 bg-surface/62 px-4 py-12 text-center">
          <div className="relative size-20 opacity-90">
            <img
              src="/images/feedback/search.png"
              alt=""
              className="h-full w-full object-contain drop-shadow-[0_14px_24px_rgba(80,52,24,0.14)]"
            />
          </div>
          <div>
            <p className="text-sm font-semibold text-ink">暂无反馈记录</p>
            <p className="mt-1 text-xs text-muted">提交后会出现在这里。</p>
          </div>
        </div>
      ) : null}

      {items.map((item, index) => {
        const statusCfg = STATUS_LABELS[item.status] ?? { label: item.status, className: "border-muted/15 bg-muted/10 text-muted" };
        const scopeLabel = SCOPE_LABELS[item.feedbackScope] ?? item.feedbackScope;
        const typeLabel = getFeedbackTypeLabel(item.feedbackScope, item.feedbackType);
        const platformLabel = PLATFORM_LABELS[item.clientPlatform];
        const surfaceLabel = item.clientSurface ? SURFACE_LABELS[item.clientSurface] ?? item.clientSurface : null;
        const display = getFeedbackDisplay(item);

        return (
          <article
            key={item.id}
            className="group grid grid-cols-[2.5rem_minmax(0,1fr)] items-start gap-3 rounded-[18px] border border-hairline/75 bg-surface/62 px-4 py-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.34)] transition-all duration-200 hover:border-muted hover:bg-surface/86 hover:shadow-[0_10px_24px_rgba(28,24,18,0.06)] sm:grid-cols-[2.5rem_minmax(0,1fr)_auto]"
            style={{ animationDelay: `${Math.min(index, 5) * 45}ms` }}
          >
            <div className="relative mt-0.5 size-10 shrink-0">
              <img
                src={iconForSentiment(item.sentiment)}
                alt=""
                className="h-full w-full object-contain drop-shadow-[0_8px_14px_rgba(80,52,24,0.12)]"
              />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="text-sm font-semibold text-ink">{scopeLabel}</span>
                <span className="text-xs text-muted">{typeLabel}</span>
                <span className={cn("inline-flex items-center rounded-[8px] border px-2 py-0.5 text-[11px] font-medium", statusCfg.className)}>
                  {statusCfg.label}
                </span>
              </div>
              {display.note ? (
                <p className="mt-1.5 break-words text-sm leading-6 text-ink-soft">
                  {display.note}
                </p>
              ) : null}
              {display.quote ? (
                <blockquote className="mt-2 max-w-[74ch] text-[13px] leading-6 text-muted">
                  <span className="text-subtle">“</span>
                  <span className="break-words whitespace-pre-wrap">{display.quote}</span>
                  <span className="text-subtle">”</span>
                </blockquote>
              ) : null}
              {item.resolutionNote ? (
                <div className="mt-2 rounded-[12px] border border-hairline/70 bg-reader-paper/65 px-3 py-2">
                  <p className="text-[12px] leading-5 text-muted">
                    处理说明：{item.resolutionNote}
                  </p>
                </div>
              ) : null}
              <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-subtle">
                <span className="inline-flex items-center gap-1">
                  <Clock className="size-3" aria-hidden="true" />
                  {formatDate(item.createdAt)}
                </span>
                <span>{platformLabel}</span>
                {surfaceLabel ? <span>{surfaceLabel}</span> : null}
                {item.rewardPoints > 0 ? (
                  <span className="inline-flex items-center gap-1 text-vocab-amber">
                    <Award className="size-3" aria-hidden="true" />
                    +{item.rewardPoints}
                  </span>
                ) : null}
              </div>
            </div>
            {item.status === "pending" ? (
              <button
                type="button"
                disabled={revokingId === item.id}
                onClick={() => handleRevoke(item.id)}
                className="focus-ring col-start-2 mt-1 inline-flex h-7 w-fit shrink-0 items-center gap-1 rounded-[8px] border border-transparent bg-transparent px-1.5 text-[11px] font-medium text-subtle transition-colors hover:border-hairline/75 hover:bg-[var(--app-control-quiet)] hover:text-ink disabled:opacity-40 sm:col-start-3 sm:row-start-1 sm:mt-0 sm:justify-self-end"
              >
                <RotateCcw className="size-3" aria-hidden="true" />
                撤回
              </button>
            ) : null}
          </article>
        );
      })}

      {hasMore ? (
        <button
          type="button"
          disabled={loadingMore}
          onClick={handleLoadMore}
          className="focus-ring mt-3 w-full rounded-[16px] border border-hairline/80 bg-surface/62 py-3 text-xs font-semibold text-muted transition-colors hover:border-muted hover:bg-surface hover:text-ink disabled:opacity-40"
        >
          {loadingMore ? "加载中..." : "加载更多记录"}
        </button>
      ) : null}
    </div>
  );
}
