import { FileText } from "lucide-react";
import Link from "next/link";
import { dailyArticleRoute } from "@/lib/routes";
import { fetchDailyReaderList, fetchDailyReaderToday } from "@/services/api/daily-reader";
import { AnalyzeSubmitForm } from "./AnalyzeSubmitForm";

export const dynamic = "force-dynamic";

export default async function PasteToReadPage() {
  const [todayResult, listResult] = await Promise.all([
    fetchDailyReaderToday(),
    fetchDailyReaderList({ limit: 6 }),
  ]);
  const leadArticle = todayResult.ok ? todayResult.data[0] ?? null : null;
  const otherTodayArticles = todayResult.ok ? todayResult.data.slice(1) : [];
  const todayIds = new Set(todayResult.ok ? todayResult.data.map((article) => article.id) : []);
  const archiveItems = listResult.ok
    ? listResult.data.items.filter((article) => !todayIds.has(article.id)).slice(0, 3)
    : [];
  const fallbackLead = !leadArticle ? archiveItems[0] ?? null : null;
  const sideArchiveItems = fallbackLead ? archiveItems.slice(1) : archiveItems.slice(0, 2);

  return (
    <main className="min-h-screen bg-[oklch(96.8%_0.012_84)] px-4 py-12 text-ink sm:px-8 lg:px-12">
      <div className="mx-auto max-w-[1400px]">
        <div className="grid gap-16 xl:grid-cols-[minmax(0,1.8fr)_340px]">
          <div className="min-w-0">
            <div className="mb-14 pl-2">
              <p className="mb-5 text-[0.65rem] font-bold uppercase tracking-[0.2em] text-lens-blue">Paste to read</p>
              <h1 className="font-headline text-[3.5rem] font-medium leading-[1.05] tracking-tight text-ink md:text-[4rem]">
                A Quiet Space <br /> for Deep Reading.
              </h1>
              <p className="mt-6 max-w-xl text-[1rem] leading-relaxed text-muted">
                粘贴你需要精读的英文材料，Claread 将为你生成一份纯粹的、结构化的阅读体验。
              </p>
            </div>
            <AnalyzeSubmitForm />
          </div>

          <aside className="min-w-0 xl:pt-4 space-y-16">
            {/* FEATURED */}
            <div>
              <h3 className="mb-6 text-[0.65rem] font-bold uppercase tracking-[0.2em] text-ink">Featured</h3>
              {leadArticle ? (
                <article>
                  <Link href={dailyArticleRoute(leadArticle.id)} className="group block focus-ring rounded-2xl outline-offset-8">
                    {leadArticle.coverImageUrl ? (
                      <div className="mb-5 aspect-[4/3] w-full overflow-hidden rounded-2xl bg-surface-warm ring-1 ring-inset ring-black/5">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img 
                          src={leadArticle.coverImageUrl} 
                          alt="" 
                          className="h-full w-full object-cover transition-transform duration-700 ease-out group-hover:scale-105" 
                        />
                      </div>
                    ) : (
                      <div className="mb-5 aspect-[4/3] w-full rounded-2xl bg-surface-raised ring-1 ring-inset ring-black/5" />
                    )}
                    <h4 className="font-headline text-[1.4rem] font-semibold leading-snug tracking-tight text-ink group-hover:text-lens-blue transition-colors">
                      {leadArticle.title}
                    </h4>
                    {leadArticle.subtitle ? (
                      <p className="mt-2.5 text-[0.95rem] leading-relaxed text-muted line-clamp-2">
                        {leadArticle.subtitle}
                      </p>
                    ) : null}
                    <div className="mt-4 flex items-center gap-3 text-[0.75rem] font-medium text-muted">
                      <span className="flex items-center gap-1.5">
                        <span className="flex h-3 w-3 items-center justify-center rounded-full border border-muted/30">
                          <span className="h-1 w-1 rounded-full bg-muted/60" />
                        </span>
                        {leadArticle.readTimeMinutes} min read
                      </span>
                      <span className="flex items-center gap-1.5">
                        <span className="flex h-3 items-center gap-0.5">
                          <span className="h-1.5 w-0.5 bg-muted/30" />
                          <span className="h-2 w-0.5 bg-muted/50" />
                          <span className="h-2.5 w-0.5 bg-muted" />
                        </span>
                        {leadArticle.difficulty}
                      </span>
                    </div>
                  </Link>
                </article>
              ) : fallbackLead ? (
                <article>
                  <Link href={dailyArticleRoute(fallbackLead.id)} className="group block focus-ring rounded-2xl outline-offset-8">
                    {fallbackLead.coverImageUrl ? (
                      <div className="mb-5 aspect-[4/3] w-full overflow-hidden rounded-2xl bg-surface-warm ring-1 ring-inset ring-black/5">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img 
                          src={fallbackLead.coverImageUrl} 
                          alt="" 
                          className="h-full w-full object-cover transition-transform duration-700 ease-out group-hover:scale-105" 
                        />
                      </div>
                    ) : (
                      <div className="mb-5 aspect-[4/3] w-full rounded-2xl bg-surface-raised ring-1 ring-inset ring-black/5" />
                    )}
                    <h4 className="font-headline text-[1.4rem] font-semibold leading-snug tracking-tight text-ink group-hover:text-lens-blue transition-colors">
                      {fallbackLead.title}
                    </h4>
                    <div className="mt-4 flex items-center gap-3 text-[0.75rem] font-medium text-muted">
                      <span>{fallbackLead.readTimeMinutes} min read</span>
                      <span>{fallbackLead.difficulty}</span>
                    </div>
                  </Link>
                </article>
              ) : (
                <p className="text-sm text-muted">No featured reading today.</p>
              )}
            </div>

            {/* MORE TODAY */}
            {otherTodayArticles.length > 0 ? (
              <div>
                <h3 className="mb-6 text-[0.65rem] font-bold uppercase tracking-[0.2em] text-lens-blue">More Today</h3>
                <div className="space-y-6">
                  {otherTodayArticles.map((article) => (
                    <Link
                      key={article.id}
                      href={dailyArticleRoute(article.id)}
                      className="group flex gap-4 focus-ring rounded-xl outline-offset-4"
                    >
                      <div className="flex-shrink-0 mt-0.5 flex h-9 w-9 items-center justify-center rounded-[0.4rem] border border-lens-blue/20 bg-lens-blue/5 text-lens-blue shadow-sm group-hover:border-lens-blue/40 group-hover:bg-lens-blue/10 transition-colors">
                        <FileText aria-hidden="true" className="h-[18px] w-[18px] stroke-[1.5]" />
                      </div>
                      <div>
                        <p className="text-[0.65rem] font-bold uppercase tracking-[0.15em] text-lens-blue">
                          {article.tags?.[0] || article.difficulty}
                        </p>
                        <h4 className="mt-1 font-headline text-[1.05rem] font-semibold leading-[1.4] tracking-tight text-ink group-hover:text-lens-blue transition-colors line-clamp-2">
                          {article.title}
                        </h4>
                      </div>
                    </Link>
                  ))}
                </div>
              </div>
            ) : null}

            {/* ARCHIVE */}
            {sideArchiveItems.length > 0 ? (
              <div>
                <h3 className="mb-6 text-[0.65rem] font-bold uppercase tracking-[0.2em] text-ink">Archive</h3>
                <div className="space-y-6">
                  {sideArchiveItems.map((article) => (
                    <Link
                      key={article.id}
                      href={dailyArticleRoute(article.id)}
                      className="group flex gap-4 focus-ring rounded-xl outline-offset-4"
                    >
                      <div className="flex-shrink-0 mt-0.5 flex h-9 w-9 items-center justify-center rounded-[0.4rem] border border-hairline bg-surface/50 text-muted shadow-sm group-hover:border-lens-blue/20 group-hover:bg-lens-blue/5 group-hover:text-lens-blue transition-colors">
                        <FileText aria-hidden="true" className="h-[18px] w-[18px] stroke-[1.5]" />
                      </div>
                      <div>
                        <p className="text-[0.65rem] font-bold uppercase tracking-[0.15em] text-muted group-hover:text-lens-blue transition-colors">
                          {article.tags?.[0] || article.difficulty}
                        </p>
                        <h4 className="mt-1 font-headline text-[1.05rem] font-semibold leading-[1.4] tracking-tight text-ink group-hover:text-lens-blue transition-colors line-clamp-2">
                          {article.title}
                        </h4>
                      </div>
                    </Link>
                  ))}
                </div>
              </div>
            ) : null}
          </aside>
        </div>
      </div>
    </main>
  );
}
