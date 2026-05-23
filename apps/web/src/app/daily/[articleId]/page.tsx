import { ArrowLeft, BookMarked, ExternalLink, Star } from "lucide-react";
import type { Route } from "next";
import Image from "next/image";
import Link from "next/link";
import { notFound } from "next/navigation";
import { fetchDailyReaderArticle } from "@/services/api/daily-reader";
import type { DailyReaderArticle } from "@/types/view/DailyReaderVm";

export const dynamic = "force-dynamic";

function loginSaveRoute(articleId: string): Route {
  return `/login?next=/daily/${encodeURIComponent(articleId)}&intent=save` as Route;
}

function formatPublishDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
  }).format(date);
}

import { DailyArticleBody } from "./DailyArticleBody";

function FooterAnalysis({ article }: { article: DailyReaderArticle }) {
  const analysis = article.footerAnalysis;
  const hasAnalysis =
    analysis.articleTakeaway ||
    analysis.keyExpressions.length > 0 ||
    (analysis.writingMoves && analysis.writingMoves.length > 0) ||
    (analysis.sentenceNotes && analysis.sentenceNotes.length > 0) ||
    analysis.discussionQuestions.length > 0;

  if (!hasAnalysis) {
    return null;
  }

  return (
    <section className="relative mt-20 border-t border-hairline pt-16">
      <div className="mb-12 flex items-center justify-center">
        <span className="block h-px w-12 bg-hairline"></span>
        <h2 className="mx-4 text-xs font-bold uppercase tracking-[0.2em] text-muted">Analysis</h2>
        <span className="block h-px w-12 bg-hairline"></span>
      </div>

      {analysis.articleTakeaway ? (
        <p className="mx-auto text-center font-reading text-lg font-medium leading-[1.7] text-ink">
          &ldquo;{analysis.articleTakeaway}&rdquo;
        </p>
      ) : null}

      <div className="mx-auto mt-16 space-y-16">
        {analysis.writingMoves && analysis.writingMoves.length > 0 ? (
          <div>
            <h3 className="mb-6 font-sans text-xs font-bold uppercase tracking-[0.15em] text-ink">Writing Moves</h3>
            <div className="space-y-10">
              {analysis.writingMoves.map((move, i) => (
                <div key={`wm-${i}`} className="border-l-2 border-hairline pl-5">
                  <span className="inline-block font-sans text-[0.7rem] font-bold uppercase tracking-wider text-lens-blue">
                    {move.moveType}
                  </span>
                  <p className="mt-3 font-reading text-[1.1rem] italic leading-[1.8] text-ink">
                    &ldquo;{move.anchor}&rdquo;
                  </p>
                  <p className="mt-3 text-[0.95rem] leading-[1.8] text-ink-soft">{move.explanation}</p>
                  {move.reusablePattern ? (
                    <p className="mt-4 font-sans text-[0.85rem] font-medium tracking-wide text-muted">
                      PATTERN: {move.reusablePattern}
                    </p>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {analysis.sentenceNotes && analysis.sentenceNotes.length > 0 ? (
          <div className="border-t border-hairline pt-12">
            <h3 className="mb-6 font-sans text-xs font-bold uppercase tracking-[0.15em] text-ink">Sentence Analysis</h3>
            <div className="space-y-10">
              {analysis.sentenceNotes.map((note, i) => (
                <div key={`sn-${i}`}>
                  <p className="font-reading text-[1.1rem] leading-[1.8] text-ink">{note.sentence}</p>
                  <p className="mt-3 text-[0.95rem] leading-[1.8] text-ink-soft">{note.translation}</p>
                  {note.breakdown ? (
                    <p className="mt-4 border-l border-hairline pl-4 text-[0.9rem] leading-[1.7] text-muted">{note.breakdown}</p>
                  ) : null}
                  {note.takeaway ? (
                    <p className="mt-4 inline-block border border-hairline px-3 py-1 font-sans text-[0.85rem] font-medium text-ink-soft">
                      {note.takeaway}
                    </p>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {analysis.keyExpressions.length > 0 ? (
          <div className="border-t border-hairline pt-12">
            <h3 className="mb-6 font-sans text-xs font-bold uppercase tracking-[0.15em] text-ink">Key Expressions</h3>
            <div className="grid gap-x-8 gap-y-6 sm:grid-cols-2">
              {analysis.keyExpressions.map((item, i) => (
                <div key={`ke-${i}`}>
                  <p className="font-sans text-[1.05rem] font-bold text-ink">{item.expression}</p>
                  <p className="mt-1 text-[0.95rem] leading-[1.6] text-ink-soft">{item.gloss}</p>
                  {item.usageNote ? (
                    <p className="mt-2 text-[0.85rem] leading-[1.6] text-muted">{item.usageNote}</p>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {analysis.discussionQuestions.length > 0 ? (
          <div className="border-t border-hairline pt-12">
            <h3 className="mb-6 font-sans text-xs font-bold uppercase tracking-[0.15em] text-ink">Discussion</h3>
            <div className="space-y-4">
              {analysis.discussionQuestions.map((question, i) => (
                <div key={`dq-${i}`} className="flex gap-3">
                  <span className="mt-1 shrink-0 font-serif text-lg text-ink-soft opacity-40">Q.</span>
                  <p className="text-[1.05rem] font-medium leading-[1.7] text-ink">
                    {question}
                  </p>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}

export default async function DailyArticlePage({
  params,
}: {
  params: Promise<{ articleId: string }>;
}) {
  const { articleId } = await params;
  const result = await fetchDailyReaderArticle(articleId);

  if (!result.ok) {
    notFound();
  }

  const article = result.data;

  return (
    <main className="min-h-screen bg-[#FAF9F6] pb-24 text-ink">
      <header className="mx-auto flex max-w-[720px] items-center justify-between px-5 pt-8 sm:px-8">
        <Link
          href={"/daily" as Route}
          className="focus-ring inline-flex items-center gap-2 rounded-pill text-sm font-semibold text-muted hover:text-ink"
        >
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

      <article className="mx-auto mt-16 max-w-[680px] px-5 sm:px-8">
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-lens-blue">
          Daily Reader · {formatPublishDate(article.publishDate)}
        </p>
        <h1 className="mt-5 font-headline text-[2.5rem] font-semibold leading-tight tracking-normal text-ink sm:text-[3.6rem]">
          {article.title}
        </h1>
        {article.subtitle ? (
          <p className="mt-4 text-base leading-7 text-muted">{article.subtitle}</p>
        ) : null}
        <p className="mt-4 text-sm leading-6 text-muted">
          {article.source} · {article.difficulty} · {article.readTimeMinutes} 分钟
          {article.tags.length > 0 ? ` · ${article.tags.join(" / ")}` : ""}
        </p>
        {article.preReadingGuide ? (
          <section className="mt-12 border-l-2 border-lens-blue/30 pl-5">
            <h2 className="mb-2 text-[0.65rem] font-bold uppercase tracking-[0.2em] text-muted">Editor&apos;s Note</h2>
            {article.preReadingGuide.overview ? (
              <p className="font-reading text-[1.05rem] italic leading-[1.8] text-ink-soft">{article.preReadingGuide.overview}</p>
            ) : null}
            {article.preReadingGuide.questions.length > 0 ? (
              <div className="mt-4 flex flex-col gap-2">
                {article.preReadingGuide.questions.map((question) => (
                  <div key={question} className="flex gap-2">
                    <span className="mt-0.5 shrink-0 text-[0.7rem] text-lens-blue">✦</span>
                    <span className="font-sans text-[0.95rem] font-medium leading-snug text-ink-soft">
                      {question}
                    </span>
                  </div>
                ))}
              </div>
            ) : null}
          </section>
        ) : null}

        <DailyArticleBody article={article} />
        <FooterAnalysis article={article} />

        <section className="mt-16 grid gap-6 border-t border-hairline pt-8 md:grid-cols-2">
          <div>
            <h2 className="text-sm font-semibold text-ink">来源</h2>
            <a
              href={article.sourceUrl}
              target="_blank"
              rel="noreferrer"
              className="mt-2 inline-flex items-center gap-2 text-sm leading-6 text-lens-blue"
            >
              {article.source}
              <ExternalLink aria-hidden="true" className="h-3.5 w-3.5" />
            </a>
          </div>
          <div>
            <h2 className="text-sm font-semibold text-ink">标注说明</h2>
            <p className="mt-2 text-sm leading-6 text-muted">
              词汇、短语和语境标注只作用于英文原文。中文译文不做逐词颜色映射。
            </p>
          </div>
        </section>

        <div className="mt-12 flex flex-wrap items-center gap-3">
          <Link
            href={loginSaveRoute(article.id)}
            className="focus-ring inline-flex min-h-11 items-center gap-2 rounded-pill bg-lens-blue px-5 text-sm font-semibold text-surface transition-opacity hover:opacity-90"
          >
            <BookMarked aria-hidden="true" className="h-4 w-4" />
            加入我的阅读记录
          </Link>
          <Link
            href={loginSaveRoute(article.id)}
            className="focus-ring inline-flex min-h-11 items-center gap-2 rounded-pill border border-hairline bg-surface-warm px-5 text-sm font-semibold text-ink transition-colors hover:border-muted"
          >
            <Star aria-hidden="true" className="h-4 w-4 text-lens-blue" />
            收藏
          </Link>
        </div>
      </article>
    </main>
  );
}
