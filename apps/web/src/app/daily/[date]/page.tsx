import { ArrowLeft, BookMarked, Star } from "lucide-react";
import type { Route } from "next";
import Image from "next/image";
import Link from "next/link";

const dailyDates = ["2026-05-14"] as const;

export function generateStaticParams() {
  return dailyDates.map((date) => ({ date }));
}

export default async function DailyArticlePage({
  params,
}: {
  params: Promise<{ date: string }>;
}) {
  const { date } = await params;

  return (
    <main className="min-h-screen bg-[oklch(97%_0.012_84)] px-5 py-6 text-ink sm:px-8 lg:px-12">
      <div className="mx-auto max-w-5xl">
        <header className="flex items-center justify-between gap-6">
          <Link href={"/daily" as Route} className="focus-ring inline-flex items-center gap-2 rounded-pill text-sm font-semibold text-muted hover:text-ink">
            <ArrowLeft aria-hidden="true" className="h-4 w-4" />
            返回每日精读
          </Link>
          <Image
            src="/brand/claread-horizontal-bilingual.png"
            alt="Claread 透读"
            width={260}
            height={76}
            priority
            className="h-auto w-40"
          />
        </header>

        <article className="reading-paper relative mt-8 overflow-hidden rounded-[2rem] border border-hairline px-6 py-8 shadow-[0_28px_80px_rgba(35,28,18,0.12)] sm:px-12 sm:py-12">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-lens-blue">
            Daily Reader · {date}
          </p>
          <h1 className="mt-5 max-w-3xl font-headline text-[2.5rem] font-semibold leading-tight tracking-normal text-ink sm:text-[3.6rem]">
            What makes a city readable?
          </h1>
          <p className="mt-4 text-sm leading-6 text-muted">
            The Atlantic · 中级 · 12 分钟 · 公开示例
          </p>

          <div className="mt-10 max-w-[68ch] font-reading text-[1.28rem] leading-[1.95] text-ink">
            <p>
              A city is readable when its{" "}
              <span className="rounded-sm bg-lens-blue-soft px-1.5 py-0.5 text-[#174ea6]">
                patterns
              </span>{" "}
              become familiar without becoming invisible.
            </p>
            <p className="mt-7">
              The best streets give the mind enough structure to wander without losing the
              sense of direction.
            </p>
          </div>

          <section className="mt-10 grid gap-4 border-t border-hairline pt-6 md:grid-cols-2">
            <div>
              <h2 className="text-sm font-semibold text-ink">Claread 标注</h2>
              <p className="mt-2 text-sm leading-6 text-muted">
                patterns 指城市空间中可被识别的规律，不是单纯的图案。
              </p>
            </div>
            <div>
              <h2 className="text-sm font-semibold text-ink">句子批注</h2>
              <p className="mt-2 text-sm leading-6 text-muted">
                without becoming invisible 表示熟悉不等于失去感知，形成观点转折。
              </p>
            </div>
          </section>
        </article>

        <div className="mt-6 flex flex-wrap items-center gap-3">
          <Link
            href={`/login?next=/daily/${encodeURIComponent(date)}&intent=save` as Route}
            className="focus-ring inline-flex min-h-11 items-center gap-2 rounded-pill bg-lens-blue px-5 text-sm font-semibold text-surface transition-opacity hover:opacity-90"
          >
            <BookMarked aria-hidden="true" className="h-4 w-4" />
            加入我的阅读记录
          </Link>
          <Link
            href={`/login?next=/daily/${encodeURIComponent(date)}&intent=save` as Route}
            className="focus-ring inline-flex min-h-11 items-center gap-2 rounded-pill border border-hairline bg-surface-warm px-5 text-sm font-semibold text-ink transition-colors hover:border-muted"
          >
            <Star aria-hidden="true" className="h-4 w-4 text-lens-blue" />
            收藏
          </Link>
        </div>
      </div>
    </main>
  );
}
