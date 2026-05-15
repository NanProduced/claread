import { ArrowRight, BookOpen, Newspaper, PenLine } from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { ApertureWatermark, BrandLockup, ClareadStamp } from "@/components/brand/BrandMarks";
import { PhoneLoginForm } from "./PhoneLoginForm";

const dailyRoute = "/daily" as Route;
const todayRoute = "/daily/2026-05-14" as Route;
const exampleRoutes = [
  { label: "新闻", title: "A short piece of global news", href: "/examples/news-brief" as Route },
  { label: "学术", title: "An abstract with dense logic", href: "/examples/academic-abstract" as Route },
  { label: "考试", title: "A passage with trap options", href: "/examples/exam-passage" as Route },
] as const;

export default function LoginPage() {
  return (
    <main className="min-h-screen overflow-hidden bg-[oklch(97%_0.012_84)] text-ink">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_8%_12%,rgba(37,99,235,0.08),transparent_26rem),radial-gradient(circle_at_90%_92%,rgba(17,17,17,0.055),transparent_24rem)]" />
      <div className="paper-grain relative min-h-screen px-5 py-6 sm:px-8 lg:px-12">
        <header className="mx-auto flex max-w-7xl items-center justify-between gap-6">
          <BrandLockup href={dailyRoute} priority />
          <nav className="hidden items-center gap-7 text-sm font-semibold text-muted md:flex">
            <Link className="focus-ring rounded-pill hover:text-ink" href={dailyRoute}>
              每日精读
            </Link>
            <Link className="focus-ring rounded-pill hover:text-ink" href={exampleRoutes[0].href}>
              公开示例
            </Link>
            <span className="text-lens-blue">登录</span>
          </nav>
        </header>

        <div className="mx-auto grid max-w-7xl gap-8 py-7 lg:grid-cols-[minmax(0,1fr)_400px] lg:py-9 xl:gap-14">
          <section className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-lens-blue">
              Read deeply, understand clearly
            </p>
            <h1 className="mt-3 max-w-4xl font-headline text-[2.45rem] font-semibold leading-[1.04] tracking-normal text-ink sm:text-[3.85rem]">
              今天 Claread 在读什么
            </h1>
            <p className="mt-4 max-w-2xl text-base leading-7 text-muted sm:text-lg">
              每天一篇精选英文，配上词汇、句子和语境标注。先读一篇，再决定要不要把 Claread 变成自己的阅读工具。
            </p>

            <article className="relative mt-7 overflow-hidden rounded-[1.75rem] border border-hairline bg-surface-raised p-5 shadow-[0_24px_70px_rgba(35,28,18,0.13)] sm:p-6">
              <ApertureWatermark
                size={280}
                className="absolute -bottom-24 -right-20 h-64 w-64 opacity-[0.07]"
              />
              <div className="relative">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p className="text-xs font-semibold text-lens-blue">今日精读 · May 14</p>
                  <ClareadStamp label="CLAREAD DAILY" />
                </div>
                <h2 className="mt-4 max-w-2xl font-headline text-3xl font-semibold leading-tight tracking-normal text-ink sm:text-[2.45rem]">
                  What makes a city readable?
                </h2>
                <p className="mt-3 text-sm leading-6 text-muted">The Atlantic · 中级 · 12 分钟</p>
                <div className="mt-5 border-t border-hairline pt-5 font-reading text-[1.28rem] leading-[1.82] text-ink">
                  Cities are not only built to be crossed, but also to be{" "}
                  <span className="rounded-sm bg-lens-blue-soft px-1.5 py-0.5 text-[#174ea6]">
                    read
                  </span>{" "}
                  through{" "}
                  <span className="rounded-sm bg-[#fff1b8] px-1.5 py-0.5 text-[#614000]">
                    signs, corners
                  </span>
                  , and quiet habits.
                </div>
                <div className="mt-5 grid gap-4 md:grid-cols-[minmax(0,1fr)_260px] md:items-start">
                  <Link
                    href={todayRoute}
                    className="focus-ring inline-flex min-h-11 w-fit items-center gap-2 rounded-pill bg-lens-blue px-5 text-sm font-semibold text-surface transition-opacity hover:opacity-90"
                  >
                    打开今日精读
                    <ArrowRight aria-hidden="true" className="h-4 w-4" />
                  </Link>
                  <div className="border-l border-hairline pl-4">
                    <p className="text-xs font-semibold text-ink">语境提示</p>
                    <p className="mt-2 text-sm leading-6 text-muted">
                      readable 在这里不是“可读”，而是城市容易被理解和辨认。
                    </p>
                  </div>
                </div>
              </div>
            </article>

            <section className="mt-4 grid gap-3 sm:grid-cols-3">
              {exampleRoutes.map((example) => (
                <Link
                  key={example.href}
                  href={example.href}
                  className="focus-ring group rounded-[1.25rem] border border-hairline bg-surface-warm/86 p-3.5 shadow-surface-quiet transition-colors hover:border-muted"
                >
                  <div className="flex items-center gap-2 text-xs font-semibold text-lens-blue">
                    {example.label === "新闻" ? (
                      <Newspaper aria-hidden="true" className="h-4 w-4" />
                    ) : example.label === "学术" ? (
                      <BookOpen aria-hidden="true" className="h-4 w-4" />
                    ) : (
                      <PenLine aria-hidden="true" className="h-4 w-4" />
                    )}
                    {example.label}
                  </div>
                  <h3 className="mt-3 font-headline text-base font-semibold leading-snug tracking-normal text-ink">
                    {example.title}
                  </h3>
                  <p className="mt-2 text-xs leading-5 text-muted">查看一份公开标注样张</p>
                </Link>
              ))}
            </section>
          </section>

          <aside className="lg:pt-16">
            <section className="rounded-[1.75rem] border border-hairline bg-surface-raised/95 p-5 shadow-[0_22px_64px_rgba(35,28,18,0.13)] sm:p-6">
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-lens-blue">
                Claread Web
              </p>
              <h2 className="mt-3 font-headline text-3xl font-semibold tracking-normal text-ink">
                继续使用 Claread
              </h2>
              <p className="mt-3 text-sm leading-6 text-muted">
                登录后保存你的解读、批注和生词。公开每日精读和示例可直接阅读。
              </p>
              <Suspense fallback={<div className="mt-6 h-64 rounded-note bg-reader-paper" />}>
                <PhoneLoginForm />
              </Suspense>
              <p className="mt-5 border-t border-hairline pt-4 text-xs leading-5 text-muted">
                保存为个人资产、收藏、加入生词本时需要登录。
              </p>
            </section>
          </aside>
        </div>
      </div>
    </main>
  );
}
