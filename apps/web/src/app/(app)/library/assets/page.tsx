import {
  ArrowUpRight,
  BookOpen,
  Bookmark,
  Highlighter,
  NotebookPen,
  Search,
  Sparkles,
} from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import { Button } from "@/components/primitives/button";
import { FilterBar, EmptyState, ListRow, PageHeader, SectionCard } from "@/components/composed";

import { getExcerptAssets, type ExcerptAssetItemVm } from "@/services/bff/excerpt-assets";
import type { ExcerptAssetStateDto } from "@/types/api/excerpt-assets";

const libraryRoute = "/library" as Route;
const assetFilters: Array<{
  key: ExcerptAssetStateDto;
  label: string;
  description: string;
}> = [
  { key: "all", label: "全部", description: "查看所有摘录与批注" },
  { key: "favorite", label: "收藏", description: "只看你单独收藏过的内容" },
  { key: "highlight", label: "高亮", description: "只看标记过的句子与局部选区" },
  { key: "note", label: "笔记", description: "只看你写下过笔记的内容" },
  { key: "insight", label: "解析", description: "查看系统提取的复习要点" },
];

function readerHref(item: ExcerptAssetItemVm): Route {
  const target = new URLSearchParams({ targetKey: item.key });
  const hash = item.sentenceId ? `#reader-sentence-${item.sentenceId}` : "";
  return `/reader/${item.recordId}?${target.toString()}${hash}` as Route;
}

function assetFilterHref(filter: ExcerptAssetStateDto): Route {
  return filter === "all"
    ? ("/library/assets" as Route)
    : (`/library/assets?assetState=${filter}` as Route);
}

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString("zh-CN");
}

function trimText(value: string | null | undefined, maxLength: number): string | null {
  if (!value) {
    return null;
  }
  const normalized = value.replace(/\s+/g, " ").trim();
  if (!normalized) {
    return null;
  }
  return normalized.length > maxLength
    ? `${normalized.slice(0, maxLength)}...`
    : normalized;
}

function assetLabel(item: ExcerptAssetItemVm): string {
  if (item.anchorType === "multi_text") {
    return "跨句选区";
  }
  if (item.anchorType === "text_range") {
    return "局部选区";
  }
  return "整句";
}

function sentenceLabel(sentenceId: string | null): string | null {
  if (!sentenceId) {
    return null;
  }
  return `第 ${sentenceId.replace(/^s/i, "")} 句`;
}

function segmentSummary(item: ExcerptAssetItemVm): string | null {
  if (item.anchorType !== "multi_text" || item.segments.length === 0) {
    return null;
  }
  const first = sentenceLabel(item.segments[0]?.sentenceId) ?? "起始句";
  const last = sentenceLabel(item.segments.at(-1)?.sentenceId ?? null) ?? first;
  return first === last
    ? `${item.segments.length} 段 · ${first}`
    : `${item.segments.length} 段 · ${first} - ${last}`;
}

