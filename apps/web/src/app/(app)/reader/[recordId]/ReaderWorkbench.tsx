"use client";

import type { CSSProperties, FormEvent, KeyboardEvent as ReactKeyboardEvent, ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { TEXT_RANGE_HASH_ALGORITHM, TEXT_RANGE_OFFSET_UNIT, USER_ANNOTATION_COLORS } from "@claread/contracts";
import {
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  MoreHorizontal,
  Pin,
  Search,
  Sparkles,
  Type,
  Volume2,
  X,
} from "lucide-react";
import { useSearchParams } from "next/navigation";

import type { ReaderRecordVm } from "@/adapters/records.adapter";
import {
  AiWorkspacePanel,
  ReaderContextPanel,
  ReaderDictionaryRail,
  ReaderFloatingSurface,
  ReaderQuickPeek,
  ReaderSettingsPanel,
  PlateReaderSurface,
  SelectionToolbar,
  defaultReaderSettings,
  persistReaderSettings,
  readerColumnWidthClassName,
  readStoredReaderSettings,
  readerTextClassName,
  readerThemeClassName,
  type ReaderSettingsState,
  textRangeAnchorAttributes,
  useReaderFloatingLayer,
} from "@/components/reader";
import {
  askAttachmentFromAnnotation,
  askAttachmentFromAnalysisBlock,
  askAttachmentFromContentSummary,
  askAttachmentFromFavorite,
  askAttachmentFromRecord,
  askAttachmentFromSelection,
  askAttachmentFromSentence,
  askAttachmentFromStructuredInspect,
  askAttachmentFromTranslation,
  askAttachmentKey,
  annotationMatchesSelection,
  annotationRequestFromAnchorPayload,
  annotationToTargetRef,
  anchorPayloadFromSelection,
  anchorPayloadFromSentence,
  copyDomRect,
  favoriteMutationFromAnchorPayload,
  favoriteTargetVmFromAnchorPayload,
  favoriteToTargetRef,
  hashAnchorText,
  jumpTargetFromAskAttachment,
  jumpTargetFromAskCitation,
  jumpToTargetKey,
  jumpToTargetRef,
  lookupIntentFromSelection,
  lookupIntentFromStructuredInspect,
  projectReaderAssets,
  readPlateReaderSelection,
  readerLookupSnapshotFromIntent,
  rectForTextOffsets,
  renderSceneToPlateDocument,
  type ReaderLookupIntent,
  type ReaderLookupPreviewAnchor,
  type ReaderStructuredInspectIntent,
  selectionToolbarRectForReaderSelection,
  sentenceToTargetRef,
  targetKeyForSelection,
  textOffsetWithinElement,
  type ReaderAskAttachment,
  type ReaderAskPageIdentity,
  type ReaderContentSummaryNode,
  type ReaderAssetProjection,
  type ReaderJumpRangeSegment,
  type ReaderJumpTarget,
  type ReaderTextSelection,
} from "@/lib/reader-plate";
import { parseSentenceAnalysisContent } from "@/components/reader/reader-entry-utils";
import type {
  UserAnnotationColorDto,
  WebAnchorSegmentVm,
  WebAnnotationCreateRequest,
  WebAnnotationVm,
} from "@/types/api/annotations";
import type { ReaderAskCitationDto } from "@/types/api/reader-ask";
import type { WebFavoriteTargetVm } from "@/types/api/favorites";
import type { WebDictCandidate, WebDictDisambiguationResult, WebDictEntry, WebDictResult } from "@/types/api/dict";
import type {
  DictionaryAIViewState,
  DictAISourceDto,
  WebDictAIErrorResult,
  WebDictAIRequest,
  WebDictAIResult,
} from "@/types/api/dict-ai";
import type { VocabularyCreateRequestDto } from "@/types/api/vocabulary";
import type { InlineGlossary, InlineMarkModel, SentenceEntryModel, SentenceModel } from "@/types/view/ReaderMockVm";
import {
  ANNOTATION_CREATED_EVENT,
} from "./ReaderAnnotations";
import {
  exchangeForms,
  firstMeaning,
  firstPartOfSpeech,
  meaningsJson,
  type DictionaryLookupSnapshot,
  type SaveState,
} from "./DictionaryMark";
import { FavoriteButton } from "./FavoriteButton";
import { findTextAnchorPosition, tokenizeText } from "./readerText";

type ReaderDataSource = "upstream-render-scene" | "upstream-source-text";
type MarkVisibility = "full" | "quiet";
type DictionaryContentTab = "meanings" | "examples" | "phrases" | "forms";
type DictionaryEntryResult = Extract<WebDictResult, { kind: "entry" }>;
type RouteFocusSegment = ReaderJumpRangeSegment;

interface ReaderWorkbenchProps {
  record: ReaderRecordVm;
  dataSource: ReaderDataSource;
  message?: string;
  initialAnnotations: WebAnnotationVm[];
  initialFavoriteTargets?: WebFavoriteTargetVm[];
}

type AnnotationSaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; message: string }
  | { kind: "error"; message: string };

const toneClass: Record<InlineMarkModel["visualTone"], string> = {
  vocab: "reader-mark reader-mark--vocab",
  phrase: "reader-mark reader-mark--phrase",
  context: "reader-mark reader-mark--context",
  grammar: "reader-mark reader-mark--grammar",
  term: "reader-mark reader-mark--term",
  logic: "reader-mark reader-mark--logic",
};

const quietToneClass: Record<InlineMarkModel["visualTone"], string> = {
  vocab: "reader-mark reader-mark--quiet reader-mark--vocab",
  phrase: "reader-mark reader-mark--quiet reader-mark--phrase",
  context: "reader-mark reader-mark--quiet reader-mark--context",
  grammar: "reader-mark reader-mark--quiet reader-mark--grammar",
  term: "reader-mark reader-mark--quiet reader-mark--term",
  logic: "reader-mark reader-mark--quiet reader-mark--logic",
};

const toneLabel: Record<InlineMarkModel["visualTone"], string> = {
  vocab: "词汇",
  phrase: "短语",
  context: "语境",
  grammar: "语法",
  term: "术语",
  logic: "逻辑",
};

const annotationLabel: Record<InlineMarkModel["annotationType"], string> = {
  vocab_highlight: "词汇",
  phrase_gloss: "短语",
  context_gloss: "语境义",
  grammar_note: "语法",
  term_note: "术语",
  logic_note: "逻辑",
};

const phraseTypeLabel: Record<NonNullable<InlineGlossary["phraseType"]>, string> = {
  collocation: "固定搭配",
  phrasal_verb: "动词短语",
  idiom: "习语",
  proper_noun: "专名",
  compound: "复合表达",
};

const tonePriority: Record<InlineMarkModel["visualTone"], number> = {
  vocab: 1,
  phrase: 2,
  context: 3,
  grammar: 4,
  term: 5,
  logic: 6,
};

const dataSourceLabel: Record<ReaderDataSource, string> = {
  "upstream-render-scene": "解析结果",
  "upstream-source-text": "原文回退",
};

const sentenceHighlightToneClass: Record<UserAnnotationColorDto, string> = {
  soft_green: "reader-user-highlight--soft-green",
  soft_blue: "reader-user-highlight--soft-blue",
  soft_purple: "reader-user-highlight--soft-purple",
  warm_yellow: "reader-user-highlight--warm-yellow",
  sage_green: "reader-user-highlight--sage-green",
};

const userRangeToneClass: Record<UserAnnotationColorDto, string> = {
  soft_green: "reader-user-range--soft-green",
  soft_blue: "reader-user-range--soft-blue",
  soft_purple: "reader-user-range--soft-purple",
  warm_yellow: "reader-user-range--warm-yellow",
  sage_green: "reader-user-range--sage-green",
};

const annotationColorValues = [...USER_ANNOTATION_COLORS];

function isUserAnnotationColor(value: string): value is UserAnnotationColorDto {
  return annotationColorValues.includes(value as UserAnnotationColorDto);
}

function belongsToCurrentRecord(candidateRecordId: string | null | undefined, targetKey: string, recordId: string) {
  if (candidateRecordId === recordId) {
    return true;
  }

  if (candidateRecordId !== null) {
    return false;
  }

  return targetKey.startsWith(`record:${recordId}:`);
}

function sentenceMarks(sentenceId: string, marks: InlineMarkModel[]) {
  return marks.filter((mark) => mark.anchor.sentenceId === sentenceId);
}

function markLookupType(mark: InlineMarkModel) {
  if (mark.lookupKind === "phrase" || mark.annotationType === "phrase_gloss") {
    return "phrase";
  }
  return "word";
}

function markLookupQuery(mark: InlineMarkModel, anchorText: string): string {
  return mark.lookupText ?? anchorText;
}

function isDictionaryMark(mark: InlineMarkModel) {
  return mark.clickable && mark.annotationType !== "grammar_note" && mark.visualTone !== "grammar";
}

function markLabel(mark: InlineMarkModel) {
  if (mark.annotationType === "phrase_gloss" && mark.glossary?.phraseType) {
    return phraseTypeLabel[mark.glossary.phraseType];
  }
  return annotationLabel[mark.annotationType] ?? toneLabel[mark.visualTone];
}

function contextualGlossaryTitle(lookup: DictionaryLookupSnapshot) {
  if (lookup.annotationType === "phrase_gloss" || lookup.lookupType === "phrase") {
    return lookup.glossary?.phraseType ? phraseTypeLabel[lookup.glossary.phraseType] : "短语含义";
  }
  if (lookup.annotationType === "context_gloss") {
    return "本文含义";
  }
  return null;
}

function contextualGlossaryText(glossary?: InlineGlossary) {
  return glossary?.zh ?? glossary?.gloss ?? "";
}

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

function annotationOverlapsRange(annotation: WebAnnotationVm, start: number, end: number) {
  if (annotation.anchorType !== "text_range") {
    return false;
  }
  if (typeof annotation.startOffset !== "number" || typeof annotation.endOffset !== "number") {
    return false;
  }
  return start < annotation.endOffset && end > annotation.startOffset;
}

function userRangeClassForSpan(annotations: WebAnnotationVm[], start: number, end: number) {
  const match = annotations.find((item) => annotationOverlapsRange(item, start, end));
  return match ? `reader-user-range ${userRangeToneClass[match.color]}` : "";
}

function userRangeClassForAnnotation(annotation: WebAnnotationVm) {
  return `reader-user-range ${userRangeToneClass[annotation.color]}`;
}

function acceptedTextRanges<T extends { startOffset: number; endOffset: number }>(ranges: T[]): T[] {
  return ranges
    .filter((item) => item.startOffset < item.endOffset)
    .sort((a, b) => {
      const startDelta = a.startOffset - b.startOffset;
      if (startDelta !== 0) {
        return startDelta;
      }
      return b.endOffset - a.endOffset;
    })
    .reduce<T[]>((accepted, item) => {
      const previous = accepted.at(-1);
      if (previous && item.startOffset < previous.endOffset) {
        return accepted;
      }
      accepted.push(item);
      return accepted;
    }, []);
}

function acceptedUserRanges(annotations: WebAnnotationVm[]): WebAnnotationVm[] {
  return acceptedTextRanges(
    annotations
    .filter(
      (
        item,
      ): item is WebAnnotationVm & { startOffset: number; endOffset: number } =>
        item.anchorType === "text_range" &&
        typeof item.startOffset === "number" &&
        typeof item.endOffset === "number" &&
        item.startOffset < item.endOffset,
    ),
  );
}

function routeFocusClassForSpan(ranges: RouteFocusSegment[], start: number, end: number) {
  return ranges.some((item) => start < item.endOffset && end > item.startOffset)
    ? "reader-route-focus-range"
    : "";
}

function projectAnnotationRangesForSentence(
  annotations: WebAnnotationVm[],
  sentenceId: string,
): WebAnnotationVm[] {
  return annotations.flatMap((annotation) => {
    if (annotation.anchorType === "text_range" && annotation.sentenceId === sentenceId) {
      return [annotation];
    }
    if (annotation.anchorType !== "multi_text") {
      return [];
    }
    return annotation.segments
      .filter((segment) => segment.sentenceId === sentenceId)
      .map((segment) => ({
        ...annotation,
        anchorType: "text_range" as const,
        sentenceId: segment.sentenceId,
        paragraphId: segment.paragraphId ?? annotation.paragraphId,
        selectedText: segment.selectedText,
        startOffset: segment.startOffset,
        endOffset: segment.endOffset,
        textHash: segment.textHash,
        segments: [segment],
      }));
  });
}

function compactReaderAnchorId(value?: string) {
  return value?.replace(/^(im|se)_/, "") ?? "";
}

function markMatchesEntry(mark: InlineMarkModel, entry?: SentenceEntryModel | null) {
  if (!entry) {
    return false;
  }
  const entryKey = compactReaderAnchorId(entry.id);
  return (
    mark.id === entry.id ||
    mark.parentId === entry.id ||
    compactReaderAnchorId(mark.id) === entryKey ||
    compactReaderAnchorId(mark.parentId) === entryKey
  );
}

function entryLabel(entry: SentenceEntryModel) {
  if (entry.entryType === "grammar_note") {
    return "语法旁注";
  }
  if (entry.entryType === "sentence_analysis") {
    return "句子拆解";
  }
  return entry.label || "解析";
}

interface MarkRange {
  key: string;
  mark: InlineMarkModel;
  start: number;
  end: number;
  anchorText: string;
  occurrence?: number;
}

interface AnalysisRange {
  key: string;
  index: number;
  order: string;
  label: string;
  start: number;
  end: number;
  text: string;
}

function buildMarkRanges(sentence: SentenceModel, marks: InlineMarkModel[]): MarkRange[] {
  const ranges: MarkRange[] = [];

  marks.forEach((mark) => {
    if (mark.anchor.kind === "text") {
      const start = findTextAnchorPosition(sentence.text, mark.anchor.anchorText, mark.anchor.occurrence ?? 1);
      if (start >= 0) {
        ranges.push({
          key: mark.id,
          mark,
          start,
          end: start + mark.anchor.anchorText.length,
          anchorText: mark.anchor.anchorText,
          occurrence: mark.anchor.occurrence,
        });
      }
      return;
    }

    mark.anchor.parts.forEach((part, index) => {
      const start = findTextAnchorPosition(sentence.text, part.anchorText, part.occurrence ?? 1);
      if (start >= 0) {
        ranges.push({
          key: `${mark.id}-part-${index}`,
          mark,
          start,
          end: start + part.anchorText.length,
          anchorText: part.anchorText,
          occurrence: part.occurrence,
        });
      }
    });
  });

  return ranges
    .sort((a, b) => {
      if (a.start !== b.start) {
        return a.start - b.start;
      }
      if (a.end !== b.end) {
        return b.end - a.end;
      }
      return tonePriority[a.mark.visualTone] - tonePriority[b.mark.visualTone];
    })
    .reduce<MarkRange[]>((accepted, range) => {
      const previous = accepted.at(-1);
      if (previous && range.start < previous.end) {
        if (range.end <= previous.end) {
          return accepted;
        }
        const visibleStart = previous.end;
        accepted.push({
          ...range,
          key: `${range.key}-tail`,
          start: visibleStart,
          anchorText: sentence.text.slice(visibleStart, range.end),
          occurrence: undefined,
        });
        return accepted;
      }
      accepted.push(range);
      return accepted;
    }, []);
}

function buildAnalysisRanges(sentence: SentenceModel, entry?: SentenceEntryModel | null): AnalysisRange[] {
  if (!entry || entry.entryType !== "sentence_analysis") {
    return [];
  }

  const parsed = parseSentenceAnalysisContent(entry.content);
  const ranges: AnalysisRange[] = [];
  let cursor = 0;

  parsed.chunks.forEach((chunk, index) => {
    const anchorText = chunk.text.trim();
    if (!anchorText) {
      return;
    }

    let start = sentence.text.indexOf(anchorText, cursor);
    if (start < 0) {
      start = findTextAnchorPosition(sentence.text, anchorText, 1);
    }
    if (start < 0) {
      return;
    }

    ranges.push({
      key: `${entry.id}-analysis-${index}`,
      index,
      order: chunk.order,
      label: chunk.label,
      start,
      end: start + anchorText.length,
      text: anchorText,
    });
    cursor = start + anchorText.length;
  });

  return ranges
    .sort((a, b) => {
      if (a.start !== b.start) {
        return a.start - b.start;
      }
      return b.end - a.end;
    })
    .reduce<AnalysisRange[]>((accepted, range) => {
      const previous = accepted.at(-1);
      if (previous && range.start < previous.end) {
        return accepted;
      }
      accepted.push(range);
      return accepted;
    }, []);
}

type LookupBase = Omit<DictionaryLookupSnapshot, "state">;
type LookupPreviewAnchor = ReaderLookupPreviewAnchor;
type SentenceLookupTarget = {
  base: LookupBase;
  anchor: ReaderLookupPreviewAnchor | null;
};
type DictionaryDockLayout = {
  left: number;
  width: number;
};

function lookupIntentFromSnapshotBase(base: LookupBase): ReaderLookupIntent {
  return {
    kind: base.label === "选区查词" ? "manual_span_lookup" : "lexical_lookup",
    query: base.query,
    lookupType: base.lookupType,
    sentenceId: base.sentenceId,
    contextSentence: base.contextSentence,
    sourceContext: base.sourceContext,
    anchorText: base.anchorText,
    occurrence: base.occurrence,
    title: base.title,
    label: base.label,
    annotationType: base.annotationType,
    visualTone: base.visualTone,
    glossary: base.glossary,
  };
}

function shouldShowLookupPreview() {
  if (typeof window === "undefined") {
    return false;
  }

  return window.matchMedia("(min-width: 768px)").matches;
}

function sameLookupTarget(lookup: DictionaryLookupSnapshot | null, base: LookupBase) {
  if (!lookup) {
    return false;
  }

  return (
    lookup.query.toLowerCase() === base.query.toLowerCase() &&
    lookup.lookupType === base.lookupType &&
    lookup.sentenceId === base.sentenceId &&
    lookup.anchorText === base.anchorText &&
    (lookup.occurrence ?? null) === (base.occurrence ?? null)
  );
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

function lookupBaseFromSentencePoint({
  element,
  sentence,
  recordId,
  sourceContext,
  clientX,
  clientY,
}: {
  element: HTMLElement;
  sentence: SentenceModel;
  recordId: string;
  sourceContext?: string;
  clientX: number;
  clientY: number;
}): SentenceLookupTarget | null {
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
    const tokenEnd = item.start + item.text.length;
    return offset >= item.start && offset <= tokenEnd;
  });
  if (!token || token.type !== "word") {
    return null;
  }

  const occurrence = buildOccurrenceByStart(sentence.text).get(token.start);
  const endOffset = token.start + token.text.length;
  return {
    base: {
      query: token.text,
      lookupType: "word",
      contextSentence: sentence.text,
      sourceContext,
      recordId,
      sentenceId: sentence.sentenceId,
      anchorText: token.text,
      occurrence,
      title: "查词",
    },
    anchor: {
      sentenceId: sentence.sentenceId,
      startOffset: token.start,
      endOffset,
      fallbackRect: rectForTextOffsets(element, token.start, endOffset),
    },
  };
}

