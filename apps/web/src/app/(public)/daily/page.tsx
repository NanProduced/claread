import { ArrowRight, LogIn } from "lucide-react";
import type { Metadata } from "next";
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

/* ---------- Lead Article (Editorial A1) ---------- */

function LeadArticle({ article, kicker }: { article: DailyReaderListItem; kicker: string }) {
  return (
    <Link href={dailyArticleRoute(article.id)} className="group block">
      <article className="border-t-2 border-[color:var(--dr-ink)] pt-6">
        <div className="grid gap-8 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)] lg:gap-12">
          {/* 文字区 */}
          <div>
            <p className="dr-font-mono text-[length:var(--dr-type-mono-size)] leading-[var(--dr-type-mono-lh)] text-[color:var(--dr-accent)]">
              {kicker} · {formatLongDate(article.publishDate)} · {article.source}
            </p>
            <h2 className="dr-font-zh mt-5 text-balance text-[length:var(--dr-type-hero-size)] font-normal leading-[var(--dr-type-hero-lh)] tracking-[-0.018em] text-[color:var(--dr-ink-zh)]">
              {article.title}
            </h2>
            {article.originalTitle ? (
              <p className="dr-font-en mt-4 max-w-[40rem] text-[length:var(--dr-type-deck-size)] leading-[var(--dr-type-deck-lh)] text-[color:var(--dr-ink)]">
                {article.originalTitle}
              </p>
            ) : null}
            {article.subtitleZh || article.subtitle ? (
              <p className="dr-font-zh mt-3 max-w-[38rem] text-[length:var(--dr-type-zh-size)] leading-[var(--dr-type-zh-lh)] text-[color:var(--dr-meta)]">
                {article.subtitleZh ?? article.subtitle}
              </p>
            ) : null}
            <div className="mt-6 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-[color:var(--dr-rule)] pt-4">
              <span className="dr-font-ui inline-flex items-center gap-2 text-[length:var(--dr-type-caption-size)] font-semibold text-[color:var(--dr-accent)]">
                阅读全文
                <ArrowRight aria-hidden="true" className="h-4 w-4 transition-transform group-hover:translate-x-1" />
              </span>
              <span className="dr-font-ui text-[length:var(--dr-type-caption-size)] text-[color:var(--dr-meta)]">
                {article.difficulty} · {article.readTimeMinutes} 分钟
                {article.tags[0] ? ` · ${article.tags[0]}` : ""}
              </span>
            </div>
          </div>

          {/* 图片区：有封面才是 figure，无封面不放占位块 */}
          {article.coverImageUrl ? (
            <figure>
              <div
                role="img"
                aria-label={`${article.title} 配图`}
                className="aspect-[var(--dr-ratio-inline)] border border-[color:var(--dr-rule)] bg-[var(--dr-paper-raised)] bg-cover bg-center grayscale-[0.12] contrast-[0.94] saturate-[0.82]"
                style={{ backgroundImage: `url("${article.coverImageUrl}")` }}
              />
            </figure>
          ) : null}
        </div>
      </article>
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

function ArticleListItem({ article, index }: { article: DailyReaderListItem; index: number }) {
  return (
    <Link
      key={article.id}
      href={dailyArticleRoute(article.id)}
      className="focus-ring group block py-5"
    >
      <div className="grid grid-cols-[2.5rem_minmax(0,1fr)_1.25rem] gap-3">
        <p className="dr-font-mono text-[length:var(--dr-type-mono-size)] leading-[var(--dr-type-mono-lh)] text-[color:var(--dr-accent)]">
          {String(index + 1).padStart(2, "0")}
          <span className="mt-1 block text-[color:var(--dr-meta)]">
            {formatPublishDate(article.publishDate)}
          </span>
        </p>
        <div>
          <h3 className="dr-font-zh text-[length:var(--dr-type-zh-size)] font-semibold leading-[var(--dr-type-zh-lh)] text-[color:var(--dr-ink-zh)]">
            {article.title}
          </h3>
          <p className="dr-font-ui mt-2 text-[length:var(--dr-type-caption-size)] leading-[var(--dr-type-caption-lh)] text-[color:var(--dr-meta)]">
            {article.tags[0] ? `${article.tags[0]} · ` : ""}
            {articleMeta(article)}
          </p>
        </div>
        <ArrowRight
          aria-hidden="true"
          className="mt-1 h-4 w-4 text-[color:var(--dr-meta)] opacity-0 transition-all group-hover:translate-x-0.5 group-hover:text-[color:var(--dr-accent)] group-hover:opacity-100"
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
    <main className="daily-reader-surface min-h-screen overflow-hidden bg-surface-canvas text-ink">
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
                      <div className="divide-y divide-[color:var(--dr-rule)] border-y border-[color:var(--dr-rule)]">
                        {otherTodayArticles.map((article, index) => (
                          <ArticleListItem key={article.id} article={article} index={index} />
                        ))}
                      </div>
                    </div>
                  )}
                  <div className="mb-4 flex items-center justify-between gap-3">
                    <h2 className="text-sm font-semibold text-ink">往期精选</h2>
                    <ClareadStamp label="READ DEEPLY" className="bg-surface/80" />
                  </div>
                  <div className="divide-y divide-[color:var(--dr-rule)] border-y border-[color:var(--dr-rule)]">
                    {archiveItems.length > 0 ? archiveItems.map((article, index) => (
                      <ArticleListItem key={article.id} article={article} index={index} />
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
