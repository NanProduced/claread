import type { SentenceModel } from "@/types/view/ReaderMockVm";

export type ReaderAnchorAttributes = Record<`data-${string}`, string>;

export function sentenceAnchorAttributes(sentence: SentenceModel): ReaderAnchorAttributes {
  return {
    "data-reader-anchor": "sentence",
    "data-paragraph-id": sentence.paragraphId,
    "data-sentence-id": sentence.sentenceId,
  };
}

interface TextRangeAnchorInput {
  paragraphId: string;
  sentenceId: string;
  startOffset: number;
  endOffset: number;
  text: string;
}

export function textRangeAnchorAttributes({
  paragraphId,
  sentenceId,
  startOffset,
  endOffset,
  text,
}: TextRangeAnchorInput): ReaderAnchorAttributes {
  return {
    "data-reader-anchor": "text-range",
    "data-paragraph-id": paragraphId,
    "data-sentence-id": sentenceId,
    "data-start-offset": String(startOffset),
    "data-end-offset": String(endOffset),
    "data-anchor-text": text,
  };
}