function articleAnchorId(key: string) {
  return `excerpt-article-${key.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

function articlePreview(item: ExcerptAssetItemVm): string | null {
  return trimText(item.note ?? item.translation ?? item.selectedText, 72);
}

function itemStatusSummary(item: ExcerptAssetItemVm): string {
  const statuses: string[] = [];
  if (item.isFavorited) {
    statuses.push("已收藏");
  }
  if (item.isHighlighted) {
    statuses.push("有高亮");
  }
  if (item.isNoted) {
    statuses.push("有笔记");
  }
  if (item.insights.length > 0) {
    statuses.push(`${item.insights.length} 条解析`);
  }
  return statuses.join(" · ");
}

function normalizeAssetState(
  value: string | string[] | undefined,
): ExcerptAssetStateDto {
  const normalized = Array.isArray(value) ? value[0] : value;
  return assetFilters.some((filter) => filter.key === normalized)
    ? (normalized as ExcerptAssetStateDto)
    : "all";
}

export default async function LearningAssetsPage({
  searchParams,
}: {
  searchParams?: Promise<{ assetState?: string | string[] }>;
}) {
  const resolvedSearchParams = searchParams ? await searchParams : undefined;
  const activeFilter = normalizeAssetState(resolvedSearchParams?.assetState);
  const activeFilterMeta =
    assetFilters.find((filter) => filter.key === activeFilter) ?? assetFilters[0];
  const result = await getExcerptAssets({
    assetState: activeFilter,
  });
  const highlightedCount = result.articles.reduce(
    (sum, article) => sum + article.items.filter((item) => item.isHighlighted).length,
    0,
  );
  const favoriteCount = result.articles.reduce(
    (sum, article) => sum + article.items.filter((item) => item.isFavorited).length,
    0,
  );
  const noteCount = result.articles.reduce(
    (sum, article) => sum + article.items.filter((item) => item.isNoted).length,
    0,
  );
  const insightCount = result.articles.reduce(
    (sum, article) => sum + article.items.filter((item) => item.insights.length > 0).length,
    0,
  );

  const hasAssets = result.totalAssets > 0;

  return (
    <main className="paper-grain min-h-screen px-4 py-6 text-ink sm:px-8 lg:px-10">
      <div className="mx-auto max-w-7xl">
        <PageHeader
          eyebrow="阅读记录 / 摘录与批注"
          title="摘录与批注"
          description="这里收起的是你在阅读里主动留下的阅读痕迹。它按文章归档，方便你回看重点句子、笔记和系统提炼出的复习要点。"
          actions={
            <Button asChild variant="outline">
              <Link href={libraryRoute}>
                <BookOpen aria-hidden="true" className="h-4 w-4" />
                返回阅读记录
              </Link>
            </Button>
          }
          className="mb-0 border-b-0 pb-0"
        />
        <div className="border-b border-hairline pb-7">
          <FilterBar
            className="space-y-4"
            activeValue={activeFilter}
            items={assetFilters.map((filter) => ({
              value: filter.key,
              label: filter.label,
              href: assetFilterHref(filter.key),
            }))}
            summary={
              <>
                <div className="flex flex-wrap items-center gap-3 text-sm leading-6">
                  <span className="rounded-pill border border-hairline bg-surface px-3 py-1.5 font-semibold text-ink shadow-surface-quiet">
                    {result.totalGroups} 篇文章 · {result.totalAssets} 条摘录
                  </span>
                  <span className="text-muted">
                    收藏 {favoriteCount} · 高亮 {highlightedCount} · 笔记 {noteCount} · 解析 {insightCount}
                  </span>
                </div>
                <p className="text-xs text-muted">
                  当前视图：{activeFilterMeta.label}，{activeFilterMeta.description}
                </p>
              </>
            }
          />

          {activeFilter === "insight" ? (
            <div className="mt-4 rounded-note border border-lens-blue/20 bg-lens-blue-soft px-4 py-3 text-sm leading-6 text-ink-soft">
              “解析”是系统从句子里提取出的复习要点，用来帮助你回看重点，不等同于你手动保存的收藏或笔记。
            </div>
          ) : null}

          {result.message ? (
            <div className="mt-4 rounded-note border border-hairline bg-surface px-4 py-3 text-sm leading-6 text-muted shadow-surface-quiet">
              {result.message}
            </div>
          ) : null}
        </div>

        {!hasAssets ? (
          <EmptyState
            icon={Search}
            title={activeFilter === "all" ? "还没有摘录与批注" : `${activeFilterMeta.label} 里还没有内容`}
            description={
              activeFilter === "all"
                ? "在 Reader 里收藏、划线或写笔记后，这里会按文章归档，方便你回看和继续读。"
                : "换一个筛选继续看，或者回到正文里继续留下新的阅读痕迹。"
            }
            action={
              activeFilter !== "all" ? (
                <Button asChild variant="outline">
                  <Link href={assetFilterHref("all")}>查看全部摘录</Link>
                </Button>
              ) : undefined
            }
            className="py-12"
          />
        ) : (
          <div className="mt-8 grid gap-10 xl:grid-cols-[18rem_minmax(0,1fr)]">
            <aside className="xl:sticky xl:top-6 xl:self-start">
              <div className="px-1">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-semibold text-ink">文章索引</h2>
                  <span className="text-xs text-muted">{result.totalGroups} 篇</span>
                </div>
                <SectionCard className="mt-4 overflow-hidden p-0">
                  {result.articles.map((article) => {
                    const articleHighlights = article.items.filter((item) => item.isHighlighted).length;
                    const articleNotes = article.items.filter((item) => item.isNoted).length;
                    const articleFavorites = article.items.filter((item) => item.isFavorited).length;
                    const previewItem =
                      article.items.find((item) => item.note) ?? article.items[0];
                    return (
                      <a
                        key={article.key}
                        href={`#${articleAnchorId(article.key)}`}
                        className="focus-ring block border-b border-hairline bg-surface px-4 py-4 transition-colors last:border-b-0 hover:bg-reader-paper/70"
                      >
                        <p className="line-clamp-2 text-sm font-semibold leading-6 text-ink">
                          {article.title}
                        </p>
                        {article.subtitle ? (
                          <p className="mt-1 line-clamp-1 text-xs leading-5 text-muted">
                            {article.subtitle}
                          </p>
                        ) : null}
                        {previewItem ? (
                          <p className="mt-2 line-clamp-2 text-xs leading-5 text-muted">
                            {articlePreview(previewItem)}
                          </p>
                        ) : null}
                        <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted">
                          <span>{article.assetCount} 条摘录</span>
                          <span>{articleFavorites} 收藏</span>
                          <span>{articleHighlights} 高亮</span>
                          <span>{articleNotes} 笔记</span>
                        </div>
                      </a>
                    );
                  })}
                </SectionCard>
              </div>
            </aside>

            <section className="min-w-0">
              <div className="space-y-8">
                {result.articles.map((article) => (
                  <SectionCard
                    id={articleAnchorId(article.key)}
                    key={article.key}
                    className="scroll-mt-6 px-4 py-5 sm:px-6"
                  >
                    <div className="flex flex-col gap-3 border-b border-hairline pb-4 md:flex-row md:items-start md:justify-between">
                      <div className="min-w-0">
                        <h2 className="font-headline text-[1.55rem] font-semibold leading-snug text-ink">
                          {article.title}
                        </h2>
                        {article.subtitle ? (
                          <p className="mt-2 line-clamp-2 max-w-3xl text-sm leading-6 text-muted">
                            {article.subtitle}
                          </p>
                        ) : null}
                      </div>
                      <div className="shrink-0 text-xs leading-5 text-muted md:text-right">
                        <p>{article.assetCount} 条摘录</p>
                        <p>
                          收藏 {article.items.filter((item) => item.isFavorited).length} · 高亮{" "}
                          {article.items.filter((item) => item.isHighlighted).length} · 笔记{" "}
                          {article.items.filter((item) => item.isNoted).length}
                        </p>
                      </div>
                    </div>

                    <div className="mt-4 divide-y divide-hairline/80">
                      {article.items.map((item) => (
                        <ListRow
                          key={item.key}
                          className="lg:grid-cols-[minmax(0,1fr)_auto] lg:items-start"
                          titleClassName="reader-serif text-[1.12rem] font-normal leading-8"
                          title={item.selectedText}
                          description={
                            <div className="max-w-[72ch]">
                              {item.translation ? (
                                <p className="text-sm leading-6 text-muted">{item.translation}</p>
                              ) : null}
                              {itemStatusSummary(item) ? <p className="mt-3 text-xs leading-5 text-muted">{itemStatusSummary(item)}</p> : null}
                              {item.note ? (
                                <div className="mt-4 rounded-note border border-hairline bg-reader-paper px-3 py-3">
                                  <p className="text-xs font-semibold text-muted">笔记</p>
                                  <p className="mt-2 text-sm leading-6 text-ink-soft">{item.note}</p>
                                </div>
                              ) : null}
                              {item.insights.length > 0 ? (
                                <div className="mt-4">
                                  <p className="text-xs font-semibold text-muted">解析要点</p>
                                  <div className="mt-2 flex flex-wrap gap-2 text-xs">
                                    {item.insights.slice(0, 3).map((insight) => (
                                      <span
                                        key={`${item.key}-${insight.id}`}
                                        className="rounded-pill border border-hairline bg-lens-blue-soft px-2.5 py-1 text-ink-soft"
                                      >
                                        {insight.label} · {insight.title}
                                      </span>
                                    ))}
                                  </div>
                                </div>
                              ) : null}
                            </div>
                          }
                          meta={
                            <>
                              <span className="rounded-pill border border-hairline bg-reader-paper px-2 py-1 font-semibold text-ink-soft">
                                {assetLabel(item)}
                              </span>
                              {item.anchorType !== "multi_text" && sentenceLabel(item.sentenceId) ? <span>{sentenceLabel(item.sentenceId)}</span> : null}
                              {segmentSummary(item) ? <span>{segmentSummary(item)}</span> : null}
                              <span>{formatDate(item.updatedAt)}</span>
                            </>
                          }
                          trailing={
                            <div className="flex flex-col items-start gap-3 lg:items-end">
                              <Button asChild variant="secondary" size="sm">
                                <Link href={readerHref(item)}>
                                  回到原文
                                  <ArrowUpRight aria-hidden="true" className="h-3.5 w-3.5" />
                                </Link>
                              </Button>
                              <div className="flex flex-wrap gap-2 text-xs text-muted lg:justify-end">
                                {item.isFavorited ? (
                                  <span className="inline-flex min-h-8 items-center gap-1 rounded-pill border border-hairline bg-reader-paper px-3">
                                    <Bookmark aria-hidden="true" className="h-3.5 w-3.5" />
                                    收藏
                                  </span>
                                ) : null}
                                {item.isHighlighted ? (
                                  <span className="inline-flex min-h-8 items-center gap-1 rounded-pill border border-hairline bg-reader-paper px-3">
                                    <Highlighter aria-hidden="true" className="h-3.5 w-3.5" />
                                    高亮
                                  </span>
                                ) : null}
                                {item.isNoted ? (
                                  <span className="inline-flex min-h-8 items-center gap-1 rounded-pill border border-hairline bg-reader-paper px-3">
                                    <NotebookPen aria-hidden="true" className="h-3.5 w-3.5" />
                                    笔记
                                  </span>
                                ) : null}
                                {item.insights.length > 0 ? (
                                  <span className="inline-flex min-h-8 items-center gap-1 rounded-pill border border-hairline bg-reader-paper px-3">
                                    <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
                                    解析
                                  </span>
                                ) : null}
                              </div>
                            </div>
                          }
                        />
                      ))}
                    </div>
                  </SectionCard>
                ))}
              </div>
            </section>
          </div>
        )}
      </div>
    </main>
  );
}
