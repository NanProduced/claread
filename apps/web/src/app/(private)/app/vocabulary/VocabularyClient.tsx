"use client";

import { ArrowRight, Check, Play, Search } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { ApertureWatermark } from "@/components/brand/BrandMarks";
import { Button } from "@/components/primitives/button";
import { ScrollArea } from "@/components/primitives/scroll-area";
import { appReaderRoute, appReviewRoute } from "@/lib/routes";
import type { VocabularyBffStatus } from "@/services/bff/vocabulary";
import type { VocabularyItemVm } from "@/types/view/VocabularyItemVm";

type FilterMode = "all" | "learning" | "mastered";

const filterOptions: Array<{ value: FilterMode; label: string }> = [
  { value: "all", label: "全部" },
  { value: "learning", label: "学习中" },
  { value: "mastered", label: "已掌握" },
];

const statusTitle: Record<VocabularyBffStatus, string> = {
  ready: "还没有生词",
  unauthenticated: "会话已过期",
  limited_debug: "调试态受限",
  upstream_unavailable: "生词本服务不可用",
  upstream_error: "读取生词本失败",
};

function statusLabel(status: VocabularyBffStatus): string {
  switch (status) {
    case "ready":
      return "已同步";
    case "unauthenticated":
      return "会话已过期";
    case "limited_debug":
      return "调试态受限";
    case "upstream_unavailable":
      return "服务暂不可用";
    case "upstream_error":
      return "读取失败";
  }
}

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString("zh-CN");
}

function normalize(value: string) {
  return value.trim().toLowerCase();
}

