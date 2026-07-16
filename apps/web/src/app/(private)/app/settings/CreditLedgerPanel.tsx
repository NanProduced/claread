"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ArrowDown, ArrowUp, BookOpenText, CalendarDays, Check, ChevronDown, Clock, MessageCircle, RotateCcw, Search, Sparkles, Ticket } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/primitives/dropdown-menu";
import type { LedgerEntryVm } from "@/types/view/LedgerEntryVm";
import { cn } from "@/lib/cn";

type ListState =
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | {
      phase: "loaded";
      items: LedgerEntryVm[];
      cursor: string | null;
      hasMore: boolean;
      loadingMore: boolean;
      loadMoreError: string | null;
    };

type EntryFilter = "all" | "deduct" | "refund" | "grant";
type BucketFilter = "all" | "daily_free" | "bonus";

const ENTRY_TYPE_CONFIG: Record<
  string,
  { label: string; action: string; filter: EntryFilter }
> = {
  analysis_deduct: { label: "文章分析", action: "分析扣减", filter: "deduct" },
  ai_capability_deduct: { label: "Ask Claread", action: "能力扣减", filter: "deduct" },
  feedback_reward: { label: "反馈奖励", action: "奖励到账", filter: "grant" },
  daily_grant: { label: "每日发放", action: "免费点数到账", filter: "grant" },
  bonus_grant: { label: "奖励到账", action: "奖励点数到账", filter: "grant" },
  refund: { label: "积分退回", action: "失败请求已退回", filter: "refund" },
  manual_adjust: { label: "账户调整", action: "管理员调整", filter: "grant" },
};

const BUCKET_LABELS: Record<string, { text: string; className: string }> = {
  daily_free: { text: "免费", className: "bg-lens-blue-soft text-lens-blue" },
  bonus: { text: "奖励", className: "bg-vocab-amber/15 text-ink" },
};

const ENTRY_LABEL_CLASSES: Record<EntryFilter, string> = {
  all: "bg-surface text-muted-foreground",
  deduct: "bg-ink/5 text-ink",
  refund: "bg-structure-green/10 text-structure-green",
  grant: "bg-vocab-amber/15 text-ink",
};

const TYPE_FILTERS: Array<{ value: EntryFilter; label: string }> = [
  { value: "all", label: "全部" },
  { value: "deduct", label: "扣减" },
  { value: "refund", label: "退回" },
  { value: "grant", label: "奖励" },
];

const BUCKET_FILTERS: Array<{ value: BucketFilter; label: string }> = [
  { value: "all", label: "全部来源" },
  { value: "daily_free", label: "每日免费" },
  { value: "bonus", label: "奖励点数" },
];

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