function splitDictionaryField(value?: string) {
  return (value ?? "")
    .split(/[；;]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

type DictionarySenseExample = {
  key: string;
  example: string;
  exampleTranslation?: string;
};

type DictionarySenseItem = {
  key: string;
  number: number;
  partOfSpeech: string;
  meaning: string;
  examples: DictionarySenseExample[];
};

type DictionaryExampleGroup = {
  key: string;
  number?: number;
  partOfSpeech?: string;
  meaning: string;
  examples: DictionarySenseExample[];
  supplemental?: boolean;
};

type DictionaryCandidateGroup = {
  key: string;
  label: string;
  hint: string;
  candidates: WebDictCandidate[];
};

type DictionaryRenderableEntry = Pick<
  WebDictEntry,
  "word" | "baseWord" | "phonetic" | "meanings" | "examples" | "phrases" | "entryKind" | "exchange" | "tags"
> & {
  id?: number;
  homographNo?: number;
};

const dictionaryAIConfidenceLabelMap: Record<NonNullable<WebDictAIResult["confidence"]>, string> = {
  high: "高置信",
  medium: "中置信",
  low: "低置信",
};

const dictionaryAIClassificationLabelMap = {
  valid_word: "可识别词条",
  slang_or_informal: "俚语/口语",
  proper_noun: "专有名词",
  domain_term: "领域术语",
  variant_or_inflection: "词形/变体",
  possible_typo_or_ocr: "拼写或 OCR 偏差",
  unrecognized_noise: "噪声串",
} satisfies Record<Extract<WebDictAIResult, { mode: "missing_fallback" }>["classification"], string>;

function dictionaryRenderableEntryKey(entry: DictionaryRenderableEntry) {
  return entry.id ?? `${entry.word.toLowerCase()}-${entry.baseWord?.toLowerCase() ?? "entry"}`;
}

function dictionaryAIConfidenceLabel(confidence?: WebDictAIResult["confidence"]) {
  return confidence ? dictionaryAIConfidenceLabelMap[confidence] : null;
}

function dictionaryAIClassificationLabel(
  classification?: Extract<WebDictAIResult, { mode: "missing_fallback" }>["classification"],
) {
  return classification ? dictionaryAIClassificationLabelMap[classification] : null;
}

function normalizeDictionaryText(value?: string) {
  return (value ?? "").replace(/\s+/g, " ").trim().toLowerCase();
}

function dictionaryAITranslationVisible(translation: string | undefined, primaryMeaning: string) {
  if (!translation) {
    return false;
  }
  return normalizeDictionaryText(translation) !== normalizeDictionaryText(primaryMeaning);
}

function dictionaryResolvedQuery(lookup: DictionaryLookupSnapshot) {
  if (lookup.state.kind === "ready") {
    return lookup.state.result.query.trim() || lookup.query;
  }
  return lookup.query;
}

function dictionaryContextExplainQuery(result: Extract<WebDictResult, { kind: "entry" }>, fallbackQuery: string) {
  return result.entry.baseWord?.trim() || result.query.trim() || result.entry.word.trim() || fallbackQuery;
}

function dictionaryIsManualLookup(lookup: DictionaryLookupSnapshot | null) {
  if (!lookup) {
    return false;
  }
  return lookup.sentenceId === "__manual__" || lookup.label === "手动查词";
}

function dictionaryAILookupSource(lookup: DictionaryLookupSnapshot): DictAISourceDto {
  if (dictionaryIsManualLookup(lookup)) {
    return "manual_search";
  }
  if (lookup.label === "选区查词" || lookup.title === "选区查词") {
    return "selection";
  }
  return "reader_click";
}

function dictionaryAIRequestForLookup(
  lookup: DictionaryLookupSnapshot | null,
  mode: WebDictAIRequest["mode"],
): WebDictAIRequest | null {
  if (!lookup || !lookup.contextSentence.trim() || dictionaryIsManualLookup(lookup)) {
    return null;
  }

  const resolvedQuery = dictionaryResolvedQuery(lookup);

  if (mode === "context_explain") {
    const result = lookup.state.kind === "ready" ? lookup.state.result : null;
    if (!result || result.kind !== "entry") {
      return null;
    }
    return {
      mode,
      query: dictionaryContextExplainQuery(result, resolvedQuery),
      queryType: lookup.lookupType,
      contextSentence: lookup.contextSentence,
      occurrence: lookup.occurrence,
      recordId: lookup.recordId,
      sentenceId: lookup.sentenceId,
      source: dictionaryAILookupSource(lookup),
      entryId: result.entry.id,
    };
  }

  return {
    mode,
    query: resolvedQuery,
    queryType: lookup.lookupType,
    contextSentence: lookup.contextSentence,
    occurrence: lookup.occurrence,
    recordId: lookup.recordId,
    sentenceId: lookup.sentenceId,
    source: dictionaryAILookupSource(lookup),
  };
}

function dictionaryAIRequestKey(request: WebDictAIRequest) {
  const entryIdPart = request.mode === "context_explain" ? String(request.entryId) : "missing";
  return [
    request.mode,
    request.query.toLowerCase(),
    request.queryType,
    request.contextSentence.trim().toLowerCase(),
    entryIdPart,
  ].join("::");
}

function dictionaryAIContextKey(lookup: DictionaryLookupSnapshot | null) {
  if (!lookup) {
    return null;
  }

  const base = [
    lookup.query.toLowerCase(),
    lookup.lookupType,
    lookup.contextSentence.trim().toLowerCase(),
    lookup.sentenceId,
    lookup.anchorText.toLowerCase(),
    lookup.occurrence ?? "",
  ].join("::");

  if (lookup.state.kind !== "ready") {
    return `${base}::${lookup.state.kind}`;
  }

  if (lookup.state.result.kind === "entry") {
    return `${base}::entry::${lookup.state.result.entry.id}`;
  }

  return `${base}::${lookup.state.result.kind}`;
}

function dictionaryLookupBase(lookup: DictionaryLookupSnapshot): LookupBase {
  const { state: _state, ...base } = lookup;
  return base;
}

function isDictionaryAIErrorResult(value: unknown): value is WebDictAIErrorResult {
  if (!value || typeof value !== "object") {
    return false;
  }

  const payload = value as Record<string, unknown>;
  return (
    payload.kind === "error" &&
    typeof payload.query === "string" &&
    typeof payload.status === "number" &&
    typeof payload.code === "string" &&
    typeof payload.message === "string"
  );
}

function dictionaryAIActionLabel(
  mode: WebDictAIRequest["mode"],
  state: DictionaryAIViewState,
  panelOpen: boolean,
) {
  const baseLabel = mode === "context_explain" ? "AI 语境解读" : "词典未收录，试试 AI";

  if (state.kind === "loading" && state.mode === mode) {
    return mode === "context_explain" ? "AI 解读中..." : "AI 生成中...";
  }

  if (state.kind === "ready" && state.mode === mode) {
    return panelOpen
      ? mode === "context_explain"
        ? "收起 AI 语境解读"
        : "收起 AI 结果"
      : baseLabel;
  }

  if (state.kind === "error" && state.mode === mode) {
    return mode === "context_explain" ? "重试 AI 语境解读" : "重试 AI 补充";
  }

  return baseLabel;
}

function dictionarySenseItems(entry: DictionaryRenderableEntry): DictionarySenseItem[] {
  let senseNumber = 0;
  const entryKey = dictionaryRenderableEntryKey(entry);

  return entry.meanings.flatMap((meaning, meaningIndex) =>
    meaning.definitions.map((definition, definitionIndex) => {
      senseNumber += 1;
      const examples = splitDictionaryField(definition.example);
      const translations = splitDictionaryField(definition.exampleTranslation);
      const seenExamples = new Set<string>();
      const definitionExamples = examples.flatMap((example, exampleIndex) => {
        const normalized = example.trim();
        if (!normalized) {
          return [];
        }
        const dedupeKey = normalized.toLowerCase();
        if (seenExamples.has(dedupeKey)) {
          return [];
        }
        seenExamples.add(dedupeKey);
        return [
          {
            key: `${entryKey}-${meaningIndex}-${definitionIndex}-example-${exampleIndex}`,
            example: normalized,
            exampleTranslation: translations[exampleIndex]?.trim() || undefined,
          },
        ];
      });

      return {
        key: `${entryKey}-${meaningIndex}-${definitionIndex}-${definition.meaning}`,
        number: senseNumber,
        partOfSpeech: meaning.partOfSpeech,
        meaning: definition.meaning,
        examples: definitionExamples,
      };
    }),
  );
}

function dictionaryExampleGroups(
  entry: DictionaryRenderableEntry,
  senseItems: DictionarySenseItem[],
): DictionaryExampleGroup[] {
  const seenExamples = new Set(
    senseItems.flatMap((sense) => sense.examples.map((example) => example.example.trim().toLowerCase())),
  );
  const entryKey = dictionaryRenderableEntryKey(entry);

  const groups: DictionaryExampleGroup[] = senseItems
    .filter((sense) => sense.examples.length > 0)
    .map((sense) => ({
      key: sense.key,
      number: sense.number,
      partOfSpeech: sense.partOfSpeech,
      meaning: sense.meaning,
      examples: sense.examples,
    }));

  const supplementalExamples = entry.examples.flatMap((example, index) => {
    const normalized = example.example.trim();
    if (!normalized) {
      return [];
    }
    const dedupeKey = normalized.toLowerCase();
    if (seenExamples.has(dedupeKey)) {
      return [];
    }
    seenExamples.add(dedupeKey);
    return [
      {
        key: `${entryKey}-supplemental-example-${index}`,
        example: normalized,
        exampleTranslation: example.exampleTranslation?.trim() || undefined,
      },
    ];
  });

  if (supplementalExamples.length > 0) {
    groups.push({
      key: `${entryKey}-supplemental`,
      meaning: "补充例句",
      examples: supplementalExamples,
      supplemental: true,
    });
  }

  return groups;
}

function dictionaryPartOfSpeechItems(entry: WebDictEntry) {
  const seen = new Set<string>();
  return entry.meanings
    .map((meaning) => meaning.partOfSpeech.trim())
    .filter((partOfSpeech) => {
      if (!partOfSpeech) {
        return false;
      }
      const normalized = partOfSpeech.toLowerCase();
      if (seen.has(normalized)) {
        return false;
      }
      seen.add(normalized);
      return true;
    });
}

const dictionaryTagLabelMap: Record<string, string> = {
  cet: "大学四六级",
  cet4: "大学四级",
  cet6: "大学六级",
  gaokao: "高考",
  gmat: "GMAT",
  gre: "GRE",
  ielts: "雅思",
  ielts_toefl: "雅思托福",
  kaoyan: "考研",
  sat: "SAT",
  tem: "专业英语",
  tem4: "专业英语",
  tem8: "专业英语",
  toefl: "托福",
};

function dictionaryDisplayTags(tags: string[], readingGoal: string) {
  if (readingGoal !== "exam") {
    return [];
  }
  const values = tags
    .map((tag) => tag.trim())
    .filter(Boolean)
    .map((tag) => dictionaryTagLabelMap[tag.toLowerCase()] ?? tag.toUpperCase());
  return Array.from(new Set(values));
}

function dictionaryLookupHistoryKey(lookup: DictionaryLookupSnapshot) {
  return `${lookup.query}-${lookup.sentenceId}-${lookup.anchorText}`;
}

function dictionaryEntrySummary(result: DictionaryEntryResult, lookup?: DictionaryLookupSnapshot | null) {
  return contextualGlossaryText(lookup?.glossary) || firstMeaning(result) || "";
}

function dictionaryLookupHistorySummary(lookup: DictionaryLookupSnapshot) {
  if (lookup.state.kind === "loading") {
    return "查询中";
  }

  if (lookup.state.kind !== "ready") {
    return "词典暂不可用";
  }

  const result = lookup.state.result;

  if (result.kind === "entry") {
    return dictionaryEntrySummary(result, lookup) || "已打开词条";
  }

  if (result.kind === "disambiguation") {
    return result.candidates[0]?.preview || result.candidates[0]?.label || "有多个候选词义";
  }

  if (result.kind === "not_found") {
    return "词典暂未收录";
  }

  return result.message || "词典暂不可用";
}

function disambiguationGroupMeta(
  candidate: WebDictCandidate,
  ambiguityKind: WebDictDisambiguationResult["ambiguityKind"],
) {
  if (candidate.candidateKind === "phrase") {
    return { key: "phrase", label: "完整短语", hint: "优先按固定表达区分" };
  }
  if (candidate.candidateKind === "proper_noun") {
    return { key: "proper_noun", label: "专有名词", hint: "区分专名与普通词义" };
  }
  if (candidate.candidateKind === "fragment" || candidate.entryKind === "fragment") {
    return { key: "fragment", label: "片段释义", hint: "更像局部搭配或片段命中" };
  }
  if (candidate.candidateKind === "variant") {
    return { key: "variant", label: "词形 / 变体", hint: "同源词形或变体词条" };
  }
  if (ambiguityKind === "proper_vs_common") {
    return { key: "common_word", label: "普通词", hint: "和专名相对的普通义项" };
  }
  return { key: "common_word", label: "普通词", hint: "按常见词义查看" };
}

function groupDisambiguationCandidates(result: WebDictDisambiguationResult) {
  const groups = new Map<string, DictionaryCandidateGroup>();

  result.candidates.forEach((candidate) => {
    const meta = disambiguationGroupMeta(candidate, result.ambiguityKind);
    const current = groups.get(meta.key);
    if (current) {
      current.candidates.push(candidate);
      return;
    }
    groups.set(meta.key, {
      ...meta,
      candidates: [candidate],
    });
  });

  const order = ["proper_noun", "common_word", "phrase", "fragment", "variant"];
  return Array.from(groups.values()).sort((left, right) => {
    const leftIndex = order.indexOf(left.key);
    const rightIndex = order.indexOf(right.key);
    const safeLeft = leftIndex === -1 ? order.length : leftIndex;
    const safeRight = rightIndex === -1 ? order.length : rightIndex;
    return safeLeft - safeRight;
  });
}

function InlineLookupPreview({
  lookup,
  floatingRef,
  style,
  onDismiss,
}: {
  lookup: DictionaryLookupSnapshot;
  floatingRef?: (node: HTMLSpanElement | null) => void;
  style?: CSSProperties;
  onDismiss: () => void;
}) {
  const glossaryTitle = contextualGlossaryTitle(lookup);
  const glossaryText = contextualGlossaryText(lookup.glossary);
  const result = lookup.state.kind === "ready" ? lookup.state.result : null;
  const entryResult = result?.kind === "entry" ? result : null;
  const disambiguationResult = result?.kind === "disambiguation" ? result : null;
  const notFoundResult = result?.kind === "not_found" ? result : null;
  const errorMessage =
    lookup.state.kind === "error"
      ? lookup.state.message
      : result?.kind === "error"
        ? result.message
        : "";

  return (
    <ReaderFloatingSurface
      floatingRef={floatingRef}
      className="reader-lookup-preview"
      role="status"
      aria-live="polite"
      style={style}
      onClick={(event) => event.stopPropagation()}
      onPointerDown={(event) => event.stopPropagation()}
    >
      <span className="flex items-start justify-between gap-3">
        <span className="min-w-0">
          <span className="block text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-muted">
            {lookup.label ?? (lookup.lookupType === "phrase" ? "短语" : "词典")}
          </span>
          <span className="mt-1 block truncate reader-serif text-[1.35rem] leading-tight text-ink">
            {entryResult?.entry.word ?? lookup.query}
          </span>
        </span>
        <span className="flex shrink-0 items-start gap-2">
          {entryResult?.entry.phonetic ? (
            <span className="mt-1 text-xs text-muted">{entryResult.entry.phonetic}</span>
          ) : null}
          <button
            type="button"
            className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-full border border-hairline bg-surface text-muted transition-colors hover:border-muted hover:text-ink"
            onClick={onDismiss}
            aria-label="关闭单词卡片"
          >
            <X aria-hidden="true" className="h-3.5 w-3.5" />
          </button>
        </span>
      </span>

      {glossaryTitle && glossaryText ? (
        <span className="mt-3 block rounded-[6px] bg-lens-blue-soft/80 px-3 py-2">
          <span className="block text-xs font-semibold text-lens-blue">{glossaryTitle}</span>
          <span className="mt-1 block text-sm leading-6 text-ink-soft">{glossaryText}</span>
        </span>
      ) : null}

      {lookup.state.kind === "loading" ? (
        <span className="mt-3 block text-sm leading-6 text-muted">正在查词...</span>
      ) : null}

      {entryResult && !glossaryText ? (
        <span className="mt-3 block text-sm leading-6 text-ink-soft">
          {firstMeaning(entryResult) || "当前词条暂无简短释义，左侧面板可查看完整信息。"}
        </span>
      ) : null}

      {disambiguationResult ? (
        <span className="mt-3 block text-sm leading-6 text-muted">
          多个候选词条，已在左侧词典面板列出。
        </span>
      ) : null}

      {notFoundResult ? (
        <span className="mt-3 block text-sm leading-6 text-muted">当前词典暂未收录。</span>
      ) : null}

      {errorMessage ? (
        <span className="mt-3 block text-sm leading-6 text-error-red">{errorMessage}</span>
      ) : null}

      <span className="mt-3 block border-t border-hairline pt-2 text-xs font-semibold text-muted">
        左侧查看完整释义
      </span>
    </ReaderFloatingSurface>
  );
}

function LookupToken({
  children,
  className,
  base,
  activeLookup,
  previewOpen,
  onLookup,
  onDismiss,
  onReveal,
  anchorTarget,
  anchorAttributes,
  focusable = false,
}: {
  children: ReactNode;
  className: string;
  base: LookupBase;
  activeLookup: DictionaryLookupSnapshot | null;
  previewOpen: boolean;
  onLookup: (snapshot: LookupBase, options?: { anchor?: LookupPreviewAnchor | null }) => void;
  onDismiss: () => void;
  onReveal: () => void;
  anchorTarget?: Omit<LookupPreviewAnchor, "fallbackRect">;
  anchorAttributes?: Record<`data-${string}`, string>;
  focusable?: boolean;
}) {
  const activeTarget = sameLookupTarget(activeLookup, base);
  const active = previewOpen && activeTarget;

  const tokenClassName = `reader-lookup-token ${className} ${active ? "reader-mark--active" : ""}`;
  const activateLookup = (eventTarget: EventTarget | null) => {
    if (active) {
      onDismiss();
      return;
    }
    if (activeTarget) {
      onReveal();
      return;
    }
    const target = eventTarget instanceof HTMLElement ? eventTarget : null;
    onLookup(base, {
      anchor: anchorTarget
        ? {
            ...anchorTarget,
            fallbackRect: target ? copyDomRect(target.getBoundingClientRect()) : null,
          }
        : null,
    });
  };

  const handleEscape = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape" && active) {
      event.stopPropagation();
      onDismiss();
    }
  };

  return (
    <span className="relative inline">
      {focusable ? (
        <button
          type="button"
          className={tokenClassName}
          onClick={(event) => {
            event.stopPropagation();
            activateLookup(event.currentTarget);
          }}
          onKeyDown={handleEscape}
          title={base.title}
          aria-label={`查询 ${base.query}`}
          aria-expanded={active}
          {...anchorAttributes}
        >
          {children}
        </button>
      ) : (
        <span
          className={tokenClassName}
          role="button"
          tabIndex={-1}
          title={base.title}
          aria-label={`查询 ${base.query}`}
          aria-expanded={active}
          onClick={(event) => {
            event.stopPropagation();
            activateLookup(event.currentTarget);
          }}
          onKeyDown={handleEscape}
          {...anchorAttributes}
        >
          {children}
        </span>
      )}
    </span>
  );
}

