"use client";

import type { ReactNode } from "react";

import type { ReaderMockVm, SentenceModel } from "@/types/view/ReaderMockVm";

interface ReaderCanvasProps {
  reader: ReaderMockVm;
  renderSentence: (sentence: SentenceModel, sentenceIndex: number) => ReactNode;
}

export function ReaderCanvas({ reader, renderSentence }: ReaderCanvasProps) {
  const sentenceById = new Map(reader.article.sentences.map((sentence) => [sentence.sentenceId, sentence]));

  return (
    <div className="px-5 py-7 sm:px-8 lg:px-10 lg:py-9">
      <div className="mx-auto max-w-[96ch] space-y-10">
        {reader.article.paragraphs.map((paragraph, paragraphIndex) => (
          <section key={paragraph.paragraphId} className="reader-paragraph">
            <div className="reader-paragraph-index">
              <span>{String(paragraphIndex + 1).padStart(2, "0")}</span>
              <span aria-hidden="true">/</span>
              <span>{String(reader.article.paragraphs.length).padStart(2, "0")}</span>
            </div>
            <div className="min-w-0 space-y-6">
              {paragraph.sentenceIds.map((sentenceId, sentenceIndex) => {
                const sentence = sentenceById.get(sentenceId);
                return sentence ? renderSentence(sentence, sentenceIndex) : null;
              })}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}