export function VocabularyClient({
  items,
  status,
  message,
}: {
  items: VocabularyItemVm[];
  status: VocabularyBffStatus;
  message?: string;
}) {
  const [query, setQuery] = useState("");
  const [filterMode, setFilterMode] = useState<FilterMode>("all");
  const normalizedQuery = normalize(query);

  const learningCount = useMemo(() => items.filter((item) => !item.mastered).length, [items]);
  const masteredCount = useMemo(() => items.length - learningCount, [items, learningCount]);
  const canReview = learningCount > 0;

  const filteredItems = useMemo(() => {
    return items.filter((item) => {
      const stateMatches =
        filterMode === "all" ||
        (filterMode === "learning" && !item.mastered) ||
        (filterMode === "mastered" && item.mastered);

      if (!stateMatches) {
        return false;
      }

      if (!normalizedQuery) {
        return true;
      }

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

  const hasQuery = normalizedQuery.length > 0 || filterMode !== "all";

  return (
    <div className="grid min-h-0 flex-1 gap-12 lg:gap-20 xl:gap-28 lg:grid-cols-[minmax(0,1fr)_280px] xl:grid-cols-[minmax(0,1fr)_320px]">
      <div className="flex h-full min-h-0 flex-col space-y-2 lg:py-12">
        {/* Vocabulary Header */}
        <div className="mb-6 shrink-0 flex flex-col sm:flex-row sm:items-end justify-between gap-4 pl-2 border-b border-hairline pb-5">
          <div>
            <div className="mb-2 flex items-center gap-3">
              <p className="text-[0.6rem] font-bold uppercase tracking-[0.2em] text-lens-blue">Vocabulary</p>
              <div className="h-[1px] w-8 bg-hairline" />
            </div>
            <h1 className="font-headline text-[2rem] font-semibold leading-[1] tracking-tight text-ink md:text-[2.5rem] lg:text-[3rem]">
              Vocabulary Book.
            </h1>
          </div>
          <div className="pb-1">
            {canReview ? (
              <Button asChild variant="primary-ink" className="group px-6 py-3 font-sans text-[0.82rem] font-semibold tracking-[0.08em] transition-all duration-300 border-transparent min-w-[130px]">
                <Link href={appReviewRoute} className="flex items-center justify-center">
                  <Play aria-hidden="true" className="mr-2 h-4 w-4 fill-current transition-transform duration-300 group-hover:scale-110" />
                  开始复习 {Math.min(learningCount, 20)} 个
                </Link>
              </Button>
            ) : (
              <Button variant="outline" disabled className="px-6 py-3 font-sans text-[0.82rem] font-semibold tracking-[0.08em] min-w-[130px]">
                暂无待复习
              </Button>
            )}
          </div>
        </div>

        {/* Minimal Transparent Search Row */}
        <div className="mb-8 shrink-0 flex items-center justify-between pb-2 pl-2">
          <div className="flex w-full max-w-sm items-center gap-3">
            <Search className="h-4 w-4 text-muted" />
            <input
              type="text"
              aria-label="搜索单词、释义或上下文"
              placeholder="搜索单词、释义或上下文..."
              className="w-full bg-transparent text-[0.95rem] text-ink outline-none placeholder:text-muted"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <p className="text-[0.72rem] font-semibold tracking-[0.08em] text-ink">
            共 {filteredItems.length} 个生词
          </p>
        </div>

        {/* Vocabulary Items List */}
        {filteredItems.length > 0 ? (
          <ScrollArea className="min-h-0 flex-1">
            <section className="pr-5 pb-8">
              {filteredItems.map((item) => (
                <div
                  key={item.id}
                  className="group relative flex items-start justify-between gap-6 border-b border-hairline py-8 transition-colors first:pt-2"
                >
                  <div className="relative z-10 min-w-0 pr-8">
                    <div className="mb-2.5 flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
                      <h2 className="font-headline text-[1.45rem] font-semibold leading-none tracking-tight text-ink">
                        {item.word}
                      </h2>
                      {item.phonetic && (
                        <span className="text-xs font-sans text-muted">{item.phonetic}</span>
                      )}
                      {item.partOfSpeech && (
                        <span className="rounded-pill border border-hairline/80 bg-surface/50 px-2 py-0.5 font-sans text-[0.68rem] font-semibold text-muted">
                          {item.partOfSpeech}
                        </span>
                      )}
                    </div>

                    <p className="text-[0.95rem] font-semibold leading-relaxed text-ink-soft">
                      {item.shortMeaning ?? "暂无释义"}
                    </p>

                    {item.contextSentence && (
                      <blockquote className="mt-2.5 border-l-2 border-hairline/80 pl-3 font-reading text-[0.92rem] italic leading-relaxed text-muted">
                        “ {item.contextSentence} ”
                        {item.contextTranslation && (
                          <span className="mt-1 block text-[0.85rem] not-italic text-subtle font-sans font-normal leading-normal">
                            {item.contextTranslation}
                          </span>
                        )}
                      </blockquote>
                    )}

                    <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-[0.68rem] font-semibold tracking-[0.08em] text-muted">
                      <span>{formatDate(item.createdAt)} 加入</span>
                      {item.sourceRecordId && (
                        <span className="flex items-center gap-1">
                          来自文章 · <Link href={appReaderRoute(item.sourceRecordId)} className="text-lens-blue hover:underline">查看来源</Link>
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="relative z-10 flex shrink-0 flex-col items-end gap-2 pt-1 lg:opacity-80 lg:transition-opacity lg:duration-200 lg:group-hover:opacity-100">
                    <span
                      className={`inline-flex items-center gap-1.5 rounded-pill border px-3 py-1.5 text-[0.72rem] font-semibold tracking-[0.08em] ${
                        item.mastered
                          ? "border-emerald-600/30 bg-emerald-50/15 text-emerald-800 dark:text-emerald-300"
                          : "border-vocab-amber/40 bg-vocab-amber/10 text-ink"
                      }`}
                    >
                      {item.mastered && <Check className="h-3 w-3" />}
                      {item.mastered ? "已掌握" : "学习中"}
                    </span>
                    {item.sourceRecordId && (
                      <Link
                        href={appReaderRoute(item.sourceRecordId)}
                        className="focus-ring inline-flex items-center gap-1.5 rounded-pill px-3 py-1.5 text-[0.72rem] font-semibold tracking-[0.08em] text-muted transition-colors hover:bg-surface-warm hover:text-ink"
                      >
                        回到文章
                        <ArrowRight className="h-3.5 w-3.5" />
                      </Link>
                    )}
                  </div>
                </div>
              ))}
            </section>
          </ScrollArea>
        ) : (
          <section className="relative mt-10 border-t border-hairline pt-16">
            <ApertureWatermark
              size={220}
              className="absolute bottom-[-4.5rem] right-[-1.5rem] opacity-[0.06] saturate-0"
            />
            <div className="relative max-w-[42rem]">
              <div className="mb-5 flex items-center gap-4">
                <span className="h-px w-10 bg-hairline" />
                <p className="text-[0.68rem] font-bold uppercase tracking-[0.18em] text-lens-blue">
                  {hasQuery ? "Vocabulary Filters" : "Vocabulary Awaits"}
                </p>
              </div>
              <h2 className="font-headline text-[2rem] font-semibold leading-[1.08] tracking-tight text-ink sm:text-[2.35rem]">
                {hasQuery ? "没有匹配的生词。" : statusTitle[status]}
              </h2>
              <p className="mt-4 max-w-[38ch] font-reading text-[1.02rem] leading-[1.8] text-ink-soft">
                {hasQuery
                  ? "换一个单词、释义或上下文片段再试。"
                  : "在 Reader 中阅读文章并添加生词后，单词、音标、上下文和释义都会在这里形成你的词汇资产。"}
              </p>
              {hasQuery && (
                <div className="mt-8 flex flex-wrap items-center gap-3">
                  <Button variant="outline" onClick={() => { setQuery(""); setFilterMode("all"); }} className="rounded-pill">
                    查看全部生词
                  </Button>
                </div>
              )}
            </div>
          </section>
        )}
      </div>

      {/* Hanging Bookmark Card on the Right */}
      <aside className="relative hidden min-w-0 lg:block">
        <div className="sticky top-8 px-2 pb-16">
          <div className="relative mx-auto w-full max-w-[18.5rem]">
            {/* Paper Clip Hook */}
            <div className="pointer-events-none absolute -top-6 right-5 z-30 text-muted/40">
              <svg width="20" height="42" viewBox="0 0 24 48" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 36V12a4 4 0 0 0-8 0v28a6 6 0 0 0 12 0V12a8 8 0 0 0-16 0v24" />
              </svg>
            </div>

            {/* Bookmark Body */}
            <div className="overflow-hidden rounded-t-[1.45rem] border border-hairline border-b-0 bg-[linear-gradient(180deg,color-mix(in_srgb,var(--surface)_82%,white)_0%,color-mix(in_srgb,var(--reader-paper)_88%,white)_100%)] px-7 pb-8 pt-10 shadow-[0_16px_40px_rgba(28,24,18,0.08)]">
              <div className="mb-6">
                <p className="text-[0.58rem] font-bold uppercase tracking-[0.18em] text-subtle">Claread Vocabulary</p>
                <h2 className="mt-1.5 font-headline text-[1.2rem] font-semibold leading-tight text-ink">我的生词本书签</h2>
              </div>

              <div className="space-y-8">
                {/* Stats */}
                <section>
                  <p className="font-reading text-[0.98rem] leading-[1.75] text-ink">
                    本册共记录了 <span className="font-semibold text-ink">{items.length}</span> 个生词，其中有 <span className="font-semibold text-vocab-amber">{learningCount}</span> 个在学习中，已掌握 <span className="font-semibold text-emerald-700">{masteredCount}</span> 个。
                  </p>
                  <p className="mt-2 font-sans text-[0.76rem] font-medium tracking-[0.02em] text-muted">
                    同步状态：{statusLabel(status)}
                  </p>
                  {message && <p className="mt-1 text-xs text-muted">{message}</p>}
                </section>

                {/* Filter list */}
                <section className="border-t border-hairline pt-7">
                  <div className="mb-4 flex items-center justify-between gap-3">
                    <h3 className="text-[0.62rem] font-bold uppercase tracking-[0.16em] text-subtle">
                      按学习状态浏览
                    </h3>
                    {filterMode !== "all" ? (
                      <button
                        type="button"
                        onClick={() => setFilterMode("all")}
                        className="text-[0.62rem] font-semibold tracking-[0.08em] text-lens-blue transition-colors hover:text-ink"
                      >
                        清除
                      </button>
                    ) : null}
                  </div>
                  <div className="space-y-1">
                    {filterOptions.map((option) => {
                      const active = filterMode === option.value;
                      const count =
                        option.value === "all"
                          ? items.length
                          : option.value === "learning"
                            ? learningCount
                            : masteredCount;

                      return (
                        <button
                          key={option.value}
                          type="button"
                          onClick={() => setFilterMode(option.value)}
                          className={`flex w-full items-center justify-between px-1 py-2 text-left transition-colors outline-none focus-visible:ring-1 focus-visible:ring-lens-blue ${
                            active ? "text-ink" : "text-subtle hover:text-muted"
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
                          <span className={`text-[0.7rem] ${active ? "text-muted font-medium" : "text-hairline/80"}`}>
                            {count}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </section>

                {/* Footnote tips */}
                <section className="border-t border-hairline pt-7">
                  <p className="text-[0.68rem] font-bold uppercase tracking-[0.14em] text-ink">查询历史不保存</p>
                  <p className="mt-2 text-[0.75rem] leading-relaxed text-muted">
                    点词查询只服务于即时阅读。被收录到生词本中的，是您在阅读时明确选择并保存的词条及其上下文。
                  </p>
                </section>
              </div>
            </div>

            {/* Cutout fold design at the bottom */}
            <div
              className="relative -mt-px h-[4.5rem] overflow-hidden border-x border-b border-hairline bg-[linear-gradient(180deg,color-mix(in_srgb,var(--surface)_84%,white)_0%,color-mix(in_srgb,var(--reader-paper)_92%,white)_100%)] shadow-[0_18px_32px_rgba(28,24,18,0.06)]"
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
    </div>
  );
}
