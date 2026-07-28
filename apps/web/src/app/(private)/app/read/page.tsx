import Link from "next/link";
import { ScrollArea } from "@/components/primitives";
import { readReadingDefaultsFromSettings } from "@/lib/reading-defaults";
import { dailyArticleRoute, dailyRoute } from "@/lib/routes";
import { fetchDailyReaderList, fetchDailyReaderToday } from "@/services/api/daily-reader";
import { getProfileSettings } from "@/services/bff/profile";
import { ReadPageIntake } from "./ReadPageIntake";
import { ReadPageHero, ReadPageUiProvider } from "./read-page-ui";
import { EditorialTagList } from "./EditorialTagList";

export const dynamic = "force-dynamic";

function formatReadingMeta(readTimeMinutes: number, difficulty: string, source?: string) {
  return [source?.trim(), `${readTimeMinutes} min read`, difficulty].filter(Boolean).join("  ·  ");
}

function getExcerpt(subtitle?: string | null, fallbackTitle?: string) {
  const value = subtitle?.trim() || fallbackTitle?.trim() || "";
  return value.length > 96 ? `${value.slice(0, 93)}...` : value;
}

export default async function PasteToReadPage() {
  const [todayResult, listResult, profileSettings] = await Promise.all([
    fetchDailyReaderToday(),
    fetchDailyReaderList({ limit: 6 }),
    getProfileSettings(),
  ]);
  const readingDefaults = readReadingDefaultsFromSettings(profileSettings.profile?.settings);
  const leadArticle = todayResult.ok ? todayResult.data[0] ?? null : null;
  const otherTodayArticles = todayResult.ok ? todayResult.data.slice(1) : [];
  const todayIds = new Set(todayResult.ok ? todayResult.data.map((article) => article.id) : []);
  const archiveItems = listResult.ok
    ? listResult.data.items.filter((article) => !todayIds.has(article.id)).slice(0, 3)
    : [];
  const fallbackLead = !leadArticle ? archiveItems[0] ?? null : null;
  const sideArchiveItems = fallbackLead ? archiveItems.slice(1) : archiveItems.slice(0, 2);
  const leadPick = leadArticle ?? fallbackLead;
  const supportingPicks =
    otherTodayArticles.length > 0
      ? otherTodayArticles.slice(0, 3)
      : (leadArticle ? archiveItems : sideArchiveItems).slice(0, 3);

  function renderCuratedReadingPanel() {
    return (
      <div className="pb-8 md:pr-4 xl:pr-5">
        <div className="mb-3 flex items-center justify-between gap-4">
          <h2 className="text-[0.68rem] font-bold tracking-[0.12em] text-ink">
            Editor&apos;s Picks
          </h2>
          <Link
            href={dailyRoute}
            className="text-[0.72rem] font-medium tracking-[0.02em] text-muted-foreground transition-colors hover:text-ink"
          >
            查看全部 &rarr;
          </Link>
        </div>

        <h3 className="border-b border-hairline/75 pb-4 font-headline text-[1.65rem] leading-[1.08] tracking-[-0.02em] text-ink sm:text-[1.85rem] xl:text-[2.05rem]">
          今日值得透读
        </h3>

        {leadPick ? (
          <article className="border-b border-hairline/70 py-6 sm:py-7">
            <Link
              href={dailyArticleRoute(leadPick.id)}
              className="group grid gap-5 rounded-lg outline-offset-8 focus-ring sm:grid-cols-[minmax(0,1fr)_108px] sm:items-start md:grid-cols-[minmax(0,1fr)_112px] xl:grid-cols-[minmax(0,1fr)_124px] xl:gap-6"
            >
              <div className="min-w-0">
                <p className="mb-3 font-sans text-[0.68rem] font-semibold tracking-[0.08em] text-muted-foreground">
                  Featured
                </p>
                <h4 className="max-w-[15ch] text-balance font-headline text-[1.42rem] leading-[1.08] tracking-[-0.025em] text-ink transition-colors group-hover:text-lens-blue xl:text-[1.58rem]">
                  {leadPick.title}
                </h4>
                {leadPick.subtitle ? (
                  <p className="mt-4 max-w-[22rem] line-clamp-3 font-reading text-[0.94rem] leading-[1.58] text-muted-foreground">
                    {getExcerpt(leadPick.subtitle, leadPick.title)}
                  </p>
                ) : null}
                <div className="mt-5 font-sans text-[0.74rem] font-medium tracking-[0.01em] text-muted-foreground">
                  {formatReadingMeta(
                    leadPick.readTimeMinutes,
                    leadPick.difficulty,
                    leadPick.source,
                  )}
                </div>
                <EditorialTagList tags={leadPick.tags} className="mt-3" />
              </div>
              {leadPick.coverImageUrl ? (
                <div className="order-first aspect-[4/3] w-full overflow-hidden rounded-[3px] bg-surface-raised sm:order-none sm:aspect-square sm:w-[108px] md:w-[112px] xl:w-[124px]">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={leadPick.coverImageUrl}
                    alt=""
                    className="h-full w-full object-cover opacity-90 saturate-[0.82] transition-transform duration-700 group-hover:scale-[1.02]"
                  />
                </div>
              ) : (
                <div className="order-first aspect-[4/3] w-full rounded-[3px] bg-surface-raised sm:order-none sm:aspect-square sm:w-[108px] md:w-[112px] xl:w-[124px]" />
              )}
            </Link>
          </article>
        ) : null}

        {supportingPicks.length > 0 ? (
          <div className="divide-y divide-hairline/70">
            {supportingPicks.map((article) => (
              <article key={article.id} className="py-6 sm:py-7">
                <Link
                  href={dailyArticleRoute(article.id)}
                  className="group grid grid-cols-[72px_minmax(0,1fr)] gap-4 rounded-lg outline-offset-4 focus-ring sm:grid-cols-[78px_minmax(0,1fr)]"
                >
                  {article.coverImageUrl ? (
                    <div className="aspect-square w-[72px] overflow-hidden rounded-[3px] bg-surface-raised sm:w-[78px]">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={article.coverImageUrl}
                        alt=""
                        className="h-full w-full object-cover opacity-[0.88] saturate-[0.82] transition-transform duration-700 group-hover:scale-[1.02]"
                      />
                    </div>
                  ) : (
                    <div className="aspect-square w-[72px] rounded-[3px] bg-surface-raised sm:w-[78px]" />
                  )}
                  <div className="min-w-0 pt-0.5">
                    <h4 className="max-w-[18ch] text-balance font-headline text-[1.06rem] leading-[1.16] tracking-[-0.02em] text-ink transition-colors group-hover:text-lens-blue">
                      {article.title}
                    </h4>
                    <div className="mt-3 font-sans text-[0.72rem] font-medium tracking-[0.01em] text-muted-foreground">
                      {formatReadingMeta(
                        article.readTimeMinutes,
                        article.difficulty,
                        article.source,
                      )}
                    </div>
                    <EditorialTagList tags={article.tags} className="mt-2.5" />
                  </div>
                </Link>
              </article>
            ))}
          </div>
        ) : null}

        <div className="mt-8 flex items-center justify-between border-t border-hairline/70 pt-5">
          <Link
            href={dailyRoute}
            className="flex items-center gap-2 font-sans text-[0.76rem] font-medium tracking-[0.02em] text-muted-foreground transition-colors hover:text-ink"
          >
            <span>阅读档案</span>
            <span className="text-[0.66rem] tracking-[0.08em]">Archive</span>
          </Link>
          <Link
            href={dailyRoute}
            className="flex items-center gap-2 font-sans text-[0.76rem] font-medium tracking-[0.02em] text-muted-foreground transition-colors hover:text-ink"
          >
            <span>更多阅读</span>
            <span className="text-[0.66rem] tracking-[0.08em]">More</span>
          </Link>
        </div>
      </div>
    );
  }

  return (
    <main className="min-h-dvh bg-surface-canvas px-5 py-6 text-ink lg:h-dvh lg:overflow-hidden sm:px-8 lg:px-12 xl:px-14 2xl:px-16">
      <div className="mx-auto flex w-full max-w-[2200px] flex-col md:h-full">
        <div className="grid gap-10 md:min-h-0 md:flex-1 md:grid-cols-[minmax(0,1fr)_20rem] md:gap-8 lg:grid-cols-[minmax(0,1fr)_24rem] lg:gap-12 xl:grid-cols-[minmax(0,1fr)_29rem] xl:gap-14 2xl:grid-cols-[minmax(0,1fr)_34rem] 2xl:gap-[4.5rem]">
          <section className="flex min-w-0 flex-col pt-4 sm:pt-6 md:min-h-0 md:pt-8 md:pr-8 xl:pt-10 xl:pr-12 2xl:pr-16">
            <ReadPageUiProvider>
              <ReadPageHero />

              <div className="mt-4 flex flex-1 flex-col md:mt-5 md:min-h-0 xl:mt-6">
                <ReadPageIntake
                  readingGoal={readingDefaults.readingGoal}
                  readingVariant={readingDefaults.readingVariant}
                />
              </div>
            </ReadPageUiProvider>

            <details className="group mt-8 border-t border-hairline/70 pt-5 md:hidden">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-4 font-sans text-[0.86rem] font-semibold text-ink marker:hidden">
                <span>今日值得透读</span>
                <span className="text-[0.72rem] font-medium text-muted-foreground transition-colors group-open:text-ink">
                  展开
                </span>
              </summary>
              <div className="mt-5">
                {renderCuratedReadingPanel()}
              </div>
            </details>
          </section>

          <aside className="hidden min-w-0 border-hairline/60 pt-4 md:block md:min-h-0 md:border-l md:border-t-0 md:pl-8 md:pt-8 lg:pl-10 xl:pl-12 2xl:pl-16">
            <ScrollArea className="max-h-none md:h-full md:pr-5 xl:pr-6">
              {renderCuratedReadingPanel()}
            </ScrollArea>
          </aside>
        </div>
      </div>
    </main>
  );
}
