import { ArrowLeft, ExternalLink } from "lucide-react";
import type { Metadata } from "next";
import type { Route } from "next";
import Image from "next/image";
import Link from "next/link";
import { notFound } from "next/navigation";
import { appReadRoute, dailyArticleRoute, dailyRoute, loginRoute } from "@/lib/routes";
import { fetchDailyReaderArticle } from "@/services/api/daily-reader";
import { getWebSession } from "@/services/bff/session";
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

import { DailyArticleBody } from "./DailyArticleBody";
import { DailyArticleSaveButton } from "./DailyArticleSaveButton";
import { DailyArticleShareButton } from "./DailyArticleShareButton";

/* ---------- SEO / 分享元信息（C-3） ---------- */

const FALLBACK_OG_IMAGE = "/brand/claread-horizontal-bilingual.png";

function articleDescription(article: DailyReaderArticle): string {
  const raw =
    article.subtitle ||
    article.footerAnalysis.articleTakeaway ||
    article.footerAnalysis.summary ||
    "每天一篇英文精读：原文、译文与逐段解析。";
  return raw.length > 150 ? `${raw.slice(0, 149)}…` : raw;
}

type DailyArticlePageProps = {
  params: Promise<{ articleId: string }>;
  searchParams: Promise<{ intent?: string | string[] }>;
};

export async function generateMetadata({ params }: DailyArticlePageProps): Promise<Metadata> {
  const { articleId } = await params;
  const result = await fetchDailyReaderArticle(articleId);

  if (!result.ok) {
    return { title: "每日精读" };
  }

  const article = result.data;
  const description = articleDescription(article);
  const image = article.coverImageUrl ?? FALLBACK_OG_IMAGE;

  return {
    title: article.title,
    description,
    openGraph: {
      title: article.title,
      description,
      type: "article",
      locale: "zh_CN",
      publishedTime: article.publishDate,
      images: [{ url: image, alt: article.title }],
    },
    twitter: {
      card: "summary_large_image",
      title: article.title,
      description,
      images: [image],
    },
  };
}

/* ---------- Publication Header ---------- */

function PublicationHeader({ isSignedIn }: { isSignedIn: boolean }) {
  return (
    <header className="mx-auto grid max-w-7xl grid-cols-[1fr_auto_1fr] items-center px-5 py-5 sm:px-8 lg:px-12">
      <Link
        href={dailyRoute}
        className="dr-font-ui focus-ring inline-flex min-h-11 w-fit items-center gap-2 text-[length:var(--dr-type-caption-size)] font-semibold text-[color:var(--dr-meta)] transition-colors hover:text-[color:var(--dr-accent)]"
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
        className="h-auto w-28 opacity-80 sm:w-36"
      />
      <Link
        href={isSignedIn ? appReadRoute : loginRoute(dailyRoute)}
        className="dr-font-ui focus-ring ml-auto inline-flex min-h-11 items-center text-[length:var(--dr-type-caption-size)] font-semibold text-[color:var(--dr-meta)] transition-colors hover:text-[color:var(--dr-accent)]"
      >
        {isSignedIn ? "进入 Claread" : "登录"}
      </Link>
    </header>
  );
}

function ArticleCover({ article }: { article: DailyReaderArticle }) {
  const image = article.body.images?.find((item) => item.role === "cover");
  const imageUrl = image?.url || article.coverImageUrl;
  const caption = image?.captionZh || image?.sourceCaption;

  if (!imageUrl) return null;

  return (
    <figure className="mx-auto mt-10 max-w-6xl px-5 sm:px-8">
      <div
        role="img"
        aria-label={caption || `${article.title} 配图`}
        className="aspect-[var(--dr-ratio-hero)] bg-[var(--dr-paper-raised)] bg-cover bg-center grayscale-[0.12] contrast-[0.94] saturate-[0.82]"
        style={{ backgroundImage: `url("${imageUrl}")` }}
      />
      {caption ? (
        <figcaption className="dr-font-zh mt-2 text-[length:var(--dr-type-caption-size)] leading-[var(--dr-type-caption-lh)] text-[color:var(--dr-meta)]">
          {caption}
        </figcaption>
      ) : null}
    </figure>
  );
}

