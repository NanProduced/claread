import type { DictLookupTypeDto } from "@/types/api/dict";
import type { InlineGlossary, InlineMarkModel } from "@/types/view/ReaderMockVm";
import type { DictionaryLookupSnapshot } from "@/components/reader/dictionary/contracts";

export interface ReaderLookupPreviewAnchor {
  sentenceId: string;
  startOffset: number;
  endOffset: number;
  fallbackRect: DOMRect | null;
}

export interface ReaderLookupIntent {
  kind: "lexical_lookup" | "manual_span_lookup";
  query: string;
  lookupType: DictLookupTypeDto;
  sentenceId: string;
  contextSentence: string;
  sourceContext?: string;
  anchorOffsets?: {
    startOffset: number;
    endOffset: number;
  };
  anchorText: string;
  occurrence?: number;
  title: string;
  label?: string;
  annotationType?: InlineMarkModel["annotationType"];
  visualTone?: InlineMarkModel["visualTone"];
  glossary?: InlineGlossary;
}

export interface ReaderStructuredInspectIntent {
  kind: "structured_annotation_inspect";
  sentenceId: string;
  contextSentence: string;
  sourceContext?: string;
  markId: string;
  annotationType: InlineMarkModel["annotationType"];
  visualTone: InlineMarkModel["visualTone"];
  anchorText: string;
  lookupText?: string;
  lookupKind?: InlineMarkModel["lookupKind"];
  glossary?: InlineGlossary;
  anchorOffsets?: {
    startOffset: number;
    endOffset: number;
  };
  occurrence?: number;
  title: string;
  label?: string;
}

export type ReaderDictionarySurfaceState =
  | { kind: "idle" }
  | {
      kind: "lookup";
      intent: ReaderLookupIntent;
      anchor: ReaderLookupPreviewAnchor | null;
      snapshot: DictionaryLookupSnapshot | null;
      railOpen: boolean;
    }
  | {
      kind: "inspect";
      intent: ReaderStructuredInspectIntent;
      anchor: ReaderLookupPreviewAnchor | null;
      railOpen: boolean;
    };