function renderSentenceText({
  sentence,
  marks,
  recordId,
  sourceContext,
  markVisibility,
  userTextRangeAnnotations,
  routeFocusRanges = [],
  activeEntry,
  activeLookup,
  lookupPreviewOpen,
  onLookup,
  onDismissLookup,
  onRevealLookup,
}: {
  sentence: SentenceModel;
  marks: InlineMarkModel[];
  recordId: string;
  sourceContext?: string;
  markVisibility: MarkVisibility;
  userTextRangeAnnotations?: WebAnnotationVm[];
  routeFocusRanges?: RouteFocusSegment[];
  activeEntry?: SentenceEntryModel | null;
  activeLookup: DictionaryLookupSnapshot | null;
  lookupPreviewOpen: boolean;
  onLookup: (snapshot: LookupBase, options?: { anchor?: LookupPreviewAnchor | null }) => void;
  onDismissLookup: () => void;
  onRevealLookup: () => void;
}): ReactNode[] {
  const ranges = buildMarkRanges(sentence, marks);
  const analysisRanges = buildAnalysisRanges(sentence, activeEntry);
  const rangeAnnotations = userTextRangeAnnotations ?? [];
  const plainUserRanges = acceptedUserRanges(rangeAnnotations);
  const plainRouteFocusRanges = acceptedTextRanges(routeFocusRanges);

  const nodes: ReactNode[] = [];
  let cursor = 0;

  function renderPlainSegment(text: string, offset: number) {
    const segmentStart = offset;
    const segmentEnd = offset + text.length;
    const overlappingRanges = plainUserRanges.filter(
      (item) =>
        typeof item.startOffset === "number" &&
        typeof item.endOffset === "number" &&
        item.startOffset < segmentEnd &&
        item.endOffset > segmentStart,
    );

    if (overlappingRanges.length === 0) {
      const overlappingRouteFocusRanges = plainRouteFocusRanges.filter(
        (item) =>
          item.startOffset < segmentEnd &&
          item.endOffset > segmentStart,
      );
      if (overlappingRouteFocusRanges.length === 0) {
        return [text];
      }

      const segmentNodes: ReactNode[] = [];
      let segmentCursor = segmentStart;

      overlappingRouteFocusRanges.forEach((range) => {
        const rangeStart = Math.max(segmentStart, range.startOffset);
        const rangeEnd = Math.min(segmentEnd, range.endOffset);

        if (rangeStart > segmentCursor) {
          segmentNodes.push(sentence.text.slice(segmentCursor, rangeStart));
        }

        if (rangeEnd > rangeStart) {
          const anchorText = sentence.text.slice(rangeStart, rangeEnd);
          segmentNodes.push(
            <span
              key={`route-focus-${sentence.sentenceId}-${rangeStart}-${rangeEnd}`}
              className="reader-route-focus-range"
              title="当前回跳位置"
            >
              {anchorText}
            </span>,
          );
        }

        segmentCursor = Math.max(segmentCursor, rangeEnd);
      });

      if (segmentCursor < segmentEnd) {
        segmentNodes.push(sentence.text.slice(segmentCursor, segmentEnd));
      }

      return segmentNodes;
    }

    const segmentNodes: ReactNode[] = [];
    let segmentCursor = segmentStart;

    overlappingRanges.forEach((annotation) => {
      const rangeStart = Math.max(segmentStart, annotation.startOffset ?? segmentStart);
      const rangeEnd = Math.min(segmentEnd, annotation.endOffset ?? segmentEnd);

      if (rangeStart > segmentCursor) {
        segmentNodes.push(sentence.text.slice(segmentCursor, rangeStart));
      }

      if (rangeEnd > rangeStart) {
        const anchorText = sentence.text.slice(rangeStart, rangeEnd);
        segmentNodes.push(
          <span
            key={`user-range-${annotation.id}-${rangeStart}-${rangeEnd}`}
            className={userRangeClassForAnnotation(annotation)}
            title={annotation.note ? `笔记：${annotation.note}` : "用户标注"}
            {...textRangeAnchorAttributes({
              paragraphId: sentence.paragraphId,
              sentenceId: sentence.sentenceId,
              startOffset: rangeStart,
              endOffset: rangeEnd,
              text: anchorText,
            })}
          >
            {anchorText}
          </span>,
        );
      }

      segmentCursor = Math.max(segmentCursor, rangeEnd);
    });

    if (segmentCursor < segmentEnd) {
      segmentNodes.push(sentence.text.slice(segmentCursor, segmentEnd));
    }

    return segmentNodes;
  }

  if (analysisRanges.length > 0 && activeEntry?.entryType === "sentence_analysis") {
    analysisRanges.forEach((range) => {
      if (range.start < cursor) {
        return;
      }
      if (range.start > cursor) {
        nodes.push(...renderPlainSegment(sentence.text.slice(cursor, range.start), cursor));
      }

      const userRangeClass = userRangeClassForSpan(rangeAnnotations, range.start, range.end);
      const routeFocusClass = routeFocusClassForSpan(plainRouteFocusRanges, range.start, range.end);
      nodes.push(
        <span
          key={range.key}
          className={`reader-analysis-atom reader-analysis-atom--${(range.index % 6) + 1} ${userRangeClass} ${routeFocusClass}`.trim()}
          data-analysis-id={activeEntry.id}
          data-analysis-index={range.index + 1}
          data-analysis-order={range.order}
          data-analysis-label={range.label}
          title={`${range.index + 1}. ${range.label}`}
          {...textRangeAnchorAttributes({
            paragraphId: sentence.paragraphId,
            sentenceId: sentence.sentenceId,
            startOffset: range.start,
            endOffset: range.end,
            text: range.text,
          })}
        >
          {range.text}
        </span>,
      );
      cursor = range.end;
    });

    if (cursor < sentence.text.length) {
      nodes.push(...renderPlainSegment(sentence.text.slice(cursor), cursor));
    }

    return nodes.length > 0 ? nodes : [sentence.text];
  }

  ranges.forEach((range) => {
    if (range.start < cursor) {
      return;
    }
    if (range.start > cursor) {
      nodes.push(...renderPlainSegment(sentence.text.slice(cursor, range.start), cursor));
    }

    const markClass =
      markVisibility === "full"
        ? toneClass[range.mark.visualTone]
        : quietToneClass[range.mark.visualTone];
    const activeEntryClass = markMatchesEntry(range.mark, activeEntry) ? "reader-mark--entry-active" : "";
    const userRangeClass = userRangeClassForSpan(rangeAnnotations, range.start, range.end);
    const routeFocusClass = routeFocusClassForSpan(plainRouteFocusRanges, range.start, range.end);
    const composedMarkClass = `${markClass} ${activeEntryClass} ${userRangeClass} ${routeFocusClass}`.trim();

    if (isDictionaryMark(range.mark)) {
      const base: LookupBase = {
        query: markLookupQuery(range.mark, range.anchorText),
        lookupType: markLookupType(range.mark),
        contextSentence: sentence.text,
        sourceContext,
        recordId,
        sentenceId: sentence.sentenceId,
        anchorText: range.anchorText,
        occurrence: range.occurrence,
        title: contextualGlossaryText(range.mark.glossary) || range.mark.lookupText || markLabel(range.mark),
        label: markLabel(range.mark),
        annotationType: range.mark.annotationType,
        visualTone: range.mark.visualTone,
        glossary: range.mark.glossary,
      };

      nodes.push(
        <LookupToken
          key={range.key}
          className={composedMarkClass}
          base={base}
          activeLookup={activeLookup}
          previewOpen={lookupPreviewOpen}
          onLookup={onLookup}
          onDismiss={onDismissLookup}
          onReveal={onRevealLookup}
          anchorTarget={{
            sentenceId: sentence.sentenceId,
            startOffset: range.start,
            endOffset: range.end,
          }}
          anchorAttributes={textRangeAnchorAttributes({
            paragraphId: sentence.paragraphId,
            sentenceId: sentence.sentenceId,
            startOffset: range.start,
            endOffset: range.end,
            text: range.anchorText,
          })}
          focusable
        >
          {range.anchorText}
        </LookupToken>,
      );
    } else {
      nodes.push(
        <span
          key={range.key}
          className={composedMarkClass}
          title={markLabel(range.mark)}
          {...textRangeAnchorAttributes({
            paragraphId: sentence.paragraphId,
            sentenceId: sentence.sentenceId,
            startOffset: range.start,
            endOffset: range.end,
            text: range.anchorText,
          })}
        >
          {range.anchorText}
        </span>,
      );
    }

    cursor = range.end;
  });

  if (cursor < sentence.text.length) {
    nodes.push(...renderPlainSegment(sentence.text.slice(cursor), cursor));
  }

  return nodes.length > 0 ? nodes : [sentence.text];
}

