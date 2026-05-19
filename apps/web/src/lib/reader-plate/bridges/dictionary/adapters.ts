import type { DictLookupTypeDto } from "@/types/api/dict";
import type { ReaderTextSelection } from "../../primitives";
import { rectForTextOffsets, textOffsetWithinElement } from "../../primitives";
import type { ReaderLookupIntent, ReaderLookupPreviewAnchor, ReaderStructuredInspectIntent } from "./types";
import type { DictionaryLookupSnapshot, LookupState } from "@/components/reader/dictionary/contracts";
import type { InlineMarkModel, SentenceModel } from "@/types/view/ReaderMockVm";
import { tokenizeText } from "../../../../app/(app)/reader/[recordId]/readerText";

type ReaderLookupSentence = Pick<SentenceModel, "sentenceId" | "text">;
type ReaderLookupMark = Pick<
  InlineMarkModel,
  "id" | "annotationType" | "visualTone" | "lookupKind" | "lookupText" | "glossary"
>;

function buildOccurrenceByStart(text: string) {
  const counters = new Map<string, number>();
  const occurrenceByStart = new Map<number, number>();

  tokenizeText(text).forEach((token) => {
    if (token.type !== "word") {
      return;
    }

    const normalized = token.text.toLowerCase();
    const occurrence = (counters.get(normalized) ?? 0) + 1;
    counters.set(normalized, occurrence);
    occurrenceByStart.set(token.start, occurrence);
  });

  return occurrenceByStart;
}

function caretRangeFromPoint(clientX: number, clientY: number): Range | null {
  const doc = document as Document & {
    caretRangeFromPoint?: (x: number, y: number) => Range | null;
    caretPositionFromPoint?: (
      x: number,
      y: number,
    ) => { offsetNode: Node; offset: number } | null;
  };

  const legacyRange = doc.caretRangeFromPoint?.(clientX, clientY);
  if (legacyRange) {
    return legacyRange;
  }

  const position = doc.caretPositionFromPoint?.(clientX, clientY);
  if (!position) {
    return null;
  }

  const range = document.createRange();
  range.setStart(position.offsetNode, position.offset);
  range.collapse(true);
  return range;
}

function inferLookupType(text: string, hint?: InlineMarkModel["lookupKind"]): DictLookupTypeDto {
  if (hint && hint !== "word") {
    return "phrase";
  }

  return /\s/.test(text.trim()) ? "phrase" : "word";
}

function inferInspectLabel(mark: ReaderLookupMark, anchorText: string): string {
  if (mark.annotationType === "phrase_gloss" && mark.glossary?.phraseType) {
    switch (mark.glossary.phraseType) {
      case "collocation":
        return "固定搭配";
      case "phrasal_verb":
        return "动词短语";
      case "idiom":
        return "习语";
      case "proper_noun":
        return "专名";
      case "compound":
        return "复合表达";
      default:
        break;
    }
  }

  if (mark.annotationType === "context_gloss") {
    return "语境义";
  }

  if (anchorText.includes(" ")) {
    return "结构化标注";
  }

  return "词典";
}

export function resolveLookupPreviewAnchor(
  element: HTMLElement,
  sentenceId: string,
  startOffset: number,
  endOffset: number,
): ReaderLookupPreviewAnchor {
  return {
    sentenceId,
    startOffset,
    endOffset,
    fallbackRect: rectForTextOffsets(element, startOffset, endOffset),
  };
}

export function lookupIntentFromTokenClick({
  element,
  sentence,
  sourceContext,
  clientX,
  clientY,
}: {
  element: HTMLElement;
  sentence: ReaderLookupSentence;
  sourceContext?: string;
  clientX: number;
  clientY: number;
}): { intent: ReaderLookupIntent; anchor: ReaderLookupPreviewAnchor | null } | null {
  const range = caretRangeFromPoint(clientX, clientY);
  if (!range) {
    return null;
  }

  const offset = textOffsetWithinElement(element, range.startContainer, range.startOffset);
  range.detach();
  if (offset === null) {
    return null;
  }

  const token = tokenizeText(sentence.text).find((item) => {
    if (item.type !== "word") {
      return false;
    }

    return offset >= item.start && offset <= item.end;
  });
  if (!token || token.type !== "word") {
    return null;
  }

  return {
    intent: {
      kind: "lexical_lookup",
      query: token.text,
      lookupType: "word",
      sentenceId: sentence.sentenceId,
      contextSentence: sentence.text,
      sourceContext,
      anchorOffsets: {
        startOffset: token.start,
        endOffset: token.end,
      },
      anchorText: token.text,
      occurrence: buildOccurrenceByStart(sentence.text).get(token.start),
      title: "查词",
    },
    anchor: resolveLookupPreviewAnchor(element, sentence.sentenceId, token.start, token.end),
  };
}