function formatMonthKey(dateStr: string): string {
  const d = new Date(dateStr);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function formatMonthLabel(monthKey: string): string {
  const [year, month] = monthKey.split("-");
  return `${year}年${Number(month)}月`;
}

function formatTime(dateStr: string): string {
  const d = new Date(dateStr);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function formatPoints(value: number) {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function readString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function readMetadataString(entry: LedgerEntryVm, key: string): string | null {
  return readString(entry.metadata?.[key]);
}

function truncateText(value: string, limit = 44) {
  return value.length > limit ? `${value.slice(0, limit)}…` : value;
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

function getEntryConfig(entryType: string) {
  return ENTRY_TYPE_CONFIG[entryType] ?? {
    label: entryType,
    action: "积分变动",
    filter: "all" as EntryFilter,
  };
}

function splitDescription(description: string) {
  const [action, ...rest] = description.split(/\s*·\s*/);
  return {
    action: action?.trim() || "",
    subject: rest.join(" · ").trim(),
  };
}

function getEntryText(entry: LedgerEntryVm) {
  const config = getEntryConfig(entry.entryType);
  const parsed = splitDescription(entry.description || "");
  const capabilityCode = readMetadataString(entry, "capability_code");
  const query = readMetadataString(entry, "query");
  const mode = readMetadataString(entry, "mode");
  const userMessage = readMetadataString(entry, "user_message");
  const reason = readMetadataString(entry, "reason");
  const articleTitle = entry.articleTitle || readMetadataString(entry, "article_title");
  const fallbackSubject = parsed.subject || entry.description || config.label;

  if (entry.entryType === "analysis_deduct") {
    return {
      subject: articleTitle || fallbackSubject,
      action: "文章分析",
      detail: "生成透读解析",
      label: "分析",
      filter: config.filter,
      icon: BookOpenText,
    };
  }

  if (entry.entryType === "ai_capability_deduct" && capabilityCode === "dict_ai_lookup") {
    return {
      subject: query ? `AI 词典 · ${query}` : fallbackSubject,
      action: "AI 词典",
      detail: mode === "context_explain" ? "上下文解释" : "补全词条",
      label: "词典",
      filter: config.filter,
      icon: Search,
    };
  }

  if (entry.entryType === "ai_capability_deduct") {
    return {
      subject: articleTitle ? `Ask Claread · ${articleTitle}` : "Ask Claread",
      action: "问答能力",
      detail: userMessage ? `问题：${truncateText(userMessage)}` : "当前文章",
      label: "Ask",
      filter: config.filter,
      icon: MessageCircle,
    };
  }

  if (entry.entryType === "refund") {
    const isDictRefund = Boolean(query) || reason?.includes("dict");
    const isReaderAskRefund = Boolean(readMetadataString(entry, "thread_id")) || reason?.includes("reader_ask");
    return {
      subject: isDictRefund
        ? `AI 词典退回${query ? ` · ${query}` : ""}`
        : isReaderAskRefund
          ? "Ask Claread 退回"
          : "积分退回",
      action: "失败或未使用点数",
      detail: reason ? refundReasonLabel(reason) : "自动退回",
      label: "退回",
      filter: config.filter,
      icon: ArrowUp,
    };
  }

  if (entry.entryType === "daily_grant") {
    return {
      subject: "每日免费点数到账",
      action: "系统发放",
      detail: "当天可用",
      label: "发放",
      filter: config.filter,
      icon: Sparkles,
    };
  }

  if (entry.entryType === "bonus_grant" || entry.entryType === "feedback_reward") {
    return {
      subject: entry.entryType === "feedback_reward" ? "反馈奖励到账" : "奖励点数到账",
      action: config.action,
      detail: parsed.subject || "奖励点数",
      label: "奖励",
      filter: config.filter,
      icon: Sparkles,
    };
  }

  return {
    subject: fallbackSubject,
    action: parsed.action && parsed.action !== config.action ? parsed.action : config.action,
    detail: config.label,
    label: config.label,
    filter: config.filter,
    icon: entry.points > 0 ? ArrowUp : ArrowDown,
  };
}

function refundReasonLabel(reason: string) {
  if (reason.includes("unused_reservation")) return "未使用点数";
  if (reason.includes("clarification")) return "仅需澄清";
  if (reason.includes("failure")) return "能力失败";
  return "自动退回";
}

function getLoadedSummary(items: LedgerEntryVm[]) {
  return items.reduce(
    (acc, entry) => {
      if (entry.points < 0) acc.spend += Math.abs(entry.points);
      if (entry.points > 0) acc.credit += entry.points;
      return acc;
    },
    { spend: 0, credit: 0 },
  );
}

function SkeletonRow() {
  return (
    <div className="flex animate-pulse items-start gap-3 rounded-note border border-hairline bg-reader-paper px-4 py-4">
      <div className="size-9 shrink-0 rounded-full bg-hairline" />
      <div className="min-w-0 flex-1 space-y-2">
        <div className="h-4 w-2/5 rounded bg-hairline" />
        <div className="h-3 w-3/5 rounded bg-hairline" />
        <div className="h-3 w-1/3 rounded bg-hairline" />
      </div>
      <div className="h-5 w-14 rounded bg-hairline" />
    </div>
  );
}

function FilterButton({
  active,
  children,
  onClick,
}: {
  active: boolean;
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "focus-ring inline-flex h-6 min-w-10 items-center justify-center rounded-full px-2 !text-[13px] font-semibold leading-none transition-[background-color,color,box-shadow] duration-fast ease-out-expo",
        active
          ? "bg-ink text-reader-paper shadow-[0_1px_3px_rgba(17,17,17,0.12)]"
          : "text-muted-foreground hover:bg-surface/70 hover:text-ink",
      )}
    >
      {children}
    </button>
  );
}

function FilterGroup<T extends string>({
  label,
  items,
  value,
  onValueChange,
}: {
  label: string;
  items: ReadonlyArray<{ value: T; label: string }>;
  value: T;
  onValueChange: (value: T) => void;
}) {
  return (
    <div className="grid w-fit max-w-full grid-cols-[2.25rem_minmax(0,1fr)] items-center gap-2">
      <span className="text-[0.68rem] font-semibold text-muted-foreground">{label}</span>
      <div className="inline-flex w-fit max-w-full items-center gap-0.5 overflow-hidden rounded-full border border-hairline bg-reader-paper/74 p-0.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.52)]">
        {items.map((item) => (
          <FilterButton
            key={item.value}
            active={value === item.value}
            onClick={() => onValueChange(item.value)}
          >
            {item.label}
          </FilterButton>
        ))}
      </div>
    </div>
  );
}

function MonthMenu({
  months,
  value,
  onValueChange,
}: {
  months: string[];
  value: string;
  onValueChange: (value: string) => void;
}) {
  const label = value === "all" ? "全部月份" : formatMonthLabel(value);
  const items = ["all", ...months];

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="focus-ring inline-flex h-8 items-center gap-2 rounded-full border border-hairline bg-reader-paper/80 px-2.5 text-xs font-semibold text-ink transition-colors hover:border-ink/30 hover:bg-surface"
        >
          <CalendarDays className="size-3.5 text-muted-foreground" aria-hidden="true" />
          <span>{label}</span>
          <ChevronDown className="size-3.5 text-muted-foreground" aria-hidden="true" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-40 p-2">
        {items.map((item) => {
          const active = item === value;
          return (
            <DropdownMenuItem
              key={item}
              onSelect={() => onValueChange(item)}
              className={cn("justify-between text-sm", active && "bg-lens-blue-soft text-ink")}
            >
              <span>{item === "all" ? "全部月份" : formatMonthLabel(item)}</span>
              {active ? <Check className="size-4 text-lens-blue" aria-hidden="true" /> : null}
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function CreditLedgerPanel() {
  const [state, setState] = useState<ListState>({ phase: "loading" });
  const [entryFilter, setEntryFilter] = useState<EntryFilter>("all");
  const [bucketFilter, setBucketFilter] = useState<BucketFilter>("all");
  const [monthFilter, setMonthFilter] = useState("all");
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
          setState({ phase: "error", message: data.message || "积分明细加载失败。" });
          return;
        }

        setState({
          phase: "loaded",
          items: data.items ?? [],
          cursor: data.cursor ?? null,
          hasMore: data.hasMore ?? false,
          loadingMore: false,
          loadMoreError: null,
        });
      } catch {
        if (!activeRef.current) return;
        setState({ phase: "error", message: "网络异常，暂时无法加载积分明细。" });
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

    setState((prev) => (prev.phase === "loaded" ? { ...prev, loadingMore: true, loadMoreError: null } : prev));

    try {
      const params = new URLSearchParams();
      params.set("cursor", cursor);
      params.set("limit", "20");

      const res = await fetch(`/api/web/credit-ledger?${params.toString()}`);
      const data = await res.json();

      if (!res.ok) {
        setState((prev) => (
          prev.phase === "loaded"
            ? { ...prev, loadingMore: false, loadMoreError: data.message || "加载更多记录失败，请重试。" }
            : prev
        ));
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
          loadMoreError: null,
        };
      });
    } catch {
      setState((prev) => (
        prev.phase === "loaded"
          ? { ...prev, loadingMore: false, loadMoreError: "网络异常，加载更多记录失败。" }
          : prev
      ));
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
          setState({ phase: "error", message: data.message || "积分明细加载失败。" });
          return;
        }

        setState({
          phase: "loaded",
          items: data.items ?? [],
          cursor: data.cursor ?? null,
          hasMore: data.hasMore ?? false,
          loadingMore: false,
          loadMoreError: null,
        });
      } catch {
        setState({ phase: "error", message: "网络异常，暂时无法加载积分明细。" });
      }
    }

    retryLoad();
  }

  const loadedData = useMemo(() => {
    if (state.phase !== "loaded") {
      return {
        items: [] as LedgerEntryVm[],
        months: [] as string[],
        filteredItems: [] as LedgerEntryVm[],
        groups: [] as GroupedEntries[],
        summary: { spend: 0, credit: 0 },
      };
    }

    const months = Array.from(new Set(state.items.map((entry) => formatMonthKey(entry.createdAt))));
    const filteredItems = state.items.filter((entry) => {
      const config = getEntryConfig(entry.entryType);
      const typeMatches = entryFilter === "all" || config.filter === entryFilter;
      const bucketMatches = bucketFilter === "all" || entry.bucketType === bucketFilter;
      const monthMatches = monthFilter === "all" || formatMonthKey(entry.createdAt) === monthFilter;
      return typeMatches && bucketMatches && monthMatches;
    });

    return {
      items: state.items,
      months,
      filteredItems,
      groups: groupByDate(filteredItems),
      summary: getLoadedSummary(filteredItems),
    };
  }, [bucketFilter, entryFilter, monthFilter, state]);

  if (state.phase === "loading") {
    return (
      <div className="mt-3 space-y-2" aria-busy="true" aria-label="正在加载积分明细">
        <SkeletonRow />
        <SkeletonRow />
        <SkeletonRow />
      </div>
    );
  }

  if (state.phase === "error") {
    return (
      <div className="mt-3 rounded-note border border-hairline bg-reader-paper px-4 py-6 text-center">
        <p className="text-sm text-muted-foreground">{state.message}</p>
        <button
          type="button"
          onClick={handleRetry}
          className="focus-ring mt-3 inline-flex min-h-11 items-center rounded-control-md px-3 text-sm font-semibold text-lens-blue hover:text-ink hover:underline"
        >
          重新加载积分明细
        </button>
      </div>
    );
  }

  if (state.phase === "loaded" && state.items.length === 0) {
    return (
      <div className="mt-3 rounded-note border border-hairline bg-reader-paper px-4 py-8 text-center">
        <Ticket className="mx-auto size-8 text-muted-foreground" strokeWidth={1.2} aria-hidden="true" />
        <p className="mt-3 text-sm font-semibold text-ink">暂无积分记录</p>
        <p className="mx-auto mt-1 max-w-sm text-sm leading-6 text-muted-foreground">
          开始阅读或使用 Ask Claread 后，积分扣减、奖励到账和失败退回会记录在这里。
        </p>
      </div>
    );
  }

  const { items, filteredItems, groups, months, summary } = loadedData;
  const { hasMore, loadingMore, loadMoreError } = state;

  return (
    <div className="mt-3 space-y-5">
      <section aria-label="Ledger controls" className="rounded-note border border-hairline bg-surface/35 px-3 py-3 sm:px-4">
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-start">
          <div className="grid w-fit max-w-full gap-2">
            <FilterGroup
              label="类型"
              items={TYPE_FILTERS}
              value={entryFilter}
              onValueChange={setEntryFilter}
            />
            <FilterGroup
              label="来源"
              items={BUCKET_FILTERS}
              value={bucketFilter}
              onValueChange={setBucketFilter}
            />
          </div>

          <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between lg:min-w-[17rem] lg:flex-col lg:items-end lg:justify-start">
            <p className="text-xs leading-5 text-muted-foreground sm:order-1 lg:text-right" aria-live="polite">
              {filteredItems.length} / {items.length} 条 · 扣减 {formatPoints(summary.spend)} · 入账 {formatPoints(summary.credit)}
            </p>
            {months.length > 0 ? (
              <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground sm:order-2 lg:justify-end">
                <span>月份</span>
                <MonthMenu
                  months={months}
                  value={monthFilter}
                  onValueChange={setMonthFilter}
                />
              </div>
            ) : null}
          </div>
        </div>
      </section>

      {filteredItems.length === 0 ? (
        <div className="rounded-note border border-hairline bg-reader-paper px-4 py-8 text-center">
          <p className="text-sm font-semibold text-ink">没有符合筛选条件的记录</p>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">调整类型、来源或月份后再查看。</p>
        </div>
      ) : (
        <div className="space-y-5">
          {groups.map((group) => (
            <section key={group.dateLabel} aria-labelledby={`ledger-group-${group.dateLabel}`}>
              <h2 id={`ledger-group-${group.dateLabel}`} className="mb-2 text-sm font-semibold tracking-wide text-muted-foreground">
                {group.dateLabel}
              </h2>
              <div className="overflow-hidden rounded-note border border-hairline bg-reader-paper">
                {group.entries.map((entry, index) => {
                  const isPositive = entry.points > 0;
                  const absPoints = Math.abs(entry.points);
                  const bucket = BUCKET_LABELS[entry.bucketType];
                  const text = getEntryText(entry);
                  const EntryIcon = text.icon;

                  return (
                    <article
                      key={entry.id}
                      className={cn(
                        "grid grid-cols-[2rem_minmax(0,1fr)_auto] items-start gap-3 px-4 py-3.5",
                        index > 0 && "border-t border-hairline",
                      )}
                    >
                      <div
                        className={cn(
                          "mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full",
                          isPositive
                            ? "bg-structure-green/10 text-structure-green"
                            : "bg-ink/5 text-ink",
                        )}
                        aria-hidden="true"
                      >
                        <EntryIcon className="size-4" strokeWidth={1.8} />
                      </div>

                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold text-ink">{text.subject}</p>
                        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs leading-5 text-muted-foreground">
                          <span
                            className={cn(
                              "inline-flex items-center rounded-full px-1.5 py-0.5 text-[0.68rem] font-semibold",
                              ENTRY_LABEL_CLASSES[text.filter],
                            )}
                          >
                            {text.action}
                          </span>
                          {text.detail ? (
                            <span>{text.detail}</span>
                          ) : null}
                          {bucket ? (
                            <span
                              className={cn(
                                "inline-flex items-center rounded-full px-1.5 py-0.5 text-[0.68rem] font-semibold",
                                bucket.className,
                              )}
                            >
                              {bucket.text}
                            </span>
                          ) : null}
                        </div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-muted-foreground">
                          <span className="inline-flex items-center gap-1.5">
                            <Clock className="size-3.5" aria-hidden="true" />
                            <span>{formatTime(entry.createdAt)}</span>
                          </span>
                          <span>变动后余额 {formatPoints(entry.balanceAfter)}</span>
                        </div>
                      </div>

                      <div className="text-right">
                        <p
                          className={cn(
                            "text-base font-semibold tabular-nums",
                            isPositive ? "text-structure-green" : "text-ink",
                          )}
                          aria-label={`${isPositive ? "入账" : "扣减"} ${formatPoints(absPoints)} 点`}
                        >
                          {isPositive ? "+" : "−"}{formatPoints(absPoints)}
                        </p>
                        <p className="mt-1 text-xs text-muted-foreground">{isPositive ? "入账" : "扣减"}</p>
                      </div>
                    </article>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      )}

      <div aria-live="polite">
        {loadMoreError ? (
          <div className="mb-2 flex flex-col gap-2 rounded-note border border-error-red/20 bg-surface/50 px-4 py-3 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
            <span>{loadMoreError}</span>
            <button
              type="button"
              onClick={handleLoadMore}
              className="focus-ring inline-flex min-h-11 items-center gap-1.5 rounded-control-md text-sm font-semibold text-lens-blue hover:text-ink hover:underline"
            >
              <RotateCcw className="size-4" aria-hidden="true" />
              重试加载
            </button>
          </div>
        ) : null}
        {hasMore ? (
          <button
            type="button"
            disabled={loadingMore}
            onClick={handleLoadMore}
            className="focus-ring w-full rounded-note border border-hairline bg-reader-paper py-3 text-sm font-semibold text-muted-foreground transition-colors hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loadingMore ? "正在加载更多记录…" : "加载更多记录"}
          </button>
        ) : null}
      </div>
    </div>
  );
}
