"use client";

import type { RenderElement } from "platejs/react";
import {
  lookupIntentFromTokenClick,
  type ReaderLookupIntent,
  type ReaderLookupPreviewAnchor,
} from "../../../../lib/reader-plate";

interface ReaderSentenceTextElementProps {
  props: Parameters<RenderElement>[0];
  readingClassName: string;
  sourceContext?: string;
  onLookupIntent?: (
    intent: ReaderLookupIntent,
    anchor: ReaderLookupPreviewAnchor | null,
    triggerEl?: HTMLElement | null,
  ) => void;
}

export function ReaderSentenceTextElement({
  onLookupIntent,
  props,
  readingClassName,
  sourceContext,
}: ReaderSentenceTextElementProps) {
  const sentenceTextElement = props.element as unknown as {
    sentenceId: string;
    children?: Array<{ text?: string }>;
  };

  return (
    <p
      {...props.attributes}
      className={readingClassName}
      data-reader-node="sentence-text"
      data-reader-sentence-text="true"
      tabIndex={-1}
      onClick={(event) => {
        if (!onLookupIntent) {
          return;
        }

        const selection = window.getSelection();
        if (selection && !selection.isCollapsed && selection.toString().trim()) {
          return;
        }

        const currentTarget = event.currentTarget;
        const sentenceText = sentenceTextElement.children?.map((child) => child.text ?? "").join("") ?? "";
        if (!sentenceText) {
          return;
        }

        const result = lookupIntentFromTokenClick({
          element: currentTarget,
          sentence: {
            sentenceId: sentenceTextElement.sentenceId,
            text: sentenceText,
          },
          sourceContext,
          clientX: event.clientX,
          clientY: event.clientY,
        });

        if (!result) {
          return;
        }

        event.stopPropagation();
        currentTarget.focus({ preventScroll: true });
        onLookupIntent(result.intent, result.anchor, currentTarget);
      }}
    >
      {props.children}
    </p>
  );
}
