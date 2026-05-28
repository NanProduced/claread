"use client";

import { useEffect, useRef, useState } from "react";
import { Award, Clock, MessageSquare, RotateCcw } from "lucide-react";

import { FEEDBACK_CONFIG_BY_SCOPE } from "@/components/reader/FeedbackSheet";
import type { FeedbackScopeDto, FeedbackTypeDto } from "@/types/api/feedback";
import { cn } from "@/lib/cn";

type FeedbackItem = {
  id: string;
  feedbackScope: FeedbackScopeDto;
  feedbackType: FeedbackTypeDto;
  sentiment: string;
  content: string | null;
  status: string;
  rewardPoints: number;
  createdAt: string;
};

type ListState =
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | { phase: "loaded"; items: FeedbackItem[]; cursor: string | null; hasMore: boolean; loadingMore: boolean };

const SCOPE_LABELS: Record<string, string> = {
  analysis_result: "结果反馈",
  annotation: "标注反馈",
  sentence: "句子反馈",
  dictionary: "词典反馈",
  app: "应用反馈",
};

const STATUS_LABELS: Record<string, { label: string; className: string }> = {
  pending: { label: "待处理", className: "bg-vocab-amber/10 text-vocab-amber" },
  adopted: { label: "已采纳", className: "bg-structure-green/10 text-structure-green" },
  resolved: { label: "已解决", className: "bg-structure-green/10 text-structure-green" },
  dismissed: { label: "已关闭", className: "bg-muted/10 text-muted" },
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

function truncate(text: string, max: number): string {
  return text.length <= max ? text : text.slice(0, max) + "…";
}

function SkeletonRow() {
  return (
    <div className="flex animate-pulse items-start gap-3 rounded-note border border-hairline bg-reader-paper px-4 py-3">
      <div className="size-5 shrink-0 rounded bg-hairline" />
      <div className="flex-1 space-y-2">
        <div className="h-3.5 w-24 rounded bg-hairline" />
        <div className="h-3 w-48 rounded bg-hairline" />
      </div>
      <div className="h-5 w-12 rounded-full bg-hairline" />
    </div>
  );
}

export function MyFeedbackList() {
  const [state, setState] = useState<ListState>({ phase: "loading" });
  const [revokingId, setRevokingId] = useState<string | null>(null);
  const activeRef = useRef(true);

  useEffect(() => {
    activeRef.current = true;

    async function loadInitial() {
      const params = new URLSearchParams();
      params.set("limit", "10");

      try {
        const res = await fetch(`/api/web/feedback?${params.toString()}`);
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

    loadInitial();

    return () => {
      activeRef.current = false;
    };
  }, []);

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
    const cursor = state.cursor;

    setState((prev) => (prev.phase === "loaded" ? { ...prev, loadingMore: true } : prev));

    try {
      const params = new URLSearchParams();
      params.set("cursor", cursor);
      params.set("limit", "10");

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

  function handleRetry() {
    setState({ phase: "loading" });

    async function retryLoad() {
      const params = new URLSearchParams();
      params.set("limit", "10");

      try {
        const res = await fetch(`/api/web/feedback?${params.toString()}`);
        const data = await res.json();

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
        setState({ phase: "error", message: "网络异常，请稍后重试。" });
      }
    }

    retryLoad();
  }

  if (state.phase === "loading") {
    return (
      <div className="mt-3 space-y-2">
        <SkeletonRow />
        <SkeletonRow />
        <SkeletonRow />
      </div>
    );
  }

  if (state.phase === "error") {
    return (
      <div className="mt-3 rounded-note border border-hairline bg-reader-paper px-4 py-6 text-center">
        <p className="text-sm text-muted">{state.message}</p>
        <button
          type="button"
          onClick={handleRetry}
          className="mt-2 text-xs font-semibold text-lens-blue hover:underline"
        >
          重试
        </button>
      </div>
    );
  }

  if (state.phase === "loaded" && state.items.length === 0) {
    return (
      <div className="mt-3 rounded-note border border-hairline bg-reader-paper px-4 py-6 text-center">
        <p className="text-sm text-muted">暂无反馈记录</p>
      </div>
    );
  }

  const { items, hasMore, loadingMore } = state;

  return (
    <div className="mt-3 space-y-2">
      {items.map((item) => {
        const statusCfg = STATUS_LABELS[item.status] ?? { label: item.status, className: "bg-muted/10 text-muted" };
        const scopeLabel = SCOPE_LABELS[item.feedbackScope] ?? item.feedbackScope;
        const typeLabel = getFeedbackTypeLabel(item.feedbackScope, item.feedbackType);

        return (
          <div
            key={item.id}
            className="flex items-start gap-3 rounded-note border border-hairline bg-reader-paper px-4 py-3"
          >
            <MessageSquare className="mt-0.5 size-4 shrink-0 text-muted" />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-ink">{scopeLabel}</span>
                <span className="text-xs text-muted">·</span>
                <span className="text-xs text-muted">{typeLabel}</span>
              </div>
              {item.content ? (
                <p className="mt-1 text-xs leading-5 text-muted">
                  {truncate(item.content, 80)}
                </p>
              ) : null}
              <div className="mt-1.5 flex flex-wrap items-center gap-2">
                <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-[0.68rem] font-medium", statusCfg.className)}>
                  {statusCfg.label}
                </span>
                <span className="inline-flex items-center gap-1 text-[0.68rem] text-subtle">
                  <Clock className="size-3" />
                  {formatDate(item.createdAt)}
                </span>
                {item.rewardPoints > 0 ? (
                  <span className="inline-flex items-center gap-1 text-[0.68rem] text-vocab-amber">
                    <Award className="size-3" />
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
                className="mt-0.5 inline-flex shrink-0 items-center gap-1 rounded-md border border-hairline px-2 py-1 text-[0.68rem] font-medium text-muted transition-colors hover:border-muted hover:text-ink disabled:opacity-40"
              >
                <RotateCcw className="size-3" />
                撤回
              </button>
            ) : null}
          </div>
        );
      })}
      {hasMore ? (
        <button
          type="button"
          disabled={loadingMore}
          onClick={handleLoadMore}
          className="w-full rounded-note border border-hairline bg-reader-paper py-2.5 text-xs font-semibold text-muted transition-colors hover:text-ink disabled:opacity-40"
        >
          {loadingMore ? "加载中…" : "加载更多"}
        </button>
      ) : null}
    </div>
  );
}
