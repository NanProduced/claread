import { ArrowRight, BookOpen } from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import { ClareadStamp } from "@/components/brand/BrandMarks";
import { AnalyzeSubmitForm } from "./AnalyzeSubmitForm";

const dailyRoute = "/daily" as Route;
const libraryRoute = "/library" as Route;

export default function PasteToReadPage() {
  return (
    <main className="min-h-screen bg-[oklch(96.8%_0.012_84)] px-4 py-5 text-ink sm:px-6 lg:px-8">
      <div className="mx-auto max-w-[1180px]">
        <header className="mb-5 flex flex-col gap-4 border-b border-hairline pb-5 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <ClareadStamp label="READING DESK" className="bg-reader-paper" />
            <h1 className="mt-2 font-headline text-[2.15rem] font-semibold leading-tight tracking-normal text-ink sm:text-[2.8rem]">
              打开一篇文章
            </h1>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-muted">
              从正文开始，而不是从配置开始。标注、旁注和生词操作会在 Reader 里贴近文章出现。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Link
              href={dailyRoute}
              className="focus-ring inline-flex min-h-10 items-center gap-2 rounded-pill border border-hairline bg-surface-warm px-4 text-sm font-semibold text-ink transition-colors hover:border-muted"
            >
              <BookOpen aria-hidden="true" className="h-4 w-4 text-lens-blue" />
              今日精读
            </Link>
            <Link
              href={libraryRoute}
              className="focus-ring inline-flex min-h-10 items-center gap-2 rounded-pill border border-hairline bg-surface-warm px-4 text-sm font-semibold text-ink transition-colors hover:border-muted"
            >
              阅读记录
              <ArrowRight aria-hidden="true" className="h-4 w-4" />
            </Link>
          </div>
        </header>

        <AnalyzeSubmitForm />

        <div className="mt-5 flex flex-col gap-3 border-t border-hairline pt-4 text-sm text-muted sm:flex-row sm:items-center sm:justify-between">
          <p>没有现成文章时，可以从今日精读开始。它是公开内容，读完后再决定是否保存为个人资产。</p>
          <Link
            href={dailyRoute}
            className="focus-ring inline-flex min-h-10 items-center gap-2 rounded-pill px-1 text-sm font-semibold text-lens-blue"
          >
            打开今日精读
            <ArrowRight aria-hidden="true" className="h-4 w-4" />
          </Link>
        </div>
      </div>
    </main>
  );
}