export function lookupIntentFromSelection(
  selection: ReaderTextSelection,
  sourceContext?: string,
): ReaderLookupIntent {
  const query = selection.selectedText.trim();

  return {
    kind: "manual_span_lookup",
    query,
    lookupType: inferLookupType(query),
    sentenceId: selection.sentence.sentenceId,
    contextSentence: selection.sentence.text,
    sourceContext,
    anchorOffsets:
      typeof selection.startOffset === "number" && typeof selection.endOffset === "number"
        ? {
            startOffset: selection.startOffset,
            endOffset: selection.endOffset,
          }
        : undefined,
    anchorText: query,
    occurrence: selection.segments.length === 1 ? selection.segments[0]?.startOffset : undefined,
    title: "选区查词",
    label: "选区查词",
  };
}

export function lookupIntentFromMark({
  mark,
  sentence,
  anchorText,
  sourceContext,
  startOffset,
  endOffset,
}: {
  mark: ReaderLookupMark;
  sentence: ReaderLookupSentence;
  anchorText: string;
  sourceContext?: string;
  startOffset: number;
  endOffset: number;
}): ReaderLookupIntent {
  const query = mark.lookupText ?? anchorText;

  return {
    kind: "lexical_lookup",
    query,
    lookupType: inferLookupType(query, mark.lookupKind),
    sentenceId: sentence.sentenceId,
    contextSentence: sentence.text,
    sourceContext,
    anchorOffsets: {
      startOffset,
      endOffset,
    },
    anchorText,
    occurrence: buildOccurrenceByStart(sentence.text).get(startOffset),
    title: query === anchorText ? "查词" : "词典查询",
    label: inferInspectLabel(mark, anchorText),
    annotationType: mark.annotationType,
    visualTone: mark.visualTone,
    glossary: mark.glossary,
  };
}

export function inspectIntentFromStructuredMark({
  mark,
  sentence,
  anchorText,
  sourceContext,
  startOffset,
  endOffset,
}: {
  mark: ReaderLookupMark;
  sentence: ReaderLookupSentence;
  anchorText: string;
  sourceContext?: string;
  startOffset: number;
  endOffset: number;
}): ReaderStructuredInspectIntent {
  return {
    kind: "structured_annotation_inspect",
    sentenceId: sentence.sentenceId,
    contextSentence: sentence.text,
    sourceContext,
    markId: mark.id,
    annotationType: mark.annotationType,
    visualTone: mark.visualTone,
    anchorText,
    lookupText: mark.lookupText,
    lookupKind: mark.lookupKind,
    glossary: mark.glossary,
    anchorOffsets: {
      startOffset,
      endOffset,
    },
    occurrence: buildOccurrenceByStart(sentence.text).get(startOffset),
    title: inferInspectLabel(mark, anchorText),
    label: inferInspectLabel(mark, anchorText),
  };
}

export function lookupIntentFromStructuredInspect(
  intent: ReaderStructuredInspectIntent,
): ReaderLookupIntent {
  const query = intent.lookupText ?? intent.anchorText;

  return {
    kind: "lexical_lookup",
    query,
    lookupType: inferLookupType(query, intent.lookupKind),
    sentenceId: intent.sentenceId,
    contextSentence: intent.contextSentence,
    sourceContext: intent.sourceContext,
    anchorOffsets: intent.anchorOffsets,
    anchorText: intent.anchorText,
    occurrence: intent.occurrence,
    title: "查短语",
    label: intent.label,
    annotationType: intent.annotationType,
    visualTone: intent.visualTone,
    glossary: intent.glossary,
  };
}

export function readerLookupSnapshotFromIntent(
  recordId: string,
  intent: ReaderLookupIntent,
  state: LookupState,
): DictionaryLookupSnapshot {
  return {
    query: intent.query,
    lookupType: intent.lookupType,
    contextSentence: intent.contextSentence,
    sourceContext: intent.sourceContext,
    recordId,
    sentenceId: intent.sentenceId,
    anchorText: intent.anchorText,
    occurrence: intent.occurrence,
    title: intent.title,
    label: intent.label,
    annotationType: intent.annotationType,
    visualTone: intent.visualTone,
    glossary: intent.glossary,
    state,
  };
}
