import type { SentenceModel } from "@/types/view/ReaderMockVm";

export interface ReaderSelectionSegment {
  paragraphId: string;
  sentenceId: string;
  sentence: SentenceModel;
  selectedText: string;
  startOffset: number;
  endOffset: number;
  textHash: string;
}

export type ReaderTextSelection =
  | {
      anchorType: "sentence";
      sentence: SentenceModel;
      selectedText: string;
      rect: DOMRect;
      range?: Range;
      segments: [ReaderSelectionSegment];
      startOffset: number;
      endOffset: number;
      textHash: string;
    }
  | {
      anchorType: "text_range";
      sentence: SentenceModel;
      selectedText: string;
      rect: DOMRect;
      range?: Range;
      segments: [ReaderSelectionSegment];
      startOffset: number;
      endOffset: number;
      textHash: string;
    }
  | {
      anchorType: "multi_text";
      sentence: SentenceModel;
      selectedText: string;
      rect: DOMRect;
      range?: Range;
      segments: ReaderSelectionSegment[];
      startOffset: number;
      endOffset: number;
      textHash: string;
    };
