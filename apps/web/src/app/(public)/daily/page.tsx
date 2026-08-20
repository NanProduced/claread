import { ArrowRight, LogIn } from "lucide-react";
import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { ClareadStamp } from "@/components/brand/BrandMarks";
import { PublicSiteHeader } from "@/components/layout";
import { fetchDailyReaderList, fetchDailyReaderToday } from "@/services/api/daily-reader";
import { appCtaForSession, getProjectedWebSession } from "@/services/bff/session";
import { appReadRoute, dailyArticleRoute, dailyRoute, loginRoute, homeRoute } from "@/lib/routes";
import type { DailyReaderListItem } from "@/types/view/DailyReaderVm";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "每日精读",
  description: "每天一篇英文精读：原文、译文与逐段解析。一份放在门口的英文报纸。",
  openGraph: {
    title: "Claread 每日精读",
    description: "每天一篇英文精读：原文、译文与逐段解析。一份放在门口的英文报纸。",
    locale: "zh_CN",
    images: [{ url: "/brand/claread-horizontal-bilingual.png", alt: "Claread 每日精读" }],
  },
};

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

function formatLongDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
  }).format(date);
}

function articleMeta(article: DailyReaderListItem): string {
  return `${article.readTimeMinutes} 分钟 · ${article.difficulty}`;
}

/* ---------- Lead Article (Magazine Cover) ---------- */

function LeadArticle({ article, kicker }: { article: DailyReaderListItem; kicker: string }) {
  const hasCover = Boolean(article.coverImageUrl);

  return (
    <Link
      href={dailyArticleRoute(article.id)}
      className="group relative block overflow-hidden rounded-xl"
    >
      {/* Cover image area */}
      <div className="daily-lead-hero relative">
        {hasCover ? (
          <Image
            src={article.coverImageUrl!}
            alt={article.title}
            fill
            priority
            sizes="(max-width: 768px) 100vw, 60vw"
            className="object-cover transition-transform duration-700 ease-out group-hover:scale-[1.02]"
          />
        ) : (
          <div className="absolute inset-0 bg-surface" />
        )}

        {/* Flat ink overlay for text readability — no gradient */}
        <div className="absolute inset-0 bg-ink/40" />

        {/* Content on top of image */}
        <div className="absolute inset-x-0 bottom-0 p-6 sm:p-8 lg:p-10">
          <p className="text-xs font-semibold tracking-[0.18em] text-white/70">
            {kicker} · {formatLongDate(article.publishDate)}
          </p>
          <h2 className="mt-3 max-w-2xl font-headline text-[clamp(1.8rem,3.5vw,2.8rem)] font-semibold leading-[1.1] tracking-normal text-white">
            {article.title}
          </h2>
          {article.subtitle ? (
            <p className="mt-3 max-w-xl text-sm leading-6 text-white/70">
              {article.subtitle}
            </p>
          ) : null}
          <div className="mt-5 flex flex-wrap items-center gap-4">
            <span className="inline-flex items-center gap-2 text-sm font-medium text-white/90 transition-colors group-hover:text-white">
              阅读全文
              <ArrowRight aria-hidden="true" className="h-4 w-4 transition-transform group-hover:translate-x-1" />
            </span>
            <span className="text-xs text-white/50">
              {article.source} · {article.difficulty} · {article.readTimeMinutes} 分钟
            </span>
          </div>
        </div>
      </div>
    </Link>
  );
}

/* ---------- Empty state ---------- */

function EmptyLeadState() {
  return (
    <div className="flex min-h-[28rem] flex-col justify-center rounded-xl border border-hairline bg-surface/40 px-8 py-12">
      <p className="text-xs font-semibold tracking-[0.18em] text-lens-blue">
        今日精读
      </p>
      <h2 className="mt-4 max-w-xl font-headline text-[clamp(1.6rem,3vw,2.4rem)] font-semibold leading-tight tracking-normal text-ink">
        今日刊物编辑中
      </h2>
      <p className="mt-3 max-w-xl text-sm leading-6 text-muted-foreground">
        新一期内容正在编辑中。请稍后再来，或先回到首页逛逛。
      </p>
      <Link
        href={homeRoute}
        className="focus-ring mt-8 inline-flex w-fit items-center gap-2 rounded-pill bg-ink px-5 py-2.5 text-sm font-semibold text-surface transition-opacity hover:opacity-90"
      >
        返回首页
        <ArrowRight aria-hidden="true" className="h-4 w-4" />
      </Link>
    </div>
  );
}

/* ---------- Article List Item ---------- */

function ArticleListItem({ article }: { article: DailyReaderListItem }) {
  return (
    <Link
      key={article.id}
      href={dailyArticleRoute(article.id)}
      className="focus-ring group block py-5 transition-colors hover:bg-surface/70"
    >
      <div className="grid grid-cols-[4.7rem_minmax(0,1fr)_1.25rem] gap-3">
        <p className="text-xs font-semibold leading-5 text-lens-blue">
          {formatPublishDate(article.publishDate)}
          <span className="mt-1 block text-muted-foreground">{article.tags[0] ?? article.difficulty}</span>
        </p>
        <div>
          <h3 className="font-headline text-xl font-semibold leading-snug tracking-normal text-ink">
            {article.title}
          </h3>
          <p className="mt-2 text-xs leading-5 text-muted-foreground">{articleMeta(article)}</p>
        </div>
        <ArrowRight
          aria-hidden="true"
          className="mt-1 h-4 w-4 text-subtle transition-transform group-hover:translate-x-0.5 group-hover:text-lens-blue"
        />
      </div>
    </Link>
  );
}

