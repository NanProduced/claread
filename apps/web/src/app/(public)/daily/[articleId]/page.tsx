import { ArrowLeft, BookMarked, ExternalLink, Share2, Star } from "lucide-react";
import type { Route } from "next";
import Image from "next/image";
import Link from "next/link";
import { notFound } from "next/navigation";
import { dailyArticleRoute, dailyRoute, loginRoute } from "@/lib/routes";
import { fetchDailyReaderArticle } from "@/services/api/daily-reader";
import type { DailyReaderArticle } from "@/types/view/DailyReaderVm";

export const dynamic = "force-dynamic";

function loginSaveRoute(articleId: string): Route {
  return loginRoute(dailyArticleRoute(articleId), "save");
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

function formatShortDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(date);
}

import { DailyArticleBody } from "./DailyArticleBody";

/* ---------- Cover Theme → Editorial Plate Fallback ---------- */

const coverThemeGradients: Record<string, string> = {
  warm: "linear-gradient(135deg, #E8D5B7 0%, #C9A96E 40%, #8B7355 100%)",
  cool: "linear-gradient(135deg, #C5D5E4 0%, #8BA7C4 40%, #5A7A9B 100%)",
  neutral: "linear-gradient(135deg, #D4D0C8 0%, #A8A196 40%, #7A756D 100%)",
  dark: "linear-gradient(135deg, #3A3530 0%, #252220 40%, #1A1816 100%)",
};

function coverGradient(theme: string): string {
  return coverThemeGradients[theme] ?? coverThemeGradients.neutral;
}

/* ---------- Publication Header ---------- */

function PublicationHeader() {
  return (
    <header className="mx-auto flex max-w-7xl items-center justify-between px-5 py-6 sm:px-8 lg:px-12">
      <Link
        href={dailyRoute}
        className="focus-ring inline-flex items-center gap-2 rounded-pill text-sm font-semibold text-ink-soft transition-colors hover:text-lens-blue"
      >
        <ArrowLeft aria-hidden="true" className="h-4 w-4" />
        每日精读
      </Link>
      <Image
        src="/brand/claread-horizontal-bilingual.png"
        alt="Claread 透读"
        width={260}
        height={76}
        priority
        className="h-auto w-32 opacity-78 sm:w-40"
      />
    </header>
  );
}

function OptionalCoverPlate({ article }: { article: DailyReaderArticle }) {
  const hasCover = Boolean(article.coverImageUrl);
  const fallback = coverGradient(article.coverTheme);

  return (
    <figure className="mt-8 xl:col-start-3 xl:row-start-2 xl:ml-6 xl:mt-0 xl:w-64 2xl:ml-12 2xl:w-80">
      <div
        aria-hidden="true"
        className="relative aspect-[4/3] overflow-hidden border border-hairline bg-surface-warm shadow-[0_1px_0_rgba(23,21,17,0.04)]"
        style={{
          backgroundImage: hasCover
            ? `linear-gradient(180deg, rgba(23,21,17,0.02), rgba(23,21,17,0.18)), url("${article.coverImageUrl}")`
            : fallback,
          backgroundPosition: "center",
          backgroundSize: "cover",
        }}
      >
        <div className="absolute inset-x-0 bottom-0 flex items-center justify-between border-t border-white/20 bg-ink/70 px-3 py-2 font-sans text-[0.62rem] font-semibold tracking-[0.12em] text-white/82">
          <span>DAILY READER</span>
          <span>{article.source}</span>
        </div>
      </div>
      <figcaption className="mt-2 font-sans text-[0.68rem] leading-5 text-muted">
        {hasCover ? "Source image, cropped as an editorial plate." : "Generated editorial cover tone."}
      </figcaption>
    </figure>
  );
}

function ArticleOpener({ article }: { article: DailyReaderArticle }) {
  return (
    <section className="border-y border-hairline bg-[#F3EFE6]">
      <div className="mx-auto grid max-w-[680px] px-5 py-10 sm:px-8 lg:py-14 xl:max-w-none xl:grid-cols-[minmax(0,1fr)_minmax(0,680px)_minmax(0,1fr)] xl:px-5">
        <div className="mb-8 flex flex-wrap items-center justify-between gap-3 border-b border-ink/18 pb-3 font-sans text-[0.72rem] font-semibold tracking-[0.12em] text-muted xl:col-start-2">
          <span>Claread Daily Reader</span>
          <span>{formatPublishDate(article.publishDate)}</span>
        </div>

        <div className="min-w-0 xl:col-start-2 xl:row-start-2">
          <p className="text-xs font-semibold tracking-[0.16em] text-lens-blue">
            Daily Reader · {formatPublishDate(article.publishDate)}
          </p>

          <h1 className="mt-5 text-balance font-headline text-[clamp(2.35rem,4.4vw,3.55rem)] font-semibold leading-[1.04] tracking-normal text-ink">
            {article.title}
          </h1>

          {article.subtitle ? (
            <p className="mt-5 max-w-[44rem] font-reading text-[1.08rem] leading-7 text-muted">
              {article.subtitle}
            </p>
          ) : null}

          {article.tags.length > 0 && (
            <div className="mt-6 flex flex-wrap gap-2">
              {article.tags.slice(0, 4).map((tag) => (
                <span
                  key={tag}
                  className="inline-block border border-hairline bg-surface-warm/54 px-3 py-1 text-xs font-medium text-muted"
                >
                  {tag}
                </span>
              ))}
            </div>
          )}

          <div className="mt-8">
            <ArticleByline article={article} />
          </div>
        </div>

        <OptionalCoverPlate article={article} />
      </div>
    </section>
  );
}

