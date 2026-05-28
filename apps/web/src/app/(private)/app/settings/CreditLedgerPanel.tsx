"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowDown, ArrowUp, Clock, Ticket } from "lucide-react";

import type { LedgerEntryVm } from "@/types/view/LedgerEntryVm";
import { cn } from "@/lib/cn";

type ListState =
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | { phase: "loaded"; items: LedgerEntryVm[]; cursor: string | null; hasMore: boolean; loadingMore: boolean };

const ENTRY_TYPE_CONFIG: Record<string, { label: string }> = {
  analysis_deduct: { label: "分析扣减" },
  ai_capability_deduct: { label: "AI 能力扣减" },
  feedback_reward: { label: "反馈奖励" },
  daily_grant: { label: "每日发放" },
  bonus_grant: { label: "奖励到账" },
  refund: { label: "积分退回" },
  manual_adjust: { label: "管理员调整" },
};

const BUCKET_LABELS: Record<string, { text: string; className: string }> = {
  daily_free: { text: "每日免费", className: "bg-lens-blue-soft text-lens-blue" },
  bonus: { text: "奖励", className: "bg-vocab-amber/10 text-vocab-amber" },
};

const WEEK_DAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

function formatDateGroup(dateStr: string): string {
  const d = new Date(dateStr);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const target = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diff = Math.floor((today.getTime() - target.getTime()) / (1000 * 60 * 60 * 24));

  if (diff === 0) return "今天";
  if (diff === 1) return "昨天";
  if (diff < 7) return WEEK_DAYS[d.getDay()];
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

function formatTime(dateStr: string): string {
  const d = new Date(dateStr);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

interface GroupedEntries {
  dateLabel: string;
  entries: LedgerEntryVm[];
}

function groupByDate(entries: LedgerEntryVm[]): GroupedEntries[] {
  const groups: GroupedEntries[] = [];
  let currentLabel = "";
  let currentEntries: LedgerEntryVm[] = [];

  for (const entry of entries) {
    const label = formatDateGroup(entry.createdAt);
    if (label !== currentLabel) {
      if (currentEntries.length > 0) groups.push({ dateLabel: currentLabel, entries: currentEntries });
      currentLabel = label;
      currentEntries = [];
    }
    currentEntries.push(entry);
  }
  if (currentEntries.length > 0) groups.push({ dateLabel: currentLabel, entries: currentEntries });
  return groups;
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

export function CreditLedgerPanel() {
  const [state, setState] = useState<ListState>({ phase: "loading" });
  const activeRef = useRef(true);

  useEffect(() => {
    activeRef.current = true;

    async function loadInitial() {
      const params = new URLSearchParams();
      params.set("limit", "20");

      try {
        const res = await fetch(`/api/web/credit-ledger?${params.toString()}`);
        const data = await res.json();

        if (!activeRef.current) return;

        if (!res.ok) {
          setState({ phase: "error", message: data.message || "加载失败" });
          return;
        }

        setState({
          phase: "loaded",
          items: data.items ?? [],
          cursor: data.cursor ?? null,
          hasMore: data.hasMore ?? false,
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

  async function handleLoadMore() {
    if (state.phase !== "loaded" || !state.cursor || state.loadingMore) return;
    const cursor = state.cursor;

    setState((prev) => (prev.phase === "loaded" ? { ...prev, loadingMore: true } : prev));

    try {
      const params = new URLSearchParams();
      params.set("cursor", cursor);
      params.set("limit", "20");

      const res = await fetch(`/api/web/credit-ledger?${params.toString()}`);
      const data = await res.json();

      if (!res.ok) {
        setState((prev) => (prev.phase === "loaded" ? { ...prev, loadingMore: false } : prev));
        return;
      }

      setState((prev) => {
        if (prev.phase !== "loaded") return prev;
        return {
          ...prev,
          items: [...prev.items, ...(data.items ?? [])],
          cursor: data.cursor ?? null,
          hasMore: data.hasMore ?? false,
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
      params.set("limit", "20");

      try {
        const res = await fetch(`/api/web/credit-ledger?${params.toString()}`);
        const data = await res.json();

        if (!res.ok) {
          setState({ phase: "error", message: data.message || "加载失败" });
          return;
        }

        setState({
          phase: "loaded",
          items: data.items ?? [],
          cursor: data.cursor ?? null,
          hasMore: data.hasMore ?? false,
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
      <div className="mt-3 rounded-note border border-hairline bg-reader-paper px-4 py-8 text-center">
        <Ticket className="mx-auto size-8 text-subtle" strokeWidth={1.2} />
        <p className="mt-3 text-sm font-semibold text-ink">暂无记录</p>
        <p className="mt-1 text-xs leading-5 text-muted">开始阅读后，积分变动会记录在这里</p>
      </div>
    );
  }

  const { items, hasMore, loadingMore } = state;
  const groups = groupByDate(items);

  return (
    <div className="mt-3 space-y-4">
      {groups.map((group) => (
        <div key={group.dateLabel}>
          <p className="mb-2 text-xs font-semibold tracking-wide text-subtle">{group.dateLabel}</p>
          <div className="space-y-2">
            {group.entries.map((entry) => {
              const config = ENTRY_TYPE_CONFIG[entry.entryType] ?? {
                label: entry.entryType,
              };
              const isPositive = entry.points > 0;
              const absPoints = Math.abs(entry.points);
              const bucket = BUCKET_LABELS[entry.bucketType];

              return (
                <div
                  key={entry.id}
                  className="flex items-start gap-3 rounded-note border border-hairline bg-reader-paper px-4 py-3"
                >
                  <div
                    className={cn(
                      "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full",
                      isPositive
                        ? "bg-structure-green/10 text-structure-green"
                        : "bg-muted/10 text-muted",
                    )}
                  >
                    {isPositive ? (
                      <ArrowUp className="size-3.5" strokeWidth={2} />
                    ) : (
                      <ArrowDown className="size-3.5" strokeWidth={2} />
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-semibold text-ink">{config.label}</span>
                    </div>
                    {entry.description ? (
                      <p className="mt-0.5 text-xs leading-5 text-muted">{entry.description}</p>
                    ) : null}
                    <div className="mt-1.5 flex flex-wrap items-center gap-2">
                      {bucket ? (
                        <span
                          className={cn(
                            "inline-flex items-center rounded-full px-2 py-0.5 text-[0.68rem] font-medium",
                            bucket.className,
                          )}
                        >
                          {bucket.text}
                        </span>
                      ) : null}
                      <span className="inline-flex items-center gap-1 text-[0.68rem] text-subtle">
                        <Clock className="size-3" />
                        {formatTime(entry.createdAt)}
                      </span>
                      <span className="text-[0.68rem] text-subtle">
                        余额 {entry.balanceAfter}
                      </span>
                    </div>
                  </div>
                  <span
                    className={cn(
                      "shrink-0 text-sm font-semibold tabular-nums",
                      isPositive ? "text-structure-green" : "text-ink",
                    )}
                  >
                    {isPositive ? "+" : "−"}{absPoints}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      ))}
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
