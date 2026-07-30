import Link from "next/link";
import { ScrollArea } from "@/components/primitives";
import { readReadingDefaultsFromSettings } from "@/lib/reading-defaults";
import { dailyArticleRoute, dailyRoute } from "@/lib/routes";
import { fetchDailyReaderList, fetchDailyReaderToday } from "@/services/api/daily-reader";
import { getProfileSettings } from "@/services/bff/profile";
import { ReadPageIntake } from "./ReadPageIntake";
import { ReadPageHero, ReadPageUiProvider } from "./read-page-ui";

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
    fetchDailyReaderList({ limit: 5 }),
    getProfileSettings(),
  ]);
  const readingDefaults = readReadingDefaultsFromSettings(profileSettings.profile?.settings);
  const todayIds = new Set(todayResult.ok ? todayResult.data.map((article) => article.id) : []);
  const archiveItems = listResult.ok
    ? listResult.data.items.filter((article) => !todayIds.has(article.id))
    : [];
  // 杂志栏稿流：今日文章在前、往期补位；头条 + 最多两条次要稿。
  const feed = [...(todayResult.ok ? todayResult.data : []), ...archiveItems];
  const leadPick = feed[0] ?? null;
  const secondaryPicks = feed.slice(1, 3);

  const dateLine = new Intl.DateTimeFormat("zh-CN", {
    month: "long",
    day: "numeric",
    weekday: "long",
  }).format(new Date());

  /**
   * 今日精选：杂志栏（masthead + 头条 + 编号次要稿）。数据为空或请求
   * 失败时入口不消失，降级为"今日内容稍后更新 · 浏览往期 →"。面板始终
   * 渲染，输入工作台宽度不随数据有无变化。
   */
  function renderDailyPickPanel() {
    return (
      <div data-testid="daily-pick-panel" data-state={leadPick ? "ready" : "fallback"}>
        <div className="border-b-2 border-ink/80 pb-3">
          <div className="flex items-baseline justify-between gap-4">
            <h2 className="font-sans text-[0.78rem] font-bold tracking-[0.22em] text-ink">
              今日精选
            </h2>
            <Link
              href={dailyRoute}
              className="shrink-0 font-sans text-[0.72rem] font-medium tracking-[0.02em] text-muted-foreground transition-colors hover:text-ink"
            >
              查看全部 &rarr;
            </Link>
          </div>
          <p className="mt-1.5 font-sans text-[0.68rem] font-medium tracking-[0.14em] text-muted-foreground/85">
            {dateLine}
          </p>
        </div>

        {leadPick ? (
          <>
            <article className="mt-5">
              <Link
                href={dailyArticleRoute(leadPick.id)}
                className="group block rounded-lg outline-offset-4 focus-ring"
              >
                <h3 className="text-balance font-headline text-[1.5rem] font-semibold leading-[1.16] tracking-[-0.02em] text-ink transition-colors group-hover:text-lens-blue">
                  {leadPick.title}
                </h3>
                <p className="mt-3 line-clamp-3 font-reading text-[0.92rem] leading-[1.68] text-muted-foreground">
                  {getExcerpt(leadPick.subtitle, leadPick.title)}
                </p>
                <div className="mt-3 font-sans text-[0.7rem] font-medium tracking-[0.06em] text-muted-foreground/90">
                  {formatReadingMeta(
                    leadPick.readTimeMinutes,
                    leadPick.difficulty,
                    leadPick.source,
                  )}
                </div>
              </Link>
            </article>

            {secondaryPicks.length > 0 ? (
              <ol className="mt-5 border-t border-hairline/80">
                {secondaryPicks.map((pick, index) => (
                  <li key={pick.id} className="border-b border-hairline/60">
                    <Link
                      href={dailyArticleRoute(pick.id)}
                      className="group flex items-baseline gap-3.5 rounded-sm py-3.5 outline-offset-4 focus-ring"
                    >
                      <span
                        aria-hidden="true"
                        className="shrink-0 font-headline text-[0.95rem] font-semibold tabular-nums text-subtle/75"
                      >
                        {String(index + 2).padStart(2, "0")}
                      </span>
                      <span className="min-w-0">
                        <span className="line-clamp-2 font-headline text-[1rem] font-semibold leading-[1.32] tracking-[-0.01em] text-ink transition-colors group-hover:text-lens-blue">
                          {pick.title}
                        </span>
                        <span className="mt-1.5 block font-sans text-[0.68rem] font-medium tracking-[0.05em] text-muted-foreground/85">
                          {formatReadingMeta(pick.readTimeMinutes, pick.difficulty, pick.source)}
                        </span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ol>
            ) : null}
          </>
        ) : (
          <p className="mt-5 font-sans text-[0.82rem] leading-6 text-muted-foreground">
            今日内容稍后更新 ·{" "}
            <Link
              href={dailyRoute}
              className="font-semibold text-ink underline-offset-4 transition-colors hover:text-lens-blue hover:underline"
            >
              浏览往期 &rarr;
            </Link>
          </p>
        )}
      </div>
    );
  }

  return (
    <main className="min-h-dvh bg-surface-canvas px-5 py-6 text-ink sm:px-8 lg:h-dvh lg:overflow-y-auto lg:px-12 xl:px-14">
      <div className="mx-auto flex w-full max-w-[1920px] flex-col lg:h-full">
        <div className="grid gap-10 lg:min-h-0 lg:flex-1 xl:grid-cols-[minmax(0,1fr)_24rem] xl:gap-12 2xl:gap-16">
          <section className="flex min-w-0 flex-col pt-4 sm:pt-6 lg:min-h-0 lg:pt-8">
            <ReadPageUiProvider>
              <ReadPageHero />

              <div className="mt-5 flex min-h-0 flex-1 flex-col lg:mt-6">
                <ReadPageIntake
                  readingGoal={readingDefaults.readingGoal}
                  readingVariant={readingDefaults.readingVariant}
                />
              </div>
            </ReadPageUiProvider>

            <details className="group mt-8 shrink-0 border-t border-hairline/70 pt-5 xl:hidden">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-4 font-sans text-sm font-semibold text-ink marker:hidden">
                <span>今日精选</span>
                <span className="text-xs font-medium text-muted-foreground transition-colors group-open:text-ink">
                  展开
                </span>
              </summary>
              <div className="mt-5 pb-2">
                {renderDailyPickPanel()}
              </div>
            </details>
          </section>

          <aside className="hidden min-w-0 border-l border-hairline/60 pl-10 pt-8 xl:block">
            <ScrollArea className="sticky top-8 max-h-[calc(100dvh-4rem)] pr-4">
              {renderDailyPickPanel()}
            </ScrollArea>
          </aside>
        </div>
      </div>
    </main>
  );
}
