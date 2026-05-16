"use client";

import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent, ReactNode } from "react";

import type { WebAnnotationVm } from "@/types/api/annotations";
import type { WebFavoriteTargetVm } from "@/types/api/favorites";
import type { SentenceModel } from "@/types/view/ReaderMockVm";
import { sentenceAnchorAttributes } from "./reader-anchors";
import { ReaderAnnotationOverlay } from "./ReaderAnnotationOverlay";

interface ReaderSentenceRowProps {
  sentence: SentenceModel;
  activeIndex?: number | null;
  annotations: WebAnnotationVm[];
  slipAnnotations?: WebAnnotationVm[];
  favoriteTargets?: WebFavoriteTargetVm[];
  frameClassName: string;
  readingClassName: string;
  textClassName: string;
  translation?: string | null;
  showTranslation: boolean;
  sentenceText: ReactNode;
  entryControls: ReactNode;
  onSelectSentence: (sentence: SentenceModel) => void;
  onSentenceTextClick: (event: MouseEvent<HTMLParagraphElement>, sentence: SentenceModel) => void;
}

export function ReaderSentenceRow({
  sentence,
  activeIndex,
  annotations,
  slipAnnotations,
  favoriteTargets = [],
  frameClassName,
  readingClassName,
  textClassName,
  translation,
  showTranslation,
  sentenceText,
  entryControls,
  onSelectSentence,
  onSentenceTextClick,
}: ReaderSentenceRowProps) {
  function handleKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelectSentence(sentence);
    }
  }

  return (
    <section
      key={sentence.sentenceId}
      id={`reader-sentence-${sentence.sentenceId}`}
      className={`group/sentence relative scroll-mt-8 px-2 py-2 transition-colors ${frameClassName}`}
      aria-label={`句子 ${sentence.sentenceId}`}
      {...sentenceAnchorAttributes(sentence)}
    >
      <ReaderAnnotationOverlay
        annotations={annotations}
        slipAnnotations={slipAnnotations}
        favoriteTargets={favoriteTargets}
        activeIndex={activeIndex}
      >
        <div
          tabIndex={0}
          className="focus-ring block w-full text-left"
          onKeyDown={handleKeyDown}
        >
          <p
            className={`${readingClassName} ${textClassName}`}
            data-reader-sentence-text="true"
            onClick={(event) => onSentenceTextClick(event, sentence)}
          >
            {sentenceText}
          </p>
          {showTranslation ? (
            <p className="mt-2 text-sm leading-7 text-muted/95">{translation ?? null}</p>
          ) : null}
        </div>
      </ReaderAnnotationOverlay>

      {entryControls}
    </section>
  );
}