/* ---------- Editorial Byline ---------- */

function ArticleByline({ article }: { article: DailyReaderArticle }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-4 border-b border-hairline pb-5">
      <div className="flex flex-wrap items-center gap-1.5 text-sm text-muted">
        <span className="font-medium text-ink">{article.source}</span>
        <span aria-hidden="true" className="text-subtle">·</span>
        <time>{formatShortDate(article.publishDate)}</time>
        <span aria-hidden="true" className="text-subtle">·</span>
        <span>{article.readTimeMinutes} min read</span>
        <span aria-hidden="true" className="text-subtle">·</span>
        <span>{article.difficulty}</span>
      </div>
      <div className="flex items-center gap-1">
        <Link
          href={loginSaveRoute(article.id)}
          className="focus-ring inline-flex h-9 w-9 items-center justify-center rounded-full text-muted transition-colors hover:bg-surface-warm hover:text-ink"
          aria-label="收藏"
        >
          <BookMarked aria-hidden="true" className="h-[18px] w-[18px]" />
        </Link>
        <a
          href={article.sourceUrl}
          target="_blank"
          rel="noreferrer"
          className="focus-ring inline-flex h-9 w-9 items-center justify-center rounded-full text-muted transition-colors hover:bg-surface-warm hover:text-ink"
          aria-label="查看原文"
        >
          <ExternalLink aria-hidden="true" className="h-[18px] w-[18px]" />
        </a>
        <button
          type="button"
          className="focus-ring inline-flex h-9 w-9 items-center justify-center rounded-full text-muted transition-colors hover:bg-surface-warm hover:text-ink"
          aria-label="分享"
        >
          <Share2 aria-hidden="true" className="h-[18px] w-[18px]" />
        </button>
      </div>
    </div>
  );
}

/* ---------- Pre-reading Guide ---------- */

