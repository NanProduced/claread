import type { ReactNode } from "react";
import { ArrowLeft, BookMarked, ExternalLink, Star } from "lucide-react";
import type { Route } from "next";
import Image from "next/image";
import Link from "next/link";
import { notFound } from "next/navigation";
import { fetchDailyReaderArticle } from "@/services/api/daily-reader";
import type { DailyReaderArticle, DailyReaderHighlight } from "@/types/view/DailyReaderVm";

export const dynamic = "force-dynamic";

const highlightClass: Record<DailyReaderHighlight["type"], string> = {
  vocab_highlight: "bg-vocab-amber/25 text-ink ring-vocab-amber/35",
  phrase_gloss: "bg-phrase-lavender/25 text-ink ring-phrase-lavender/35",
  context_gloss: "bg-context-blue/18 text-ink ring-context-blue/30",
};

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

function renderHighlightedText(text: string, highlights: DailyReaderHighlight[]): ReactNode {
  const ranges = highlights
    .filter((highlight) => highlight.start >= 0 && highlight.end > highlight.start && highlight.end <= text.length)
    .sort((a, b) => a.start - b.start);
  const accepted: DailyReaderHighlight[] = [];

  for (const range of ranges) {
    const previous = accepted[accepted.length - 1];
    if (!previous || range.start >= previous.end) {
      accepted.push(range);
    }
  }

  if (accepted.length === 0) {
    return text;
  }

  const nodes: ReactNode[] = [];
  let cursor = 0;

  accepted.forEach((highlight) => {
    if (highlight.start > cursor) {
      nodes.push(text.slice(cursor, highlight.start));
    }

    nodes.push(
      <span
        key={highlight.id}
        className={`rounded-sm px-1 py-0.5 ring-1 ${highlightClass[highlight.type]}`}
        title={highlight.gloss}
      >
        {text.slice(highlight.start, highlight.end)}
      </span>,
    );
    cursor = highlight.end;
  });

  if (cursor < text.length) {
    nodes.push(text.slice(cursor));
  }

  return nodes;
}

function ArticleBody({ article }: { article: DailyReaderArticle }) {
  if (article.body.paragraphs.length === 0) {
    return (
      <p className="text-sm leading-7 text-muted">
        这篇每日精读暂无可展示正文。请稍后再试。
      </p>
    );
  }

  return (
    <div className="mt-10 max-w-[68ch] space-y-8 font-reading text-[1.22rem] leading-[1.95] text-ink sm:text-[1.28rem]">
      {article.body.paragraphs.map((paragraph, index) => (
        <section key={paragraph.id} className="group">
          <p>{renderHighlightedText(paragraph.text, paragraph.highlights)}</p>
          {paragraph.readingNote || paragraph.translation ? (
            <div className="mt-4 border-l border-hairline pl-4 font-sans text-sm leading-7 text-muted">
              {paragraph.readingNote?.focusQuestion ? (
                <p className="font-semibold text-ink">{paragraph.readingNote.focusQuestion}</p>
              ) : null}
              {paragraph.readingNote?.microSummary ? (
                <p className="mt-1">{paragraph.readingNote.microSummary}</p>
              ) : null}
              {paragraph.translation ? (
                <p className="mt-2 text-ink-soft">{paragraph.translation}</p>
              ) : null}
            </div>
          ) : null}
          <span className="mt-3 block font-sans text-[0.7rem] font-semibold text-subtle">
            {String(index + 1).padStart(2, "0")}
          </span>
        </section>
      ))}
    </div>
  );
}

