import { BookOpen, Bookmark, Highlighter, NotebookPen, Search } from "lucide-react";
import type { Route } from "next";
import Link from "next/link";

import { getLearningAssets, type LearningAssetItemVm } from "@/services/bff/learning-assets";

const libraryRoute = "/library" as Route;

function readerHref(item: LearningAssetItemVm): Route {
  const target = new URLSearchParams({ targetKey: item.key });
  const hash = item.sentenceId ? `#reader-sentence-${item.sentenceId}` : "";
  return `/reader/${item.recordId}?${target.toString()}${hash}` as Route;
}

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString("zh-CN");
}

function assetLabel(item: LearningAssetItemVm): string {
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

function offsetLabel(item: LearningAssetItemVm): string | null {
  if (item.anchorType !== "text_range" || item.startOffset === null || item.endOffset === null) {
    return null;
  }
  return `${item.startOffset}-${item.endOffset}`;
}

function segmentSummary(item: LearningAssetItemVm): string | null {
  if (item.anchorType !== "multi_text" || item.segments.length === 0) {
    return null;
  }
  const first = sentenceLabel(item.segments[0]?.sentenceId) ?? "起始句";
  const last = sentenceLabel(item.segments.at(-1)?.sentenceId ?? null) ?? first;
  return first === last
    ? `${item.segments.length} 段 · ${first}`
    : `${item.segments.length} 段 · ${first} - ${last}`;
}

export default async function LearningAssetsPage() {
  const result = await getLearningAssets();
  const highlightedCount = result.articles.reduce(
    (sum, article) => sum + article.items.filter((item) => item.isHighlighted).length,
    0,
  );
  const favoriteCount = result.articles.reduce(
    (sum, article) => sum + article.items.filter((item) => item.isFavorited).length,
    0,
  );
  const noteCount = result.articles.reduce(
    (sum, article) => sum + article.items.filter((item) => item.note).length,
    0,
  );

  return (
    <main className="paper-grain min-h-screen px-5 py-7 text-ink sm:px-8 lg:px-10">
      <div className="mx-auto grid max-w-7xl gap-8 xl:grid-cols-[minmax(0,1fr)_320px]">
        <section className="min-w-0">
          <header className="mb-7 flex flex-col gap-5 border-b border-hairline pb-6 md:flex-row md:items-end md:justify-between">
            <div className="max-w-2xl">
              <p className="mb-3 text-xs font-semibold text-muted">学习资产</p>
              <h1 className="font-headline text-[2.15rem] font-semibold leading-tight tracking-normal text-ink sm:text-[2.65rem]">
                摘录与批注
              </h1>
              <p className="mt-3 text-sm leading-6 text-muted">
                按文章聚合收藏、高亮和笔记；Web 创建的局部选区会和整句资产放在同一篇文章下。
              </p>
              {result.message ? (
                <p className="mt-3 text-sm leading-6 text-muted">{result.message}</p>
              ) : null}
            </div>
            <Link
              href={libraryRoute}
              className="focus-ring inline-flex min-h-11 items-center justify-center gap-2 rounded-pill border border-hairline bg-surface px-5 text-sm font-semibold text-ink transition-colors hover:border-muted"
            >
              <BookOpen aria-hidden="true" className="h-4 w-4" />
              阅读记录
            </Link>
          </header>

          {result.articles.length === 0 ? (
            <section className="border-t border-hairline py-10">
              <div className="max-w-xl">
                <div className="flex h-11 w-11 items-center justify-center rounded-full bg-lens-blue-soft text-lens-blue">
                  <Search aria-hidden="true" className="h-5 w-5" />
                </div>
                <h2 className="mt-4 font-headline text-2xl font-semibold text-ink">还没有学习资产</h2>
                <p className="mt-3 text-sm leading-6 text-muted">
                  在 Reader 中收藏、划线或写笔记后，这里会按文章汇总，方便回看。
                </p>
              </div>
            </section>
          ) : (
            <div className="space-y-7">
              {result.articles.map((article) => (
                <section key={article.recordId} className="border-y border-hairline py-5">
                  <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
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
                    <div className="shrink-0 text-xs text-muted">{article.items.length} 条</div>
                  </div>

                  <div className="mt-4 divide-y divide-hairline">
                    {article.items.map((item) => (
                      <article key={item.key} className="grid gap-3 py-4 md:grid-cols-[minmax(0,1fr)_auto] md:items-start">
                        <Link href={readerHref(item)} className="focus-ring min-w-0 rounded-note px-1 py-1">
                          <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted">
                            <span className="rounded-pill border border-hairline bg-surface px-2 py-0.5">
                              {assetLabel(item)}
                            </span>
                            {item.anchorType !== "multi_text" && sentenceLabel(item.sentenceId) ? (
                              <span>{sentenceLabel(item.sentenceId)}</span>
                            ) : null}
                            {offsetLabel(item) ? <span>offset {offsetLabel(item)}</span> : null}
                            {segmentSummary(item) ? <span>{segmentSummary(item)}</span> : null}
                            <span>{formatDate(item.updatedAt)}</span>
                          </div>
                          <p className="reader-serif text-[1.15rem] leading-8 text-ink">
                            {item.text}
                          </p>
                          {item.translation ? (
                            <p className="mt-2 text-sm leading-6 text-muted">{item.translation}</p>
                          ) : null}
                          {item.anchorType === "multi_text" && item.segments.length > 0 ? (
                            <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted">
                              {item.segments.map((segment, index) => (
                                <span
                                  key={`${item.key}-segment-${segment.sentenceId}-${segment.startOffset}-${index}`}
                                  className="rounded-pill border border-hairline bg-surface px-2 py-1"
                                >
                                  {sentenceLabel(segment.sentenceId)} · {segment.startOffset}-{segment.endOffset}
                                </span>
                              ))}
                            </div>
                          ) : null}
                          {item.note ? (
                            <p className="mt-3 rounded-[8px] border border-hairline bg-reader-paper px-3 py-2 text-sm leading-6 text-ink-soft">
                              {item.note}
                            </p>
                          ) : null}
                        </Link>
                        <div className="flex flex-wrap gap-2 md:justify-end">
                          {item.isFavorited ? (
                            <span className="inline-flex min-h-8 items-center gap-1 rounded-pill border border-hairline bg-surface px-3 text-xs font-semibold text-ink-soft">
                              <Bookmark aria-hidden="true" className="h-3.5 w-3.5" />
                              收藏
                            </span>
                          ) : null}
                          {item.isHighlighted ? (
                            <span className="inline-flex min-h-8 items-center gap-1 rounded-pill border border-hairline bg-surface px-3 text-xs font-semibold text-ink-soft">
                              <Highlighter aria-hidden="true" className="h-3.5 w-3.5" />
                              高亮
                            </span>
                          ) : null}
                          {item.note ? (
                            <span className="inline-flex min-h-8 items-center gap-1 rounded-pill border border-hairline bg-surface px-3 text-xs font-semibold text-ink-soft">
                              <NotebookPen aria-hidden="true" className="h-3.5 w-3.5" />
                              笔记
                            </span>
                          ) : null}
                        </div>
                      </article>
                    ))}
                  </div>
                </section>
              ))}
            </div>
          )}
        </section>

        <aside className="space-y-5 xl:pt-[7.4rem]">
          <section className="rounded-panel border border-hairline bg-surface p-5 shadow-surface-quiet">
            <h2 className="text-sm font-semibold text-ink">资产概览</h2>
            <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
              <div>
                <dt className="text-xs text-muted">总条目</dt>
                <dd className="mt-1 font-semibold text-ink">{result.total}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted">文章</dt>
                <dd className="mt-1 font-semibold text-ink">{result.articles.length}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted">高亮</dt>
                <dd className="mt-1 font-semibold text-ink">{highlightedCount}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted">收藏</dt>
                <dd className="mt-1 font-semibold text-ink">{favoriteCount}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted">笔记</dt>
                <dd className="mt-1 font-semibold text-ink">{noteCount}</dd>
              </div>
            </dl>
          </section>
        </aside>
      </div>
    </main>
  );
}
