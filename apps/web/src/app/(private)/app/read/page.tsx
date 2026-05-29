import Link from "next/link";
import { ScrollArea } from "@/components/primitives";
import { readReadingDefaultsFromSettings } from "@/lib/reading-defaults";
import { dailyArticleRoute, dailyRoute } from "@/lib/routes";
import { fetchDailyReaderList, fetchDailyReaderToday } from "@/services/api/daily-reader";
import { getProfileSettings } from "@/services/bff/profile";
import { AnalyzeSubmitForm } from "./AnalyzeSubmitForm";
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

  return (
    <main className="min-h-dvh bg-reader-paper px-5 py-6 text-ink lg:h-dvh lg:overflow-hidden sm:px-8 lg:px-12 xl:px-16 2xl:px-20">
      <div className="mx-auto flex w-full max-w-[1500px] flex-col md:h-full">
        <div className="grid gap-10 md:min-h-0 md:flex-1 md:grid-cols-[minmax(0,1fr)_20rem] md:gap-8 lg:grid-cols-[minmax(0,1fr)_22rem] lg:gap-10 xl:grid-cols-[minmax(0,1fr)_27rem] xl:gap-12 2xl:grid-cols-[minmax(0,1fr)_29rem] 2xl:gap-14">
          <section className="flex min-w-0 flex-col pt-4 sm:pt-6 md:min-h-0 md:pt-8 md:pr-8 xl:pt-10 xl:pr-10 2xl:pr-12">
            <div className="max-w-[46rem]">
              <span className="mb-3 inline-block text-[0.72rem] font-bold tracking-[0.22em] text-lens-blue">
                Paste to Begin
              </span>
              <h1 className="font-headline text-[clamp(2.5rem,4.5vw,4rem)] font-semibold leading-[0.94] tracking-[-0.045em] text-ink">
                <span className="block">Bring it to Claread.</span>
                <span className="mt-1 block">Read It Deeply.</span>
              </h1>
              <p className="mt-4 max-w-[28rem] font-reading text-[1.08rem] leading-[1.65] text-muted sm:text-[1.12rem]">
                从粘贴开始，进入深度阅读。
              </p>
            </div>

            <div className="mt-8 flex flex-1 flex-col md:mt-8 md:min-h-0 xl:mt-10">
              <AnalyzeSubmitForm
                readingGoal={readingDefaults.readingGoal}
                readingVariant={readingDefaults.readingVariant}
              />
            </div>
          </section>

          <aside className="min-w-0 border-t border-hairline/70 pt-4 md:min-h-0 md:border-l md:border-t-0 md:pl-8 md:pt-8 lg:pl-10 xl:pl-12 2xl:pl-14">
            <ScrollArea className="max-h-none md:h-full md:pr-5 xl:pr-6">
              <div className="pb-8 md:pr-5 xl:pr-6">
                <div className="mb-3 flex items-center justify-between gap-4">
                  <h2 className="text-[0.68rem] font-bold tracking-[0.2em] text-ink">
                    Editor&apos;s Picks
                  </h2>
                  <Link
                    href={dailyRoute}
                    className="text-[0.72rem] font-medium tracking-[0.04em] text-muted transition-colors hover:text-ink"
                  >
                    查看全部 &rarr;
                  </Link>
                </div>

                <h3 className="border-b border-hairline/80 pb-4 font-headline text-[1.8rem] leading-[1.1] tracking-[-0.03em] text-ink sm:text-[2rem] xl:text-[2.2rem]">
                  今日值得透读
                </h3>

                {leadPick ? (
                  <article className="border-b border-hairline/80 py-6 sm:py-7">
                    <p className="mb-3 text-[0.65rem] font-bold tracking-[0.18em] text-muted">
                      Featured
                    </p>
                    <Link
                      href={dailyArticleRoute(leadPick.id)}
                      className="group grid gap-5 sm:grid-cols-[minmax(0,1fr)_120px] sm:items-start md:grid-cols-[minmax(0,1fr)_128px] xl:grid-cols-[minmax(0,1fr)_144px] xl:gap-7 focus-ring rounded-lg outline-offset-8"
                    >
                      <div className="min-w-0">
                        <h4 className="max-w-[14ch] text-balance font-headline text-[1.52rem] leading-[1.06] tracking-[-0.035em] text-ink transition-colors group-hover:text-lens-blue line-clamp-4 xl:text-[1.7rem]">
                          {leadPick.title}
                        </h4>
                        {leadPick.subtitle ? (
                          <p className="mt-4 max-w-[22rem] line-clamp-4 font-reading text-[0.96rem] leading-[1.58] text-muted">
                            {getExcerpt(leadPick.subtitle, leadPick.title)}
                          </p>
                        ) : null}
                        <div className="mt-5 font-sans text-[0.76rem] font-medium tracking-[0.02em] text-muted">
                          {formatReadingMeta(
                            leadPick.readTimeMinutes,
                            leadPick.difficulty,
                            leadPick.source,
                          )}
                        </div>
                        <EditorialTagList tags={leadPick.tags} className="mt-3" />
                      </div>
                      {leadPick.coverImageUrl ? (
                        <div className="order-first aspect-square w-[120px] overflow-hidden rounded-[2px] bg-surface-raised sm:order-none md:w-[128px] xl:w-[144px]">
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img
                            src={leadPick.coverImageUrl}
                            alt=""
                            className="h-full w-full object-cover transition-transform duration-700 group-hover:scale-[1.02]"
                          />
                        </div>
                      ) : (
                        <div className="order-first aspect-square w-[120px] rounded-[2px] bg-surface-raised sm:order-none md:w-[128px] xl:w-[144px]" />
                      )}
                    </Link>
                  </article>
                ) : null}

                {supportingPicks.length > 0 ? (
                  <div className="divide-y divide-hairline/80">
                    {supportingPicks.map((article) => (
                      <article key={article.id} className="py-7 sm:py-8">
                      <Link
                        href={dailyArticleRoute(article.id)}
                        className="group grid grid-cols-[86px_minmax(0,1fr)] gap-4 sm:grid-cols-[92px_minmax(0,1fr)] sm:gap-5 focus-ring rounded-lg outline-offset-4"
                      >
                        {article.coverImageUrl ? (
                            <div className="aspect-square w-[86px] overflow-hidden rounded-[2px] bg-surface-raised sm:w-[92px]">
                              {/* eslint-disable-next-line @next/next/no-img-element */}
                              <img
                                src={article.coverImageUrl}
                                alt=""
                                className="h-full w-full object-cover opacity-95 transition-transform duration-700 group-hover:scale-[1.02]"
                              />
                            </div>
                          ) : (
                            <div className="aspect-square w-[86px] rounded-[2px] bg-surface-raised sm:w-[92px]" />
                          )}
                          <div className="min-w-0 pt-0.5">
                            <h4 className="max-w-[17ch] text-balance font-headline text-[1.18rem] leading-[1.14] tracking-[-0.03em] text-ink transition-colors group-hover:text-lens-blue">
                              {article.title}
                            </h4>
                            <div className="mt-3 font-sans text-[0.76rem] font-medium tracking-[0.02em] text-muted">
                              {formatReadingMeta(
                                article.readTimeMinutes,
                                article.difficulty,
                                article.source,
                              )}
                            </div>
                            <EditorialTagList tags={article.tags} className="mt-3" />
                          </div>
                        </Link>
                      </article>
                    ))}
                  </div>
                ) : null}

                <div className="mt-10 flex items-center justify-between border-t border-hairline/80 pt-6">
                      <Link
                        href={dailyRoute}
                        className="flex items-center gap-2 font-sans text-[0.78rem] font-medium tracking-[0.03em] text-muted transition-colors hover:text-ink"
                      >
                        <span>阅读档案</span>
                        <span className="text-[0.66rem] tracking-[0.14em]">Archive</span>
                      </Link>
                      <Link
                        href={dailyRoute}
                        className="flex items-center gap-2 font-sans text-[0.78rem] font-medium tracking-[0.03em] text-muted transition-colors hover:text-ink"
                      >
                        <span>更多阅读</span>
                        <span className="text-[0.66rem] tracking-[0.14em]">More</span>
                      </Link>
                </div>
              </div>
            </ScrollArea>
          </aside>
        </div>
      </div>
    </main>
  );
}
