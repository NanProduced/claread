"use client";

import { useEffect, useRef, useState } from "react";
import { Clock, Loader2, RotateCcw } from "lucide-react";

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

const SENTIMENT_LABELS: Record<string, string> = {
  positive: "喜欢",
  neutral: "建议",
  negative: "遇阻",
};

const STATUS_LABELS: Record<string, { label: string; className: string }> = {
  pending: { label: "待处理", className: "text-muted-foreground" },
  adopted: { label: "已采纳", className: "text-feedback-success" },
  resolved: { label: "已解决", className: "text-feedback-success" },
  dismissed: { label: "已关闭", className: "text-muted-foreground" },
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
    <div className="flex animate-pulse items-start gap-3 border-b border-hairline px-1 py-4">
      <div className="size-8 shrink-0 rounded bg-hairline/70" />
      <div className="flex-1 space-y-2">
        <div className="h-3.5 w-28 rounded bg-hairline/70" />
        <div className="h-3 w-56 max-w-full rounded bg-hairline/60" />
      </div>
      <div className="h-7 w-14 rounded bg-hairline/60" />
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
      <div className="space-y-2">
        <SkeletonRow />
        <SkeletonRow />
        <SkeletonRow />
      </div>
    );
  }

  if (state.phase === "error") {
    return (
      <div className="border-y border-hairline px-1 py-6 text-center">
        <p className="text-sm text-muted-foreground">{state.message}</p>
      </div>
    );
  }

  const { items, hasMore, loadingMore } = state;

  return (
    <div className="space-y-2">
      {items.length === 0 ? (
        <div className="border-y border-hairline px-1 py-10 text-center">
          <p className="text-sm font-medium text-ink">暂无反馈记录</p>
          <p className="mt-1 text-xs text-muted-foreground">提交后会出现在这里。</p>
        </div>
      ) : null}

      {items.map((item) => {
        const statusCfg = STATUS_LABELS[item.status] ?? {
          label: item.status,
          className: "text-muted-foreground",
        };
        const scopeLabel = SCOPE_LABELS[item.feedbackScope] ?? item.feedbackScope;
        const typeLabel = getFeedbackTypeLabel(item.feedbackScope, item.feedbackType);
        const platformLabel = PLATFORM_LABELS[item.clientPlatform];
        const sentimentLabel = SENTIMENT_LABELS[item.sentiment] ?? item.sentiment;
        const display = getFeedbackDisplay(item);

        return (
          <article
            key={item.id}
            className="group flex flex-col gap-3 border-b border-hairline px-1 py-4 transition-colors hover:bg-[var(--interactive-quiet-hover)]"
          >
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="text-sm font-medium text-ink">{scopeLabel}</span>
              <span className="text-xs text-muted-foreground">{typeLabel}</span>
              <span className="text-xs text-muted-foreground">·</span>
              <span className="text-xs text-muted-foreground">{sentimentLabel}</span>
              <span className={cn("ml-auto text-xs font-medium", statusCfg.className)}>
                {statusCfg.label}
              </span>
            </div>

            {display.note ? (
              <p className="break-words text-sm leading-6 text-ink-soft">{display.note}</p>
            ) : null}
            {display.quote ? (
              <blockquote className="max-w-[74ch] text-xs leading-5 text-muted-foreground">
                <span className="text-subtle">“</span>
                <span className="break-words whitespace-pre-wrap">{display.quote}</span>
                <span className="text-subtle">”</span>
              </blockquote>
            ) : null}
            {item.resolutionNote ? (
              <div className="border-t border-hairline pt-2">
                <p className="text-xs leading-5 text-muted-foreground">
                  处理说明：{item.resolutionNote}
                </p>
              </div>
            ) : null}

            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <span className="inline-flex items-center gap-1 text-xs text-subtle">
                <Clock className="size-3" aria-hidden="true" />
                {formatDate(item.createdAt)}
              </span>
              <span className="text-xs text-subtle">{platformLabel}</span>
              {item.rewardPoints > 0 ? (
                <span className="text-xs text-muted-foreground">+{item.rewardPoints}</span>
              ) : null}

              {item.status === "pending" ? (
                <button
                  type="button"
                  disabled={revokingId === item.id}
                  onClick={() => handleRevoke(item.id)}
                  className="focus-ring ml-auto inline-flex min-h-11 items-center gap-1 rounded-md border border-transparent px-3 text-xs font-medium text-subtle transition-colors hover:border-hairline/75 hover:bg-surface-raised hover:text-ink disabled:opacity-40"
                >
                  {revokingId === item.id ? (
                    <Loader2 className="size-3 animate-spin" aria-hidden="true" />
                  ) : (
                    <RotateCcw className="size-3" aria-hidden="true" />
                  )}
                  撤回
                </button>
              ) : null}
            </div>
          </article>
        );
      })}

      {hasMore ? (
        <button
          type="button"
          disabled={loadingMore}
          onClick={handleLoadMore}
          className="min-h-11 w-full rounded-lg border border-hairline/60 bg-surface px-3 text-xs font-medium text-muted-foreground transition-colors hover:border-hairline hover:bg-surface-raised hover:text-ink disabled:opacity-40"
        >
          {loadingMore ? "加载中..." : "加载更多记录"}
        </button>
      ) : null}
    </div>
  );
}
