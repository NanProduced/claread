import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import type { DailyReaderArticle, DailyReaderHighlight } from "@/types/view/DailyReaderVm";
import { InteractiveHighlight } from "./InteractiveHighlight";
import { ReadingNoteExpander, TranslationExpander } from "./EditorialExpanders";

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
      <InteractiveHighlight key={highlight.id} highlight={highlight}>
        {text.slice(highlight.start, highlight.end)}
      </InteractiveHighlight>
    );
    cursor = highlight.end;
  });

  if (cursor < text.length) {
    nodes.push(text.slice(cursor));
  }

  return nodes;
}

export function DailyArticleBody({ article }: { article: DailyReaderArticle }) {
  if (article.body.paragraphs.length === 0) {
    return (
      <p className="text-sm leading-7 text-muted-foreground">
        这篇每日精读暂无可展示正文。请稍后再试。
      </p>
    );
  }

  return (
    <div className="mt-14 max-w-[65ch] space-y-14 font-reading text-[1.22rem] leading-[2] text-ink sm:text-[1.28rem]">
      {article.body.paragraphs.map((paragraph, index) => {
        const isFirst = index === 0;

        return (
          <section key={paragraph.id} className="group relative">
            <span className="absolute -left-12 top-1.5 hidden w-6 select-none text-right font-sans text-[0.7rem] font-medium text-subtle transition-colors group-hover:text-muted-foreground sm:block">
              {String(index + 1).padStart(2, "0")}
            </span>
            
            {paragraph.readingNote && (
              <ReadingNoteExpander note={paragraph.readingNote} />
            )}

            <p
              className={cn(
                isFirst &&
                  "first-letter:float-left first-letter:mr-3 first-letter:text-[4rem] first-letter:font-bold first-letter:leading-[0.8] first-letter:text-ink"
              )}
            >
              {renderHighlightedText(paragraph.text, paragraph.highlights)}
            </p>

            {paragraph.translation && (
              <TranslationExpander translation={paragraph.translation} />
            )}
          </section>
        );
      })}
    </div>
  );
}
