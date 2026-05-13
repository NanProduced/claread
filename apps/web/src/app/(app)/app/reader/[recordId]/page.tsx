import type { ReactNode } from "react";
import { getReaderRecord, type ReaderDataSource } from "@/services/bff/reader";
import type { InlineMarkModel, SentenceModel } from "@/types/view/ReaderMockVm";

type ReaderPageProps = {
  params: Promise<{ recordId: string }>;
};

const toneClass: Record<InlineMarkModel["visualTone"], string> = {
  vocab: "bg-vocab-amber/25 text-ink underline decoration-vocab-amber decoration-2 underline-offset-4",
  phrase: "bg-phrase-lavender/25 text-ink underline decoration-phrase-lavender decoration-2 underline-offset-4",
  context: "bg-context-blue/15 text-ink underline decoration-context-blue decoration-2 underline-offset-4",
  grammar: "bg-grammar-violet/15 text-ink underline decoration-grammar-violet decoration-2 underline-offset-4",
  term: "bg-structure-green/15 text-ink underline decoration-structure-green decoration-2 underline-offset-4",
  logic: "bg-lens-blue/10 text-ink underline decoration-lens-blue decoration-2 underline-offset-4",
};

const toneLabel: Record<InlineMarkModel["visualTone"], string> = {
  vocab: "词汇",
  phrase: "短语",
  context: "语境",
  grammar: "语法",
  term: "术语",
  logic: "逻辑",
};

const dataSourceLabel: Record<ReaderDataSource, string> = {
  "upstream-render-scene": "FastAPI Render Scene",
  "upstream-source-text": "FastAPI Source Text",
  "mock-fallback": "Mock Fallback",
};

const entryTypeLabel: Record<string, string> = {
  grammar_note: "语法",
  sentence_analysis: "句析",
  term_note: "术语",
  logic_note: "逻辑",
  interpretation_note: "解读",
  content_summary: "概要",
};

function sentenceMarks(sentenceId: string, marks: InlineMarkModel[]) {
  return marks.filter((mark) => mark.anchor.sentenceId === sentenceId);
}

function renderSentenceText(sentence: SentenceModel, marks: InlineMarkModel[]): ReactNode[] {
  const ranges = marks
    .filter((mark) => mark.anchor.kind === "text")
    .map((mark) => {
      const anchorText = mark.anchor.kind === "text" ? mark.anchor.anchorText : "";
      const start = sentence.text.indexOf(anchorText);
      return start >= 0 ? { mark, start, end: start + anchorText.length, anchorText } : null;
    })
    .filter((range): range is NonNullable<typeof range> => range !== null)
    .sort((a, b) => a.start - b.start);

  const nodes: ReactNode[] = [];
  let cursor = 0;

  ranges.forEach((range) => {
    if (range.start < cursor) {
      return;
    }
    if (range.start > cursor) {
      nodes.push(sentence.text.slice(cursor, range.start));
    }
    nodes.push(
      <mark
        key={range.mark.id}
        className={`rounded-[3px] px-0.5 ${toneClass[range.mark.visualTone]}`}
        title={range.mark.glossary?.zh ?? range.mark.lookupText ?? toneLabel[range.mark.visualTone]}
      >
        {range.anchorText}
      </mark>,
    );
    cursor = range.end;
  });

  if (cursor < sentence.text.length) {
    nodes.push(sentence.text.slice(cursor));
  }

  return nodes.length > 0 ? nodes : [sentence.text];
}

export default async function ReaderPage({ params }: ReaderPageProps) {
  const { recordId } = await params;
  const result = await getReaderRecord(recordId);
  const { record, dataSource, fallbackReason } = result;
  const reader = record.reader;
  const translationBySentence = new Map(
    reader.translations.map((item) => [item.sentenceId, item.translationZh]),
  );

  return (
    <main className="min-h-[calc(100vh-56px)] bg-reader-paper px-5 py-8 text-ink">
      <div className="mx-auto grid max-w-6xl gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <article className="rounded-note border border-hairline bg-surface p-6 shadow-surface-quiet md:p-10">
          <div className="mb-8 flex items-center justify-between border-b border-hairline pb-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.12em] text-lens-blue">
                Record {record.id}
              </p>
              <h1 className="mt-2 font-headline text-3xl font-semibold tracking-normal text-ink">
                {record.title}
              </h1>
            </div>
            <div className="rounded-pill border border-hairline bg-surface-warm px-3 py-1 text-xs font-semibold text-muted">
              {dataSourceLabel[dataSource]}
            </div>
          </div>

          {fallbackReason ? (
            <div className="mb-6 rounded-md border border-hairline bg-lens-blue-soft px-4 py-3 text-sm leading-6 text-ink-soft">
              {fallbackReason}
            </div>
          ) : null}

          <div className="space-y-8">
            {reader.article.paragraphs.map((paragraph, paragraphIndex) => (
              <section key={paragraph.paragraphId} className="space-y-5">
                <p className="text-xs font-semibold text-subtle">
                  {String(paragraphIndex + 1).padStart(2, "0")} /
                </p>
                {paragraph.sentenceIds.map((sentenceId) => {
                  const sentence = reader.article.sentences.find((item) => item.sentenceId === sentenceId);
                  if (!sentence) {
                    return null;
                  }
                  const marks = sentenceMarks(sentence.sentenceId, reader.inlineMarks);
                  return (
                    <div key={sentence.sentenceId} className="space-y-2">
                      <p className="reader-serif text-[1.35rem] leading-[1.85] text-ink">
                        {renderSentenceText(sentence, marks)}
                      </p>
                      <p className="text-sm leading-7 text-muted">
                        {translationBySentence.get(sentence.sentenceId) ?? null}
                      </p>
                      {marks.length > 0 ? (
                        <div className="flex flex-wrap gap-2 pt-1">
                          {marks.map((mark) => (
                            <span
                              key={mark.id}
                              className="rounded-pill border border-hairline bg-surface-warm px-2.5 py-1 text-xs text-ink-soft"
                            >
                              {toneLabel[mark.visualTone]} · {mark.lookupText ?? mark.glossary?.zh ?? mark.annotationType}
                            </span>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </section>
            ))}
          </div>
        </article>

        <aside className="h-fit rounded-note border border-hairline bg-surface p-5 shadow-surface-quiet lg:sticky lg:top-20">
          <div className="border-b border-hairline pb-4">
            <h2 className="font-title text-lg font-semibold tracking-normal text-ink">轻旁注</h2>
            <p className="mt-2 text-sm leading-6 text-muted">
              当前页面只消费 Web Reader VM。真实记录由 BFF 投影；不可用时回落到 mock demo。
            </p>
          </div>
          <div className="mt-4 space-y-4">
            {reader.sentenceEntries.map((entry) => (
              <section key={entry.id} className="rounded-md border border-hairline bg-surface-warm p-4">
                <div className="mb-2 flex items-center justify-between gap-3">
                  <h3 className="text-sm font-semibold text-ink">{entry.title ?? entry.label}</h3>
                  <span className="shrink-0 rounded-pill bg-surface px-2 py-0.5 text-[0.6875rem] text-muted">
                    {entryTypeLabel[entry.entryType] ?? "解析"}
                  </span>
                </div>
                <p className="whitespace-pre-line text-sm leading-6 text-ink-soft">{entry.content}</p>
              </section>
            ))}
          </div>
        </aside>
      </div>
    </main>
  );
}