function FooterAnalysis({ article }: { article: DailyReaderArticle }) {
  const analysis = article.footerAnalysis;
  const hasAnalysis =
    analysis.summary ||
    analysis.articleTakeaway ||
    analysis.structure.length > 0 ||
    analysis.keyExpressions.length > 0 ||
    analysis.discussionQuestions.length > 0;

  if (!hasAnalysis) {
    return null;
  }

  return (
    <section className="mt-10 border-t border-hairline pt-7">
      <h2 className="text-sm font-semibold text-ink">今日精读收束</h2>
      {analysis.summary ? (
        <p className="mt-3 text-sm leading-7 text-muted">{analysis.summary}</p>
      ) : null}
      {analysis.articleTakeaway ? (
        <p className="mt-3 rounded-[12px] border border-hairline bg-surface-warm px-4 py-3 text-sm leading-7 text-ink-soft">
          {analysis.articleTakeaway}
        </p>
      ) : null}
      {analysis.structure.length > 0 ? (
        <div className="mt-5 grid gap-3 md:grid-cols-3">
          {analysis.structure.slice(0, 3).map((part) => (
            <div key={`${part.label}-${part.title}`} className="border-t border-hairline pt-3">
              <p className="text-xs font-semibold text-structure-green">{part.label}</p>
              <h3 className="mt-1 text-sm font-semibold text-ink">{part.title}</h3>
              <p className="mt-2 text-xs leading-6 text-muted">{part.summary}</p>
            </div>
          ))}
        </div>
      ) : null}
      {analysis.keyExpressions.length > 0 ? (
        <div className="mt-6">
          <h3 className="text-xs font-semibold text-ink">关键表达</h3>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            {analysis.keyExpressions.slice(0, 4).map((item) => (
              <div key={`${item.expression}-${item.contextSentence}`} className="rounded-[12px] border border-hairline bg-reader-paper px-4 py-3">
                <p className="font-reading text-base leading-6 text-ink">{item.expression}</p>
                <p className="mt-1 text-xs leading-5 text-muted">{item.gloss}</p>
              </div>
            ))}
          </div>
        </div>
      ) : null}
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
    <main className="min-h-screen bg-[oklch(97%_0.012_84)] px-5 py-6 text-ink sm:px-8 lg:px-12">
      <div className="mx-auto max-w-5xl">
        <header className="flex items-center justify-between gap-6">
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

        <article className="reading-paper relative mt-8 overflow-hidden rounded-[2rem] border border-hairline px-6 py-8 shadow-[0_28px_80px_rgba(35,28,18,0.12)] sm:px-12 sm:py-12">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-lens-blue">
            Daily Reader · {formatPublishDate(article.publishDate)}
          </p>
          <h1 className="mt-5 max-w-3xl font-headline text-[2.5rem] font-semibold leading-tight tracking-normal text-ink sm:text-[3.6rem]">
            {article.title}
          </h1>
          {article.subtitle ? (
            <p className="mt-4 max-w-2xl text-base leading-7 text-muted">{article.subtitle}</p>
          ) : null}
          <p className="mt-4 text-sm leading-6 text-muted">
            {article.source} · {article.difficulty} · {article.readTimeMinutes} 分钟
            {article.tags.length > 0 ? ` · ${article.tags.join(" / ")}` : ""}
          </p>
          {article.preReadingGuide ? (
            <section className="mt-8 rounded-[16px] border border-hairline bg-surface-warm px-5 py-4">
              {article.preReadingGuide.overview ? (
                <p className="text-sm leading-7 text-ink-soft">{article.preReadingGuide.overview}</p>
              ) : null}
              {article.preReadingGuide.questions.length > 0 ? (
                <div className="mt-3 flex flex-wrap gap-2">
                  {article.preReadingGuide.questions.map((question) => (
                    <span key={question} className="rounded-pill border border-hairline bg-reader-paper px-3 py-1.5 text-xs font-semibold text-muted">
                      {question}
                    </span>
                  ))}
                </div>
              ) : null}
            </section>
          ) : null}

          <ArticleBody article={article} />
          <FooterAnalysis article={article} />

          <section className="mt-10 grid gap-4 border-t border-hairline pt-6 md:grid-cols-2">
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
        </article>

        <div className="mt-6 flex flex-wrap items-center gap-3">
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
      </div>
    </main>
  );
}
