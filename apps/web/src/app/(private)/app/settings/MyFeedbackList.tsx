"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Award,
  Clock,
  RotateCcw,
  Sparkles,
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

type PlatformFilter = "all" | FeedbackClientPlatformDto;
type StatusFilter = "all" | "pending" | "adopted" | "resolved" | "dismissed";

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
  const [platformFilter, setPlatformFilter] = useState<PlatformFilter>("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const activeRef = useRef(true);

  const querySuffix = useMemo(() => {
    const params = new URLSearchParams();
    params.set("limit", "10");
    if (platformFilter !== "all") params.set("client_platform", platformFilter);
    if (statusFilter !== "all") params.set("status", statusFilter);
    return params.toString();
  }, [platformFilter, statusFilter]);

  useEffect(() => {
    activeRef.current = true;

    async function loadInitial() {
      setState({ phase: "loading" });
      try {
        const res = await fetch(`/api/web/feedback?${querySuffix}`);
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
  }, [querySuffix]);

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
      const params = new URLSearchParams(querySuffix);
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
      <div className="mt-3 space-y-2">
        <FilterBar
          platformFilter={platformFilter}
          statusFilter={statusFilter}
          onPlatformChange={setPlatformFilter}
          onStatusChange={setStatusFilter}
        />
        <SkeletonRow />
        <SkeletonRow />
        <SkeletonRow />
      </div>
    );
  }

  if (state.phase === "error") {
    return (
      <div className="mt-3 space-y-3">
        <FilterBar
          platformFilter={platformFilter}
          statusFilter={statusFilter}
          onPlatformChange={setPlatformFilter}
          onStatusChange={setStatusFilter}
        />
        <div className="rounded-note border border-hairline bg-reader-paper px-4 py-6 text-center">
          <p className="text-sm text-muted">{state.message}</p>
        </div>
      </div>
    );
  }

  const { items, hasMore, loadingMore } = state;

  return (
    <div className="mt-3 space-y-3">
      <FilterBar
        platformFilter={platformFilter}
        statusFilter={statusFilter}
        onPlatformChange={setPlatformFilter}
        onStatusChange={setStatusFilter}
      />

      {items.length === 0 ? (
        <div className="rounded-note border border-hairline bg-reader-paper px-4 py-6 text-center">
          <p className="text-sm text-muted">暂无反馈记录</p>
        </div>
      ) : null}

      {items.map((item) => {
        const statusCfg = STATUS_LABELS[item.status] ?? { label: item.status, className: "bg-muted/10 text-muted" };
        const scopeLabel = SCOPE_LABELS[item.feedbackScope] ?? item.feedbackScope;
        const typeLabel = getFeedbackTypeLabel(item.feedbackScope, item.feedbackType);
        const platformLabel = PLATFORM_LABELS[item.clientPlatform];
        const surfaceLabel = item.clientSurface ? SURFACE_LABELS[item.clientSurface] ?? item.clientSurface : null;
        const summary = item.contextSummary || item.content;

        return (
          <div
            key={item.id}
            className="flex items-start gap-3 rounded-note border border-hairline bg-reader-paper px-4 py-3"
          >
            <span className="mt-0.5 inline-flex size-8 shrink-0 items-center justify-center rounded-full bg-[radial-gradient(circle_at_30%_25%,#FFF4D7_0%,#E8C16D_48%,#C08E3B_100%)] text-stone-900 shadow-[0_10px_18px_rgba(139,100,38,0.16)]">
              <Sparkles className="size-4" strokeWidth={2.1} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-semibold text-ink">{scopeLabel}</span>
                <span className="text-xs text-muted">·</span>
                <span className="text-xs text-muted">{typeLabel}</span>
                <span className="inline-flex items-center rounded-full bg-secondary px-2 py-0.5 text-[0.68rem] text-muted">
                  {platformLabel}
                </span>
                {surfaceLabel ? (
                  <span className="inline-flex items-center rounded-full bg-secondary px-2 py-0.5 text-[0.68rem] text-muted">
                    {surfaceLabel}
                  </span>
                ) : null}
              </div>
              {summary ? (
                <p className="mt-1 text-xs leading-5 text-muted">
                  {truncate(summary, 88)}
                </p>
              ) : null}
              {item.content && item.contextSummary && item.content !== item.contextSummary ? (
                <p className="mt-1 text-[0.7rem] leading-5 text-subtle">
                  备注：{truncate(item.content, 88)}
                </p>
              ) : null}
              {item.resolutionNote ? (
                <div className="mt-2 rounded-xl border border-hairline/70 bg-surface-warm/65 px-3 py-2">
                  <p className="text-[0.72rem] leading-5 text-muted">
                    处理说明：{item.resolutionNote}
                  </p>
                </div>
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

function FilterBar({
  platformFilter,
  statusFilter,
  onPlatformChange,
  onStatusChange,
}: {
  platformFilter: PlatformFilter;
  statusFilter: StatusFilter;
  onPlatformChange: (value: PlatformFilter) => void;
  onStatusChange: (value: StatusFilter) => void;
}) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {[
          { value: "all" as const, label: "全部" },
          { value: "web" as const, label: "Web" },
          { value: "wechat_miniprogram" as const, label: "小程序" },
        ].map((tab) => (
          <button
            key={tab.value}
            type="button"
            onClick={() => onPlatformChange(tab.value)}
            className={cn(
              "rounded-full border px-3 py-1.5 text-xs font-medium transition-colors",
              platformFilter === tab.value
                ? "border-lens-blue/30 bg-lens-blue-soft/60 text-lens-blue"
                : "border-hairline bg-reader-paper text-muted hover:text-ink",
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        {[
          { value: "all" as const, label: "全部状态" },
          { value: "pending" as const, label: "待处理" },
          { value: "adopted" as const, label: "已采纳" },
          { value: "resolved" as const, label: "已解决" },
          { value: "dismissed" as const, label: "已关闭" },
        ].map((tab) => (
          <button
            key={tab.value}
            type="button"
            onClick={() => onStatusChange(tab.value)}
            className={cn(
              "rounded-full border px-3 py-1.5 text-xs font-medium transition-colors",
              statusFilter === tab.value
                ? "border-lens-blue/30 bg-lens-blue-soft/60 text-lens-blue"
                : "border-hairline bg-reader-paper text-muted hover:text-ink",
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>
    </div>
  );
}
