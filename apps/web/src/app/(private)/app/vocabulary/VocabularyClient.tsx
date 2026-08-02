"use client";

import { Play, Search } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApertureWatermark } from "@/components/brand/BrandMarks";
import { Button } from "@/components/primitives/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/primitives/alert-dialog";
import { ScrollArea } from "@/components/primitives/scroll-area";
import { Sheet, SheetContent } from "@/components/primitives/sheet";
import { VocabularyDetailPanel } from "@/components/vocabulary/VocabularyDetailPanel";
import {
  appReaderRoute,
  appReviewRoute,
} from "@/lib/routes";
import type { VocabularyBffStatus } from "@/services/bff/vocabulary";
import type { VocabularyItemVm } from "@/types/view/VocabularyItemVm";

type FilterMode = "all" | "learning" | "mastered";

const statusTitle: Record<VocabularyBffStatus, string> = {
  ready: "还没有生词",
  unauthenticated: "会话已过期",
  limited_debug: "调试态受限",
  upstream_unavailable: "生词本服务不可用",
  upstream_error: "读取生词本失败",
};

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString("zh-CN");
}

function normalize(value: string) {
  return value.trim().toLowerCase();
}

function getReviewStatusLabel(item: VocabularyItemVm): string {
  if (item.mastered) return "已掌握";
  if (!item.nextReviewAt) return "待复习";
  const next = new Date(item.nextReviewAt).getTime();
  const now = Date.now();
  if (next <= now) return "今日复习";
  const diffDays = Math.ceil((next - now) / (24 * 60 * 60 * 1000));
  return `${diffDays}天后`;
}

function sourceCountLabel(item: VocabularyItemVm): string {
  const parts: string[] = [];
  if (item.totalSourceCount > 0) parts.push(`${item.totalSourceCount} 个语境`);
  if (item.totalSourceArticleCount > 0) parts.push(`${item.totalSourceArticleCount} 篇文章`);
  return parts.join(" / ");
}

function sourceHrefForItem(item: VocabularyItemVm): string | null {
  if (item.sourceReadingRecordId) {
    return appReaderRoute(item.sourceReadingRecordId);
  }

  return null;
}

/**
 * 决定从 Vocabulary 条目（或单条 source ref）跳回 Reader 的最终 URL。
 *
 * 决策规则：
 * - 仅用 readingRecordId → /app/reader/{id}
 * - legacy recordId 不再回退到旧 Reader 页面
 * - 两者都为空 → null（不跳转）
 * - sentenceId 非空时附加为 ?sentenceId= query
 *
 * 抽出为纯函数以便测试。handleGoToSource 通过 window.location.href
 * 跳转，jsdom 下无法直接观察最终 URL，因此 URL 决策必须可独立测试。
 */
export function resolveReaderSourceHref(target: {
  readingRecordId?: string | null;
  recordId?: string | null;
  sentenceId?: string;
}): string | null {
  const baseUrl = target.readingRecordId ? appReaderRoute(target.readingRecordId) : null;

  if (!baseUrl) {
    return null;
  }

  if (target.sentenceId) {
    return `${baseUrl}?sentenceId=${target.sentenceId}`;
  }
  return baseUrl;
}

/* ---------- Bookmark Rail ---------- */