/* ---------- Main Page ---------- */

export default async function DailyReaderPage() {
  const session = await getProjectedWebSession();
  const cta = appCtaForSession(session);
  const [todayResult, listResult] = await Promise.all([
    fetchDailyReaderToday(),
    fetchDailyReaderList({ limit: 8 }),
  ]);
  const todayArticles = todayResult.ok ? todayResult.data : [];
  const listItems = listResult.ok ? listResult.data.items : [];
  const todayIds = new Set(todayArticles.map((article) => article.id));
  const publishedArchive = listItems.filter((article) => !todayIds.has(article.id));

  // 今日为空时用最新一篇已发布文章降级做头条，往期列表不再依赖头条存在（P0-2）。
  const leadArticle = todayArticles[0] ?? null;
  const fallbackLead = leadArticle ? null : (publishedArchive[0] ?? null);
  const displayLead = leadArticle ?? fallbackLead;
  const otherTodayArticles = todayArticles.slice(1);
  const archiveItems = publishedArchive
    .filter((article) => article.id !== fallbackLead?.id)
    .slice(0, 5);

  return (
    <main className="min-h-screen overflow-hidden bg-surface-canvas text-ink">
      <div className="relative min-h-screen px-5 py-6 sm:px-8 lg:px-12">
        <PublicSiteHeader currentHref={dailyRoute} priority />

        <section className="mx-auto max-w-7xl py-12 lg:py-16">
          <p className="text-xs font-semibold tracking-[0.18em] text-lens-blue">
            Claread Daily
          </p>
          <h1 className="mt-4 max-w-3xl font-headline text-[clamp(2rem,4vw,3.6rem)] font-semibold leading-[1.06] tracking-normal text-ink">
            一份放在门口的英文报纸
          </h1>
          <p className="mt-5 max-w-2xl text-base leading-8 text-muted-foreground sm:text-lg">
            每天一篇，不催促、不打卡。打开就读，喜欢再加入自己的阅读记录。
          </p>

          {/* Lead article — magazine cover style */}
          <div className="mt-12">
            {displayLead ? (
              <div className="grid gap-8 xl:grid-cols-[minmax(0,1fr)_380px]">
                {/* Lead article with cover image */}
                <LeadArticle article={displayLead} kicker={leadArticle ? "今日精读" : "往期精选"} />

                {/* Sidebar */}
                <aside id="archive">
                  {otherTodayArticles.length > 0 && (
                    <div className="mb-8">
                      <div className="mb-4 flex items-center justify-between gap-3">
                        <h2 className="text-sm font-semibold text-lens-blue">更多今日</h2>
                      </div>
                      <div className="divide-y divide-hairline border-y border-hairline">
                        {otherTodayArticles.map((article) => (
                          <ArticleListItem key={article.id} article={article} />
                        ))}
                      </div>
                    </div>
                  )}
                  <div className="mb-4 flex items-center justify-between gap-3">
                    <h2 className="text-sm font-semibold text-ink">往期精选</h2>
                    <ClareadStamp label="READ DEEPLY" className="bg-surface/80" />
                  </div>
                  <div className="divide-y divide-hairline border-y border-hairline">
                    {archiveItems.length > 0 ? archiveItems.map((article) => (
                      <ArticleListItem key={article.id} article={article} />
                    )) : (
                      <p className="py-5 text-sm leading-6 text-muted-foreground">
                        暂无往期内容。
                      </p>
                    )}
                  </div>
                  <p className="mt-6 inline-flex items-center gap-2 text-xs leading-5 text-muted-foreground">
                    <LogIn aria-hidden="true" className="h-3.5 w-3.5 text-lens-blue" />
                    {session.state === "signed_out"
                      ? "公开每日精读可完整阅读；保存资产时再登录。"
                      : session.state === "limited_debug"
                        ? "当前是调试工作区；你可以继续浏览应用，但真实账户资产能力受限。"
                        : "当前已连接 Claread 工作区，可直接继续你的私人阅读。"}
                  </p>
                  <div className="mt-5 flex flex-wrap gap-3">
                    <Link
                      href={cta.href}
                      className="focus-ring inline-flex min-h-10 items-center rounded-pill bg-ink px-4 text-sm font-semibold text-surface transition-opacity hover:opacity-90"
                    >
                      {cta.label}
                    </Link>
                    <Link
                      href={session.state === "signed_out" ? loginRoute(appReadRoute) : appReadRoute}
                      className="focus-ring inline-flex min-h-10 items-center rounded-pill border border-hairline px-4 text-sm font-semibold text-ink transition-colors hover:border-muted"
                    >
                      进入解读工作区
                    </Link>
                  </div>
                </aside>
              </div>
            ) : (
              <EmptyLeadState />
            )}
          </div>
        </section>
      </div>
    </main>
  );
}