function DictionaryDetailPanel({
  lookup,
  readingGoal,
  saveState,
  dictionaryAI,
  dictionaryAIPanelOpen,
  searchQuery,
  searchExpanded,
  onSave,
  onRequestAI,
  onSelectAISuggestedQuery,
  onSearchQueryChange,
  onSearchSubmit,
  onSelectCandidate,
  onToggleAIPanel,
  onToggleSearchExpanded,
  onDismiss,
  pinned = false,
  onTogglePinned,
  variant = "sheet",
  canSaveVocabulary = true,
}: {
  lookup: DictionaryLookupSnapshot | null;
  readingGoal: string;
  saveState: SaveState;
  dictionaryAI: DictionaryAIViewState;
  dictionaryAIPanelOpen: boolean;
  searchQuery: string;
  searchExpanded: boolean;
  onSave: () => void;
  onRequestAI: (mode: WebDictAIRequest["mode"]) => void;
  onSelectAISuggestedQuery: (query: string) => void;
  onSearchQueryChange: (value: string) => void;
  onSearchSubmit: (query: string) => void;
  onSelectCandidate: (entryId: number) => void;
  onToggleAIPanel: () => void;
  onToggleSearchExpanded: () => void;
  onDismiss?: () => void;
  pinned?: boolean;
  onTogglePinned?: () => void;
  variant?: "card" | "sheet";
  canSaveVocabulary?: boolean;
}) {
  const lookupResult = lookup?.state.kind === "ready" ? lookup.state.result : null;
  const entryResult = lookupResult?.kind === "entry" ? lookupResult : null;
  const disambiguationResult = lookupResult?.kind === "disambiguation" ? lookupResult : null;
  const notFoundResult = lookupResult?.kind === "not_found" ? lookupResult : null;
  const errorResult = lookupResult?.kind === "error" ? lookupResult : null;
  const isCard = variant === "card";
  const [meaningsExpanded, setMeaningsExpanded] = useState(false);
  const [expandedMeaningKeys, setExpandedMeaningKeys] = useState<string[]>([]);
  const [phrasesExpanded, setPhrasesExpanded] = useState(false);
  const [formsExpanded, setFormsExpanded] = useState(false);
  const [activeTab, setActiveTab] = useState<DictionaryContentTab>("meanings");
  const entryScrollRef = useRef<HTMLDivElement | null>(null);
  const senseItems = entryResult ? dictionarySenseItems(entryResult.entry) : [];
  const exampleGroups = entryResult ? dictionaryExampleGroups(entryResult.entry, senseItems) : [];
  const exampleCount = exampleGroups.reduce((total, group) => total + group.examples.length, 0);
  const displayTags = entryResult ? dictionaryDisplayTags(entryResult.entry.tags, readingGoal) : [];
  const visibleTags = displayTags.slice(0, 3);
  const hiddenTagCount = Math.max(displayTags.length - visibleTags.length, 0);
  const candidateGroups = disambiguationResult ? groupDisambiguationCandidates(disambiguationResult) : [];
  const contextExplainResult =
    dictionaryAI.kind === "ready" && dictionaryAI.result.mode === "context_explain"
      ? dictionaryAI.result
      : null;
  const missingFallbackResult =
    dictionaryAI.kind === "ready" && dictionaryAI.result.mode === "missing_fallback"
      ? dictionaryAI.result
      : null;
  const aiEntry = missingFallbackResult?.kind === "ai_entry" ? missingFallbackResult.entry : null;
  const aiEntrySenseItems = aiEntry ? dictionarySenseItems(aiEntry) : [];
  const aiEntryExampleGroups = aiEntry ? dictionaryExampleGroups(aiEntry, aiEntrySenseItems) : [];
  const aiEntryTags = aiEntry ? dictionaryDisplayTags(aiEntry.tags, readingGoal) : [];
  const isManualLookup = dictionaryIsManualLookup(lookup);
  const canRequestContextExplain = Boolean(entryResult && lookup?.contextSentence.trim() && !isManualLookup);
  const canRequestMissingFallback = Boolean(notFoundResult && lookup?.contextSentence.trim() && !isManualLookup);
  const dictionaryAIMode = dictionaryAI.kind === "idle" ? null : dictionaryAI.mode;
  const panelSizing = isCard
    ? "h-full min-h-0"
    : onDismiss
      ? "h-full min-h-0"
      : lookup
        ? "min-h-[18rem] xl:max-h-[calc(100vh-1.5rem)]"
        : "min-h-[14rem]";
  const contentClass = entryResult
    ? "min-h-0 flex-1 overflow-hidden px-4 py-3.5 md:px-5 md:py-4"
    : "min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-3.5 md:px-5 md:py-4";
  const saveDisabled = saveState.kind === "saving" || saveState.kind === "saved";
  const primaryMeaning =
    (entryResult ? dictionaryEntrySummary(entryResult, lookup) : "") ||
    (entryResult ? "当前词条暂无简短释义。" : "") ||
    (notFoundResult ? "当前词典没有匹配到这个词条。" : "");
  const lemmaWord =
    entryResult?.entry.baseWord && entryResult.entry.baseWord.trim().toLowerCase() !== entryResult.entry.word.trim().toLowerCase()
      ? entryResult.entry.baseWord.trim()
      : null;
  const phoneticLabel = entryResult?.entry.phonetic?.trim() || null;
  const lemmaLabel = lemmaWord ? `原形 ${lemmaWord}` : null;
  const homographLabel = entryResult?.entry.homographNo ? `义项 ${entryResult.entry.homographNo}` : null;
  const tabItems = [
    { id: "meanings" as const, label: "释义", count: senseItems.length },
    { id: "examples" as const, label: "例句", count: exampleCount },
    { id: "phrases" as const, label: "搭配", count: entryResult?.entry.phrases.length ?? 0 },
    { id: "forms" as const, label: "词形", count: entryResult?.entry.exchange.length ?? 0 },
  ].filter((item) => item.count > 0);
  const activeTabItem = tabItems.find((item) => item.id === activeTab) ?? tabItems[0] ?? null;

  useEffect(() => {
    setMeaningsExpanded(false);
    setPhrasesExpanded(false);
    setFormsExpanded(false);
    setExpandedMeaningKeys([]);
    setActiveTab("meanings");
  }, [lookup?.query, lookupResult?.kind]);

  useEffect(() => {
    if (!entryResult || !tabItems.length) {
      return;
    }
    if (!tabItems.some((item) => item.id === activeTab)) {
      setActiveTab(tabItems[0].id);
    }
  }, [activeTab, entryResult, tabItems]);

  useEffect(() => {
    entryScrollRef.current?.scrollTo({ top: 0 });
  }, [activeTab, dictionaryAI.kind, dictionaryAIMode, dictionaryAIPanelOpen, lookup?.query, lookupResult?.kind]);

  function toggleMeaningExpanded(key: string) {
    setExpandedMeaningKeys((value) => (value.includes(key) ? value.filter((item) => item !== key) : [...value, key]));
  }

  function renderAIStatusCard(mode: WebDictAIRequest["mode"]) {
    if (!dictionaryAIPanelOpen || dictionaryAI.kind === "idle" || dictionaryAI.mode !== mode) {
      return null;
    }

    if (dictionaryAI.kind === "loading") {
      return (
        <div className="rounded-[16px] border border-lens-blue/16 bg-lens-blue-soft/70 px-4 py-3">
          <div className="flex items-center gap-2 text-[0.72rem] font-semibold tracking-[0.04em] text-lens-blue">
            <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
            <span>{mode === "context_explain" ? "AI 语境解读" : "AI 补充结果"}</span>
          </div>
          <div className="mt-3 space-y-2">
            <div className="h-3.5 w-24 rounded-full bg-surface/70" />
            <div className="h-3.5 w-5/6 rounded-full bg-surface/70" />
            <div className="h-3.5 w-2/3 rounded-full bg-surface/70" />
          </div>
        </div>
      );
    }

    if (dictionaryAI.kind !== "error") {
      return null;
    }

    const canRetry =
      dictionaryAI.error.code === "upstream_unavailable" || dictionaryAI.error.code === "upstream_error";

    return (
      <div className="rounded-[16px] border border-error-red/16 bg-error-red/6 px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[0.72rem] font-semibold tracking-[0.04em] text-error-red">
              <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
              <span>{mode === "context_explain" ? "AI 语境解读" : "AI 补充结果"}</span>
            </div>
            <p className="mt-2 text-sm leading-6 text-error-red">{dictionaryAI.error.message}</p>
          </div>
          {canRetry ? (
            <button
              type="button"
              className="focus-ring inline-flex shrink-0 items-center rounded-[10px] border border-error-red/18 bg-surface px-3 py-1.5 text-[0.72rem] font-semibold text-error-red transition-colors hover:bg-error-red/8"
              onClick={() => onRequestAI(mode)}
            >
              重试
            </button>
          ) : null}
        </div>
      </div>
    );
  }

  function renderContextExplainCard() {
    if (!dictionaryAIPanelOpen) {
      return null;
    }

    if (dictionaryAI.kind !== "ready" || dictionaryAI.result.mode !== "context_explain" || !contextExplainResult) {
      return renderAIStatusCard("context_explain");
    }

    const details = [
      { label: "词义", value: contextExplainResult.bestFitSense },
      { label: "语境", value: contextExplainResult.whyHere },
      { label: "线索", value: contextExplainResult.cue },
      {
        label: "译法",
        value: dictionaryAITranslationVisible(contextExplainResult.translation, primaryMeaning)
          ? contextExplainResult.translation
          : undefined,
      },
      { label: "易混", value: contextExplainResult.contrast },
      { label: "记忆点", value: contextExplainResult.learningTip },
    ].filter((item) => item.value);
    const confidenceLabel = dictionaryAIConfidenceLabel(contextExplainResult.confidence);

    return (
      <div className="overflow-hidden rounded-[16px] border border-lens-blue/16 bg-lens-blue-soft/70 px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 text-[0.72rem] font-semibold tracking-[0.04em] text-lens-blue">
              <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
              <span>语境解读</span>
              {confidenceLabel ? (
                <span className="rounded-pill border border-lens-blue/14 bg-surface/70 px-2 py-0.5 text-[0.68rem] text-lens-blue">
                  {confidenceLabel}
                </span>
              ) : null}
            </div>
          </div>
          <button
            type="button"
            className="focus-ring reader-dictionary-toolbar-button inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border reader-dictionary-toolbar-button--active"
            onClick={onToggleAIPanel}
            aria-label="收起 AI 语境解读"
          >
            <ChevronUp aria-hidden="true" className="h-3.5 w-3.5" />
          </button>
        </div>
        <div className="mt-3 max-h-[min(18rem,42vh)] overflow-y-auto overscroll-contain pr-1">
          <p className="text-sm leading-6 text-ink-soft">{contextExplainResult.summary}</p>
          {details.length > 0 ? (
            <div className="mt-3 border-t border-lens-blue/12 pt-3">
              <dl className="space-y-2.5">
                {details.map((item) => (
                  <div key={item.label}>
                    <dt className="text-[0.68rem] font-semibold tracking-[0.04em] text-subtle">{item.label}</dt>
                    <dd className="mt-1 text-sm leading-6 text-ink-soft">{item.value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  function renderMissingFallbackCard() {
    if (!dictionaryAIPanelOpen) {
      return null;
    }

    if (dictionaryAI.kind !== "ready" || dictionaryAI.result.mode !== "missing_fallback" || !missingFallbackResult) {
      return renderAIStatusCard("missing_fallback");
    }

    const confidenceLabel = dictionaryAIConfidenceLabel(missingFallbackResult.confidence);
    const classificationLabel = dictionaryAIClassificationLabel(missingFallbackResult.classification);

    if (missingFallbackResult.kind === "ai_unresolved") {
      return (
        <div className="rounded-[16px] border border-hairline/85 bg-surface/78 px-4 py-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2 text-[0.72rem] font-semibold tracking-[0.04em] text-lens-blue">
                <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
                <span>未识别结果</span>
                <span className="rounded-pill border border-hairline/80 bg-reader-paper/74 px-2 py-0.5 text-[0.68rem] text-muted">
                  未验证
                </span>
                {classificationLabel ? (
                  <span className="rounded-pill border border-hairline/80 bg-reader-paper/74 px-2 py-0.5 text-[0.68rem] text-muted">
                    {classificationLabel}
                  </span>
                ) : null}
                {confidenceLabel ? (
                  <span className="rounded-pill border border-hairline/80 bg-reader-paper/74 px-2 py-0.5 text-[0.68rem] text-muted">
                    {confidenceLabel}
                  </span>
                ) : null}
              </div>
              <p className="mt-2 text-sm leading-6 text-ink-soft">{missingFallbackResult.summary}</p>
              {missingFallbackResult.reason ? (
                <p className="mt-2 text-xs leading-5 text-muted">{missingFallbackResult.reason}</p>
              ) : null}
            </div>
            <button
              type="button"
              className="focus-ring reader-dictionary-toolbar-button inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border"
              onClick={onToggleAIPanel}
              aria-label="收起未识别结果"
            >
              <ChevronUp aria-hidden="true" className="h-3.5 w-3.5" />
            </button>
          </div>
          {missingFallbackResult.suggestedQuery.length > 0 ? (
            <div className="mt-3 border-t border-hairline pt-3">
              <p className="text-[0.68rem] font-semibold tracking-[0.04em] text-subtle">换个词再查</p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {missingFallbackResult.suggestedQuery.map((query) => (
                    <button
                      key={query}
                      type="button"
                      className="focus-ring reader-dictionary-secondary-button rounded-[10px] border px-2.5 py-1 text-[0.72rem] font-semibold"
                      onClick={() => onSelectAISuggestedQuery(query)}
                    >
                      {query}
                  </button>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      );
    }

    return (
      <div className="rounded-[16px] border border-structure-green/14 bg-surface/82 px-4 py-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 text-[0.72rem] font-semibold tracking-[0.04em] text-structure-green">
              <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
              <span>未验证词条</span>
              <span className="rounded-pill border border-hairline/80 bg-reader-paper/74 px-2 py-0.5 text-[0.68rem] text-muted">
                未验证
              </span>
              {classificationLabel ? (
                <span className="rounded-pill border border-hairline/80 bg-reader-paper/74 px-2 py-0.5 text-[0.68rem] text-muted">
                  {classificationLabel}
                </span>
              ) : null}
              {confidenceLabel ? (
                <span className="rounded-pill border border-hairline/80 bg-reader-paper/74 px-2 py-0.5 text-[0.68rem] text-muted">
                  {confidenceLabel}
                </span>
              ) : null}
            </div>
            <h4 className="mt-3 reader-serif text-[1.55rem] leading-[1.02] tracking-[-0.02em] text-ink">
              {missingFallbackResult.entry.word}
            </h4>
            {missingFallbackResult.entry.baseWord || missingFallbackResult.entry.phonetic ? (
              <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.8rem] leading-5 text-muted">
                {missingFallbackResult.entry.baseWord &&
                normalizeDictionaryText(missingFallbackResult.entry.baseWord) !==
                  normalizeDictionaryText(missingFallbackResult.entry.word) ? (
                  <span className="rounded-pill border border-hairline/90 bg-reader-paper/84 px-2 py-0.5 text-[0.72rem] font-semibold text-muted">
                    原形 {missingFallbackResult.entry.baseWord}
                  </span>
                ) : null}
                {missingFallbackResult.entry.phonetic ? <span>{missingFallbackResult.entry.phonetic}</span> : null}
              </div>
            ) : null}
            {!aiEntrySenseItems.length ? (
              <p className="mt-3 text-sm leading-6 text-ink-soft">{missingFallbackResult.summary}</p>
            ) : null}
          </div>
          <button
            type="button"
            className="focus-ring reader-dictionary-toolbar-button inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border"
            onClick={onToggleAIPanel}
            aria-label="收起未验证词条"
          >
            <ChevronUp aria-hidden="true" className="h-3.5 w-3.5" />
          </button>
        </div>

        {aiEntryTags.length > 0 ? (
          <div className="mt-3 flex flex-wrap gap-1">
            {aiEntryTags.slice(0, 3).map((item) => (
              <span
                key={item}
                className="reader-dictionary-meta-tag"
              >
                {item}
              </span>
            ))}
            {aiEntryTags.length > 3 ? (
              <span className="reader-dictionary-meta-tag reader-dictionary-meta-tag--count">
                +{aiEntryTags.length - 3}
              </span>
            ) : null}
          </div>
        ) : null}

        {missingFallbackResult.suggestedQuery.length > 0 ? (
          <div className="mt-3 border-t border-hairline pt-3">
            <p className="text-[0.68rem] font-semibold tracking-[0.04em] text-subtle">换个词再查</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {missingFallbackResult.suggestedQuery.map((query) => (
                <button
                  key={query}
                  type="button"
                  className="focus-ring reader-dictionary-secondary-button rounded-[10px] border px-2.5 py-1 text-[0.72rem] font-semibold"
                  onClick={() => onSelectAISuggestedQuery(query)}
                >
                  {query}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        {aiEntrySenseItems.length > 0 ? (
          <div className="mt-4 border-t border-hairline pt-3">
            <p className="text-[0.68rem] font-semibold tracking-[0.04em] text-subtle">释义</p>
            <ol className="mt-2 overflow-hidden rounded-[14px] border border-hairline/80 bg-surface/70">
              {aiEntrySenseItems.slice(0, 4).map((sense) => (
                <li key={sense.key} className="border-t border-hairline/70 px-3.5 py-3 first:border-t-0">
                  <div className="flex items-start gap-3">
                    <span className="inline-flex min-w-[3rem] items-center justify-center rounded-pill bg-structure-green/10 px-2 py-1 text-[0.68rem] font-semibold text-structure-green">
                      {sense.partOfSpeech || "词性"}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-start justify-between gap-2">
                        <p className="text-sm leading-6 text-ink-soft">{sense.meaning}</p>
                        <span className="shrink-0 text-[0.68rem] font-semibold text-subtle">{sense.number}</span>
                      </div>
                      {sense.examples[0] ? (
                        <div className="mt-2 border-t border-hairline/60 pt-2">
                          <blockquote className="reader-serif text-[0.9rem] leading-6 text-ink-soft">
                            {sense.examples[0].example}
                          </blockquote>
                          {sense.examples[0].exampleTranslation ? (
                            <p className="mt-1 text-xs leading-5 text-muted">
                              {sense.examples[0].exampleTranslation}
                            </p>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        ) : null}

        {aiEntryExampleGroups.length > 0 ? (
          <div className="mt-4 border-t border-hairline pt-3">
            <p className="text-[0.68rem] font-semibold tracking-[0.04em] text-subtle">例句</p>
            <div className="mt-2 overflow-hidden rounded-[14px] border border-hairline/80 bg-surface/70">
              {aiEntryExampleGroups.slice(0, 2).map((group) => (
                <section key={group.key} className="border-t border-hairline/70 px-3.5 py-3 first:border-t-0">
                  <div className="flex items-center gap-2">
                    {group.partOfSpeech ? (
                      <span className="rounded-pill bg-structure-green/10 px-2 py-0.5 text-[0.68rem] font-semibold text-structure-green">
                        {group.partOfSpeech}
                      </span>
                    ) : null}
                    {group.number ? (
                      <span className="text-[0.68rem] font-semibold text-subtle">{group.number}</span>
                    ) : null}
                  </div>
                  <p className="mt-2 text-sm font-semibold leading-6 text-ink-soft">{group.meaning}</p>
                  {group.examples[0] ? (
                    <div className="mt-2 rounded-[12px] bg-reader-paper/74 px-3 py-2.5">
                      <blockquote className="reader-serif text-[0.92rem] leading-6 text-ink-soft">
                        {group.examples[0].example}
                      </blockquote>
                      {group.examples[0].exampleTranslation ? (
                        <p className="mt-1 text-xs leading-5 text-muted">
                          {group.examples[0].exampleTranslation}
                        </p>
                      ) : null}
                    </div>
                  ) : null}
                </section>
              ))}
            </div>
          </div>
        ) : null}

        {missingFallbackResult.entry.phrases.length > 0 ? (
          <div className="mt-4 border-t border-hairline pt-3">
            <p className="text-[0.68rem] font-semibold tracking-[0.04em] text-subtle">搭配</p>
            <div className="mt-2 overflow-hidden rounded-[14px] border border-hairline/80 bg-surface/70">
              {missingFallbackResult.entry.phrases.slice(0, 4).map((phrase) => (
                <div
                  key={phrase.phrase}
                  className="flex items-start justify-between gap-3 border-t border-hairline/70 px-3.5 py-3 first:border-t-0"
                >
                  <p className="min-w-0 text-sm font-semibold leading-6 text-ink">{phrase.phrase}</p>
                  {phrase.meaning ? (
                    <p className="max-w-[60%] text-right text-xs leading-5 text-muted">{phrase.meaning}</p>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {missingFallbackResult.entry.exchange.length > 0 ? (
          <div className="mt-4 border-t border-hairline pt-3">
            <p className="text-[0.68rem] font-semibold tracking-[0.04em] text-subtle">词形</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {missingFallbackResult.entry.exchange.slice(0, 6).map((form) => (
                <span
                  key={form}
                  className="rounded-pill border border-hairline bg-surface px-2.5 py-1 text-xs font-semibold text-ink-soft"
                >
                  {form}
                </span>
              ))}
            </div>
          </div>
        ) : null}

      </div>
    );
  }

  function renderEntryTabContent() {
    if (!entryResult || !activeTabItem) {
      return null;
    }

    if (activeTabItem.id === "meanings") {
      const visibleItems = meaningsExpanded ? senseItems : senseItems.slice(0, 6);

      return (
        <div className="space-y-2.5">
          {senseItems.length > 6 ? (
            <div className="flex justify-end">
              <button
                type="button"
                className="focus-ring reader-dictionary-tertiary-button inline-flex items-center gap-1 border px-2.5 py-1 text-xs font-semibold"
                onClick={() => setMeaningsExpanded((value) => !value)}
                aria-expanded={meaningsExpanded}
              >
                {meaningsExpanded ? "收起" : `还有 ${senseItems.length - 6} 条`}
                {meaningsExpanded ? (
                  <ChevronUp aria-hidden="true" className="h-3.5 w-3.5" />
                ) : (
                  <ChevronDown aria-hidden="true" className="h-3.5 w-3.5" />
                )}
              </button>
            </div>
          ) : null}
          <ol className="overflow-hidden rounded-[16px] border border-hairline/85 bg-surface/60">
            {visibleItems.map((sense) => {
              const expanded = expandedMeaningKeys.includes(sense.key);
              const hasExamples = sense.examples.length > 0;

              return (
                <li key={sense.key} className="border-t border-hairline/70 first:border-t-0">
                  <button
                    type="button"
                    className={`focus-ring block w-full px-3.5 py-2.5 text-left transition-colors ${
                      hasExamples ? "hover:bg-reader-paper/60" : "cursor-default"
                    }`}
                    onClick={hasExamples ? () => toggleMeaningExpanded(sense.key) : undefined}
                    aria-expanded={hasExamples ? expanded : undefined}
                  >
                    <div className="flex items-start gap-3">
                      <span className="reader-dictionary-meta-tag min-w-[3rem] justify-center px-2 py-1">
                        {sense.partOfSpeech || "词性"}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-start justify-between gap-2">
                          <p className="text-sm leading-6 text-ink-soft">{sense.meaning}</p>
                          <div className="flex shrink-0 items-center gap-2 pl-2">
                            {hasExamples ? (
                              <span className="reader-dictionary-meta-tag reader-dictionary-meta-tag--count px-2 py-0.5">
                                {sense.examples.length} 例句
                              </span>
                            ) : null}
                            <span className="text-[0.68rem] font-semibold text-subtle">{sense.number}</span>
                            {hasExamples ? (
                              expanded ? (
                                <ChevronUp aria-hidden="true" className="h-3.5 w-3.5 text-subtle" />
                              ) : (
                                <ChevronDown aria-hidden="true" className="h-3.5 w-3.5 text-subtle" />
                              )
                            ) : null}
                          </div>
                        </div>
                        {expanded ? (
                          <div className="mt-3 space-y-2 border-t border-hairline/60 pt-3">
                            {sense.examples.slice(0, 2).map((example) => (
                              <figure key={example.key} className="space-y-1">
                                <blockquote className="reader-serif text-[0.9rem] leading-6 text-ink-soft">
                                  {example.example}
                                </blockquote>
                                {example.exampleTranslation ? (
                                  <figcaption className="text-xs leading-5 text-muted">
                                    {example.exampleTranslation}
                                  </figcaption>
                                ) : null}
                              </figure>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    </div>
                  </button>
                </li>
              );
            })}
          </ol>
        </div>
      );
    }

    if (activeTabItem.id === "examples") {
      return (
        <div className="overflow-hidden rounded-[16px] border border-hairline/85 bg-surface/60">
          {exampleGroups.map((group) => (
            <section key={group.key} className="border-t border-hairline/70 px-3.5 py-3 first:border-t-0">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    {group.supplemental ? (
                      <span className="rounded-pill bg-reader-paper px-2 py-0.5 text-[0.68rem] font-semibold text-muted">
                        补充
                      </span>
                    ) : (
                      <>
                        <span className="rounded-pill bg-structure-green/10 px-2 py-0.5 text-[0.68rem] font-semibold text-structure-green">
                          {group.partOfSpeech || "词性"}
                        </span>
                        {group.number ? (
                          <span className="text-[0.68rem] font-semibold text-subtle">{group.number}</span>
                        ) : null}
                      </>
                    )}
                  </div>
                  <p className="mt-2 text-sm font-semibold leading-6 text-ink-soft">{group.meaning}</p>
                </div>
                <span className="shrink-0 rounded-pill bg-reader-paper px-2 py-0.5 text-[0.68rem] font-semibold text-muted">
                  {group.examples.length}
                </span>
              </div>
              <ol className="mt-3 space-y-2.5">
                {group.examples.map((example) => (
                  <li key={example.key} className="rounded-[12px] bg-reader-paper/74 px-3 py-2.5">
                    <blockquote className="reader-serif text-[0.92rem] leading-6 text-ink-soft">
                      {example.example}
                    </blockquote>
                    {example.exampleTranslation ? (
                      <p className="mt-1 text-xs leading-5 text-muted">{example.exampleTranslation}</p>
                    ) : null}
                  </li>
                ))}
              </ol>
            </section>
          ))}
        </div>
      );
    }

    if (activeTabItem.id === "phrases") {
      const visibleItems = phrasesExpanded ? entryResult.entry.phrases : entryResult.entry.phrases.slice(0, 8);

      return (
        <div className="space-y-3">
          {entryResult.entry.phrases.length > 8 ? (
            <div className="flex justify-end">
              <button
                type="button"
                className="focus-ring reader-dictionary-tertiary-button inline-flex items-center gap-1 border px-2.5 py-1 text-xs font-semibold"
                onClick={() => setPhrasesExpanded((value) => !value)}
                aria-expanded={phrasesExpanded}
              >
                {phrasesExpanded ? "收起" : `还有 ${entryResult.entry.phrases.length - 8} 条`}
                {phrasesExpanded ? (
                  <ChevronUp aria-hidden="true" className="h-3.5 w-3.5" />
                ) : (
                  <ChevronDown aria-hidden="true" className="h-3.5 w-3.5" />
                )}
              </button>
            </div>
          ) : null}
          <div className="overflow-hidden rounded-[16px] border border-hairline/85 bg-surface/60">
            {visibleItems.map((phrase) => (
              <div
                key={phrase.phrase}
                className="flex items-start justify-between gap-3 border-t border-hairline/70 px-3.5 py-3 first:border-t-0"
              >
                <p className="min-w-0 text-sm font-semibold leading-6 text-ink">{phrase.phrase}</p>
                {phrase.meaning ? (
                  <p className="max-w-[60%] text-right text-xs leading-5 text-muted">{phrase.meaning}</p>
                ) : (
                  <span className="text-[0.72rem] text-subtle">搭配</span>
                )}
              </div>
            ))}
          </div>
        </div>
      );
    }

    const visibleItems = formsExpanded ? entryResult.entry.exchange : entryResult.entry.exchange.slice(0, 10);

    return (
      <div className="space-y-3">
        {entryResult.entry.exchange.length > 10 ? (
          <div className="flex justify-end">
            <button
              type="button"
              className="focus-ring reader-dictionary-tertiary-button inline-flex items-center gap-1 border px-2.5 py-1 text-xs font-semibold"
              onClick={() => setFormsExpanded((value) => !value)}
              aria-expanded={formsExpanded}
            >
              {formsExpanded ? "收起" : `还有 ${entryResult.entry.exchange.length - 10} 项`}
              {formsExpanded ? (
                <ChevronUp aria-hidden="true" className="h-3.5 w-3.5" />
              ) : (
                <ChevronDown aria-hidden="true" className="h-3.5 w-3.5" />
              )}
            </button>
          </div>
        ) : null}
        <div className="rounded-[14px] border border-hairline/80 bg-surface/62 px-3.5 py-3">
          <div className="flex flex-wrap gap-1.5">
            {visibleItems.map((form) => (
              <span
                key={form}
                className="rounded-pill border border-hairline bg-surface px-2.5 py-1 text-xs font-semibold text-ink-soft"
              >
                {form}
              </span>
            ))}
          </div>
        </div>
      </div>
    );
  }

  function handleSearchSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSearchSubmit(searchQuery);
  }

  return (
    <section
      className={`reader-tool-panel reader-dictionary-panel ${
        isCard ? "reader-dictionary-card" : ""
      } flex flex-col overflow-hidden ${panelSizing}`}
    >
      <div className="border-b border-hairline px-5 py-3.5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-ink">词典</h2>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              className={`focus-ring reader-dictionary-toolbar-button inline-flex h-11 w-11 items-center justify-center rounded-full border md:h-9 md:w-9 ${
                searchExpanded ? "reader-dictionary-toolbar-button--active" : ""
              }`}
              onClick={onToggleSearchExpanded}
              aria-expanded={searchExpanded}
              aria-label={searchExpanded ? "收起手动搜索" : "展开手动搜索"}
            >
              <Search aria-hidden="true" className="h-4 w-4" />
            </button>
            {onTogglePinned ? (
              <button
                type="button"
                className={`focus-ring reader-dictionary-toolbar-button inline-flex h-11 w-11 items-center justify-center rounded-full border md:h-9 md:w-9 ${
                  pinned ? "reader-dictionary-toolbar-button--active" : ""
                }`}
                onClick={onTogglePinned}
                aria-pressed={pinned}
                aria-label={pinned ? "取消钉住词典" : "钉住词典"}
              >
                <Pin aria-hidden="true" className="h-4 w-4" />
              </button>
            ) : null}
            {onDismiss ? (
              <button
                type="button"
                className="focus-ring reader-dictionary-toolbar-button inline-flex h-11 w-11 items-center justify-center rounded-full border md:h-9 md:w-9"
                onClick={onDismiss}
                aria-label="收起词典"
              >
                <ChevronRight aria-hidden="true" className="h-4 w-4" />
              </button>
            ) : null}
          </div>
        </div>
        {searchExpanded ? (
          <div className="mt-4 border-t border-hairline pt-4">
            <form
              className="rounded-[16px] border border-hairline/85 bg-surface/80 px-3.5 py-3.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.72)]"
              onSubmit={handleSearchSubmit}
            >
              <div className="flex flex-wrap items-center gap-2">
                <div className="flex min-w-0 flex-1 items-center gap-2 rounded-[12px] border border-hairline bg-reader-paper px-3 py-2 transition-colors focus-within:border-lens-blue/35 focus-within:bg-surface">
                  <Search aria-hidden="true" className="h-4 w-4 shrink-0 text-muted" />
                  <input
                    className="min-w-0 flex-1 bg-transparent text-sm leading-6 text-ink outline-none placeholder:text-subtle"
                    value={searchQuery}
                    onChange={(event) => onSearchQueryChange(event.target.value)}
                    placeholder="输入单词或短语"
                    aria-label="输入单词或短语"
                  />
                  {searchQuery ? (
                    <button
                      type="button"
                      className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-full text-subtle transition-colors hover:bg-surface hover:text-ink"
                      onClick={() => onSearchQueryChange("")}
                      aria-label="清空词典搜索"
                    >
                      <X aria-hidden="true" className="h-3.5 w-3.5" />
                    </button>
                  ) : null}
                </div>
                <button
                  type="submit"
                  className="focus-ring reader-dictionary-primary-button inline-flex min-h-11 items-center justify-center rounded-pill px-4 text-xs font-semibold text-surface md:min-h-10"
                >
                  查询
                </button>
              </div>
            </form>
          </div>
        ) : null}
      </div>

      <div className={contentClass}>
        {!lookup ? (
          <div className="flex min-h-40 flex-col justify-center">
            <p className="text-[0.72rem] font-semibold tracking-[0.08em] text-muted">默认状态</p>
            <h3 className="mt-2 reader-serif text-[1.8rem] leading-tight text-ink">先从正文点一个词</h3>
            <p className="mt-3 max-w-[24ch] text-sm leading-6 text-muted">点正文中的词，或直接搜索。</p>
          </div>
        ) : null}

        {lookup?.state.kind === "loading" ? (
          <div className="space-y-5">
            <div>
              <p className="text-[0.72rem] font-semibold tracking-[0.08em] text-muted">正在查询</p>
              <h3 className="mt-2 reader-serif text-[1.95rem] leading-tight text-ink">{lookup.query}</h3>
            </div>
            <div className="rounded-[16px] border border-hairline bg-surface/72 px-4 py-4">
              <div className="h-3 w-24 rounded-full bg-reader-paper" />
              <div className="mt-3 space-y-2">
                <div className="h-3 w-5/6 rounded-full bg-reader-paper" />
                <div className="h-3 w-4/5 rounded-full bg-reader-paper" />
                <div className="h-3 w-2/3 rounded-full bg-reader-paper" />
              </div>
            </div>
            <div className="space-y-2">
              <div className="h-12 rounded-[14px] bg-reader-paper/90" />
              <div className="h-12 rounded-[14px] bg-reader-paper/90" />
            </div>
          </div>
        ) : null}

        {lookup?.state.kind === "error" ? (
          <div className="space-y-4">
            <div>
              <p className="text-[0.72rem] font-semibold tracking-[0.08em] text-muted">查询失败</p>
              <h3 className="mt-2 reader-serif text-[1.95rem] leading-tight text-ink">{lookup.query}</h3>
            </div>
            <div className="rounded-[16px] border border-error-red/18 bg-error-red/6 px-4 py-4">
              <p className="text-sm leading-6 text-error-red">{lookup.state.message}</p>
            </div>
          </div>
        ) : null}

        {lookup && entryResult ? (
          <div className="flex h-full min-h-0 flex-col">
            <div className="shrink-0 space-y-3 border-b border-hairline pb-3">
              <section className="space-y-2.5">
                <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <h3 className="reader-serif text-[clamp(1.95rem,3vw,2.45rem)] leading-[0.98] tracking-[-0.025em] text-ink">
                      {entryResult.entry.word}
                    </h3>
                    {lemmaLabel || phoneticLabel || homographLabel ? (
                      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.79rem] leading-5 text-muted">
                        {lemmaLabel ? (
                          <span className="rounded-pill border border-hairline/90 bg-reader-paper/84 px-2 py-0.5 text-[0.7rem] font-semibold text-muted">
                            {lemmaLabel}
                          </span>
                        ) : null}
                        {phoneticLabel ? <span>{phoneticLabel}</span> : null}
                        {homographLabel ? (
                          <span className="rounded-pill border border-hairline/90 bg-reader-paper/84 px-2 py-0.5 text-[0.7rem] font-semibold text-muted">
                            {homographLabel}
                          </span>
                        ) : null}
                      </div>
                    ) : null}
                    <p className="mt-2.5 max-w-[34ch] text-[0.96rem] leading-6 text-ink-soft">{primaryMeaning}</p>
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-2">
                    <Volume2 aria-hidden="true" className="mt-1 h-4 w-4 text-muted" />
                    <div className="flex items-center gap-1.5">
                      {canRequestContextExplain ? (
                        <button
                          type="button"
                          className={`focus-ring reader-dictionary-inline-action inline-flex h-10 w-10 items-center justify-center rounded-full border md:h-9 md:w-9 ${
                            dictionaryAI.kind === "loading" && dictionaryAI.mode === "context_explain"
                              ? "reader-dictionary-inline-action--accent cursor-wait"
                              : dictionaryAIPanelOpen &&
                                  dictionaryAI.kind === "ready" &&
                                  dictionaryAI.mode === "context_explain"
                                ? "reader-dictionary-inline-action--accent"
                                : ""
                          }`}
                          onClick={() => onRequestAI("context_explain")}
                          disabled={dictionaryAI.kind === "loading" && dictionaryAI.mode === "context_explain"}
                          aria-label={dictionaryAIActionLabel("context_explain", dictionaryAI, dictionaryAIPanelOpen)}
                          aria-pressed={
                            dictionaryAIPanelOpen &&
                            dictionaryAI.kind === "ready" &&
                            dictionaryAI.mode === "context_explain"
                          }
                          title={dictionaryAIActionLabel("context_explain", dictionaryAI, dictionaryAIPanelOpen)}
                        >
                          <Sparkles aria-hidden="true" className="h-4 w-4" />
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className={`focus-ring reader-dictionary-inline-action inline-flex h-10 w-10 items-center justify-center rounded-full border md:h-9 md:w-9 ${
                          saveState.kind === "saved"
                            ? "reader-dictionary-inline-action--saved"
                            : saveState.kind === "error"
                              ? "reader-dictionary-inline-action--error"
                              : ""
                        }`}
                        onClick={onSave}
                        disabled={saveDisabled}
                        aria-label={
                          saveState.kind === "saved"
                            ? "已加入生词本"
                            : saveState.kind === "saving"
                              ? "正在加入生词本"
                              : "加入生词本"
                        }
                        title={
                          saveState.kind === "saved"
                            ? saveState.message
                            : saveState.kind === "error"
                              ? saveState.message
                              : "加入生词本"
                        }
                      >
                        {saveState.kind === "saved" ? (
                          <Check aria-hidden="true" className="h-4 w-4" />
                        ) : (
                          <BookOpen aria-hidden="true" className="h-4 w-4" />
                        )}
                      </button>
                    </div>
                  </div>
                </div>
                {(displayTags.length > 0 || saveState.kind === "saved" || saveState.kind === "error") ? (
                  <div className="space-y-2">
                    {displayTags.length > 0 ? (
                      <div className="flex flex-wrap items-center gap-1.5">
                        {visibleTags.map((item) => (
                          <span key={item} className="reader-dictionary-meta-tag">
                            {item}
                          </span>
                        ))}
                        {hiddenTagCount > 0 ? (
                          <span className="reader-dictionary-meta-tag reader-dictionary-meta-tag--count">
                            +{hiddenTagCount}
                          </span>
                        ) : null}
                      </div>
                    ) : null}
                    {saveState.kind === "saved" ? (
                      <p className="text-[0.76rem] font-semibold text-structure-green">{saveState.message}</p>
                    ) : saveState.kind === "error" ? (
                      <p className="text-[0.76rem] font-semibold text-error-red">{saveState.message}</p>
                    ) : null}
                  </div>
                ) : null}
              </section>

              {activeTabItem ? (
                <div className="reader-dictionary-segmented p-1">
                  <div className="flex flex-wrap gap-1">
                    {tabItems.map((item) => (
                      <button
                        key={item.id}
                        type="button"
                        className={`focus-ring reader-dictionary-tab inline-flex min-h-10 min-w-[4.85rem] flex-1 items-center justify-center gap-1.5 rounded-[11px] border border-transparent px-3 py-1.5 text-[0.76rem] font-semibold md:min-h-8 ${
                          item.id === activeTab
                            ? "reader-dictionary-tab--active"
                            : ""
                        }`}
                        onClick={() => setActiveTab(item.id)}
                        aria-pressed={item.id === activeTab}
                      >
                        <span>{item.label}</span>
                        <span
                          className={`reader-dictionary-tab-count text-[0.66rem] ${
                            item.id === activeTab ? "reader-dictionary-tab-count--active" : ""
                          }`}
                        >
                          {item.count}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>

            <div ref={entryScrollRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain pt-3 pr-1">
              <div className="space-y-3.5 pb-4">
                {canRequestContextExplain && dictionaryAIPanelOpen ? renderContextExplainCard() : null}
                {renderEntryTabContent()}
              </div>
            </div>

          </div>
        ) : null}

        {lookup && disambiguationResult ? (
          <div className="space-y-5">
            <div>
              <p className="text-[0.72rem] font-semibold tracking-[0.08em] text-muted">歧义选择</p>
              <h3 className="mt-2 reader-serif text-[1.95rem] leading-tight text-ink">{lookup.query}</h3>
            </div>
            <div className="space-y-4">
              {candidateGroups.map((group) => (
                <section key={group.key} className="border-t border-hairline pt-4 first:border-t-0 first:pt-0">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold text-ink">{group.label}</p>
                      <p className="mt-1 text-xs leading-5 text-muted">{group.hint}</p>
                    </div>
                    <span className="rounded-pill bg-reader-paper px-2.5 py-1 text-[0.68rem] font-semibold text-muted">
                      {group.candidates.length}
                    </span>
                  </div>
                  <div className="mt-3 space-y-2.5">
                    {group.candidates.map((candidate) => (
                      <button
                        key={candidate.entryId}
                        type="button"
                        className="focus-ring block w-full rounded-[14px] border border-hairline bg-surface/75 px-4 py-3 text-left transition-colors hover:bg-reader-paper"
                        onClick={() => onSelectCandidate(candidate.entryId)}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="text-sm font-semibold text-ink">{candidate.label}</p>
                            <div className="mt-2 flex flex-wrap gap-1.5">
                              <span className="rounded-pill bg-lens-blue-soft px-2 py-0.5 text-[0.68rem] font-semibold text-lens-blue">
                                {candidate.lookupType === "phrase" ? "短语" : "单词"}
                              </span>
                              {candidate.candidateKind === "proper_noun" ? (
                                <span className="rounded-pill bg-structure-green/10 px-2 py-0.5 text-[0.68rem] font-semibold text-structure-green">
                                  专名
                                </span>
                              ) : null}
                              {candidate.entryKind === "fragment" ? (
                                <span className="rounded-pill bg-reader-paper px-2 py-0.5 text-[0.68rem] font-semibold text-muted">
                                  片段
                                </span>
                              ) : null}
                              {candidate.partOfSpeech ? (
                                <span className="rounded-pill bg-reader-paper px-2 py-0.5 text-[0.68rem] font-semibold text-muted">
                                  {candidate.partOfSpeech}
                                </span>
                              ) : null}
                            </div>
                            {candidate.preview ? (
                              <p className="mt-2 text-sm leading-6 text-muted">{candidate.preview}</p>
                            ) : null}
                          </div>
                          <ChevronRight aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-subtle" />
                        </div>
                      </button>
                    ))}
                  </div>
                </section>
              ))}
            </div>
          </div>
        ) : null}

        {lookup && notFoundResult ? (
          <div className="space-y-5">
            <div>
              <p className="text-[0.72rem] font-semibold tracking-[0.08em] text-muted">未收录结果</p>
              <h3 className="mt-2 reader-serif text-[1.95rem] leading-tight text-ink">{lookup.query}</h3>
            </div>
            <div className="space-y-3 rounded-[16px] border border-hairline bg-surface/78 px-4 py-4">
              <p className="text-sm font-semibold text-ink">当前词典没有匹配到这个词条。</p>
              {canRequestMissingFallback ? (
                <div className="space-y-3 border-t border-hairline pt-3">
                  <button
                    type="button"
                    className={`focus-ring reader-dictionary-secondary-button inline-flex min-h-10 items-center gap-2 rounded-[12px] border px-3.5 text-xs font-semibold md:min-h-9 ${
                      dictionaryAI.kind === "loading" && dictionaryAI.mode === "missing_fallback"
                        ? "reader-dictionary-secondary-button--active cursor-wait"
                        : ""
                    }`}
                    onClick={() => onRequestAI("missing_fallback")}
                    disabled={dictionaryAI.kind === "loading" && dictionaryAI.mode === "missing_fallback"}
                  >
                    <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
                    <span>{dictionaryAIActionLabel("missing_fallback", dictionaryAI, dictionaryAIPanelOpen)}</span>
                  </button>
                  {renderMissingFallbackCard()}
                </div>
              ) : null}
            </div>
          </div>
        ) : null}

        {lookup && errorResult ? (
          <div className="space-y-4">
            <div>
              <p className="text-[0.72rem] font-semibold tracking-[0.08em] text-muted">词典暂不可用</p>
              <h3 className="mt-2 reader-serif text-[1.95rem] leading-tight text-ink">{lookup.query}</h3>
            </div>
            <div className="rounded-[16px] border border-error-red/18 bg-error-red/6 px-4 py-4">
              <p className="text-sm leading-6 text-error-red">{errorResult.message}</p>
            </div>
          </div>
        ) : null}

      </div>
    </section>
  );
}

function DictionaryRecentStrip({
  history,
  activeLookup,
  onSelectHistory,
}: {
  history: DictionaryLookupSnapshot[];
  activeLookup: DictionaryLookupSnapshot | null;
  onSelectHistory: (lookup: DictionaryLookupSnapshot) => void;
}) {
  const recentItems = history.slice(0, 8);
  const [collapsed, setCollapsed] = useState(recentItems.length > 3);

  if (recentItems.length <= 1) {
    return null;
  }

  return (
    <section className="rounded-[18px] border border-hairline/85 bg-surface/84 px-3.5 py-3.5 shadow-[0_1px_2px_rgba(17,17,17,0.04)]">
      <button
        type="button"
        className="focus-ring flex min-h-11 w-full items-center justify-between gap-3 text-left"
        onClick={() => setCollapsed((value) => !value)}
        aria-expanded={!collapsed}
      >
        <span className="text-[0.72rem] font-semibold tracking-[0.06em] text-muted">查过历史</span>
        <span className="inline-flex items-center gap-2 text-[0.68rem] font-semibold text-subtle">
          <span className="reader-dictionary-meta-tag reader-dictionary-meta-tag--count px-2 py-0.5">
            {recentItems.length}
          </span>
          {collapsed ? (
            <ChevronDown aria-hidden="true" className="h-3.5 w-3.5" />
          ) : (
            <ChevronUp aria-hidden="true" className="h-3.5 w-3.5" />
          )}
        </span>
      </button>
      {!collapsed ? (
        <div className="mt-3 max-h-[18rem] overflow-y-auto pr-1">
          <div className="space-y-1.5">
            {recentItems.map((item) => {
              const active =
                activeLookup?.query.toLowerCase() === item.query.toLowerCase() &&
                  activeLookup?.sentenceId === item.sentenceId;
              const summary = dictionaryLookupHistorySummary(item);
              return (
                <button
                  key={dictionaryLookupHistoryKey(item)}
                  type="button"
                  className={`focus-ring reader-dictionary-history-row block w-full rounded-[14px] border px-3 py-2.5 text-left ${
                    active ? "reader-dictionary-history-row--active border-lens-blue/16" : "border-hairline/75"
                  }`}
                  onClick={() => onSelectHistory(item)}
                  title={`${item.query}: ${summary}`}
                >
                  <div className="grid grid-cols-[minmax(0,6.35rem)_minmax(0,1fr)] items-start gap-3 md:grid-cols-[minmax(0,6.85rem)_minmax(0,1fr)]">
                    <p
                      className={`line-clamp-2 break-words text-[0.88rem] font-semibold leading-5 ${
                        active ? "text-lens-blue" : "text-ink"
                      }`}
                    >
                      {item.query}
                    </p>
                    <p className="line-clamp-2 text-[0.78rem] leading-5 text-muted">{summary}</p>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function SentenceEntrySummary({
  entries,
  onOpen,
}: {
  entries: SentenceEntryModel[];
  onOpen: (entry: SentenceEntryModel) => void;
}) {
  if (entries.length === 0) {
    return null;
  }

  return (
    <div className="mt-3 flex flex-wrap gap-2">
      {entries.map((entry) => (
        <button
          key={entry.id}
          type="button"
          className="focus-ring reader-entry-chip"
          onClick={(event) => {
            event.stopPropagation();
            onOpen(entry);
          }}
        >
          {entry.entryType === "sentence_analysis" ? (
            <BookOpen aria-hidden="true" className="h-3.5 w-3.5" />
          ) : (
            <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
          )}
          <span>{entry.title ?? entryLabel(entry)}</span>
        </button>
      ))}
    </div>
  );
}

export function ReaderWorkbench({
  record,
  dataSource,
  message,
  initialAnnotations,
  initialFavoriteTargets = [],
}: ReaderWorkbenchProps) {
  const reader = record.reader;
  const searchParams = useSearchParams();
  const [activeLookup, setActiveLookup] = useState<DictionaryLookupSnapshot | null>(null);
  const [activeInspect, setActiveInspect] = useState<ReaderStructuredInspectIntent | null>(null);
  const [lookupPreviewOpen, setLookupPreviewOpen] = useState(false);
  const [lookupPreviewAnchor, setLookupPreviewAnchor] = useState<ReaderLookupPreviewAnchor | null>(null);
  const [lookupPreviewEpoch, setLookupPreviewEpoch] = useState(0);
  const [lookupHistory, setLookupHistory] = useState<DictionaryLookupSnapshot[]>([]);
  const [dictionarySaveState, setDictionarySaveState] = useState<SaveState>({ kind: "idle" });
  const [annotations, setAnnotations] = useState(initialAnnotations);
  const [favoriteTargets, setFavoriteTargets] = useState(initialFavoriteTargets);
  const [jumpTarget, setJumpTarget] = useState<ReaderJumpTarget | null>(null);
  const [activeSentence, setActiveSentence] = useState<SentenceModel | null>(null);
  const [textSelection, setTextSelection] = useState<ReaderTextSelection | null>(null);
  const [contextPanelOpen, setContextPanelOpen] = useState(false);
  const [settingsPanelOpen, setSettingsPanelOpen] = useState(false);
  const [expandedSentenceId, setExpandedSentenceId] = useState<string | null>(null);
  const [activeEntryId, setActiveEntryId] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [selectionNoteOpen, setSelectionNoteOpen] = useState(false);
  const [selectionNoteDraft, setSelectionNoteDraft] = useState("");
  const [selectionFavorited, setSelectionFavorited] = useState(false);
  const [selectionFavoriteLoading, setSelectionFavoriteLoading] = useState(false);
  const [annotationColor, setAnnotationColor] = useState<UserAnnotationColorDto>("warm_yellow");
  const [annotationSaveState, setAnnotationSaveState] = useState<AnnotationSaveState>({ kind: "idle" });
  const [readerSettings, setReaderSettings] = useState<ReaderSettingsState>(defaultReaderSettings);
  const [aiOpen, setAiOpen] = useState(false);
  const [askAttachments, setAskAttachments] = useState<ReaderAskAttachment[]>([]);
  const [dictionaryPinned, setDictionaryPinned] = useState(false);
  const [dictionaryRailOpen, setDictionaryRailOpen] = useState(false);
  const [dictionaryQuery, setDictionaryQuery] = useState("");
  const [dictionarySearchExpanded, setDictionarySearchExpanded] = useState(false);
  const [dictionaryAI, setDictionaryAI] = useState<DictionaryAIViewState>({ kind: "idle" });
  const [dictionaryAIPanelOpen, setDictionaryAIPanelOpen] = useState(false);
  const articleRef = useRef<HTMLElement | null>(null);
  const readingColumnRef = useRef<HTMLDivElement | null>(null);
  const focusedRouteTargetKeyRef = useRef<string | null>(null);
  const dictionaryAIRequestKeyRef = useRef<string | null>(null);
  const [dictionaryDockLayout, setDictionaryDockLayout] = useState<DictionaryDockLayout | null>(null);
  const dictionaryPanelVisible = Boolean(dictionaryRailOpen || dictionaryPinned);

  const {
    refs: {
      setFloating: setSelectionToolbarFloating,
      setPositionReference: setSelectionToolbarReference,
    },
    floatingStyles: selectionToolbarStyles,
  } = useReaderFloatingLayer({
    open: Boolean(textSelection),
    placement: "top-start",
    offsetPx: 14,
    crossAxisOffsetPx: 28,
    strategy: "fixed",
  });
  const {
    refs: {
      setFloating: setLookupPreviewFloating,
      setPositionReference: setLookupPreviewReference,
    },
    floatingStyles: lookupPreviewStyles,
  } = useReaderFloatingLayer({
    open: Boolean((activeLookup || activeInspect) && lookupPreviewOpen && lookupPreviewAnchor),
    placement: "top",
    offsetPx: 12,
    strategy: "fixed",
  });

  useEffect(() => {
    setReaderSettings(readStoredReaderSettings());
  }, []);

  useEffect(() => {
    persistReaderSettings(readerSettings);
  }, [readerSettings]);

  const translationBySentence = useMemo(
    () => new Map(reader.translations.map((item) => [item.sentenceId, item.translationZh])),
    [reader.translations],
  );
  const translationModelBySentence = useMemo(
    () => new Map(reader.translations.map((item) => [item.sentenceId, item])),
    [reader.translations],
  );

  const sentenceById = useMemo(
    () => new Map(reader.article.sentences.map((sentence) => [sentence.sentenceId, sentence])),
    [reader.article.sentences],
  );
  const sentenceTextById = useMemo(
    () => new Map(reader.article.sentences.map((sentence) => [sentence.sentenceId, sentence.text])),
    [reader.article.sentences],
  );

  const entriesBySentence = useMemo(() => {
    const map = new Map<string, SentenceEntryModel[]>();
    reader.sentenceEntries
      .filter((entry) => entry.entryType === "grammar_note" || entry.entryType === "sentence_analysis")
      .forEach((entry) => {
        const current = map.get(entry.sentenceId) ?? [];
        map.set(entry.sentenceId, [...current, entry]);
      });
    return map;
  }, [reader.sentenceEntries]);

  const activeEntry = useMemo(() => {
    if (!activeEntryId) {
      return null;
    }
    return reader.sentenceEntries.find((entry) => entry.id === activeEntryId) ?? null;
  }, [activeEntryId, reader.sentenceEntries]);

  const assetProjection: ReaderAssetProjection = useMemo(
    () =>
      projectReaderAssets({
        annotations,
        favoriteTargets,
        recordId: record.id,
      }),
    [annotations, favoriteTargets, record.id],
  );

  const annotationsBySentence = assetProjection.sentenceAssetSummaryBySentence;
  const favoriteTargetsBySentence = useMemo(() => {
    const map = new Map<string, WebFavoriteTargetVm[]>();
    assetProjection.sentenceAssetSummaryBySentence.forEach((summary, sentenceId) => {
      map.set(sentenceId, summary.favoriteTargets);
    });
    return map;
  }, [assetProjection]);

  const activeSentenceAnnotations = activeSentence
    ? annotationsBySentence.get(activeSentence.sentenceId)?.annotations ?? []
    : [];
  const plateDocument = useMemo(() => renderSceneToPlateDocument(reader), [reader]);
  const pageIdentity: ReaderAskPageIdentity = useMemo(
    () => ({
      recordId: record.id,
      recordTitle: record.title,
      surface: "reader",
      source: "reader_2_0",
    }),
    [record.id, record.title],
  );
  const activeLookupAIContextKey = useMemo(() => dictionaryAIContextKey(activeLookup), [activeLookup]);

  const selectedAnnotation = useMemo(() => {
    if (!textSelection) {
      return null;
    }
    return (
      annotations.find(
        (item) =>
          belongsToCurrentRecord(item.recordId, item.targetKey, record.id) &&
          annotationMatchesSelection(item, textSelection),
      ) ?? null
    );
  }, [annotations, record.id, textSelection]);

  useEffect(() => {
    const targetKey = searchParams.get("targetKey");
    if (!targetKey || focusedRouteTargetKeyRef.current === targetKey) {
      return;
    }

    const nextJumpTarget = jumpToTargetKey(targetKey, {
      annotations,
      favoriteTargets,
    });
    if (!nextJumpTarget) {
      return;
    }

    setJumpTarget(nextJumpTarget);
    focusedRouteTargetKeyRef.current = targetKey;
  }, [annotations, favoriteTargets, searchParams]);

  useEffect(() => {
    if (!jumpTarget) {
      return;
    }

    if (jumpTarget.targetType === "content_summary") {
      if (jumpTarget.scrollStrategy === "center") {
        window.requestAnimationFrame(() => {
          articleRef.current
            ?.querySelector<HTMLElement>("#reader-content-summary")
            ?.scrollIntoView({ block: "center", behavior: "smooth" });
        });
      }

      const targetKey = jumpTarget.targetKey;
      const timer = window.setTimeout(() => {
        setJumpTarget((current) => (current?.targetKey === targetKey ? null : current));
      }, 4200);
      return () => window.clearTimeout(timer);
    }

    const targetSentenceId = jumpTarget.primarySentenceId ?? jumpTarget.sentenceIds[0];
    if (targetSentenceId) {
      const targetSentence = sentenceById.get(targetSentenceId);
      if (targetSentence) {
        setActiveSentence(targetSentence);
        if (jumpTarget.scrollStrategy === "center") {
          window.requestAnimationFrame(() => {
            articleRef.current
              ?.querySelector<HTMLElement>(`#reader-sentence-${CSS.escape(targetSentenceId)}`)
              ?.scrollIntoView({ block: "center", behavior: "smooth" });
          });
        }
      }
    }

    const targetKey = jumpTarget.targetKey;
    const timer = window.setTimeout(() => {
      setJumpTarget((current) => (current?.targetKey === targetKey ? null : current));
    }, 4200);
    return () => window.clearTimeout(timer);
  }, [jumpTarget, sentenceById]);

  const dismissLookupPreview = useCallback(() => {
    setLookupPreviewOpen(false);
  }, []);

  const clearLookup = useCallback(() => {
    setLookupPreviewOpen(false);
    setLookupPreviewAnchor(null);
    setActiveLookup(null);
    setActiveInspect(null);
    setDictionaryRailOpen(false);
    dictionaryAIRequestKeyRef.current = null;
    setDictionaryAI({ kind: "idle" });
    setDictionaryAIPanelOpen(false);
  }, []);

  const closeDictionaryPanel = useCallback(() => {
    setDictionaryPinned(false);
    clearLookup();
  }, [clearLookup]);

  useEffect(() => {
    dictionaryAIRequestKeyRef.current = null;
    setDictionaryAI({ kind: "idle" });
    setDictionaryAIPanelOpen(false);
  }, [activeLookupAIContextKey]);

  useEffect(() => {
    function handleCreated(event: Event) {
      const item = (event as CustomEvent<WebAnnotationVm>).detail;
      if (belongsToCurrentRecord(item.recordId, item.targetKey, record.id)) {
        setAnnotations((current) => [item, ...current.filter((existing) => existing.id !== item.id)]);
      }
    }

    window.addEventListener(ANNOTATION_CREATED_EVENT, handleCreated);
    return () => window.removeEventListener(ANNOTATION_CREATED_EVENT, handleCreated);
  }, [record.id]);

  useEffect(() => {
    if (!lookupPreviewOpen) {
      return;
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setLookupPreviewOpen(false);
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [lookupPreviewOpen]);

  useEffect(() => {
    if (!lookupPreviewOpen || !lookupPreviewAnchor || !articleRef.current) {
      return;
    }

    const articleElement = articleRef.current;
    const handleWindowResize = () => {
      setLookupPreviewEpoch((value) => value + 1);
    };
    const observer = new ResizeObserver(() => {
      setLookupPreviewEpoch((value) => value + 1);
    });

    observer.observe(articleElement);
    window.addEventListener("resize", handleWindowResize);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", handleWindowResize);
    };
  }, [lookupPreviewAnchor, lookupPreviewOpen]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const syncPreviewVisibility = () => {
      if (!shouldShowLookupPreview()) {
        setLookupPreviewOpen(false);
        setLookupPreviewAnchor(null);
      }
    };

    syncPreviewVisibility();
    window.addEventListener("resize", syncPreviewVisibility);
    return () => window.removeEventListener("resize", syncPreviewVisibility);
  }, []);

  useEffect(() => {
    if (!lookupPreviewOpen || !lookupPreviewAnchor) {
      setLookupPreviewReference(null);
      return;
    }

    setLookupPreviewReference({
      getBoundingClientRect: () => {
        const sentenceElement = articleRef.current?.querySelector<HTMLElement>(
          `[data-reader-anchor="sentence"][data-sentence-id="${CSS.escape(lookupPreviewAnchor.sentenceId)}"] [data-reader-sentence-text="true"]`,
        );
        const liveRect = sentenceElement
          ? rectForTextOffsets(
              sentenceElement,
              lookupPreviewAnchor.startOffset,
              lookupPreviewAnchor.endOffset,
            )
          : null;

        return liveRect ?? lookupPreviewAnchor.fallbackRect ?? new DOMRect(0, 0, 0, 0);
      },
      contextElement: articleRef.current ?? undefined,
    });
  }, [lookupPreviewAnchor, lookupPreviewEpoch, lookupPreviewOpen, setLookupPreviewReference]);

  useEffect(() => {
    if (!dictionaryPanelVisible || typeof window === "undefined") {
      setDictionaryDockLayout(null);
      return;
    }

    const articleElement = articleRef.current;
    const readingColumnElement = readingColumnRef.current;
    if (!articleElement || !readingColumnElement) {
      setDictionaryDockLayout(null);
      return;
    }

    const updateDictionaryDockLayout = () => {
      if (window.innerWidth < 1200) {
        setDictionaryDockLayout(null);
        return;
      }

      const articleRect = articleElement.getBoundingClientRect();
      const readingColumnRect = readingColumnElement.getBoundingClientRect();
      const gapToReadingColumn = 28;
      const minLeft = articleRect.left + 18;
      const availableWidth = readingColumnRect.left - minLeft - gapToReadingColumn;

      if (availableWidth < 320) {
        setDictionaryDockLayout(null);
        return;
      }

      const width = Math.min(496, availableWidth);
      const left = Math.max(minLeft, readingColumnRect.left - width - gapToReadingColumn);
      setDictionaryDockLayout({
        left: Math.round(left),
        width: Math.round(width),
      });
    };

    updateDictionaryDockLayout();
    const articleObserver = new ResizeObserver(updateDictionaryDockLayout);
    const readingColumnObserver = new ResizeObserver(updateDictionaryDockLayout);
    articleObserver.observe(articleElement);
    readingColumnObserver.observe(readingColumnElement);
    window.addEventListener("resize", updateDictionaryDockLayout);
    const intervalId = window.setInterval(updateDictionaryDockLayout, 250);

    return () => {
      articleObserver.disconnect();
      readingColumnObserver.disconnect();
      window.removeEventListener("resize", updateDictionaryDockLayout);
      window.clearInterval(intervalId);
    };
  }, [dictionaryPanelVisible]);

  useEffect(() => {
    if (!textSelection) {
      setSelectionToolbarReference(null);
      return;
    }

    setSelectionToolbarReference({
      getBoundingClientRect: () => selectionToolbarRectForReaderSelection(articleRef.current, textSelection),
      contextElement: articleRef.current ?? undefined,
    });
  }, [setSelectionToolbarReference, textSelection]);

  useEffect(() => {
    if (!textSelection) {
      return;
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setTextSelection(null);
        window.getSelection()?.removeAllRanges();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [textSelection]);

  const handleLookupSnapshot = useCallback((snapshot: DictionaryLookupSnapshot) => {
    setActiveLookup(snapshot);
    setActiveInspect(null);
    setDictionaryQuery(snapshot.query);
    setDictionarySaveState({ kind: "idle" });

    if (snapshot.state.kind === "ready") {
      setLookupHistory((current) => [
        snapshot,
        ...current.filter(
          (item) =>
            item.query.toLowerCase() !== snapshot.query.toLowerCase() ||
            item.sentenceId !== snapshot.sentenceId,
        ),
      ].slice(0, 8));
    }
  }, []);

  const lookupPlainText = useCallback(
    async (
      intent: ReaderLookupIntent,
      options?: {
        showPreview?: boolean;
        anchor?: ReaderLookupPreviewAnchor | null;
        openRail?: boolean;
      },
    ) => {
      const nextShowPreview = options?.showPreview ?? shouldShowLookupPreview();

      if (nextShowPreview) {
        setDictionarySearchExpanded(false);
      }
      if (options?.openRail) {
        setDictionaryRailOpen(true);
      }
      setLookupPreviewOpen(nextShowPreview);
      setLookupPreviewAnchor(nextShowPreview ? (options?.anchor ?? null) : null);
      const loadingState = { kind: "loading" } satisfies DictionaryLookupSnapshot["state"];
      handleLookupSnapshot(readerLookupSnapshotFromIntent(record.id, intent, loadingState));

      try {
        const params = new URLSearchParams({
          word: intent.query,
          type: intent.lookupType,
          context: intent.contextSentence,
          sentenceId: intent.sentenceId,
        });
        if (intent.occurrence !== undefined) {
          params.set("occurrence", String(intent.occurrence));
        }
        const response = await fetch(`/api/web/dict/lookup?${params.toString()}`);
        const payload = (await response.json()) as WebDictResult;
        handleLookupSnapshot(readerLookupSnapshotFromIntent(record.id, intent, { kind: "ready", result: payload }));

        if (!response.ok && payload.kind !== "error") {
          handleLookupSnapshot(readerLookupSnapshotFromIntent(record.id, intent, {
            kind: "error",
            message: "词典查询失败。",
          }));
        }
      } catch (error) {
        handleLookupSnapshot(readerLookupSnapshotFromIntent(record.id, intent, {
          kind: "error",
          message: error instanceof Error ? error.message : "词典查询失败。",
        }));
      }
    },
    [handleLookupSnapshot, record.id],
  );

  const lookupDictionaryQuery = useCallback((query: string) => {
    const trimmed = query.trim();
    if (!trimmed) {
      return;
    }

    setDictionarySearchExpanded(true);
    setDictionaryRailOpen(true);
    void lookupPlainText(
      {
        kind: "lexical_lookup",
        query: trimmed,
        lookupType: trimmed.includes(" ") ? "phrase" : "word",
        contextSentence: "",
        sourceContext: undefined,
        sentenceId: "__manual__",
        anchorText: trimmed,
        title: "手动查词",
        label: "手动查词",
      },
      { showPreview: false, openRail: true },
    );
  }, [lookupPlainText]);

  const selectLookupFromTrail = useCallback((lookup: DictionaryLookupSnapshot) => {
    setActiveLookup(lookup);
    setActiveInspect(null);
    setDictionaryRailOpen(true);
    setDictionaryQuery(lookup.query);
    setDictionarySaveState({ kind: "idle" });
    setLookupPreviewAnchor(null);
    setLookupPreviewOpen(false);
  }, []);

  const selectDictionaryCandidate = useCallback(async (entryId: number) => {
    if (!activeLookup) {
      return;
    }

    const base: Omit<DictionaryLookupSnapshot, "state"> = {
      query: activeLookup.query,
      lookupType: activeLookup.lookupType,
      contextSentence: activeLookup.contextSentence,
      sourceContext: activeLookup.sourceContext,
      recordId: activeLookup.recordId,
      sentenceId: activeLookup.sentenceId,
      anchorText: activeLookup.anchorText,
      occurrence: activeLookup.occurrence,
      title: activeLookup.title,
      label: activeLookup.label,
      annotationType: activeLookup.annotationType,
      visualTone: activeLookup.visualTone,
      glossary: activeLookup.glossary,
    };

    handleLookupSnapshot({ ...base, state: { kind: "loading" } });

    try {
      const params = new URLSearchParams({ id: String(entryId) });
      const response = await fetch(`/api/web/dict/entry?${params.toString()}`);
      const payload = (await response.json()) as WebDictResult;
      handleLookupSnapshot({ ...base, state: { kind: "ready", result: payload } });

      if (!response.ok && payload.kind !== "error") {
        handleLookupSnapshot({
          ...base,
          state: { kind: "error", message: "词条加载失败。" },
        });
      }
    } catch (error) {
      handleLookupSnapshot({
        ...base,
        state: {
          kind: "error",
          message: error instanceof Error ? error.message : "词条加载失败。",
        },
      });
    }
  }, [activeLookup, handleLookupSnapshot]);

  const toggleDictionaryAIPanel = useCallback(() => {
    setDictionaryAIPanelOpen((value) => !value);
  }, []);

  const requestDictionaryAI = useCallback(
    async (mode: WebDictAIRequest["mode"]) => {
      if (!activeLookup) {
        return;
      }

      const requestBody = dictionaryAIRequestForLookup(activeLookup, mode);
      if (!requestBody) {
        return;
      }

      const requestKey = dictionaryAIRequestKey(requestBody);
      if (
        dictionaryAI.kind === "ready" &&
        dictionaryAI.mode === mode &&
        dictionaryAI.requestKey === requestKey
      ) {
        setDictionaryAIPanelOpen((value) => !value);
        return;
      }

      if (
        dictionaryAI.kind === "loading" &&
        dictionaryAI.mode === mode &&
        dictionaryAI.requestKey === requestKey
      ) {
        return;
      }

      const lookupAtRequest = activeLookup;
      dictionaryAIRequestKeyRef.current = requestKey;
      setDictionaryAI({ kind: "loading", mode, requestKey });
      setDictionaryAIPanelOpen(true);

      try {
        const response = await fetch("/api/web/dict/ai", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(requestBody),
        });
        const payload = (await response.json().catch(() => null)) as WebDictAIResult | { kind: "error"; [key: string]: unknown } | null;

        if (dictionaryAIRequestKeyRef.current !== requestKey) {
          return;
        }

        if (!response.ok || !payload || payload.kind === "error") {
          const fallbackError: WebDictAIErrorResult = {
            kind: "error",
            query: requestBody.query,
            mode,
            status: response.status || 503,
            code:
              response.status >= 500 || response.status === 0
                ? "upstream_unavailable"
                : "upstream_error",
            message:
              response.status >= 500 || response.status === 0
                ? "AI 查词暂时不可用，请稍后再试。"
                : "AI 查词失败。",
          };
          const errorResult =
            payload && isDictionaryAIErrorResult(payload) ? payload : fallbackError;

          setDictionaryAI({ kind: "error", mode, requestKey, error: errorResult });
          setDictionaryAIPanelOpen(true);

          if (errorResult.code === "canonical_dictionary_available") {
            void lookupPlainText(lookupIntentFromSnapshotBase(dictionaryLookupBase(lookupAtRequest)), {
              showPreview: false,
              openRail: true,
            });
          }

          return;
        }

        setDictionaryAI({
          kind: "ready",
          mode,
          requestKey,
          result: payload,
        });
        setDictionaryAIPanelOpen(true);
      } catch {
        if (dictionaryAIRequestKeyRef.current !== requestKey) {
          return;
        }

        setDictionaryAI({
          kind: "error",
          mode,
          requestKey,
          error: {
            kind: "error",
            query: requestBody.query,
            mode,
            status: 503,
            code: "upstream_unavailable",
            message: "AI 查词暂时不可用，请稍后再试。",
          },
        });
        setDictionaryAIPanelOpen(true);
      }
    },
    [activeLookup, dictionaryAI, lookupPlainText],
  );

  const selectAISuggestedQuery = useCallback(
    (query: string) => {
      if (!activeLookup) {
        return;
      }

      const trimmed = query.trim();
      if (!trimmed) {
        return;
      }

      dictionaryAIRequestKeyRef.current = null;
      setDictionaryAI({ kind: "idle" });
      setDictionaryAIPanelOpen(false);

      void lookupPlainText(
        {
          ...lookupIntentFromSnapshotBase(dictionaryLookupBase(activeLookup)),
          query: trimmed,
          anchorText: trimmed,
          lookupType: trimmed.includes(" ") ? "phrase" : "word",
          title: "AI 建议查词",
          label: "AI 建议",
        },
        { showPreview: false, openRail: true },
      );
    },
    [activeLookup, lookupPlainText],
  );

  const handleLookupIntent = useCallback(
    (intent: ReaderLookupIntent, anchor: ReaderLookupPreviewAnchor | null, options?: { showPreview?: boolean; openRail?: boolean }) => {
      setActiveSentence(sentenceById.get(intent.sentenceId) ?? null);
      setContextPanelOpen(false);
      setAiOpen(false);
      void lookupPlainText(intent, {
        showPreview: options?.showPreview,
        anchor,
        openRail: options?.openRail,
      });
    },
    [lookupPlainText, sentenceById],
  );

  const handleInspectIntent = useCallback(
    (intent: ReaderStructuredInspectIntent, anchor: ReaderLookupPreviewAnchor | null, options?: { showPreview?: boolean; openRail?: boolean }) => {
      const nextShowPreview = options?.showPreview ?? shouldShowLookupPreview();
      if (nextShowPreview) {
        setDictionarySearchExpanded(false);
      }
      if (options?.openRail) {
        setDictionaryRailOpen(true);
      }
      setLookupPreviewOpen(nextShowPreview);
      setLookupPreviewAnchor(nextShowPreview ? anchor : null);
      setActiveLookup(null);
      setActiveInspect(intent);
      setDictionaryQuery(intent.lookupText ?? intent.anchorText);
      setDictionarySaveState({ kind: "idle" });
      dictionaryAIRequestKeyRef.current = null;
      setDictionaryAI({ kind: "idle" });
      setDictionaryAIPanelOpen(false);
      setActiveSentence(sentenceById.get(intent.sentenceId) ?? null);
      setContextPanelOpen(false);
      setAiOpen(false);
    },
    [sentenceById],
  );

  const openDictionaryRail = useCallback(() => {
    setDictionaryRailOpen(true);
    setLookupPreviewOpen(false);
  }, []);

  const updateTextSelectionFromDom = useCallback(() => {
    const nextSelection = readPlateReaderSelection(articleRef.current, sentenceById);
    setTextSelection(nextSelection);

    if (nextSelection) {
      setActiveSentence(nextSelection.sentence);
      setSettingsPanelOpen(false);
      setContextPanelOpen(false);
      setLookupPreviewOpen(false);
      setLookupPreviewAnchor(null);
      setSelectionNoteOpen(false);
      setSelectionNoteDraft("");
      setSelectionFavorited(false);
      setSelectionFavoriteLoading(false);
      setAnnotationSaveState({ kind: "idle" });
    } else {
      setSelectionNoteOpen(false);
      setSelectionNoteDraft("");
      setSelectionFavorited(false);
      setSelectionFavoriteLoading(false);
    }
  }, [sentenceById]);

  useEffect(() => {
    if (!textSelection) {
      return;
    }

    const controller = new AbortController();
    const favoriteMutation = favoriteMutationFromAnchorPayload(
      anchorPayloadFromSelection(record.id, textSelection),
    );

    const params = new URLSearchParams({
      targetType: favoriteMutation.targetType,
      targetKey: favoriteMutation.targetKey,
    });

    void fetch(`/api/web/favorites/target?${params.toString()}`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        const payload = (await response.json()) as { ok: boolean; favorited?: boolean };
        if (response.ok && payload.ok) {
          setSelectionFavorited(Boolean(payload.favorited));
        }
      })
      .catch((error) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setSelectionFavorited(false);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setSelectionFavoriteLoading(false);
        }
      });

    return () => controller.abort();
  }, [record.id, textSelection]);

  function openEntry(sentence: SentenceModel, entry: SentenceEntryModel) {
    setActiveSentence(sentence);
    setContextPanelOpen(false);
    setExpandedSentenceId(sentence.sentenceId);
    setActiveEntryId(entry.id);
    setTextSelection(null);
    setAnnotationSaveState({ kind: "idle" });
  }

  function activateEntry(entry: SentenceEntryModel) {
    setActiveEntryId(entry.id);
  }

  function mergeAnnotation(item: WebAnnotationVm) {
    setAnnotations((current) => [item, ...current.filter((existing) => existing.id !== item.id)]);
  }

  function removeAnnotation(annotationId: string) {
    setAnnotations((current) => current.filter((existing) => existing.id !== annotationId));
  }

  async function patchAnnotation(
    annotationId: string,
    body: { color?: UserAnnotationColorDto | null; note?: string | null },
    errorMessage: string,
  ) {
    const response = await fetch(`/api/web/annotations/${encodeURIComponent(annotationId)}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = (await response.json()) as
      | { ok: true; item: WebAnnotationVm }
      | { ok: false; message?: string };

    if (!response.ok || !payload.ok) {
      throw new Error(payload.ok === false && payload.message ? payload.message : errorMessage);
    }

    mergeAnnotation(payload.item);
    return payload.item;
  }

  async function saveVocabularyFromDictionary() {
    if (activeLookup?.state.kind !== "ready" || activeLookup.state.result.kind !== "entry") {
      setDictionarySaveState({ kind: "error", message: "请先查到明确词条后再加入生词本。" });
      return;
    }

    const result = activeLookup.state.result;
    const shortMeaning = firstMeaning(result);

    if (!shortMeaning) {
      setDictionarySaveState({ kind: "error", message: "当前词条缺少可写入的释义。" });
      return;
    }

    if (!activeLookup.contextSentence.trim()) {
      setDictionarySaveState({ kind: "error", message: "手动查词需要先选中正文句子后再加入生词本。" });
      return;
    }

    setDictionarySaveState({ kind: "saving" });

    const body: VocabularyCreateRequestDto = {
      lemma: result.entry.baseWord ?? result.entry.word,
      display_word: result.entry.word,
      phonetic: result.entry.phonetic ?? null,
      part_of_speech: firstPartOfSpeech(result),
      short_meaning: shortMeaning,
      meanings_json: meaningsJson(result),
      tags: result.entry.tags,
      exchange: exchangeForms(result),
      source_provider: result.provider,
      dict_entry_id: result.entry.id,
      source_sentence: activeLookup.contextSentence,
      source_context: activeLookup.sourceContext ?? null,
      payload_json: {
        source_refs: [
          {
            client_record_id: record.id,
            cloud_record_id: record.id,
            source_sentence: activeLookup.contextSentence,
            source_context: activeLookup.sourceContext ?? null,
            source_sentence_id: activeLookup.sentenceId,
            source_anchor_text: activeLookup.anchorText,
            source_occurrence: activeLookup.occurrence ?? null,
            collected_at: new Date().toISOString(),
          },
        ],
        collected_forms: [activeLookup.anchorText, activeLookup.query],
      },
    };

    try {
      const response = await fetch("/api/web/vocabulary", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = (await response.json().catch(() => ({}))) as { message?: string };

      if (!response.ok) {
        setDictionarySaveState({ kind: "error", message: payload.message ?? "加入生词本失败。" });
        return;
      }

      setDictionarySaveState({ kind: "saved", message: payload.message ?? "已加入生词本。" });
    } catch (error) {
      setDictionarySaveState({
        kind: "error",
        message: error instanceof Error ? error.message : "加入生词本失败。",
      });
    }
  }

  async function saveSentenceAnnotation(
    useNote: boolean,
    options?: { color?: UserAnnotationColorDto; selection?: ReaderTextSelection | null; noteText?: string },
  ) {
    const targetSelection = options?.selection ?? textSelection;
    const targetSentence = targetSelection?.sentence ?? activeSentence;

    if (!targetSentence) {
      setAnnotationSaveState({ kind: "error", message: "请先选择一个句子。" });
      return;
    }

    setAnnotationSaveState({ kind: "saving" });

    const color = options?.color ?? annotationColor;
    const noteText = options?.noteText ?? note;
    const sentenceAnnotations = annotationsBySentence.get(targetSentence.sentenceId)?.annotations ?? [];
    const existingTargetAnnotation = targetSelection
      ? annotations.find(
          (item) =>
            belongsToCurrentRecord(item.recordId, item.targetKey, record.id) &&
            annotationMatchesSelection(item, targetSelection),
        ) ?? null
      : sentenceAnnotations.find((item) => item.anchorType === "sentence") ?? null;

    if (useNote && existingTargetAnnotation) {
      try {
        const item = await patchAnnotation(
          existingTargetAnnotation.id,
          {
            note: noteText.trim(),
            color: existingTargetAnnotation.color ?? color,
          },
          "笔记保存失败。",
        );
        setNote(item.note ?? "");
        setSelectionNoteDraft(item.note ?? "");
        setSelectionNoteOpen(Boolean(item.note));
        setAnnotationSaveState({ kind: "saved", message: "笔记已保存。" });
      } catch (error) {
        setAnnotationSaveState({
          kind: "error",
          message: error instanceof Error ? error.message : "笔记保存失败。",
        });
      }
      return;
    }

    const anchorPayload = targetSelection
      ? anchorPayloadFromSelection(record.id, targetSelection)
      : anchorPayloadFromSentence(record.id, targetSentence);
    const body: WebAnnotationCreateRequest = annotationRequestFromAnchorPayload(anchorPayload, {
      color,
      note: useNote ? noteText.trim() : undefined,
      sentenceTextById,
      translationBySentence: translationModelBySentence,
    });

    try {
      const response = await fetch("/api/web/annotations", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = (await response.json()) as
        | { ok: true; item: WebAnnotationVm }
        | { ok: false; message?: string };

      if (!response.ok || !payload.ok) {
        setAnnotationSaveState({
          kind: "error",
          message: payload.ok === false && payload.message ? payload.message : "批注保存失败。",
        });
        return;
      }

      mergeAnnotation(payload.item);
      window.dispatchEvent(
        new CustomEvent<WebAnnotationVm>(ANNOTATION_CREATED_EVENT, { detail: payload.item }),
      );
      setNote(payload.item.note ?? "");
      setSelectionNoteDraft(payload.item.note ?? "");
      setSelectionNoteOpen(Boolean(payload.item.note));
      setAnnotationSaveState({ kind: "saved", message: useNote ? "笔记已保存。" : "高亮已保存。" });
    } catch (error) {
      setAnnotationSaveState({
        kind: "error",
        message: error instanceof Error ? error.message : "批注保存失败。",
      });
    }
  }

  function highlightTextSelection(colorValue: string) {
    if (!textSelection) {
      return;
    }
    const color = isUserAnnotationColor(colorValue) ? colorValue : annotationColor;
    setAnnotationColor(color);
    void saveSentenceAnnotation(false, { color, selection: textSelection });
  }

  function openTextSelectionNote() {
    if (!textSelection) {
      return;
    }
    setActiveSentence(textSelection.sentence);
    setSettingsPanelOpen(false);
    setContextPanelOpen(false);
    setAiOpen(false);
    setSelectionNoteDraft(selectedAnnotation?.note ?? "");
    setSelectionNoteOpen((value) => !value);
    setAnnotationSaveState({ kind: "idle" });
  }

  async function saveTextSelectionNote() {
    if (!textSelection) {
      return;
    }

    const trimmed = selectionNoteDraft.trim();
    if (!trimmed) {
      setAnnotationSaveState({ kind: "error", message: "请先输入笔记内容。" });
      return;
    }

    if (selectedAnnotation) {
      setAnnotationSaveState({ kind: "saving" });
      try {
        await patchAnnotation(
          selectedAnnotation.id,
          { note: trimmed, color: selectedAnnotation.color ?? annotationColor },
          "笔记保存失败。",
        );
        setAnnotationSaveState({ kind: "saved", message: "笔记已保存。" });
      } catch (error) {
        setAnnotationSaveState({
          kind: "error",
          message: error instanceof Error ? error.message : "笔记保存失败。",
        });
      }
      return;
    }

    await saveSentenceAnnotation(true, {
      color: annotationColor,
      selection: textSelection,
      noteText: trimmed,
    });
  }

  async function clearTextSelectionNote() {
    if (!selectedAnnotation) {
      setSelectionNoteDraft("");
      setSelectionNoteOpen(false);
      return;
    }

    setAnnotationSaveState({ kind: "saving" });
    try {
      if (selectedAnnotation.type === "note") {
        const response = await fetch(`/api/web/annotations/${encodeURIComponent(selectedAnnotation.id)}`, {
          method: "DELETE",
        });
        const payload = (await response.json()) as { ok: true } | { ok: false; message?: string };

        if (!response.ok || !payload.ok) {
          setAnnotationSaveState({
            kind: "error",
            message: payload.ok === false && payload.message ? payload.message : "删除笔记失败。",
          });
          return;
        }

        removeAnnotation(selectedAnnotation.id);
        setSelectionNoteDraft("");
        setSelectionNoteOpen(false);
        setAnnotationSaveState({ kind: "saved", message: "笔记已删除。" });
        return;
      }

      await patchAnnotation(selectedAnnotation.id, { note: null }, "删除笔记失败。");
      setSelectionNoteDraft("");
      setSelectionNoteOpen(false);
      setAnnotationSaveState({ kind: "saved", message: "笔记已删除。" });
    } catch (error) {
      setAnnotationSaveState({
        kind: "error",
        message: error instanceof Error ? error.message : "删除笔记失败。",
      });
    }
  }

  async function deleteTextSelectionAnnotation() {
    if (!selectedAnnotation) {
      return;
    }

    setAnnotationSaveState({ kind: "saving" });
    try {
      const response = await fetch(`/api/web/annotations/${encodeURIComponent(selectedAnnotation.id)}`, {
        method: "DELETE",
      });
      const payload = (await response.json()) as { ok: true } | { ok: false; message?: string };

      if (!response.ok || !payload.ok) {
        setAnnotationSaveState({
          kind: "error",
          message: payload.ok === false && payload.message ? payload.message : "取消标注失败。",
        });
        return;
      }

      setAnnotations((current) => current.filter((existing) => existing.id !== selectedAnnotation.id));
      setSelectionNoteDraft("");
      setSelectionNoteOpen(false);
      setAnnotationSaveState({ kind: "saved", message: "标注已取消。" });
    } catch (error) {
      setAnnotationSaveState({
        kind: "error",
        message: error instanceof Error ? error.message : "取消标注失败。",
      });
    }
  }

  async function toggleTextSelectionFavorite() {
    if (!textSelection) {
      return;
    }

    const anchorPayload = anchorPayloadFromSelection(record.id, textSelection);
    const favoriteMutation = favoriteMutationFromAnchorPayload(anchorPayload);
    setSelectionFavoriteLoading(true);

    try {
      const response = selectionFavorited
        ? await fetch(
            `/api/web/favorites/target?${new URLSearchParams({
              targetType: favoriteMutation.targetType,
              targetKey: favoriteMutation.targetKey,
            }).toString()}`,
            { method: "DELETE" },
          )
        : await fetch("/api/web/favorites/target", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(favoriteMutation),
          });
      const payload = (await response.json()) as { ok: boolean; favorited?: boolean; message?: string };

      if (!response.ok || !payload.ok) {
        setAnnotationSaveState({ kind: "error", message: payload.message ?? "收藏操作失败。" });
        return;
      }

      setSelectionFavorited(Boolean(payload.favorited));
      setFavoriteTargets((current) => {
        const withoutTarget = current.filter(
          (item) => !(item.targetType === favoriteMutation.targetType && item.targetKey === favoriteMutation.targetKey),
        );
        if (!payload.favorited) {
          return withoutTarget;
        }
        return [
          favoriteTargetVmFromAnchorPayload(anchorPayload, favoriteMutation.targetKey),
          ...withoutTarget,
        ];
      });
      setAnnotationSaveState({
        kind: "saved",
        message: payload.favorited ? "已收藏。" : "已取消收藏。",
      });
    } catch (error) {
      setAnnotationSaveState({
        kind: "error",
        message: error instanceof Error ? error.message : "收藏操作失败。",
      });
    } finally {
      setSelectionFavoriteLoading(false);
    }
  }

  function selectCurrentSentenceFromToolbar() {
    if (!textSelection || textSelection.anchorType !== "text_range") {
      return;
    }

    const sentence = textSelection.sentence;
    const sentenceElement = articleRef.current?.querySelector<HTMLElement>(
      `[data-reader-anchor="sentence"][data-sentence-id="${CSS.escape(sentence.sentenceId)}"] [data-reader-sentence-text="true"]`,
    );
    const rect = sentenceElement ? copyDomRect(sentenceElement.getBoundingClientRect()) : textSelection.rect;
    setActiveSentence(sentence);
    setSettingsPanelOpen(false);
    setContextPanelOpen(false);
    setAiOpen(false);
    setTextSelection({
      anchorType: "sentence",
      sentence,
      selectedText: sentence.text,
      segments: [
        {
          paragraphId: sentence.paragraphId,
          sentenceId: sentence.sentenceId,
          sentence,
          selectedText: sentence.text,
          startOffset: 0,
          endOffset: sentence.text.length,
          textHash: hashAnchorText(sentence.text),
        },
      ],
      startOffset: 0,
      endOffset: sentence.text.length,
      textHash: hashAnchorText(sentence.text),
      rect,
    });
    setSelectionNoteOpen(false);
    setSelectionNoteDraft("");
    setSelectionFavorited(false);
    setSelectionFavoriteLoading(false);
    setAnnotationSaveState({ kind: "idle" });
    window.getSelection()?.removeAllRanges();
  }

  function lookupTextSelection() {
    if (!textSelection) {
      return;
    }

    const intent = lookupIntentFromSelection(
      textSelection,
      translationBySentence.get(textSelection.sentence.sentenceId),
    );
    handleLookupIntent(intent, null, { showPreview: false, openRail: true });
  }

  function showSelectionActionPending(message: string) {
    if (textSelection) {
      setActiveSentence(textSelection.sentence);
    }
    setSettingsPanelOpen(false);
    setContextPanelOpen(false);
    setAnnotationSaveState({ kind: "error", message });
  }

  function appendAskAttachments(nextAttachments: ReaderAskAttachment[]) {
    setAskAttachments((current) => {
      const merged = [...current];
      const seen = new Set(current.map((attachment) => askAttachmentKey(attachment)));
      nextAttachments.forEach((attachment) => {
        const key = askAttachmentKey(attachment);
        if (!seen.has(key)) {
          merged.push(attachment);
          seen.add(key);
        }
      });
      return merged;
    });
  }

  function removeAskAttachment(attachmentKey: string) {
    setAskAttachments((current) => current.filter((attachment) => askAttachmentKey(attachment) !== attachmentKey));
  }

  function clearAskAttachments() {
    setAskAttachments([]);
  }

  function openAskWithAttachments(nextAttachments: ReaderAskAttachment[]) {
    if (nextAttachments.length === 0) {
      return;
    }
    appendAskAttachments(nextAttachments);
    setContextPanelOpen(false);
    setAiOpen(true);
  }

  function openAskWithSelection() {
    if (!textSelection) {
      return;
    }
    setActiveSentence(textSelection.sentence);
    openAskWithAttachments([
      askAttachmentFromSelection(pageIdentity, textSelection, {
        sourceSurface: "selection_toolbar",
        entryAction: "ask_about_this",
      }),
    ]);
  }

  function openAskWithSentenceContext() {
    if (!activeSentence) {
      return;
    }
    openAskWithAttachments([
      askAttachmentFromSentence(pageIdentity, activeSentence, {
        sourceSurface: "context_panel",
        entryAction: "explain_this",
      }),
    ]);
  }

  function openAskWithTranslation(sentenceId: string, translationZh: string) {
    const sentence = sentenceById.get(sentenceId);
    if (!sentence) {
      return;
    }
    setActiveSentence(sentence);
    openAskWithAttachments([
      askAttachmentFromTranslation(pageIdentity, sentence, translationZh, {
        sourceSurface: "translation",
        entryAction: "compare_translation",
      }),
    ]);
  }

  function openAskWithAnalysis(sentenceId: string, entryId: string) {
    const sentence = sentenceById.get(sentenceId);
    const entry = reader.sentenceEntries.find((item) => item.id === entryId);
    if (!sentence || !entry) {
      return;
    }
    if (
      entry.entryType !== "grammar_note" &&
      entry.entryType !== "sentence_analysis" &&
      entry.entryType !== "term_note" &&
      entry.entryType !== "logic_note" &&
      entry.entryType !== "interpretation_note"
    ) {
      return;
    }
    setActiveSentence(sentence);
    openAskWithAttachments([
      askAttachmentFromAnalysisBlock(
        pageIdentity,
        sentence,
        {
          entryId: entry.id,
          entryType: entry.entryType,
          label: entryLabel(entry),
          title: entryLabel(entry),
          content: entry.content,
        },
        {
          sourceSurface: "analysis_block",
          entryAction: "explain_this",
        },
      ),
    ]);
  }

  function openAskWithContentSummary(summary: ReaderContentSummaryNode) {
    openAskWithAttachments([
      askAttachmentFromContentSummary(pageIdentity, summary, {
        sourceSurface: "content_summary",
        entryAction: "explain_this",
      }),
    ]);
  }

  function openAskWithAnnotation(annotation: WebAnnotationVm) {
    openAskWithAttachments([
      askAttachmentFromAnnotation(pageIdentity, annotation, {
        sourceSurface: "annotation",
        entryAction: "ask_about_this",
      }),
    ]);
  }

  function openAskWithFavorite(favorite: WebFavoriteTargetVm) {
    openAskWithAttachments([
      askAttachmentFromFavorite(pageIdentity, favorite, {
        sourceSurface: "favorite",
        entryAction: "ask_about_this",
      }),
    ]);
  }

  function openAskWithStructuredInspect(intent: ReaderStructuredInspectIntent) {
    const sentence = sentenceById.get(intent.sentenceId);
    if (!sentence) {
      return;
    }
    setActiveSentence(sentence);
    openAskWithAttachments([
      askAttachmentFromStructuredInspect(pageIdentity, intent, sentence, {
        sourceSurface: "dictionary_inspect",
        entryAction: "lookup_in_context",
      }),
    ]);
  }

  function attachCurrentRecordToAsk() {
    openAskWithAttachments([
      askAttachmentFromRecord(pageIdentity, {
        sourceSurface: "ask_panel",
        entryAction: "ask_about_this",
      }),
    ]);
  }

  function jumpToReaderSentence(sentenceId: string) {
    const sentence = sentenceById.get(sentenceId);
    if (!sentence) {
      return;
    }
    const nextJumpTarget = jumpToTargetRef(sentenceToTargetRef(record.id, sentence));
    if (nextJumpTarget) {
      setJumpTarget(nextJumpTarget);
    }
  }

  function jumpToAnnotation(annotation: WebAnnotationVm) {
    const nextJumpTarget = jumpToTargetRef(annotationToTargetRef(annotation), {
      annotations,
      favoriteTargets,
    });
    if (nextJumpTarget) {
      setJumpTarget(nextJumpTarget);
    }
  }

  function jumpToFavorite(favorite: WebFavoriteTargetVm) {
    const nextJumpTarget = jumpToTargetRef(favoriteToTargetRef(favorite), {
      annotations,
      favoriteTargets,
    });
    if (nextJumpTarget) {
      setJumpTarget(nextJumpTarget);
    }
  }

  function lookupPhraseFromInspect(intent: ReaderStructuredInspectIntent) {
    const nextIntent = lookupIntentFromStructuredInspect(intent);
    handleLookupIntent(nextIntent, lookupPreviewAnchor, { showPreview: false, openRail: true });
  }

  function jumpToAskAttachment(attachment: ReaderAskAttachment) {
    const nextJumpTarget = jumpTargetFromAskAttachment(attachment, {
      annotations,
      favoriteTargets,
    });
    if (nextJumpTarget) {
      setJumpTarget(nextJumpTarget);
    }
  }

  function jumpToAskCitation(citation: ReaderAskCitationDto) {
    const nextJumpTarget = jumpTargetFromAskCitation(citation, record.id, {
      annotations,
      favoriteTargets,
    });
    if (nextJumpTarget) {
      setJumpTarget(nextJumpTarget);
      return;
    }

    if (citation.record_id === record.id && citation.sentence_id) {
      jumpToReaderSentence(citation.sentence_id);
    }
  }

  function selectSentence(sentence: SentenceModel) {
    setActiveSentence(sentence);
    setTextSelection(null);
    setContextPanelOpen(true);
    setSettingsPanelOpen(false);
    setExpandedSentenceId(null);
    setActiveEntryId(null);
    const sentenceAnnotation =
      (annotationsBySentence.get(sentence.sentenceId)?.annotations ?? []).find((item) => item.anchorType === "sentence") ?? null;
    setNote(sentenceAnnotation?.note ?? "");
    setAnnotationColor(sentenceAnnotation?.color ?? "warm_yellow");
    setAnnotationSaveState({ kind: "idle" });
  }

  function openSettingsPanel() {
    setSettingsPanelOpen(true);
    setContextPanelOpen(false);
  }

  function toggleAiWorkspace() {
    setAiOpen((value) => {
      const nextValue = !value;
      if (nextValue) {
        setContextPanelOpen(false);
      }
      return nextValue;
    });
  }

  function closeContextPanel() {
    setContextPanelOpen(false);
    setActiveSentence(null);
    setExpandedSentenceId(null);
    setActiveEntryId(null);
    setTextSelection(null);
  }

  const canvasThemeClass = readerThemeClassName(readerSettings.theme);
  const readingClass = readerTextClassName(readerSettings);
  const readingColumnClass = readerColumnWidthClassName(readerSettings.columnWidth);
  const contextPanelVisible = Boolean(contextPanelOpen && activeSentence);
  const compactDictionaryPanelVisible = Boolean(dictionaryPanelVisible && !dictionaryDockLayout && !aiOpen && !contextPanelVisible);
  const settingsPanelStyle = compactDictionaryPanelVisible
    ? ({ bottom: "min(calc(72vh + 6.5rem), calc(100vh - 18rem))" } satisfies CSSProperties)
    : undefined;

  return (
    <main className="paper-grain min-h-screen px-3 pb-24 pt-3 text-ink sm:px-4 md:pb-6 lg:px-5">
      <div className="relative">
        <div className="relative min-w-0">
          <article
          ref={articleRef}
          className={`min-w-0 overflow-visible rounded-panel border border-hairline shadow-surface-quiet ${canvasThemeClass}`}
          onClick={lookupPreviewOpen ? dismissLookupPreview : undefined}
          onMouseUp={() => {
            window.requestAnimationFrame(updateTextSelectionFromDom);
          }}
          onKeyUp={(event) => {
            if (event.key === "Escape") {
              setTextSelection(null);
              return;
            }
            window.requestAnimationFrame(updateTextSelectionFromDom);
          }}
        >
          <header className="border-b border-hairline px-5 py-4 sm:px-8 lg:px-10">
            <div
              ref={readingColumnRef}
              className={`mx-auto flex ${readingColumnClass} flex-col gap-4 lg:flex-row lg:items-start lg:justify-between`}
            >
              <div className="min-w-0">
                <p className="text-xs font-semibold text-muted">透读正文</p>
                <h1 className="mt-2 max-w-[24ch] font-headline text-3xl font-semibold leading-tight tracking-normal text-ink md:text-[2.35rem]">
                  {record.title}
                </h1>
                <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted">
                  <span>{dataSourceLabel[dataSource]}</span>
                  <span aria-hidden="true" className="h-1 w-1 rounded-full bg-hairline" />
                  <span>{reader.article.sentences.length} 句</span>
                </div>
              </div>
              <div className="flex shrink-0 flex-wrap items-center justify-end gap-2 lg:gap-3">
                <FavoriteButton recordId={record.id} />
                <div className="flex items-stretch gap-1 rounded-[1.1rem] border border-hairline/90 bg-[linear-gradient(180deg,rgba(255,255,255,0.88),rgba(248,245,238,0.96))] p-1 shadow-[0_10px_24px_rgba(17,17,17,0.05),inset_0_1px_0_rgba(255,255,255,0.74)]">
                  <button
                    type="button"
                    className={`focus-ring inline-flex min-h-[3.25rem] min-w-[7.8rem] items-center gap-2 rounded-[0.95rem] border px-3 text-left transition-[background-color,border-color,color,box-shadow,transform] ${
                      readerSettings.showTranslation
                        ? "border-lens-blue/18 bg-[linear-gradient(180deg,rgba(255,255,255,0.86),rgba(234,241,255,0.92))] text-ink shadow-[0_8px_18px_rgba(37,99,235,0.08),inset_0_1px_0_rgba(255,255,255,0.7)]"
                        : "border-transparent bg-transparent text-ink-soft hover:border-hairline hover:bg-[rgba(255,255,255,0.58)] hover:text-ink"
                    }`}
                    onClick={() =>
                      setReaderSettings((current) => ({
                        ...current,
                        showTranslation: !current.showTranslation,
                      }))
                    }
                  >
                    <BookOpen
                      aria-hidden="true"
                      className={`h-4 w-4 shrink-0 ${readerSettings.showTranslation ? "text-lens-blue" : "text-muted"}`}
                    />
                    <span className="flex min-w-0 flex-col">
                      <span className="text-[0.86rem] font-semibold leading-none">
                        {readerSettings.showTranslation ? "原文 + 译文" : "仅看原文"}
                      </span>
                      <span className="mt-1 text-[0.66rem] leading-none text-subtle">
                        {readerSettings.showTranslation ? "双语对照已开" : "安静阅读视图"}
                      </span>
                    </span>
                  </button>
                  <button
                    type="button"
                    className={`focus-ring inline-flex min-h-[3.25rem] min-w-[6.5rem] items-center gap-2 rounded-[0.95rem] border px-3 text-left transition-[background-color,border-color,color,box-shadow,transform] ${
                      settingsPanelOpen
                        ? "border-lens-blue/18 bg-[linear-gradient(180deg,rgba(255,255,255,0.86),rgba(234,241,255,0.92))] text-ink shadow-[0_8px_18px_rgba(37,99,235,0.08),inset_0_1px_0_rgba(255,255,255,0.7)]"
                        : "border-transparent bg-transparent text-ink-soft hover:border-hairline hover:bg-[rgba(255,255,255,0.58)] hover:text-ink"
                    }`}
                    onClick={openSettingsPanel}
                  >
                    <Type
                      aria-hidden="true"
                      className={`h-4 w-4 shrink-0 ${settingsPanelOpen ? "text-lens-blue" : "text-muted"}`}
                    />
                    <span className="flex min-w-0 flex-col">
                      <span className="text-[0.86rem] font-semibold leading-none">阅读显示</span>
                      <span className="mt-1 text-[0.66rem] leading-none text-subtle">字号、行距、底色</span>
                    </span>
                  </button>
                  <div className="mx-0.5 my-1 hidden w-px bg-[linear-gradient(180deg,rgba(232,228,218,0),rgba(232,228,218,0.92),rgba(232,228,218,0))] sm:block" />
                  <button
                    type="button"
                    className="focus-ring inline-flex h-[3.25rem] w-[3.25rem] items-center justify-center rounded-[0.95rem] border border-transparent bg-transparent text-muted transition-[background-color,border-color,color,box-shadow] hover:border-hairline hover:bg-[rgba(255,255,255,0.58)] hover:text-ink"
                    aria-label="更多阅读操作"
                  >
                    <MoreHorizontal aria-hidden="true" className="h-4.5 w-4.5" />
                  </button>
                </div>
              </div>
            </div>

            {message ? (
              <div className={`mx-auto mt-5 ${readingColumnClass} rounded-[10px] border border-lens-blue/20 bg-lens-blue-soft px-4 py-3 text-sm leading-6 text-ink-soft`}>
                {message}
              </div>
            ) : null}
          </header>

          <PlateReaderSurface
            document={plateDocument}
            showTranslation={readerSettings.showTranslation}
            readingClassName={readingClass}
            columnWidth={readerSettings.columnWidth}
            themeClassName={canvasThemeClass}
            activeSentenceId={activeSentence?.sentenceId ?? null}
            jumpTarget={jumpTarget}
            assetProjection={assetProjection}
            annotationVisibilityGroups={readerSettings.annotationVisibilityGroups}
            onAnnotationJump={jumpToAnnotation}
            onAnnotationAsk={openAskWithAnnotation}
            onFavoriteJump={jumpToFavorite}
            onAskTranslation={openAskWithTranslation}
            onAskAnalysis={openAskWithAnalysis}
            onAskContentSummary={openAskWithContentSummary}
            onLookupIntent={(intent, anchor) => handleLookupIntent(intent, anchor, { showPreview: true })}
            onInspectIntent={(intent, anchor) => handleInspectIntent(intent, anchor, { showPreview: true })}
            onSentenceActivate={(sentenceId) => {
              const sentence = sentenceById.get(sentenceId);
              if (sentence) {
                selectSentence(sentence);
              }
            }}
          />
        </article>

        {textSelection && !contextPanelVisible ? (
          <div
            ref={setSelectionToolbarFloating}
            style={selectionToolbarStyles}
            className="z-50"
            onPointerDown={(event) => {
              const target = event.target instanceof HTMLElement ? event.target : null;
              if (target?.closest("[data-selection-note-input='true']")) {
                return;
              }
              event.preventDefault();
            }}
          >
            <SelectionToolbar
              selectedText={textSelection.selectedText}
              selectionMode={textSelection.anchorType}
              activeColor={selectedAnnotation?.color ?? annotationColor}
              hasAnnotation={Boolean(selectedAnnotation)}
              hasHighlight={selectedAnnotation?.type === "highlight"}
              hasNote={Boolean(selectedAnnotation?.note)}
              favorited={selectionFavorited}
              noteOpen={selectionNoteOpen}
              noteValue={selectionNoteDraft}
              noteSaving={annotationSaveState.kind === "saving"}
              statusMessage={
                annotationSaveState.kind === "saved" || annotationSaveState.kind === "error"
                  ? annotationSaveState.message
                  : undefined
              }
              statusKind={
                annotationSaveState.kind === "saved" || annotationSaveState.kind === "error"
                  ? annotationSaveState.kind
                  : undefined
              }
              disabled={{
                favorite: selectionFavoriteLoading,
              }}
              onSelectSentence={selectCurrentSentenceFromToolbar}
              onHighlight={(color) => highlightTextSelection(color)}
              onNote={openTextSelectionNote}
              onNoteChange={setSelectionNoteDraft}
              onNoteSave={saveTextSelectionNote}
              onNoteClear={clearTextSelectionNote}
              onClearAnnotation={deleteTextSelectionAnnotation}
              onFavorite={toggleTextSelectionFavorite}
              onLookup={lookupTextSelection}
              onAsk={openAskWithSelection}
              onFeedback={() => showSelectionActionPending("选区反馈稍后接入；当前可先用笔记记录问题。")}
              onMore={() => showSelectionActionPending("更多选区操作稍后接入。")}
            />
          </div>
        ) : null}
        {lookupPreviewOpen && lookupPreviewAnchor && (activeLookup || activeInspect) ? (
          <ReaderQuickPeek
            lookup={activeLookup}
            inspect={activeInspect}
            floatingRef={setLookupPreviewFloating}
            style={lookupPreviewStyles}
            onDismiss={dismissLookupPreview}
            onOpenDetail={openDictionaryRail}
            onLookupPhrase={activeInspect ? () => lookupPhraseFromInspect(activeInspect) : undefined}
            onAttachToAsk={activeInspect ? () => openAskWithStructuredInspect(activeInspect) : undefined}
          />
        ) : null}
        </div>
      </div>

      {dictionaryPanelVisible && dictionaryDockLayout ? (
        <div
          className="fixed top-3 bottom-3 z-40 hidden xl:block"
          style={{ left: `${dictionaryDockLayout.left}px`, width: `${dictionaryDockLayout.width}px` }}
        >
          <ReaderDictionaryRail
            className="h-full"
            lookup={activeLookup}
            inspect={activeInspect}
            history={lookupHistory}
            readingGoal={record.readingGoal}
            saveState={dictionarySaveState}
            dictionaryAI={dictionaryAI}
            dictionaryAIPanelOpen={dictionaryAIPanelOpen}
            searchQuery={dictionaryQuery}
            searchExpanded={dictionarySearchExpanded}
            onSave={saveVocabularyFromDictionary}
            onRequestAI={requestDictionaryAI}
            onSelectAISuggestedQuery={selectAISuggestedQuery}
            onSearchQueryChange={setDictionaryQuery}
            onSearchSubmit={lookupDictionaryQuery}
            onSelectCandidate={selectDictionaryCandidate}
            onToggleAIPanel={toggleDictionaryAIPanel}
            onToggleSearchExpanded={() => setDictionarySearchExpanded((value) => !value)}
            onDismiss={closeDictionaryPanel}
            pinned={dictionaryPinned}
            onTogglePinned={() => setDictionaryPinned((value) => !value)}
            variant="card"
            canSaveVocabulary={Boolean(activeLookup?.contextSentence.trim())}
            onLookupPhraseFromInspect={lookupPhraseFromInspect}
            onAttachToAsk={openAskWithStructuredInspect}
            onSelectHistory={selectLookupFromTrail}
          />
        </div>
      ) : null}

      {dictionaryPanelVisible && !dictionaryDockLayout && !aiOpen && !contextPanelVisible ? (
        <div className="fixed inset-x-3 bottom-[5.25rem] z-50 flex max-h-[72vh] flex-col md:bottom-6">
          <ReaderDictionaryRail
            lookup={activeLookup}
            inspect={activeInspect}
            history={lookupHistory}
            readingGoal={record.readingGoal}
            saveState={dictionarySaveState}
            dictionaryAI={dictionaryAI}
            dictionaryAIPanelOpen={dictionaryAIPanelOpen}
            searchQuery={dictionaryQuery}
            searchExpanded={dictionarySearchExpanded}
            onSave={saveVocabularyFromDictionary}
            onRequestAI={requestDictionaryAI}
            onSelectAISuggestedQuery={selectAISuggestedQuery}
            onSearchQueryChange={setDictionaryQuery}
            onSearchSubmit={lookupDictionaryQuery}
            onSelectCandidate={selectDictionaryCandidate}
            onToggleAIPanel={toggleDictionaryAIPanel}
            onToggleSearchExpanded={() => setDictionarySearchExpanded((value) => !value)}
            onDismiss={clearLookup}
            canSaveVocabulary={Boolean(activeLookup?.contextSentence.trim())}
            onLookupPhraseFromInspect={lookupPhraseFromInspect}
            onAttachToAsk={openAskWithStructuredInspect}
            onSelectHistory={selectLookupFromTrail}
          />
        </div>
      ) : null}

      {contextPanelVisible ? (
        <div className="fixed inset-x-3 bottom-[5.25rem] z-50 md:bottom-6 md:left-1/2 md:right-auto md:w-[min(460px,calc(100vw-8rem))] md:-translate-x-1/2">
          <ReaderContextPanel
            sentence={activeSentence}
            selectedText={textSelection?.selectedText ?? null}
            annotationScope={textSelection ? "text_range" : "sentence"}
            note={note}
            color={annotationColor}
            saveState={annotationSaveState}
            sentenceAnnotations={activeSentenceAnnotations}
            sentenceFavorites={activeSentence ? favoriteTargetsBySentence.get(activeSentence.sentenceId) ?? [] : []}
            onNoteChange={setNote}
            onColorChange={setAnnotationColor}
            onSaveAnnotation={saveSentenceAnnotation}
            onAsk={openAskWithSentenceContext}
            onAnnotationJump={jumpToAnnotation}
            onAnnotationAsk={openAskWithAnnotation}
            onFavoriteJump={jumpToFavorite}
            onFavoriteAsk={openAskWithFavorite}
            onClose={closeContextPanel}
          />
        </div>
      ) : null}

      {settingsPanelOpen ? (
        <div
          className="fixed inset-x-3 bottom-[5.25rem] z-50 md:bottom-6 md:left-1/2 md:right-auto md:w-[min(640px,calc(100vw-6rem))] md:-translate-x-1/2"
          style={settingsPanelStyle}
        >
          <ReaderSettingsPanel
            value={readerSettings}
            onChange={setReaderSettings}
            onClose={() => setSettingsPanelOpen(false)}
          />
        </div>
      ) : null}

      {!contextPanelVisible || aiOpen ? (
        <AiWorkspacePanel
          key={record.id}
          open={aiOpen}
          recordId={record.id}
          recordTitle={record.title}
          activeSentence={activeSentence}
          pageIdentity={pageIdentity}
          attachments={askAttachments}
          hideLauncherOnMobile={Boolean(dictionaryPanelVisible)}
          hideLauncherInCompactLayout={Boolean(dictionaryPanelVisible)}
          onRemoveAttachment={removeAskAttachment}
          onClearAttachments={clearAskAttachments}
          onAttachCurrentRecord={attachCurrentRecordToAsk}
          onJumpToAttachment={jumpToAskAttachment}
          onJumpToCitation={jumpToAskCitation}
          onToggle={toggleAiWorkspace}
        />
      ) : null}
    </main>
  );
}
