import { ArrowRight, BookOpen } from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import { fetchDailyReaderList, fetchDailyReaderToday } from "@/services/api/daily-reader";
import type { DailyReaderArticle, DailyReaderListItem } from "@/types/view/DailyReaderVm";
import { AnalyzeSubmitForm } from "./AnalyzeSubmitForm";

const dailyRoute = "/daily" as Route;

export const dynamic = "force-dynamic";

function dailyArticleRoute(articleId: string): Route {
  return `/daily/${articleId}` as Route;
}

function formatPublishDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
  }).format(date);
}

function archiveMeta(article: DailyReaderListItem): string {
  return `${article.readTimeMinutes} 分钟 · ${article.difficulty}`;
}

function featureMeta(article: DailyReaderArticle | DailyReaderListItem): string {
  return `${formatPublishDate(article.publishDate)} · ${article.readTimeMinutes} 分钟 · ${article.difficulty}`;
}

export default async function PasteToReadPage() {
  const [todayResult, listResult] = await Promise.all([
    fetchDailyReaderToday(),
    fetchDailyReaderList({ limit: 6 }),
  ]);
  const todayArticle = todayResult.ok ? todayResult.data[0] ?? null : null;
  const todayIds = new Set(todayResult.ok ? todayResult.data.map((article) => article.id) : []);
  const archiveItems = listResult.ok
    ? listResult.data.items.filter((article) => !todayIds.has(article.id)).slice(0, 3)
    : [];
  const fallbackLead = !todayArticle ? archiveItems[0] ?? null : null;
  const sideArchiveItems = fallbackLead ? archiveItems.slice(1) : archiveItems.slice(0, 2);

  return (
    <main className="min-h-screen bg-[oklch(96.8%_0.012_84)] px-4 py-6 text-ink sm:px-6 lg:px-8">
      <div className="mx-auto max-w-[1340px]">
        <header className="mb-6 border-b border-hairline pb-5">
          <h1 className="font-headline text-[2.35rem] font-semibold leading-[1.04] tracking-normal text-ink sm:text-[3.25rem]">
            打开一篇文章
          </h1>
        </header>

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.78fr)_300px]">
          <div className="min-w-0">
            <AnalyzeSubmitForm />
          </div>

          <aside className="min-w-0 xl:pt-1">
            <section className="rounded-[1.5rem] border border-hairline bg-surface-raised/92 px-5 py-5 shadow-surface-quiet xl:sticky xl:top-6">
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-sm font-semibold text-ink">今日精读</h2>
                <Link
                  href={dailyRoute}
                  className="focus-ring inline-flex items-center gap-1 text-xs font-semibold text-lens-blue"
                >
                  全部
                  <ArrowRight aria-hidden="true" className="h-3.5 w-3.5" />
                </Link>
              </div>

              {todayArticle ? (
                <article className="mt-4 border-t border-hairline pt-4">
                  <p className="text-[0.72rem] font-semibold text-lens-blue">
                    {featureMeta(todayArticle)}
                  </p>
                  <Link
                    href={dailyArticleRoute(todayArticle.id)}
                    className="focus-ring mt-2 block font-headline text-[1.22rem] font-semibold leading-snug tracking-normal text-ink transition-colors hover:text-lens-blue"
                  >
                    {todayArticle.title}
                  </Link>
                  {todayArticle.subtitle ? (
                    <p className="mt-2 text-sm leading-6 text-muted">{todayArticle.subtitle}</p>
                  ) : null}
                  <Link
                    href={dailyArticleRoute(todayArticle.id)}
                    className="focus-ring mt-3 inline-flex items-center gap-1 text-sm font-semibold text-lens-blue"
                  >
                    打开精读
                    <ArrowRight aria-hidden="true" className="h-3.5 w-3.5" />
                  </Link>
                </article>
              ) : fallbackLead ? (
                <article className="mt-4 border-t border-hairline pt-4">
                  <p className="text-[0.72rem] font-semibold text-muted">今日未更新</p>
                  <Link
                    href={dailyArticleRoute(fallbackLead.id)}
                    className="focus-ring mt-2 block font-headline text-[1.22rem] font-semibold leading-snug tracking-normal text-ink transition-colors hover:text-lens-blue"
                  >
                    {fallbackLead.title}
                  </Link>
                  <p className="mt-2 text-xs leading-5 text-muted">{featureMeta(fallbackLead)}</p>
                  <Link
                    href={dailyArticleRoute(fallbackLead.id)}
                    className="focus-ring mt-3 inline-flex items-center gap-1 text-sm font-semibold text-lens-blue"
                  >
                    从往期开始
                    <ArrowRight aria-hidden="true" className="h-3.5 w-3.5" />
                  </Link>
                </article>
              ) : (
                <p className="mt-4 border-t border-hairline pt-4 text-sm leading-6 text-muted">
                  暂无可读文章。
                </p>
              )}

              {sideArchiveItems.length > 0 ? (
                <div className="mt-6 border-t border-hairline pt-4">
                  <h3 className="text-xs font-semibold text-muted">往期精选</h3>
                  <div className="mt-3 space-y-3">
                    {sideArchiveItems.map((article) => (
                      <Link
                        key={article.id}
                        href={dailyArticleRoute(article.id)}
                        className="focus-ring block rounded-[1rem] px-3 py-2.5 transition-colors hover:bg-surface-warm/78"
                      >
                        <p className="text-[0.72rem] font-semibold text-lens-blue">
                          {formatPublishDate(article.publishDate)}
                        </p>
                        <h4 className="mt-1 font-headline text-[1rem] font-semibold leading-snug tracking-normal text-ink">
                          {article.title}
                        </h4>
                        <p className="mt-1.5 text-xs leading-5 text-muted">{archiveMeta(article)}</p>
                      </Link>
                    ))}
                  </div>
                </div>
              ) : null}

              <div className="mt-6 border-t border-hairline pt-4">
                <Link
                  href={dailyRoute}
                  className="focus-ring inline-flex items-center gap-2 text-sm font-semibold text-muted transition-colors hover:text-ink"
                >
                  <BookOpen aria-hidden="true" className="h-4 w-4 text-lens-blue" />
                  浏览精读列表
                </Link>
              </div>
            </section>
          </aside>
        </div>
      </div>
    </main>
  );
}
