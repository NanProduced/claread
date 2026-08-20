"use client";

import Image from "next/image";
import { useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import type {
  DailyReaderArticle,
  DailyReaderHighlight,
  DailyReaderImageBlock,
} from "@/types/view/DailyReaderVm";
import { ReadingNoteExpander, TranslationExpander } from "./EditorialExpanders";
import { InteractiveHighlight } from "./InteractiveHighlight";

function renderHighlightedText(
  text: string,
  highlights: DailyReaderHighlight[],
  activeHighlightId: string | null,
  onActivate: (highlight: DailyReaderHighlight) => void,
): ReactNode {
  const ranges = highlights
    .filter(
      (highlight) =>
        highlight.start >= 0 && highlight.end > highlight.start && highlight.end <= text.length,
    )
    .sort((a, b) => a.start - b.start);
  const accepted: DailyReaderHighlight[] = [];

  for (const range of ranges) {
    const previous = accepted[accepted.length - 1];
    if (!previous || range.start >= previous.end) accepted.push(range);
  }

  if (accepted.length === 0) return text;

  const nodes: ReactNode[] = [];
  let cursor = 0;

  accepted.forEach((highlight) => {
    if (highlight.start > cursor) nodes.push(text.slice(cursor, highlight.start));

    nodes.push(
      <InteractiveHighlight
        key={highlight.id}
        highlight={highlight}
        isActive={activeHighlightId === highlight.id}
        noteId={`daily-reader-note-${highlight.id}`}
        onActivate={onActivate}
      >
        {text.slice(highlight.start, highlight.end)}
      </InteractiveHighlight>,
    );
    cursor = highlight.end;
  });

  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
}

function AnnotationNote({ highlight }: { highlight: DailyReaderHighlight }) {
  return (
    <aside
      id={`daily-reader-note-${highlight.id}`}
      role="complementary"
      aria-label={`${highlight.text} 注释`}
      className="dr-font-zh mt-5 border-t border-[color:var(--dr-rule)] bg-[var(--dr-paper-raised)] px-4 py-3 text-[length:var(--dr-type-caption-size)] leading-6 text-[color:var(--dr-ink-zh)] xl:absolute xl:left-[calc(100%+2.5rem)] xl:top-0 xl:mt-0 xl:w-64"
    >
      <p className="dr-font-en text-[length:var(--dr-type-body-size)] font-semibold leading-[var(--dr-type-body-lh)] text-[color:var(--dr-ink)]">
        {highlight.text}
      </p>
      {highlight.detail?.phonetic || highlight.detail?.pos ? (
        <p className="dr-font-mono mt-1 text-[length:var(--dr-type-mono-size)] leading-[var(--dr-type-mono-lh)] text-[color:var(--dr-meta)]">
          {[highlight.detail?.phonetic, highlight.detail?.pos].filter(Boolean).join(" · ")}
        </p>
      ) : null}
      <p className="mt-2">{highlight.gloss}</p>
      {highlight.detail?.contextExplanation ? (
        <p className="mt-2 border-t border-[color:var(--dr-rule)] pt-2 text-[color:var(--dr-meta)]">
          {highlight.detail.contextExplanation}
        </p>
      ) : null}
    </aside>
  );
}

const inlineImageWidth: Record<DailyReaderImageBlock["layout"], string> = {
  "full-bleed": "w-full",
  "two-third": "sm:w-2/3",
  "half-float": "sm:w-1/2",
};

function InlineImage({ image }: { image: DailyReaderImageBlock }) {
  const caption = image.captionZh || image.sourceCaption;

  return (
    <figure className={cn("my-14", inlineImageWidth[image.layout])}>
      <div className="relative aspect-[3/2] overflow-hidden bg-[var(--dr-paper-raised)]">
        <Image
          src={image.url}
          alt={caption || "文章配图"}
          fill
          sizes="(min-width: 640px) 680px, 100vw"
          className="object-cover grayscale-[0.12] contrast-[0.94] saturate-[0.82]"
        />
      </div>
      {caption ? (
        <figcaption className="dr-font-zh mt-2 text-[length:var(--dr-type-caption-size)] leading-[var(--dr-type-caption-lh)] text-[color:var(--dr-meta)]">
          {caption}
        </figcaption>
      ) : null}
    </figure>
  );
}

export function DailyArticleBody({ article }: { article: DailyReaderArticle }) {
  const [activeHighlightId, setActiveHighlightId] = useState<string | null>(null);
  const inlineImage = article.body.images?.find((image) => image.role === "inline");
  const inlineImageAfter = Math.min(2, Math.max(0, Math.floor(article.body.paragraphs.length / 3)));

  if (article.body.paragraphs.length === 0) {
    return (
      <p className="dr-font-zh text-[length:var(--dr-type-caption-size)] leading-7 text-[color:var(--dr-meta)]">
        这篇每日精读暂无可展示正文。请稍后再试。
      </p>
    );
  }

  const activeHighlight = article.body.paragraphs
    .flatMap((paragraph) => paragraph.highlights)
    .find((highlight) => highlight.id === activeHighlightId);

  const activateHighlight = (highlight: DailyReaderHighlight) => {
    setActiveHighlightId((current) => (current === highlight.id ? null : highlight.id));
  };

  return (
    <div className="dr-font-en mt-16 text-[length:var(--dr-type-body-size)] leading-[var(--dr-type-body-lh)] text-[color:var(--dr-ink)]">
      {article.body.paragraphs.map((paragraph, index) => {
        const paragraphNumber = index + 1;
        const hasDropCap = index === 0 && /^[A-Za-z]/.test(paragraph.text);
        const paragraphNote =
          activeHighlight?.paragraphId === paragraph.id ? activeHighlight : undefined;

        return (
          <div key={paragraph.id}>
            <section className="group relative mb-14">
              <span className="dr-font-mono mb-2 block select-none text-[length:var(--dr-type-mono-size)] leading-[var(--dr-type-mono-lh)] text-[color:var(--dr-meta)] md:absolute md:-left-10 md:top-2 md:mb-0 md:w-6 md:text-right">
                {String(paragraphNumber).padStart(2, "0")}
              </span>

              {paragraph.readingNote ? (
                <ReadingNoteExpander
                  note={paragraph.readingNote}
                  paragraphNumber={paragraphNumber}
                />
              ) : null}

              <p
                className={cn(
                  hasDropCap &&
                    "first-letter:float-left first-letter:mr-3 first-letter:font-normal first-letter:text-[4.2em] first-letter:leading-[0.73] first-letter:text-[color:var(--dr-ink)]",
                )}
              >
                {renderHighlightedText(
                  paragraph.text,
                  paragraph.highlights,
                  activeHighlightId,
                  activateHighlight,
                )}
              </p>

              {paragraphNote ? <AnnotationNote highlight={paragraphNote} /> : null}

              {paragraph.translation ? (
                <TranslationExpander
                  translation={paragraph.translation}
                  paragraphNumber={paragraphNumber}
                />
              ) : null}
            </section>

            {inlineImage && index === inlineImageAfter ? <InlineImage image={inlineImage} /> : null}
          </div>
        );
      })}
    </div>
  );
}
