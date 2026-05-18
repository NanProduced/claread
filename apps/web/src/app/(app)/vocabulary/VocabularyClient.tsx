"use client";

import { ArrowRight, Check, Search } from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import { useMemo, useState } from "react";
import { Button } from "@/components/primitives/button";
import { IconButton } from "@/components/primitives/icon-button";
import { EmptyState } from "@/components/composed/empty-state";
import { FilterBar } from "@/components/composed/filter-bar";
import { ListRow } from "@/components/composed/list-row";
import { SearchField } from "@/components/composed/search-field";
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
      <SearchField
        label="搜索生词"
        placeholder="搜索单词、释义或上下文"
        value={query}
        onValueChange={setQuery}
      />

      <FilterBar
        items={filterOptions.map((option) => ({ value: option.value, label: option.label }))}
        activeValue={filterMode}
        onValueChange={(nextValue) => setFilterMode(nextValue as FilterMode)}
      />

      {filteredItems.length > 0 ? (
        <section className="overflow-hidden rounded-panel border border-hairline bg-[linear-gradient(180deg,rgba(255,255,255,0.96),rgba(251,250,246,0.985))] shadow-surface-quiet">
          {filteredItems.map((item) => (
            <ListRow
              key={item.id}
              className="lg:items-start lg:px-6"
              contentClassName="lg:grid lg:grid-cols-[minmax(12rem,14rem)_minmax(0,1fr)] lg:items-start lg:gap-x-8"
              titleClassName="text-[1.7rem] leading-none lg:pr-3"
              title={
                <div className="flex flex-wrap items-baseline gap-2">
                  <span>{item.word}</span>
                  {item.phonetic ? <span className="text-xs text-muted">{item.phonetic}</span> : null}
                </div>
              }
              description={
                <div>
                  <p className="text-sm font-semibold leading-6 text-ink-soft">{item.shortMeaning ?? "暂无释义"}</p>
                  {item.contextSentence ? <p className="mt-1 line-clamp-2 text-sm leading-6 text-muted">{item.contextSentence}</p> : null}
                </div>
              }
              bodyClassName="lg:mt-0"
              meta={
                <>
                  <span>{formatDate(item.createdAt)} 加入</span>
                  {item.partOfSpeech ? (
                    <span className="rounded-pill border border-hairline bg-surface px-2 py-0.5">{item.partOfSpeech}</span>
                  ) : null}
                </>
              }
              trailing={
                <>
                  <Button
                    variant="quiet"
                    size="sm"
                    className={
                      item.mastered
                        ? "border-structure-green/35 bg-structure-green/10 text-ink hover:bg-structure-green/15"
                        : "border-vocab-amber/45 bg-vocab-amber/15 text-ink hover:bg-vocab-amber/20"
                    }
                  >
                    {item.mastered ? <Check aria-hidden="true" className="h-3.5 w-3.5" /> : null}
                    {item.mastered ? "已掌握" : "学习中"}
                  </Button>
                  {item.sourceRecordId ? (
                    <IconButton asChild aria-label={`回到 ${item.word} 的来源文章`}>
                      <Link href={readerRoute(item.sourceRecordId)}>
                        <ArrowRight aria-hidden="true" className="h-4 w-4" />
                      </Link>
                    </IconButton>
                  ) : null}
                </>
              }
            />
          ))}
        </section>
      ) : (
        <EmptyState
          icon={Search}
          title={hasQuery ? "没有匹配的生词" : statusTitle[status]}
          description={
            hasQuery ? "换一个单词、释义或上下文片段再试。" : "在 Reader 中把词加入生词本后，这里会显示来源句和复习状态。"
          }
        />
      )}
    </div>
  );
}