function PreReadingGuide({ article }: { article: DailyReaderArticle }) {
  if (!article.preReadingGuide) return null;

  return (
    <section className="mt-12 border-y border-hairline py-8">
      <h2 className="mb-4 text-[0.65rem] font-bold tracking-[0.2em] text-lens-blue">
        Editor&apos;s Note
      </h2>
      {article.preReadingGuide.overview ? (
        <p className="font-reading text-[1.05rem] italic leading-[1.8] text-ink-soft">
          {article.preReadingGuide.overview}
        </p>
      ) : null}
      {article.preReadingGuide.questions.length > 0 ? (
        <div className="mt-5 flex flex-col gap-3">
          {article.preReadingGuide.questions.map((question) => (
            <div key={question} className="flex gap-3">
              <span className="mt-0.5 shrink-0 text-[0.7rem] text-lens-blue">✦</span>
              <span className="font-sans text-[0.95rem] font-medium leading-snug text-ink-soft">
                {question}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

/* ---------- Footer Analysis ---------- */

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

  const sectionTitleClass =
    "font-sans text-[0.78rem] font-bold tracking-[0.08em] text-ink";
  const sectionCountClass =
    "ml-2 align-middle font-sans text-[0.68rem] font-semibold tracking-normal text-muted";
  const analysisLabelClass =
    "font-sans text-[0.7rem] font-semibold tracking-[0.08em] text-muted";

  return (
    <section className="relative mt-20 border-t border-hairline pt-16">
      <div className="mb-12 flex items-center justify-center">
        <span className="block h-px w-12 bg-hairline"></span>
        <h2 className="mx-4 text-xs font-bold tracking-[0.2em] text-muted">Analysis</h2>
        <span className="block h-px w-12 bg-hairline"></span>
      </div>

      {analysis.articleTakeaway ? (
        <div className="mx-auto max-w-[38rem] text-center">
          <p className={analysisLabelClass}>本篇要点</p>
          <p className="mt-3 font-reading text-[1.08rem] leading-[1.75] text-ink-soft">
            {analysis.articleTakeaway}
          </p>
        </div>
      ) : null}

      <div className="mx-auto mt-16 space-y-16">
        {analysis.writingMoves && analysis.writingMoves.length > 0 ? (
          <div>
            <h3 className={`${sectionTitleClass} mb-6`}>
              写作借鉴
              <span className={sectionCountClass}>{analysis.writingMoves.length}</span>
            </h3>
            <div className="space-y-10">
              {analysis.writingMoves.map((move, i) => (
                <div key={`wm-${i}`} className="relative pl-8">
                  <span className="absolute left-0 top-0 font-sans text-[0.75rem] font-bold text-subtle">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span className="inline-block font-sans text-[0.7rem] font-bold tracking-wider text-lens-blue">
                    {move.moveType}
                  </span>
                  <p className={`${analysisLabelClass} mt-4`}>原文例句</p>
                  <p className="mt-2 font-reading text-[1rem] italic leading-[1.75] text-ink">
                    &ldquo;{move.anchor}&rdquo;
                  </p>
                  <p className={`${analysisLabelClass} mt-4`}>为什么值得借</p>
                  <p className="mt-3 text-[0.95rem] leading-[1.8] text-ink-soft">{move.explanation}</p>
                  {move.reusablePattern ? (
                    <div className="mt-4 border border-hairline bg-surface-warm/55 px-4 py-3">
                      <p className="font-sans text-[0.72rem] font-semibold tracking-[0.08em] text-muted">
                        可借句式
                      </p>
                      <p className="mt-1 font-reading text-[0.98rem] leading-[1.65] text-ink-soft">
                        {move.reusablePattern}
                      </p>
                    </div>
                  ) : null}
                  {i < analysis.writingMoves!.length - 1 && (
                    <div className="mt-10 h-px bg-hairline" />
                  )}
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {analysis.sentenceNotes && analysis.sentenceNotes.length > 0 ? (
          <div className="border-t border-hairline pt-12">
            <h3 className={`${sectionTitleClass} mb-6`}>
              长难句
              <span className={sectionCountClass}>{analysis.sentenceNotes.length}</span>
            </h3>
            <div className="space-y-9">
              {analysis.sentenceNotes.map((note, i) => (
                <div key={`sn-${i}`}>
                  <p className={analysisLabelClass}>原句</p>
                  <p className="font-reading text-[1.1rem] leading-[1.8] text-ink">{note.sentence}</p>
                  <p className={`${analysisLabelClass} mt-4`}>译文</p>
                  <p className="mt-3 text-[0.95rem] leading-[1.8] text-ink-soft">{note.translation}</p>
                  {note.breakdown ? (
                    <div className="mt-4 rounded-md bg-surface-warm/60 px-5 py-4">
                      <p className={analysisLabelClass}>结构拆解</p>
                      <p className="mt-2 text-[0.9rem] leading-[1.7] text-muted">{note.breakdown}</p>
                    </div>
                  ) : null}
                  {note.takeaway ? (
                    <div className="mt-4 border border-hairline px-3 py-2">
                      <p className={analysisLabelClass}>学习点</p>
                      <p className="mt-1 font-sans text-[0.9rem] font-medium leading-[1.65] text-ink-soft">
                        {note.takeaway}
                      </p>
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {analysis.keyExpressions.length > 0 ? (
          <div className="border-t border-hairline pt-12">
            <h3 className={`${sectionTitleClass} mb-6`}>
              关键表达
              <span className={sectionCountClass}>{analysis.keyExpressions.length}</span>
            </h3>
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
            <h3 className={`${sectionTitleClass} mb-6`}>
              讨论问题
              <span className={sectionCountClass}>{analysis.discussionQuestions.length}</span>
            </h3>
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

/* ---------- Main Page ---------- */

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
      <PublicationHeader />

      <article>
        <ArticleOpener article={article} />

        <div className="mx-auto max-w-[680px] px-5 sm:px-8 lg:px-0">
          {/* Pre-reading guide */}
          <PreReadingGuide article={article} />

          {/* Body */}
          <DailyArticleBody article={article} />

          {/* Footer analysis */}
          <FooterAnalysis article={article} />

          {/* Source */}
          <section className="mt-16 border-t border-hairline pt-8">
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
          </section>

          {/* Bottom actions */}
          <div className="mt-12 flex flex-wrap items-center gap-3">
            <Link
              href={loginSaveRoute(article.id)}
              className="focus-ring inline-flex min-h-11 items-center gap-2 rounded-pill bg-ink px-5 text-sm font-semibold transition-opacity hover:opacity-90"
              style={{ color: "#FAF9F6" }}
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
        </div>
      </article>
    </main>
  );
}