function ArticleOpener({ article }: { article: DailyReaderArticle }) {
  const hasCover = Boolean(
    article.coverImageUrl || article.body.images?.some((image) => image.role === "cover"),
  );
  const originalTitle = article.originalTitle && article.originalTitle !== article.title
    ? article.originalTitle
    : null;

  return (
    <section className="border-y border-[color:var(--dr-rule)] py-10 sm:py-14">
      <div className="mx-auto max-w-[780px] px-5 sm:px-8">
        <div className="dr-font-mono mb-9 flex flex-wrap items-center justify-between gap-3 border-b border-[color:var(--dr-rule)] pb-3 text-[length:var(--dr-type-mono-size)] leading-[var(--dr-type-mono-lh)] text-[color:var(--dr-meta)]">
          <span>CLAREAD · 每日精读</span>
          <span>{formatPublishDate(article.publishDate)}</span>
        </div>

        <div
          className={hasCover ? undefined : "border-t-2 border-[color:var(--dr-accent)] bg-[var(--dr-paper-raised)] px-5 py-8 sm:px-8"}
        >
          <h1 className="dr-font-zh text-balance text-[length:var(--dr-type-hero-size)] font-normal leading-[var(--dr-type-hero-lh)] tracking-[-0.018em] text-[color:var(--dr-ink-zh)]">
            {article.title}
          </h1>

          {originalTitle ? (
            <p className="dr-font-en mt-5 max-w-[44rem] text-[length:var(--dr-type-deck-size)] leading-[var(--dr-type-deck-lh)] text-[color:var(--dr-ink)]">
              {originalTitle}
            </p>
          ) : null}

          {article.subtitleZh || article.subtitle ? (
            <p className="dr-font-zh mt-3 max-w-[42rem] text-[length:var(--dr-type-zh-size)] leading-[var(--dr-type-zh-lh)] text-[color:var(--dr-meta)]">
              {article.subtitleZh || article.subtitle}
            </p>
          ) : null}

          {article.tags.length > 0 && (
            <div className="mt-6 flex flex-wrap gap-2">
              {article.tags.slice(0, 4).map((tag) => (
                <span
                  key={tag}
                  className="dr-font-ui inline-block border border-[color:var(--dr-rule)] px-3 py-1 text-[length:var(--dr-type-caption-size)] text-[color:var(--dr-meta)]"
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
      </div>
      <ArticleCover article={article} />
    </section>
  );
}

/* ---------- Editorial Byline ---------- */

function ArticleByline({ article }: { article: DailyReaderArticle }) {
  return (
    <div className="dr-font-ui flex flex-wrap items-center justify-between gap-4 border-b border-[color:var(--dr-rule)] pb-5">
      <div className="flex flex-wrap items-center gap-1.5 text-[length:var(--dr-type-caption-size)] text-[color:var(--dr-meta)]">
        <span className="font-semibold text-[color:var(--dr-ink)]">{article.source}</span>
        <span aria-hidden="true">·</span>
        <time>{formatPublishDate(article.publishDate)}</time>
        <span aria-hidden="true">·</span>
        <span>{article.readTimeMinutes} 分钟阅读</span>
        <span aria-hidden="true">·</span>
        <span>{article.difficulty}</span>
      </div>
      <div className="flex items-center gap-1">
        <a
          href={article.sourceUrl}
          target="_blank"
          rel="noreferrer"
          className="focus-ring inline-flex min-h-11 min-w-11 items-center justify-center text-[color:var(--dr-meta)] transition-colors hover:text-[color:var(--dr-accent)]"
          aria-label="查看原文"
        >
          <ExternalLink aria-hidden="true" className="h-[18px] w-[18px]" />
        </a>
        <DailyArticleShareButton title={article.title} />
      </div>
    </div>
  );
}

/* ---------- Pre-reading Guide ---------- */

function PreReadingGuide({ article }: { article: DailyReaderArticle }) {
  if (!article.preReadingGuide) return null;

  return (
    <section className="mt-12 border-y border-[color:var(--dr-rule)] py-8">
      <h2 className="dr-font-mono mb-4 text-[length:var(--dr-type-mono-size)] leading-[var(--dr-type-mono-lh)] text-[color:var(--dr-accent)]">
        读前导引 · BEFORE YOU READ
      </h2>
      {article.preReadingGuide.overview ? (
        <p className="dr-font-zh text-[length:var(--dr-type-zh-size)] leading-[var(--dr-type-zh-lh)] text-[color:var(--dr-ink-zh)]">
          {article.preReadingGuide.overview}
        </p>
      ) : null}
      {article.preReadingGuide.questions.length > 0 ? (
        <ol className="mt-5 flex flex-col gap-3">
          {article.preReadingGuide.questions.map((question, index) => (
            <li key={question} className="flex gap-3">
              <span className="dr-font-mono shrink-0 text-[length:var(--dr-type-mono-size)] leading-[var(--dr-type-mono-lh)] text-[color:var(--dr-accent)]">
                {String(index + 1).padStart(2, "0")}
              </span>
              <span className="dr-font-zh text-[length:var(--dr-type-zh-size)] leading-[var(--dr-type-zh-lh)] text-[color:var(--dr-ink-zh)]">
                {question}
              </span>
            </li>
          ))}
        </ol>
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
    "dr-font-zh text-[length:var(--dr-type-zh-size)] font-semibold leading-[var(--dr-type-zh-lh)] text-[color:var(--dr-ink)]";
  const sectionCountClass =
    "dr-font-mono ml-2 align-middle text-[length:var(--dr-type-mono-size)] font-normal leading-[var(--dr-type-mono-lh)] text-[color:var(--dr-meta)]";
  const analysisLabelClass =
    "dr-font-zh text-[length:var(--dr-type-caption-size)] font-semibold leading-[var(--dr-type-caption-lh)] text-[color:var(--dr-meta)]";

  return (
    <section className="relative mt-20 border-t border-[color:var(--dr-rule)] pt-12">
      <div className="mb-12 flex items-center gap-4">
        <h2 className="dr-font-zh text-[length:var(--dr-type-headline-size)] font-normal leading-[var(--dr-type-headline-lh)] text-[color:var(--dr-ink-zh)]">
          今日精读收束
        </h2>
        <span aria-hidden="true" className="h-px flex-1 bg-[var(--dr-rule)]" />
      </div>

      {analysis.articleTakeaway ? (
        <div className="border-t border-[color:var(--dr-rule)] pt-4">
          <p className={analysisLabelClass}>本篇要点</p>
          <p className="dr-font-zh mt-3 text-[length:var(--dr-type-zh-size)] leading-[var(--dr-type-zh-lh)] text-[color:var(--dr-ink-zh)]">
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
                  <span className="dr-font-mono absolute left-0 top-0 text-[length:var(--dr-type-mono-size)] leading-[var(--dr-type-mono-lh)] text-[color:var(--dr-meta)]">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span className="dr-font-zh inline-block text-[length:var(--dr-type-caption-size)] font-semibold leading-[var(--dr-type-caption-lh)] text-[color:var(--dr-accent)]">
                    {move.moveType}
                  </span>
                  <p className={`${analysisLabelClass} mt-4`}>原文例句</p>
                  <p className="dr-font-en mt-2 text-[length:var(--dr-type-body-size)] italic leading-[var(--dr-type-body-lh)] text-[color:var(--dr-ink)]">
                    &ldquo;{move.anchor}&rdquo;
                  </p>
                  <p className={`${analysisLabelClass} mt-4`}>为什么值得借</p>
                  <p className="dr-font-zh mt-3 text-[length:var(--dr-type-zh-size)] leading-[var(--dr-type-zh-lh)] text-[color:var(--dr-ink-zh)]">
                    {move.explanation}
                  </p>
                  {move.reusablePattern ? (
                    <div className="mt-4 border-t border-[color:var(--dr-rule)] pt-3">
                      <p className={analysisLabelClass}>
                        可借句式
                      </p>
                      <p className="dr-font-en mt-1 text-[length:var(--dr-type-zh-size)] leading-[var(--dr-type-zh-lh)] text-[color:var(--dr-ink)]">
                        {move.reusablePattern}
                      </p>
                    </div>
                  ) : null}
                  {i < analysis.writingMoves!.length - 1 && (
                    <div className="mt-10 h-px bg-[var(--dr-rule)]" />
                  )}
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {analysis.sentenceNotes && analysis.sentenceNotes.length > 0 ? (
          <div className="border-t border-[color:var(--dr-rule)] pt-12">
            <h3 className={`${sectionTitleClass} mb-6`}>
              长难句
              <span className={sectionCountClass}>{analysis.sentenceNotes.length}</span>
            </h3>
            <div className="space-y-9">
              {analysis.sentenceNotes.map((note, index) => (
                <div key={`sn-${index}`}>
                  <p className={analysisLabelClass}>原句</p>
                  <p className="dr-font-en text-[length:var(--dr-type-body-size)] leading-[var(--dr-type-body-lh)] text-[color:var(--dr-ink)]">
                    {note.sentence}
                  </p>
                  <p className={`${analysisLabelClass} mt-4`}>译文</p>
                  <p className="dr-font-zh mt-3 text-[length:var(--dr-type-zh-size)] leading-[var(--dr-type-zh-lh)] text-[color:var(--dr-ink-zh)]">
                    {note.translation}
                  </p>
                  {note.breakdown ? (
                    <div className="mt-4 border-t border-[color:var(--dr-rule)] pt-3">
                      <p className={analysisLabelClass}>结构拆解</p>
                      <p className="dr-font-zh mt-2 text-[length:var(--dr-type-zh-size)] leading-[var(--dr-type-zh-lh)] text-[color:var(--dr-meta)]">
                        {note.breakdown}
                      </p>
                    </div>
                  ) : null}
                  {note.takeaway ? (
                    <div className="mt-4 border-t border-[color:var(--dr-rule)] pt-3">
                      <p className={analysisLabelClass}>学习点</p>
                      <p className="dr-font-zh mt-1 text-[length:var(--dr-type-zh-size)] leading-[var(--dr-type-zh-lh)] text-[color:var(--dr-ink-zh)]">
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
          <div className="border-t border-[color:var(--dr-rule)] pt-12">
            <h3 className={`${sectionTitleClass} mb-6`}>
              关键表达
              <span className={sectionCountClass}>{analysis.keyExpressions.length}</span>
            </h3>
            <div className="grid gap-x-8 gap-y-6 sm:grid-cols-2">
              {analysis.keyExpressions.map((item, i) => (
                <div key={`ke-${i}`}>
                  <p className="dr-font-en text-[length:var(--dr-type-body-size)] font-semibold leading-[var(--dr-type-body-lh)] text-[color:var(--dr-ink)]">
                    {item.expression}
                  </p>
                  <p className="dr-font-zh mt-1 text-[length:var(--dr-type-zh-size)] leading-[var(--dr-type-zh-lh)] text-[color:var(--dr-ink-zh)]">
                    {item.gloss}
                  </p>
                  {item.usageNote ? (
                    <p className="dr-font-zh mt-2 text-[length:var(--dr-type-caption-size)] leading-[var(--dr-type-caption-lh)] text-[color:var(--dr-meta)]">
                      {item.usageNote}
                    </p>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {analysis.discussionQuestions.length > 0 ? (
          <div className="border-t border-[color:var(--dr-rule)] pt-12">
            <h3 className={`${sectionTitleClass} mb-6`}>
              讨论问题
              <span className={sectionCountClass}>{analysis.discussionQuestions.length}</span>
            </h3>
            <div className="space-y-4">
              {analysis.discussionQuestions.map((question, i) => (
                <div key={`dq-${i}`} className="flex gap-3">
                  <span className="dr-font-mono mt-1 shrink-0 text-[length:var(--dr-type-mono-size)] leading-[var(--dr-type-mono-lh)] text-[color:var(--dr-accent)]">
                    Q{String(i + 1).padStart(2, "0")}
                  </span>
                  <p className="dr-font-zh text-[length:var(--dr-type-zh-size)] leading-[var(--dr-type-zh-lh)] text-[color:var(--dr-ink-zh)]">
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

export default async function DailyArticlePage({ params, searchParams }: DailyArticlePageProps) {
  const { articleId } = await params;
  const { intent } = await searchParams;
  const [result, session] = await Promise.all([
    fetchDailyReaderArticle(articleId),
    getWebSession(),
  ]);

  if (!result.ok) {
    notFound();
  }

  const article = result.data;
  const canFavorite = session.kind === "authenticated" || session.kind === "debug";

  // Article 结构化数据（JSON-LD），供搜索引擎与分享卡片识别。
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "Article",
    headline: article.title,
    description: articleDescription(article),
    ...(article.coverImageUrl ? { image: article.coverImageUrl } : {}),
    datePublished: article.publishDate,
    inLanguage: "en",
    sourceOrganization: { "@type": "Organization", name: article.source },
  };

  return (
    <main className="daily-reader-surface min-h-screen pb-24">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd).replace(/</g, "\\u003c") }}
      />
      <PublicationHeader isSignedIn={canFavorite} />

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
          <section className="mt-16 border-t border-[color:var(--dr-rule)] pt-8">
            <div>
              <h2 className="dr-font-zh text-[length:var(--dr-type-zh-size)] font-semibold leading-[var(--dr-type-zh-lh)] text-[color:var(--dr-ink)]">
                来源
              </h2>
              <a
                href={article.sourceUrl}
                target="_blank"
                rel="noreferrer"
                className="dr-font-ui mt-2 inline-flex min-h-11 items-center gap-2 text-[length:var(--dr-type-caption-size)] leading-[var(--dr-type-caption-lh)] text-[color:var(--dr-accent)]"
              >
                {article.source}
                <ExternalLink aria-hidden="true" className="h-3.5 w-3.5" />
              </a>
            </div>
          </section>

          {/* Bottom actions — 单一主行动（P2-11：删除与主 CTA 同 href 的「收藏」） */}
          <div className="mt-12 border-t border-[color:var(--dr-rule)] pt-8">
            <p className="dr-font-zh max-w-[34rem] text-[length:var(--dr-type-zh-size)] leading-[var(--dr-type-zh-lh)] text-[color:var(--dr-meta)]">
              保存这篇精读，把逐段导读、译文与表达解析带进你的阅读记录。
            </p>
            <DailyArticleSaveButton
              articleId={article.id}
              autoSave={canFavorite && intent === "save"}
              canFavorite={canFavorite}
              loginHref={loginSaveRoute(article.id)}
            />
          </div>
        </div>
      </article>
    </main>
  );
}
