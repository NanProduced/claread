import { BookMarked, Play, Search } from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import { getVocabularyList, type VocabularyBffStatus } from "@/services/bff/vocabulary";
import { VocabularyClient } from "./VocabularyClient";

const reviewRoute = "/review" as Route;

function statusLabel(status: VocabularyBffStatus): string {
  switch (status) {
    case "ready":
      return "已同步";
    case "unauthenticated":
      return "会话已过期";
    case "mock_session":
      return "会话不可用";
    case "upstream_unavailable":
      return "服务暂不可用";
    case "upstream_error":
      return "读取失败";
  }
}

export default async function VocabularyPage() {
  const vocabulary = await getVocabularyList();
  const learningCount = vocabulary.items.filter((item) => !item.mastered).length;
  const masteredCount = vocabulary.items.length - learningCount;
  const canReview = learningCount > 0;

  return (
    <main className="paper-grain min-h-screen px-5 py-7 text-ink sm:px-8 lg:px-10">
      <div className="mx-auto grid max-w-7xl gap-8 xl:grid-cols-[minmax(0,1fr)_320px]">
        <section className="min-w-0">
          <header className="mb-7 flex flex-col gap-5 border-b border-hairline pb-6 md:flex-row md:items-end md:justify-between">
            <div className="max-w-2xl">
              <p className="mb-3 text-xs font-semibold text-muted">词汇资产</p>
              <h1 className="font-headline text-[2.15rem] font-semibold leading-tight tracking-normal text-ink sm:text-[2.65rem]">
                生词本
              </h1>
              <p className="mt-3 text-sm leading-6 text-muted">
                只保存你主动加入的原词、上下文和来源文章。词典保持轻量，给后续 AI 词汇增强留下干净数据。
              </p>
              {vocabulary.message ? (
                <p className="mt-3 text-sm leading-6 text-muted">{vocabulary.message}</p>
              ) : null}
            </div>
            {canReview ? (
              <Link
                href={reviewRoute}
                className="focus-ring inline-flex min-h-11 items-center justify-center gap-2 rounded-pill bg-lens-blue px-5 text-sm font-semibold text-surface transition-opacity hover:opacity-90"
              >
                <Play aria-hidden="true" className="h-4 w-4 fill-current" />
                开始复习 {Math.min(learningCount, 20)} 个
              </Link>
            ) : (
              <button
                className="inline-flex min-h-11 cursor-not-allowed items-center justify-center rounded-pill border border-hairline bg-surface px-5 text-sm font-semibold text-muted"
                disabled
                type="button"
              >
                暂无待复习
              </button>
            )}
          </header>

          <VocabularyClient items={vocabulary.items} status={vocabulary.status} />
        </section>

        <aside className="space-y-5 xl:pt-[7.4rem]">
          <section className="rounded-panel border border-hairline bg-surface p-5 shadow-surface-quiet">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
              <BookMarked aria-hidden="true" className="h-4 w-4 text-lens-blue" />
              生词状态
            </h2>
            <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
              <div>
                <dt className="text-xs text-muted">学习中</dt>
                <dd className="mt-1 font-semibold text-ink">{learningCount}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted">已掌握</dt>
                <dd className="mt-1 font-semibold text-ink">{masteredCount}</dd>
              </div>
              <div className="col-span-2">
                <dt className="text-xs text-muted">同步</dt>
                <dd className="mt-1 font-semibold text-ink">{statusLabel(vocabulary.status)}</dd>
              </div>
            </dl>
          </section>

          <section className="rounded-panel border border-hairline bg-reader-paper p-5">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
              <Search aria-hidden="true" className="h-4 w-4 text-lens-blue" />
              查询历史不保存
            </h2>
            <p className="mt-2 text-sm leading-6 text-muted">
              点词查询只服务当前阅读。进入生词本的，是用户明确保存过的词和上下文。
            </p>
          </section>
        </aside>
      </div>
    </main>
  );
}