function VocabularyBookmarkRail({
  totalCount,
  dueCount,
  learningCount,
  masteredCount,
  recentItems,
  multiContextItems,
  goalFilter,
  onGoalFilterChange,
}: {
  totalCount: number;
  dueCount: number;
  learningCount: number;
  masteredCount: number;
  recentItems: VocabularyItemVm[];
  multiContextItems: VocabularyItemVm[];
  goalFilter: FilterMode;
  onGoalFilterChange: (value: FilterMode) => void;
}) {
  const statusOptions = [
    { value: "all" as const, label: "全部", count: totalCount },
    { value: "learning" as const, label: "学习中", count: learningCount },
    { value: "mastered" as const, label: "已掌握", count: masteredCount },
  ];

  return (
    <aside className="relative w-full h-full pt-8 lg:pt-12">
      <div className="sticky top-8 px-4 lg:px-8 pb-16">
        <div className="relative mx-auto w-full max-w-[18.5rem]">
          {/* Paper clip decoration */}
          <div className="pointer-events-none absolute -top-6 right-5 z-30 text-muted-foreground/40">
            <svg width="20" height="42" viewBox="0 0 24 48" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M14 36V12a4 4 0 0 0-8 0v28a6 6 0 0 0 12 0V12a8 8 0 0 0-16 0v24" />
            </svg>
          </div>

          {/* Bookmark card */}
          <div className="overflow-hidden rounded-t-[1.45rem] border border-hairline border-b-0 bg-surface px-7 pb-8 pt-10 shadow-[var(--cl-shadow-2)]">
            {/* Header */}
            <div className="mb-6">
              <p className="text-[0.58rem] font-bold tracking-[0.18em] text-subtle">Claread Vocabulary</p>
              <h2 className="mt-1.5 font-headline text-[1.2rem] font-semibold leading-tight text-ink">我的词汇书签</h2>
            </div>

            <div className="space-y-8">
              {/* Summary */}
              <section>
                <p className="font-reading text-[0.98rem] leading-[1.75] text-ink">
                  本册收录了 <span className="font-semibold text-ink">{totalCount}</span> 个生词，其中 {dueCount} 个待复习，{masteredCount} 个已掌握。
                </p>
              </section>

              {/* Browse by status */}
              <section className="border-t border-hairline pt-7">
                <div className="mb-4 flex items-center justify-between gap-3">
                  <h3 className="text-[0.62rem] font-bold tracking-[0.16em] text-subtle">
                    按状态浏览
                  </h3>
                </div>
                <div className="space-y-1">
                  {statusOptions.map((option) => {
                    const active = goalFilter === option.value;
                    return (
                      <button
                        key={option.value}
                        type="button"
                        onClick={() => onGoalFilterChange(option.value)}
                        className={`flex w-full items-center justify-between px-1 py-2 text-left transition-colors outline-none focus-visible:ring-1 focus-visible:ring-lens-blue ${
                          active ? "text-ink" : "text-subtle hover:text-muted-foreground"
                        }`}
                      >
                        <span className="inline-flex items-center gap-3">
                          <span
                            className={`h-[3px] w-[3px] rounded-full transition-colors ${
                              active ? "bg-lens-blue" : "bg-hairline"
                            }`}
                          />
                          <span className="text-[0.85rem] font-medium tracking-[0.02em]">
                            {option.label}
                          </span>
                        </span>
                        <span className={`text-[0.7rem] ${active ? "text-muted-foreground font-medium" : "text-hairline/80"}`}>
                          {option.count}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </section>

              {/* Recent additions */}
              {recentItems.length > 0 && (
                <section className="border-t border-hairline pt-7">
                  <h3 className="mb-4 text-[0.62rem] font-bold tracking-[0.16em] text-subtle">
                    最近加入
                  </h3>
                  <div className="space-y-3">
                    {recentItems.map((item) => (
                      <div key={item.id}>
                        <p className="font-headline text-[1rem] font-semibold leading-tight text-ink">
                          {item.word}
                        </p>
                        <p className="mt-1 text-[0.72rem] text-muted-foreground">
                          {formatDate(item.createdAt)} 加入
                        </p>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* Multi-context words */}
              {multiContextItems.length > 0 && (
                <section className="border-t border-hairline pt-7">
                  <h3 className="mb-4 text-[0.62rem] font-bold tracking-[0.16em] text-subtle">
                    多语境词条
                  </h3>
                  <div className="space-y-3">
                    {multiContextItems.map((item) => (
                      <div key={item.id}>
                        <p className="font-headline text-[1rem] font-semibold leading-tight text-ink">
                          {item.word}
                        </p>
                        <p className="mt-1 text-[0.72rem] text-muted-foreground">
                          {sourceCountLabel(item)}
                        </p>
                      </div>
                    ))}
                  </div>
                </section>
              )}
            </div>
          </div>

          {/* Bookmark bottom tab with aperture cutout */}
          <div
            className="relative -mt-px h-[4.5rem] overflow-hidden border-x border-b border-hairline bg-surface shadow-[var(--app-panel-shadow-quiet)]"
            style={{ clipPath: "polygon(0 0, 100% 0, 100% 100%, 66% 100%, 50% 70%, 34% 100%, 0 100%)" }}
          >
            <ApertureWatermark
              size={120}
              className="absolute bottom-[-3.5rem] right-[-2.5rem] opacity-[0.04] saturate-0"
            />
          </div>
        </div>
      </div>
    </aside>
  );
}

/* ---------- Main Component ---------- */

export function VocabularyClient({
  items: initialItems,
  status,
  message,
  dueCount: dueCountProp,
}: {
  items: VocabularyItemVm[];
  status: VocabularyBffStatus;
  message?: string;
  dueCount: number;
  learningCount: number;
  masteredCount: number;
  recentItems: VocabularyItemVm[];
  multiContextItems: VocabularyItemVm[];
}) {
  const searchParams = useSearchParams();
  const initialVocabId = searchParams.get("vocab");
  const [items, setItems] = useState<VocabularyItemVm[]>(initialItems);
  const [dueCount, setDueCount] = useState(dueCountProp);
  const [query, setQuery] = useState("");
  const [filterMode, setFilterMode] = useState<FilterMode>("all");
  const [selectedId, setSelectedId] = useState<string | null>(initialVocabId);
  const [isDesktop, setIsDesktop] = useState(true);
  const [deleteTarget, setDeleteTarget] = useState<VocabularyItemVm | null>(null);
  const urlSyncedRef = useRef(false);
  const normalizedQuery = normalize(query);

  // Derived counts from live items state
  const learningCount = useMemo(() => items.filter((i) => !i.mastered).length, [items]);
  const masteredCount = useMemo(() => items.filter((i) => i.mastered).length, [items]);

  const recentItems = useMemo(
    () =>
      [...items]
        .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime())
        .slice(0, 2),
    [items],
  );

  const multiContextItems = useMemo(
    () =>
      items
        .filter((i) => i.totalSourceCount > 1)
        .sort((a, b) => b.totalSourceCount - a.totalSourceCount)
        .slice(0, 2),
    [items],
  );

  const canReview = dueCount > 0;

  useEffect(() => {
    if (!urlSyncedRef.current) {
      urlSyncedRef.current = true;
      return;
    }
    const url = new URL(window.location.href);
    if (selectedId) {
      url.searchParams.set("vocab", selectedId);
    } else {
      url.searchParams.delete("vocab");
    }
    window.history.replaceState(null, "", url.toString());
  }, [selectedId]);

  // Initialize isDesktop from the current media query (adjust state during render)
  const [mqInitialized, setMqInitialized] = useState(false);
  if (!mqInitialized && typeof window !== "undefined") {
    setMqInitialized(true);
    setIsDesktop(window.matchMedia("(min-width: 1024px)").matches);
  }

  useEffect(() => {
    const mq = window.matchMedia("(min-width: 1024px)");
    const handler = (e: MediaQueryListEvent) => {
      setIsDesktop(e.matches);
    };
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  const filteredItems = useMemo(() => {
    return items.filter((item) => {
      const stateMatches =
        filterMode === "all" ||
        (filterMode === "learning" && !item.mastered) ||
        (filterMode === "mastered" && item.mastered);

      if (!stateMatches) return false;

      if (!normalizedQuery) return true;

      const haystack = [
        item.word,
        item.lemma,
        item.shortMeaning,
        item.contextSentence,
        item.contextTranslation,
      ]
        .filter(Boolean)
        .join("\n")
        .toLowerCase();

      return haystack.includes(normalizedQuery);
    });
  }, [filterMode, items, normalizedQuery]);

  const selectedItem = useMemo(
    () => (selectedId ? items.find((item) => item.id === selectedId) ?? null : null),
    [items, selectedId],
  );

  const hasQuery = normalizedQuery.length > 0 || filterMode !== "all";

  const handleSelectItem = useCallback(
    (item: VocabularyItemVm) => {
      setSelectedId((prevId) => (prevId === item.id ? null : item.id));
    },
    [],
  );

  const handleCloseDetail = useCallback(() => {
    setSelectedId(null);
  }, []);

  const handleToggleMastery = useCallback(async (item: VocabularyItemVm) => {
    const newStatus = item.mastered ? "learning" : "mastered";
    const wasDue = !item.mastered && item.nextReviewAt && new Date(item.nextReviewAt).getTime() <= Date.now();
    try {
      const res = await fetch(`/api/web/vocabulary/${item.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mastery_status: newStatus }),
      });
      if (!res.ok) throw new Error("Failed to update mastery");
      const data = await res.json();
      if (data.ok && data.item) {
        const updated = data.item as VocabularyItemVm;
        setItems((prev) =>
          prev.map((v) => (v.id === item.id ? updated : v)),
        );
        if (newStatus === "mastered" && wasDue) {
          setDueCount((c) => Math.max(0, c - 1));
        } else if (newStatus === "learning") {
          const isNowDue = !updated.mastered && updated.nextReviewAt && new Date(updated.nextReviewAt).getTime() <= Date.now();
          if (isNowDue) setDueCount((c) => c + 1);
        }
      }
    } catch {
      // TODO: toast error
    }
  }, []);

  const handleRequestDelete = useCallback((item: VocabularyItemVm) => {
    setDeleteTarget(item);
  }, []);

  const handleConfirmDelete = useCallback(async () => {
    if (!deleteTarget) return;
    const wasDue = !deleteTarget.mastered && deleteTarget.nextReviewAt && new Date(deleteTarget.nextReviewAt).getTime() <= Date.now();
    try {
      const res = await fetch(`/api/web/vocabulary/${deleteTarget.id}`, {
        method: "DELETE",
      });
      if (!res.ok) throw new Error("Failed to delete");
      const data = await res.json();
      if (data.ok) {
        const remaining = items.filter((v) => v.id !== deleteTarget.id);
        setItems(remaining);
        if (wasDue) setDueCount((c) => Math.max(0, c - 1));
        if (selectedId === deleteTarget.id) {
          const deletedIndex = items.findIndex((v) => v.id === deleteTarget.id);
          const nextItem =
            remaining[deletedIndex] ??
            remaining[Math.max(0, deletedIndex - 1)] ??
            null;
          setSelectedId(nextItem?.id ?? null);
        }
      }
    } catch {
      // TODO: toast error
    } finally {
      setDeleteTarget(null);
    }
  }, [deleteTarget, selectedId, items]);

  const handleGoToSource = useCallback((target: {
    readingRecordId?: string | null;
    recordId?: string | null;
    sentenceId?: string;
  }) => {
    const url = resolveReaderSourceHref(target);
    if (!url) {
      return;
    }
    window.location.href = url;
  }, []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const currentIndex = filteredItems.findIndex((v) => v.id === selectedId);
        let nextIndex: number;
        if (e.key === "ArrowDown") {
          nextIndex = currentIndex < filteredItems.length - 1 ? currentIndex + 1 : 0;
        } else {
          nextIndex = currentIndex > 0 ? currentIndex - 1 : filteredItems.length - 1;
        }
        const nextItem = filteredItems[nextIndex];
        if (nextItem) {
          setSelectedId(nextItem.id);
          document.getElementById(`vocab-item-${nextItem.id}`)?.scrollIntoView({ block: "nearest" });
        }
      } else if (e.key === "Escape") {
        setSelectedId(null);
      }
    },
    [filteredItems, selectedId],
  );



  return (
    <div className="grid min-h-0 flex-1 gap-8 lg:gap-0 lg:grid-cols-[minmax(0,1fr)_380px] xl:grid-cols-[minmax(0,1fr)_440px]">
      {/* Main column */}
      <div className="flex h-full min-h-0 flex-col space-y-2 lg:py-12 lg:pr-8 xl:pr-12">
        {/* ── Vocabulary Header ── */}
        <div className="mb-6 shrink-0 flex flex-col sm:flex-row sm:items-end justify-between gap-4 pl-2 border-b border-hairline pb-5">
          <div>
            <div className="mb-2 flex items-center gap-3">
              <p className="text-[0.6rem] font-bold tracking-[0.2em] text-lens-blue">Vocabulary</p>
              <div className="h-[1px] w-8 bg-hairline" />
            </div>
            <h1 className="font-headline text-[2rem] font-semibold leading-[1] tracking-tight text-ink md:text-[2.5rem] lg:text-[3rem]">
              Vocabulary Book.
            </h1>
            <p className="mt-3 max-w-[32ch] font-reading text-[1rem] leading-[1.75] text-muted-foreground">
              阅读中留下的重点词汇与语境。
            </p>
          </div>
          <div className="pb-1">
            {canReview ? (
              <Button asChild variant="primary-ink" className="group relative px-6 py-3 font-sans text-[0.82rem] font-semibold tracking-[0.08em] transition-all duration-300 border-transparent min-w-[130px] overflow-hidden hover:scale-[1.02] active:scale-[0.98] shadow-[var(--app-secondary-shadow)]">
                <Link href={appReviewRoute}>
                  <div className="flex items-center justify-center relative z-10">
                    <Play aria-hidden="true" className="mr-2 h-3.5 w-3.5 fill-current" />
                    开始复习 {dueCount} 个
                  </div>
                  <div className="absolute inset-0 z-0 bg-white/20 blur-md rounded-full translate-x-[-100%] group-hover:animate-[shimmer_1.5s_infinite]" />
                </Link>
              </Button>
            ) : (
              <Button variant="outline" disabled className="px-6 py-3 font-sans text-[0.82rem] font-semibold tracking-[0.08em] min-w-[130px] opacity-50">
                暂无待复习
              </Button>
            )}
          </div>
        </div>

        {/* ── Search Row ── */}
        <div className="mb-2 shrink-0 flex items-center justify-between pb-2 pl-2">
          <div className="flex w-full max-w-sm items-center gap-3">
            <Search className="h-4 w-4 text-muted-foreground" />
            <input
              type="text"
              aria-label="搜索单词、释义或来源文章"
              placeholder="搜索单词、释义或来源文章..."
              className="w-full bg-transparent text-[0.95rem] text-ink outline-none placeholder:text-muted-foreground"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <p className="shrink-0 text-[0.72rem] font-semibold tracking-[0.08em] text-muted-foreground">
            共 {items.length} 个生词 · {dueCount} 个待复习
          </p>
        </div>



        {/* ── Vocabulary List ── */}
        {filteredItems.length > 0 ? (
          <ScrollArea className="min-h-0 flex-1">
            <section className="pr-5 pb-8" role="listbox" tabIndex={0} onKeyDown={handleKeyDown}>
              {filteredItems.map((item) => {
                const isSelected = selectedId === item.id;
                const reviewLabel = getReviewStatusLabel(item);
                const sourceLabel = sourceCountLabel(item);

                return (
                  <div
                    key={item.id}
                    id={`vocab-item-${item.id}`}
                    role="option"
                    aria-selected={isSelected}
                    onClick={() => handleSelectItem(item)}
                    className={`group relative cursor-pointer border-b border-hairline/40 py-7 pl-3 pr-2 transition-colors first:pt-2 ${
                      isSelected
                        ? "bg-surface/70"
                        : "hover:bg-surface/35"
                    }`}
                  >
                    <div className="min-w-0">
                      {/* Word line: word + phonetic + POS */}
                      <div className="mb-2 flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
                        <h2 className="font-headline text-[1.45rem] font-semibold leading-none tracking-tight text-ink">
                          {item.word}
                        </h2>
                        {item.phonetic && (
                          <span className="text-xs font-sans text-muted-foreground">{item.phonetic}</span>
                        )}
                        {item.partOfSpeech && (
                          <span className="rounded-pill border border-hairline/80 bg-surface/50 px-2 py-0.5 font-sans text-[0.68rem] font-semibold text-muted-foreground">
                            {item.partOfSpeech}
                          </span>
                        )}
                      </div>

                      {/* Short meaning */}
                      <p className="text-[0.95rem] font-semibold leading-relaxed text-ink-soft">
                        {item.shortMeaning ?? "暂无释义"}
                      </p>

                      {/* Primary context — NO border-left */}
                      {item.contextSentence && (
                        <div className="mt-2.5 pl-4">
                          <p className="font-reading text-[0.92rem] italic leading-relaxed text-muted-foreground">
                            {item.contextSentence}
                          </p>
                          {item.contextTranslation && (
                            <p className="mt-1 text-[0.85rem] font-sans font-normal leading-normal text-subtle">
                              {item.contextTranslation}
                            </p>
                          )}
                        </div>
                      )}

                      {/* Metadata line */}
                      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-[0.68rem] font-semibold tracking-[0.08em] text-muted-foreground">
                        <span>{formatDate(item.createdAt)} 加入</span>
                        <span className="text-muted-foreground/30 select-none">·</span>
                        <span
                          className={
                            item.mastered
                              ? "text-structure-green"
                              : reviewLabel === "今日复习"
                                ? "text-vocab-amber"
                                : "text-muted-foreground"
                          }
                        >
                          {reviewLabel}
                        </span>
                        {sourceLabel && (
                          <>
                            <span className="text-muted-foreground/30 select-none">·</span>
                            <span>{sourceLabel}</span>
                          </>
                        )}
                        {sourceHrefForItem(item) && (
                          <>
                            <span className="text-muted-foreground/30 select-none">·</span>
                            <span
                              onClick={(e) => e.stopPropagation()}
                            >
                              <Link
                                href={sourceHrefForItem(item)!}
                                className="text-lens-blue hover:underline"
                              >
                                查看来源语境
                              </Link>
                            </span>
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </section>
          </ScrollArea>
        ) : (
          /* ── Empty State ── */
          <section className="relative mt-10 border-t border-hairline pt-16">
            <ApertureWatermark
              size={220}
              className="absolute bottom-[-4.5rem] right-[-1.5rem] opacity-[0.06] saturate-0"
            />
            <div className="relative max-w-[42rem]">
              <div className="mb-5 flex items-center gap-4">
                <span className="h-px w-10 bg-hairline" />
                <p className="text-[0.68rem] font-bold tracking-[0.18em] text-lens-blue">
                  {hasQuery ? "Vocabulary Filters" : "Vocabulary Awaits"}
                </p>
              </div>
              <h2 className="font-headline text-[2rem] font-semibold leading-[1.08] tracking-tight text-ink sm:text-[2.35rem]">
                {hasQuery ? "没有匹配的生词。" : statusTitle[status]}
              </h2>
              <p className="mt-4 max-w-[38ch] font-reading text-[1.02rem] leading-[1.8] text-ink-soft">
                {hasQuery
                  ? "换一个单词、释义或上下文片段再试。"
                  : message ?? "在 Reader 中阅读文章并添加生词后，单词、音标、上下文和释义都会在这里形成你的词汇资产。"}
              </p>
              <div className="mt-8 flex flex-wrap items-center gap-3">
                {hasQuery ? (
                  <Button variant="outline" onClick={() => { setQuery(""); setFilterMode("all"); }}>
                    查看全部生词
                  </Button>
                ) : (
                  <div className="mt-4 grid gap-5 border-t border-hairline/80 pt-6 text-[0.8rem] leading-6 text-muted-foreground sm:grid-cols-3">
                    <div>
                      <p className="text-[0.68rem] font-bold tracking-[0.14em] text-ink">词汇与语境</p>
                      <p className="mt-2">每个生词都会留下来源语境、音标和释义，方便回看。</p>
                    </div>
                    <div>
                      <p className="text-[0.68rem] font-bold tracking-[0.14em] text-ink">复习与掌握</p>
                      <p className="mt-2">间隔复习帮你把阅读中遇到的词汇真正记住。</p>
                    </div>
                    <div>
                      <p className="text-[0.68rem] font-bold tracking-[0.14em] text-ink">多语境积累</p>
                      <p className="mt-2">同一个词在不同文章中的语境，会在这里汇聚成立体的理解。</p>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </section>
        )}
      </div>

      {/* ── Right Column: Bookmark Rail or Detail Panel (Desktop) ── */}
      <div className="hidden min-w-0 lg:block h-full relative">
        {selectedItem ? (
          <div className="absolute inset-0 flex flex-col border-l border-hairline/80 overflow-hidden animate-in fade-in slide-in-from-right-8 duration-300 bg-surface shadow-[var(--app-panel-shadow-quiet)]">
            <VocabularyDetailPanel
              key={selectedItem.id}
              item={selectedItem}
              onToggleMastery={handleToggleMastery}
              onDelete={handleRequestDelete}
              onGoToSource={handleGoToSource}
              onClose={handleCloseDetail}
            />
          </div>
        ) : (
          <div className="absolute inset-0 animate-in fade-in duration-300">
            <VocabularyBookmarkRail
              totalCount={items.length}
              dueCount={dueCount}
              learningCount={learningCount}
              masteredCount={masteredCount}
              recentItems={recentItems}
              multiContextItems={multiContextItems}
              goalFilter={filterMode}
              onGoalFilterChange={setFilterMode}
            />
          </div>
        )}
      </div>

      {/* ── Mobile/Tablet Detail Sheet ── */}
      {!isDesktop && (
        <Sheet open={!!selectedId} onOpenChange={(open) => { if (!open) handleCloseDetail(); }}>
          <SheetContent side="bottom" className="p-0 h-[85vh] rounded-t-[1.5rem]">
            {selectedItem && (
              <VocabularyDetailPanel
                key={selectedItem.id}
                item={selectedItem}
                onToggleMastery={handleToggleMastery}
                onDelete={handleRequestDelete}
                onGoToSource={handleGoToSource}
                onClose={handleCloseDetail}
              />
            )}
          </SheetContent>
        </Sheet>
      )}

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={deleteTarget !== null} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
        <AlertDialogContent className="max-w-[400px]">
          <AlertDialogHeader>
            <AlertDialogTitle>删除生词</AlertDialogTitle>
            <AlertDialogDescription>
              确定要删除「{deleteTarget?.word}」吗？此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel asChild>
              <Button variant="outline">取消</Button>
            </AlertDialogCancel>
            <AlertDialogAction asChild>
              <Button variant="danger" onClick={handleConfirmDelete}>删除</Button>
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
