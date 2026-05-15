"use client";

import { ArrowRight, Check, Search, X } from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import { useMemo, useState } from "react";
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
  mock_session: "会话不可用",
  upstream_unavailable: "生词本服务不可用",
  upstream_error: "读取生词本失败",
};

function readerRoute(recordId: string): Route {
  return `/reader/${recordId}` as Route;
}

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString("zh-CN");
}

function normalize(value: string) {
  return value.trim().toLowerCase();
}

function EmptyState({
  status,
  hasQuery,
}: {
  status: VocabularyBffStatus;
  hasQuery: boolean;
}) {
  return (
    <section className="border-t border-hairline py-10">
      <div className="max-w-xl">
        <div className="flex h-11 w-11 items-center justify-center rounded-full bg-vocab-amber/20 text-ink">
          <Search aria-hidden="true" className="h-5 w-5" />
        </div>
        <h2 className="mt-4 font-headline text-2xl font-semibold text-ink">
          {hasQuery ? "没有匹配的生词" : statusTitle[status]}
        </h2>
        <p className="mt-3 text-sm leading-6 text-muted">
          {hasQuery
            ? "换一个单词、释义或上下文片段再试。"
            : "在 Reader 中把词加入生词本后，这里会显示来源句和复习状态。"}
        </p>
      </div>
    </section>
  );
}

export function VocabularyClient({
  items,
  status,
}: {
  items: VocabularyItemVm[];
  status: VocabularyBffStatus;
}) {
  const [query, setQuery] = useState("");
  const [filterMode, setFilterMode] = useState<FilterMode>("all");
  const normalizedQuery = normalize(query);
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
    <div className="space-y-5">
      <div className="flex flex-col gap-3 border-b border-hairline pb-4 lg:flex-row lg:items-center lg:justify-between">
        <label className="focus-within:border-muted flex min-h-11 flex-1 items-center gap-3 rounded-pill border border-hairline bg-surface px-4 transition-colors lg:max-w-xl">
          <Search aria-hidden="true" className="h-4 w-4 shrink-0 text-muted" />
          <span className="sr-only">搜索生词</span>
          <input
            className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-subtle"
            placeholder="搜索单词、释义或上下文"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          {normalizedQuery ? (
            <button
              type="button"
              className="focus-ring -mr-1 flex h-7 w-7 items-center justify-center rounded-full text-muted transition-colors hover:bg-reader-paper hover:text-ink"
              onClick={() => setQuery("")}
              aria-label="清空搜索"
            >
              <X aria-hidden="true" className="h-4 w-4" />
            </button>
          ) : null}
        </label>

        <div className="flex flex-wrap gap-2">
          {filterOptions.map((option) => (
            <button
              key={option.value}
              type="button"
              className={`focus-ring min-h-9 rounded-pill border px-3 text-xs font-semibold transition-colors ${
                filterMode === option.value
                  ? "border-lens-blue bg-lens-blue-soft text-lens-blue"
                  : "border-hairline bg-surface text-muted hover:border-muted hover:text-ink"
              }`}
              onClick={() => setFilterMode(option.value)}
              aria-pressed={filterMode === option.value}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {filteredItems.length > 0 ? (
        <section className="divide-y divide-hairline border-y border-hairline">
          {filteredItems.map((item) => (
            <article
              key={item.id}
              className="grid gap-4 py-5 transition-colors hover:bg-reader-paper/55 lg:grid-cols-[minmax(13rem,0.35fr)_minmax(0,1fr)_auto] lg:items-center lg:px-3"
            >
              <div className="min-w-0">
                <div className="flex flex-wrap items-baseline gap-2">
                  <h2 className="font-headline text-[1.35rem] font-semibold leading-tight text-ink">
                    {item.word}
                  </h2>
                  {item.phonetic ? (
                    <span className="text-xs text-muted">{item.phonetic}</span>
                  ) : null}
                </div>
                <div className="mt-2 flex flex-wrap gap-2 text-xs text-muted">
                  <span>{formatDate(item.createdAt)} 加入</span>
                  {item.partOfSpeech ? (
                    <span className="rounded-pill border border-hairline bg-surface px-2 py-0.5">
                      {item.partOfSpeech}
                    </span>
                  ) : null}
                </div>
              </div>

              <div className="min-w-0">
                <p className="text-sm font-semibold leading-6 text-ink-soft">
                  {item.shortMeaning ?? "暂无释义"}
                </p>
                {item.contextSentence ? (
                  <p className="mt-1 line-clamp-2 text-sm leading-6 text-muted">
                    {item.contextSentence}
                  </p>
                ) : null}
              </div>

              <div className="flex items-center gap-2 lg:justify-end">
                <span
                  className={`inline-flex min-h-8 items-center gap-1 rounded-pill border px-3 text-xs font-semibold ${
                    item.mastered
                      ? "border-structure-green/35 bg-structure-green/10 text-ink"
                      : "border-vocab-amber/45 bg-vocab-amber/15 text-ink"
                  }`}
                >
                  {item.mastered ? <Check aria-hidden="true" className="h-3.5 w-3.5" /> : null}
                  {item.mastered ? "已掌握" : "学习中"}
                </span>
                {item.sourceRecordId ? (
                  <Link
                    href={readerRoute(item.sourceRecordId)}
                    className="focus-ring inline-flex h-9 w-9 items-center justify-center rounded-pill border border-hairline bg-surface text-muted transition-colors hover:border-muted hover:text-ink"
                    aria-label={`回到 ${item.word} 的来源文章`}
                  >
                    <ArrowRight aria-hidden="true" className="h-4 w-4" />
                  </Link>
                ) : null}
              </div>
            </article>
          ))}
        </section>
      ) : (
        <EmptyState status={status} hasQuery={hasQuery} />
      )}
    </div>
  );
}
