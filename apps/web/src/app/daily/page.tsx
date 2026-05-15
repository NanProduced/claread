import { ArrowRight, CalendarDays, LogIn } from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import { ApertureWatermark, BrandLockup, ClareadStamp } from "@/components/brand/BrandMarks";
import { fetchDailyReaderList, fetchDailyReaderToday } from "@/services/api/daily-reader";
import type { DailyReaderListItem } from "@/types/view/DailyReaderVm";

const dailyRoute = "/daily" as Route;
const examplesRoute = "/examples/news-brief" as Route;

export const dynamic = "force-dynamic";

function dailyArticleRoute(articleId: string): Route {
  return `/daily/${articleId}` as Route;
}

function loginSaveRoute(articleId: string): Route {
  return `/login?next=/daily/${encodeURIComponent(articleId)}&intent=save` as Route;
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

function articleMeta(article: DailyReaderListItem): string {
  return `${article.readTimeMinutes} 分钟 · ${article.difficulty}`;
}

export default async function DailyReaderPage() {
  const [todayResult, listResult] = await Promise.all([
    fetchDailyReaderToday(),
    fetchDailyReaderList({ limit: 8 }),
  ]);
  const todayArticles = todayResult.ok ? todayResult.data : [];
  const firstArticle = todayArticles[0] ?? null;
  const todayIds = new Set(todayArticles.map((article) => article.id));
  const archiveItems = listResult.ok
    ? listResult.data.items.filter((article) => !todayIds.has(article.id)).slice(0, 5)
    : [];

  return (
    <main className="min-h-screen overflow-hidden bg-[oklch(97%_0.012_84)] text-ink">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_80%_12%,rgba(37,99,235,0.08),transparent_24rem),radial-gradient(circle_at_8%_85%,rgba(17,17,17,0.055),transparent_22rem)]" />
      <div className="paper-grain relative min-h-screen px-5 py-6 sm:px-8 lg:px-12">
        <header className="mx-auto flex max-w-7xl items-center justify-between gap-6">
          <BrandLockup href={dailyRoute} priority />
          <nav className="hidden items-center gap-7 text-sm font-semibold text-muted md:flex">
            <a className="focus-ring rounded-pill hover:text-ink" href="#archive">
              往期精选
            </a>
            <Link className="focus-ring rounded-pill hover:text-ink" href={examplesRoute}>
              公开示例
            </Link>
            <Link className="focus-ring rounded-pill text-lens-blue" href={"/login?next=/daily&intent=save" as Route}>
              登录
            </Link>
          </nav>
        </header>

        <section className="mx-auto max-w-7xl py-12 lg:py-16">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-lens-blue">
            Claread Daily
          </p>
          <h1 className="mt-4 max-w-4xl font-headline text-[2.7rem] font-semibold leading-[1.04] tracking-normal text-ink sm:text-[4.4rem]">
            一份放在门口的英文报纸
          </h1>
          <p className="mt-5 max-w-2xl text-base leading-8 text-muted sm:text-lg">
            每天一篇，不催促、不打卡。打开就读，喜欢再加入自己的阅读记录。
          </p>

          <div className="mt-12 grid gap-8 xl:grid-cols-[minmax(0,790px)_390px]">
            <article className="relative overflow-hidden rounded-[2rem] border border-hairline bg-surface-raised p-5 shadow-[0_28px_80px_rgba(35,28,18,0.14)] sm:p-7">
              <ApertureWatermark
                size={340}
                className="absolute -bottom-28 -right-24 h-80 w-80 opacity-[0.07]"
              />
              <div className="relative">
                {firstArticle ? (
                  <>
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <p className="text-xs font-semibold text-lens-blue">
                        今日精读 · {formatPublishDate(firstArticle.publishDate)}
                      </p>
                      <span className="inline-flex items-center gap-2 rounded-pill border border-hairline bg-reader-paper px-3 py-1.5 text-xs font-semibold text-muted">
                        <CalendarDays aria-hidden="true" className="h-3.5 w-3.5 text-lens-blue" />
                        每日更新
                      </span>
                    </div>
                    <h2 className="mt-6 max-w-2xl font-headline text-3xl font-semibold leading-tight tracking-normal text-ink sm:text-[2.65rem]">
                      {firstArticle.title}
                    </h2>
                    {firstArticle.subtitle ? (
                      <p className="mt-3 max-w-2xl text-sm leading-6 text-muted">
                        {firstArticle.subtitle}
                      </p>
                    ) : null}
                    <p className="mt-3 text-sm leading-6 text-muted">
                      {firstArticle.source} · {firstArticle.difficulty} · {firstArticle.readTimeMinutes} 分钟
                      {firstArticle.tags.length > 0 ? ` · ${firstArticle.tags.slice(0, 2).join(" / ")}` : ""}
                    </p>
                    <div className="mt-8 border-t border-hairline pt-7 font-reading text-[1.35rem] leading-[1.85] text-ink">
                      {firstArticle.subtitle || "今日精读已经准备好。打开文章，阅读正文、段落透读和关键表达。"}
                    </div>
                    <div className="mt-7 grid gap-4 md:grid-cols-[minmax(0,1fr)_270px]">
                      <div className="flex flex-wrap items-start gap-4">
                        <Link
                          href={dailyArticleRoute(firstArticle.id)}
                          className="focus-ring inline-flex min-h-11 items-center gap-2 rounded-pill bg-lens-blue px-5 text-sm font-semibold text-surface transition-opacity hover:opacity-90"
                        >
                          打开今日
                          <ArrowRight aria-hidden="true" className="h-4 w-4" />
                        </Link>
                        <Link
                          href={loginSaveRoute(firstArticle.id)}
                          className="focus-ring inline-flex min-h-11 items-center gap-2 rounded-pill px-1 text-sm font-semibold text-lens-blue"
                        >
                          加入我的阅读记录
                        </Link>
                      </div>
                      <div className="border-l border-hairline pl-4">
                        <p className="text-xs font-semibold text-ink">Claread 标注预览</p>
                        <p className="mt-2 text-sm leading-6 text-muted">
                          {firstArticle.tags.length > 0
                            ? `主题：${firstArticle.tags.slice(0, 3).join("、")}`
                            : "打开后查看词汇、语境和段落透读。"}
                        </p>
                      </div>
                    </div>
                  </>
                ) : (
                  <div className="flex min-h-[24rem] flex-col justify-center">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <p className="text-xs font-semibold text-lens-blue">今日精读</p>
                      <span className="inline-flex items-center gap-2 rounded-pill border border-hairline bg-reader-paper px-3 py-1.5 text-xs font-semibold text-muted">
                        <CalendarDays aria-hidden="true" className="h-3.5 w-3.5 text-lens-blue" />
                        等待发布
                      </span>
                    </div>
                    <h2 className="mt-6 max-w-2xl font-headline text-3xl font-semibold leading-tight tracking-normal text-ink sm:text-[2.65rem]">
                      今日精读暂未发布
                    </h2>
                    <p className="mt-3 max-w-xl text-sm leading-6 text-muted">
                      Web 已接入真实每日精读数据源。当前上游没有返回今日已发布文章，请稍后再来，或先阅读公开示例。
                    </p>
                    <Link
                      href={examplesRoute}
                      className="focus-ring mt-8 inline-flex min-h-11 w-fit items-center gap-2 rounded-pill bg-lens-blue px-5 text-sm font-semibold text-surface transition-opacity hover:opacity-90"
                    >
                      打开公开示例
                      <ArrowRight aria-hidden="true" className="h-4 w-4" />
                    </Link>
                  </div>
                )}
              </div>
            </article>

            <aside id="archive">
              <div className="mb-4 flex items-center justify-between gap-3">
                <h2 className="text-sm font-semibold text-ink">往期精选</h2>
                <ClareadStamp label="READ DEEPLY" className="bg-surface-warm/80" />
              </div>
              <div className="divide-y divide-hairline border-y border-hairline">
                {archiveItems.length > 0 ? archiveItems.map((article) => (
                  <Link
                    key={article.id}
                    href={dailyArticleRoute(article.id)}
                    className="focus-ring group block py-5 transition-colors hover:bg-surface-warm/70"
                  >
                    <div className="grid grid-cols-[4.7rem_minmax(0,1fr)_1.25rem] gap-3">
                      <p className="text-xs font-semibold leading-5 text-lens-blue">
                        {formatPublishDate(article.publishDate)}
                        <span className="mt-1 block text-muted">{article.tags[0] ?? article.difficulty}</span>
                      </p>
                      <div>
                        <h3 className="font-headline text-xl font-semibold leading-snug tracking-normal text-ink">
                          {article.title}
                        </h3>
                        <p className="mt-2 text-xs leading-5 text-muted">{articleMeta(article)}</p>
                      </div>
                      <ArrowRight
                        aria-hidden="true"
                        className="mt-1 h-4 w-4 text-subtle transition-transform group-hover:translate-x-0.5 group-hover:text-lens-blue"
                      />
                    </div>
                  </Link>
                )) : (
                  <p className="py-5 text-sm leading-6 text-muted">
                    暂无往期已发布文章。发布后会自动出现在这里。
                  </p>
                )}
              </div>
              <p className="mt-6 inline-flex items-center gap-2 text-xs leading-5 text-muted">
                <LogIn aria-hidden="true" className="h-3.5 w-3.5 text-lens-blue" />
                公开每日精读可完整阅读；保存资产时再登录。
              </p>
            </aside>
          </div>
        </section>
      </div>
    </main>
  );
}
