import { ArrowRight, CalendarDays, LogIn } from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import { ApertureWatermark, BrandLockup, ClareadStamp } from "@/components/brand/BrandMarks";

const dailyRoute = "/daily" as Route;
const todayRoute = "/daily/2026-05-14" as Route;
const loginSaveRoute = "/login?next=/daily/2026-05-14&intent=save" as Route;
const examplesRoute = "/examples/news-brief" as Route;

const pastArticles = [
  { date: "May 13", topic: "科技", title: "The quiet cost of faster chips", meta: "8 分钟 · 术语密度中等" },
  { date: "May 12", topic: "文化", title: "Why old books feel slow", meta: "10 分钟 · 长句较多" },
  { date: "May 11", topic: "商业", title: "A memo that changed a company", meta: "7 分钟 · 适合通勤阅读" },
] as const;

export default function DailyReaderPage() {
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
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p className="text-xs font-semibold text-lens-blue">今日精读 · May 14</p>
                  <span className="inline-flex items-center gap-2 rounded-pill border border-hairline bg-reader-paper px-3 py-1.5 text-xs font-semibold text-muted">
                    <CalendarDays aria-hidden="true" className="h-3.5 w-3.5 text-lens-blue" />
                    每日更新
                  </span>
                </div>
                <h2 className="mt-6 max-w-2xl font-headline text-3xl font-semibold leading-tight tracking-normal text-ink sm:text-[2.65rem]">
                  What makes a city readable?
                </h2>
                <p className="mt-3 text-sm leading-6 text-muted">
                  The Atlantic · 中级 · 12 分钟 · 城市与日常生活
                </p>
                <div className="mt-8 border-t border-hairline pt-7 font-reading text-[1.35rem] leading-[1.85] text-ink">
                  A city is readable when its{" "}
                  <span className="rounded-sm bg-lens-blue-soft px-1.5 py-0.5 text-[#174ea6]">
                    patterns
                  </span>{" "}
                  become familiar without becoming{" "}
                  <span className="rounded-sm bg-[#e6f3e7] px-1.5 py-0.5 text-[#276247]">
                    invisible
                  </span>
                  .
                </div>
                <div className="mt-7 grid gap-4 md:grid-cols-[minmax(0,1fr)_270px]">
                  <div className="flex flex-wrap items-start gap-4">
                    <Link
                      href={todayRoute}
                      className="focus-ring inline-flex min-h-11 items-center gap-2 rounded-pill bg-lens-blue px-5 text-sm font-semibold text-surface transition-opacity hover:opacity-90"
                    >
                      打开今日
                      <ArrowRight aria-hidden="true" className="h-4 w-4" />
                    </Link>
                    <Link
                      href={loginSaveRoute}
                      className="focus-ring inline-flex min-h-11 items-center gap-2 rounded-pill px-1 text-sm font-semibold text-lens-blue"
                    >
                      加入我的阅读记录
                    </Link>
                  </div>
                  <div className="border-l border-hairline pl-4">
                    <p className="text-xs font-semibold text-ink">Claread 标注预览</p>
                    <p className="mt-2 text-sm leading-6 text-muted">
                      patterns 指可识别的空间规律，不是装饰图案。
                    </p>
                  </div>
                </div>
              </div>
            </article>

            <aside id="archive">
              <div className="mb-4 flex items-center justify-between gap-3">
                <h2 className="text-sm font-semibold text-ink">往期精选</h2>
                <ClareadStamp label="READ DEEPLY" className="bg-surface-warm/80" />
              </div>
              <div className="divide-y divide-hairline border-y border-hairline">
                {pastArticles.map((article) => (
                  <Link
                    key={article.title}
                    href={todayRoute}
                    className="focus-ring group block py-5 transition-colors hover:bg-surface-warm/70"
                  >
                    <div className="grid grid-cols-[4.7rem_minmax(0,1fr)_1.25rem] gap-3">
                      <p className="text-xs font-semibold leading-5 text-lens-blue">
                        {article.date}
                        <span className="mt-1 block text-muted">{article.topic}</span>
                      </p>
                      <div>
                        <h3 className="font-headline text-xl font-semibold leading-snug tracking-normal text-ink">
                          {article.title}
                        </h3>
                        <p className="mt-2 text-xs leading-5 text-muted">{article.meta}</p>
                      </div>
                      <ArrowRight
                        aria-hidden="true"
                        className="mt-1 h-4 w-4 text-subtle transition-transform group-hover:translate-x-0.5 group-hover:text-lens-blue"
                      />
                    </div>
                  </Link>
                ))}
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
