"use client";

import type { CSSProperties, FormEvent, KeyboardEvent as ReactKeyboardEvent, ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { TEXT_RANGE_HASH_ALGORITHM, TEXT_RANGE_OFFSET_UNIT, USER_ANNOTATION_COLORS } from "@claread/contracts";
import {
  BookOpen,
  Check,
  ChevronRight,
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
  ReaderCanvas,
  ReaderContextPanel,
  ReaderFloatingSurface,
  ReaderSentenceRow,
  SelectionToolbar,
  SentenceEntryCard,
  annotationMatchesSelection,
  copyDomRect,
  favoriteTargetForSelection,
  firstUsableRangeRect,
  hashAnchorText,
  readReaderSelection,
  rectForTextOffsets,
  textRangeAnchorAttributes,
  textOffsetWithinElement,
  type ReaderTextSelection,
  useReaderFloatingLayer,
} from "@/components/reader";
import { parseSentenceAnalysisContent } from "@/components/reader/reader-entry-utils";
import type {
  UserAnnotationColorDto,
  WebAnchorSegmentVm,
  WebAnnotationCreateRequest,
  WebAnnotationVm,
} from "@/types/api/annotations";
import type { WebFavoriteTargetVm } from "@/types/api/favorites";
import type { WebDictResult } from "@/types/api/dict";
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
type LowerPanelMode = "sentence" | "settings";
type ReadingDensity = "calm" | "roomy";
type ReaderTheme = "paper" | "white" | "green";
type MarkVisibility = "full" | "quiet";
type ReaderFontSize = "compact" | "normal" | "large";
type DictionaryEntryResult = Extract<WebDictResult, { kind: "entry" }>;
type RouteFocusSegment = {
  sentenceId: string;
  startOffset: number;
  endOffset: number;
};
type RouteFocusState = {
  targetKey: string;
  anchorType: "sentence" | "text_range" | "multi_text";
  sentenceIds: string[];
  segments: RouteFocusSegment[];
};

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

function routeFocusStateFromFavoriteTarget(target: WebFavoriteTargetVm): RouteFocusState | null {
  if (target.anchorType === "multi_text") {
    const segments = target.segments
      .map((segment) => ({
        sentenceId: segment.sentenceId,
        startOffset: segment.startOffset,
        endOffset: segment.endOffset,
      }))
      .filter((segment) => segment.sentenceId && segment.startOffset < segment.endOffset);
    if (segments.length === 0) {
      return null;
    }
    return {
      targetKey: target.targetKey,
      anchorType: "multi_text",
      sentenceIds: Array.from(new Set(segments.map((segment) => segment.sentenceId))),
      segments,
    };
  }

  if (target.anchorType === "text_range") {
    if (
      !target.sentenceId ||
      typeof target.startOffset !== "number" ||
      typeof target.endOffset !== "number" ||
      target.startOffset >= target.endOffset
    ) {
      return null;
    }
    return {
      targetKey: target.targetKey,
      anchorType: "text_range",
      sentenceIds: [target.sentenceId],
      segments: [
        {
          sentenceId: target.sentenceId,
          startOffset: target.startOffset,
          endOffset: target.endOffset,
        },
      ],
    };
  }

  if (!target.sentenceId) {
    return null;
  }

  return {
    targetKey: target.targetKey,
    anchorType: "sentence",
    sentenceIds: [target.sentenceId],
    segments: [],
  };
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
type SentenceLookupTarget = {
  base: LookupBase;
  anchorRect: DOMRect | null;
};

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
    anchorRect: rectForTextOffsets(element, token.start, token.start + token.text.length),
  };
}

function splitDictionaryField(value?: string) {
  return (value ?? "")
    .split(/[；;]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function dictionaryExampleItems(entry: DictionaryEntryResult["entry"]) {
  const items: Array<{ example: string; exampleTranslation?: string }> = [];
  const seen = new Set<string>();

  function add(example?: string, exampleTranslation?: string) {
    const normalized = example?.trim();
    if (!normalized || seen.has(normalized.toLowerCase())) {
      return;
    }
    seen.add(normalized.toLowerCase());
    items.push({
      example: normalized,
      exampleTranslation: exampleTranslation?.trim() || undefined,
    });
  }

  entry.examples.forEach((example) => {
    add(example.example, example.exampleTranslation);
  });

  if (items.length === 0) {
    entry.meanings.forEach((meaning) => {
      meaning.definitions.forEach((definition) => {
        const examples = splitDictionaryField(definition.example);
        const translations = splitDictionaryField(definition.exampleTranslation);
        examples.forEach((example, index) => add(example, translations[index]));
      });
    });
  }

  return items;
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
  anchorAttributes,
  focusable = false,
}: {
  children: ReactNode;
  className: string;
  base: LookupBase;
  activeLookup: DictionaryLookupSnapshot | null;
  previewOpen: boolean;
  onLookup: (snapshot: LookupBase, options?: { anchorRect?: DOMRect | null }) => void;
  onDismiss: () => void;
  onReveal: () => void;
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
    onLookup(base, { anchorRect: target ? copyDomRect(target.getBoundingClientRect()) : null });
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
  onLookup: (snapshot: LookupBase, options?: { anchorRect?: DOMRect | null }) => void;
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
  saveState,
  searchQuery,
  onSave,
  onSearchQueryChange,
  onSearchSubmit,
  onSelectCandidate,
  onDismiss,
  pinned = false,
  onTogglePinned,
  variant = "sheet",
  canSaveVocabulary = true,
}: {
  lookup: DictionaryLookupSnapshot | null;
  saveState: SaveState;
  searchQuery: string;
  onSave: () => void;
  onSearchQueryChange: (value: string) => void;
  onSearchSubmit: (query: string) => void;
  onSelectCandidate: (entryId: number) => void;
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
  const glossaryTitle = lookup ? contextualGlossaryTitle(lookup) : null;
  const glossaryText = contextualGlossaryText(lookup?.glossary);
  const isCard = variant === "card";
  const examples = entryResult ? dictionaryExampleItems(entryResult.entry) : [];
  const panelSizing = isCard
    ? "h-full min-h-[22rem]"
    : lookup
      ? onDismiss
        ? "h-full min-h-[18rem]"
        : "min-h-[18rem] xl:max-h-[calc(100vh-1.5rem)]"
      : onDismiss
        ? "h-full min-h-[13.5rem]"
        : "min-h-[13.5rem]";
  const contentClass =
    isCard && entryResult
      ? "min-h-0 flex-1 overflow-hidden px-5 py-4"
      : "min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 py-4";
  const saveDisabled =
    saveState.kind === "saving" || saveState.kind === "saved" || !canSaveVocabulary;

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
      <div className={`border-b border-hairline px-5 ${isCard ? "py-3" : "py-4"}`}>
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-ink">词典</h2>
            <p className="mt-1 text-xs leading-5 text-muted">
              {isCard ? "完整词条与当前语境" : "完整词条"}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {onTogglePinned ? (
              <button
                type="button"
                className={`focus-ring inline-flex h-9 w-9 items-center justify-center rounded-full border transition-colors ${
                  pinned
                    ? "border-lens-blue/25 bg-lens-blue-soft text-lens-blue"
                    : "border-hairline bg-surface text-muted hover:border-muted hover:text-ink"
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
                className="focus-ring inline-flex h-9 w-9 items-center justify-center rounded-full border border-hairline bg-surface text-muted transition-colors hover:border-muted hover:text-ink"
                onClick={onDismiss}
                aria-label="收起词典"
              >
                <ChevronRight aria-hidden="true" className="h-4 w-4" />
              </button>
            ) : null}
          </div>
        </div>
      </div>

      <form className="border-b border-hairline px-5 py-3" onSubmit={handleSearchSubmit}>
        <div className="flex items-center gap-2 rounded-[12px] border border-hairline bg-reader-paper px-3 py-2 transition-colors focus-within:border-lens-blue/35 focus-within:bg-surface">
          <Search aria-hidden="true" className="h-4 w-4 shrink-0 text-muted" />
          <input
            className="min-w-0 flex-1 bg-transparent text-sm leading-6 text-ink outline-none placeholder:text-subtle"
            value={searchQuery}
            onChange={(event) => onSearchQueryChange(event.target.value)}
            placeholder="输入单词或短语"
            aria-label="输入单词或短语"
          />
          <button
            type="submit"
            className="focus-ring rounded-pill bg-ink px-3 py-1.5 text-xs font-semibold text-surface transition-colors hover:bg-ink-soft"
          >
            查询
          </button>
        </div>
      </form>

      <div className={contentClass}>
        {!lookup ? (
          <div className="flex min-h-32 flex-col justify-center">
            <Search aria-hidden="true" className="mb-4 h-5 w-5 text-lens-blue" />
            <p className="text-sm font-semibold text-ink">从原文或搜索框开始查词</p>
            <p className="mt-2 text-sm leading-6 text-muted">
              点击正文保留语境，手动输入适合快速查一个独立词条。
            </p>
          </div>
        ) : null}

        {lookup && glossaryTitle && glossaryText && (!entryResult || !isCard) ? (
          <div className="mb-5 rounded-[10px] bg-lens-blue-soft/70 px-3 py-3 ring-1 ring-lens-blue/15">
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs font-semibold text-lens-blue">{glossaryTitle}</p>
              {lookup.label && lookup.label !== glossaryTitle ? (
                <span className="text-[0.7rem] font-semibold text-muted">{lookup.label}</span>
              ) : null}
            </div>
            <p className="mt-2 text-sm leading-6 text-ink">{glossaryText}</p>
            {lookup.glossary?.reason ? (
              <p className="mt-2 text-xs leading-5 text-muted">{lookup.glossary.reason}</p>
            ) : null}
          </div>
        ) : null}

        {lookup?.state.kind === "loading" ? (
          <div className="space-y-4">
            <div>
              <p className="text-xs font-semibold text-muted">正在查询</p>
              <h3 className="mt-2 reader-serif text-3xl text-ink">{lookup.query}</h3>
            </div>
            <div className="space-y-2">
              <div className="h-3 w-2/3 rounded-full bg-reader-paper" />
              <div className="h-3 w-5/6 rounded-full bg-reader-paper" />
              <div className="h-3 w-1/2 rounded-full bg-reader-paper" />
            </div>
          </div>
        ) : null}

        {lookup?.state.kind === "error" ? (
          <div>
            <p className="text-xs font-semibold text-muted">查询失败</p>
            <h3 className="mt-2 reader-serif text-3xl text-ink">{lookup.query}</h3>
            <p className="mt-3 text-sm leading-6 text-error-red">{lookup.state.message}</p>
          </div>
        ) : null}

        {lookup && entryResult ? (
          <div
            className={
              isCard
                ? "grid h-full min-h-0 gap-5 lg:grid-cols-[minmax(12.5rem,0.68fr)_minmax(0,1.32fr)]"
                : "space-y-5"
            }
          >
            <div className={isCard ? "flex min-h-0 flex-col gap-4" : "space-y-5"}>
              <div>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3
                      className={`reader-serif font-semibold leading-tight text-ink ${
                        isCard ? "text-[1.9rem]" : "text-[2.05rem]"
                      }`}
                    >
                      {entryResult.entry.word}
                    </h3>
                    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
                      {entryResult.entry.phonetic ? <span>{entryResult.entry.phonetic}</span> : null}
                      {entryResult.entry.baseWord && entryResult.entry.baseWord !== entryResult.entry.word ? (
                        <span>原形 {entryResult.entry.baseWord}</span>
                      ) : null}
                      {entryResult.entry.homographNo ? <span>义项 {entryResult.entry.homographNo}</span> : null}
                      <span>{entryResult.provider}</span>
                      <span>{entryResult.cached ? "缓存" : "实时查询"}</span>
                    </div>
                  </div>
                  <Volume2 aria-hidden="true" className="mt-1 h-4 w-4 shrink-0 text-muted" />
                </div>
                {entryResult.entry.tags.length > 0 ? (
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {entryResult.entry.tags.slice(0, 8).map((tag) => (
                      <span
                        key={tag}
                        className="rounded-pill border border-hairline bg-reader-paper px-2 py-1 text-[0.7rem] font-semibold uppercase text-muted"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                ) : null}
                {entryResult.entry.exchange.length > 0 ? (
                  <div className="mt-3">
                    <p className="text-xs font-semibold text-muted">词形</p>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {entryResult.entry.exchange.slice(0, 8).map((form) => (
                        <span
                          key={form}
                          className="rounded-pill bg-surface px-2 py-1 text-xs font-semibold text-ink-soft ring-1 ring-hairline"
                        >
                          {form}
                        </span>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>

              {glossaryTitle && glossaryText ? (
                <div
                  className={
                    isCard
                      ? "rounded-[14px] bg-lens-blue-soft/70 px-4 py-3 ring-1 ring-lens-blue/15"
                      : "rounded-[10px] bg-lens-blue-soft/70 px-3 py-3 ring-1 ring-lens-blue/15"
                  }
                >
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-xs font-semibold text-lens-blue">{glossaryTitle}</p>
                    {lookup.label && lookup.label !== glossaryTitle ? (
                      <span className="text-[0.7rem] font-semibold text-muted">{lookup.label}</span>
                    ) : null}
                  </div>
                  <p className="mt-2 text-sm leading-6 text-ink">{glossaryText}</p>
                  {lookup.glossary?.reason ? (
                    <p className="mt-2 text-xs leading-5 text-muted">{lookup.glossary.reason}</p>
                  ) : null}
                </div>
              ) : null}

              <div className="border-t border-hairline pt-4">
                <button
                  type="button"
                  className="focus-ring inline-flex min-h-10 w-full items-center justify-center gap-2 rounded-[10px] bg-ink px-4 text-sm font-semibold text-surface transition-colors hover:bg-ink-soft disabled:cursor-not-allowed disabled:opacity-60"
                  onClick={onSave}
                  disabled={saveDisabled}
                  title={!canSaveVocabulary ? "手动查词需要先选中正文句子，才能保存到生词本。" : undefined}
                >
                  {!canSaveVocabulary
                    ? "选中句子后保存"
                    : saveState.kind === "saving"
                      ? "写入中"
                      : "加入生词本"}
                </button>
                {saveState.kind === "saved" ? (
                  <span className="mt-2 inline-flex items-center gap-1 text-xs font-semibold text-structure-green">
                    <Check aria-hidden="true" className="h-3.5 w-3.5" />
                    {saveState.message}
                  </span>
                ) : null}
                {saveState.kind === "error" ? (
                  <span className="mt-2 block text-xs font-semibold text-error-red">{saveState.message}</span>
                ) : null}
              </div>
            </div>

            <div className={isCard ? "min-h-0 overflow-y-auto overscroll-contain pr-2" : "space-y-5"}>
              <div className={isCard ? "" : "border-t border-hairline py-4"}>
                <div className="flex items-center justify-between gap-3">
                  <p className="text-sm font-semibold text-ink">详细释义</p>
                  <span className="text-xs font-semibold text-muted">
                    {entryResult.entry.meanings.reduce((count, meaning) => count + meaning.definitions.length, 0)} 条
                  </span>
                </div>
                <div className="mt-3 space-y-5">
                  {entryResult.entry.meanings.map((meaning) => (
                    <section key={`${entryResult.entry.id}-${meaning.partOfSpeech}`} className="border-t border-hairline pt-4 first:border-t-0 first:pt-0">
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-xs font-semibold text-structure-green">{meaning.partOfSpeech}</p>
                        <span className="text-[0.7rem] font-semibold text-subtle">
                          {meaning.definitions.length}
                        </span>
                      </div>
                      <ol className="mt-2 space-y-2.5">
                        {meaning.definitions.map((definition, index) => (
                          <li
                            key={`${entryResult.entry.id}-${meaning.partOfSpeech}-${definition.meaning}`}
                            className="grid grid-cols-[1.65rem_1fr] gap-2 text-sm leading-6 text-ink-soft"
                          >
                            <span className="pt-0.5 text-xs font-semibold text-subtle">{index + 1}</span>
                            <span>{definition.meaning}</span>
                          </li>
                        ))}
                      </ol>
                    </section>
                  ))}
                </div>
              </div>

              {entryResult.entry.phrases.length > 0 || examples.length > 0 ? (
                <div className="mt-5 space-y-5">
                  {entryResult.entry.phrases.length > 0 ? (
                    <div className="border-t border-hairline pt-4">
                      <p className="text-sm font-semibold text-ink">短语与搭配</p>
                      <div className="mt-2 space-y-2">
                        {entryResult.entry.phrases.map((phrase) => (
                          <p key={phrase.phrase} className="text-sm leading-6 text-ink-soft">
                            <span className="font-semibold text-ink">{phrase.phrase}</span>
                            {phrase.meaning ? <span className="ml-2 text-muted">{phrase.meaning}</span> : null}
                          </p>
                        ))}
                      </div>
                    </div>
                  ) : null}
                  {examples.length > 0 ? (
                    <div className="border-t border-hairline pt-4">
                      <p className="text-sm font-semibold text-ink">例句</p>
                      <div className="mt-2 space-y-3">
                        {examples.map((example) => (
                          <figure key={example.example}>
                            <blockquote className="reader-serif text-sm leading-6 text-ink-soft">
                              {example.example}
                            </blockquote>
                            {example.exampleTranslation ? (
                              <figcaption className="mt-1 text-xs leading-5 text-muted">
                                {example.exampleTranslation}
                              </figcaption>
                            ) : null}
                          </figure>
                        ))}
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          </div>
        ) : null}

        {lookup && disambiguationResult ? (
          <div>
            <p className="text-xs font-semibold text-muted">需要选择词条</p>
            <h3 className="mt-2 reader-serif text-3xl text-ink">{lookup.query}</h3>
            <div className="mt-4 divide-y divide-hairline border-y border-hairline">
              {disambiguationResult.candidates.slice(0, 6).map((candidate) => (
                <button
                  key={candidate.entryId}
                  type="button"
                  className="focus-ring block w-full py-3 text-left transition-colors hover:bg-reader-paper"
                  onClick={() => onSelectCandidate(candidate.entryId)}
                >
                  <p className="text-sm font-semibold text-ink">{candidate.label}</p>
                  {candidate.preview ? <p className="mt-1 text-sm leading-6 text-muted">{candidate.preview}</p> : null}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        {lookup && notFoundResult ? (
          <div>
            <p className="text-xs font-semibold text-muted">未收录</p>
            <h3 className="mt-2 reader-serif text-3xl text-ink">{lookup.query}</h3>
            <p className="mt-3 text-sm leading-6 text-muted">当前词典暂未收录这个词条。</p>
          </div>
        ) : null}

        {lookup && errorResult ? (
          <div>
            <p className="text-xs font-semibold text-muted">查询失败</p>
            <h3 className="mt-2 reader-serif text-3xl text-ink">{lookup.query}</h3>
            <p className="mt-3 text-sm leading-6 text-error-red">{errorResult.message}</p>
          </div>
        ) : null}

      </div>
    </section>
  );
}

function LookupTrail({
  history,
  activeLookup,
  onSelect,
}: {
  history: DictionaryLookupSnapshot[];
  activeLookup: DictionaryLookupSnapshot | null;
  onSelect: (lookup: DictionaryLookupSnapshot) => void;
}) {
  if (history.length < 2) {
    return null;
  }

  return (
    <nav
      className="reader-tool-panel reader-lookup-trail px-4 py-3"
      aria-label="本次查词轨迹"
    >
      <div className="mb-2 flex items-center justify-between gap-3">
        <p className="text-xs font-semibold text-muted">查询轨迹</p>
        <span className="text-[0.7rem] font-semibold text-subtle">{history.length}</span>
      </div>
      <div className="flex gap-2 overflow-x-auto pb-1">
        {history.slice(0, 8).map((item) => {
          const active =
            activeLookup?.query.toLowerCase() === item.query.toLowerCase() &&
            activeLookup?.sentenceId === item.sentenceId;
          return (
            <button
              key={`${item.query}-${item.sentenceId}-${item.anchorText}`}
              type="button"
              className={`focus-ring shrink-0 rounded-pill border px-3 py-1.5 text-xs font-semibold transition-colors ${
                active
                  ? "border-lens-blue/30 bg-lens-blue-soft text-lens-blue"
                  : "border-hairline bg-reader-paper text-ink-soft hover:border-muted hover:text-ink"
              }`}
              onClick={() => onSelect(item)}
            >
              {item.query}
            </button>
          );
        })}
      </div>
    </nav>
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
  const [lookupPreviewOpen, setLookupPreviewOpen] = useState(false);
  const [lookupPreviewAnchorRect, setLookupPreviewAnchorRect] = useState<DOMRect | null>(null);
  const [lookupHistory, setLookupHistory] = useState<DictionaryLookupSnapshot[]>([]);
  const [dictionarySaveState, setDictionarySaveState] = useState<SaveState>({ kind: "idle" });
  const [annotations, setAnnotations] = useState(initialAnnotations);
  const [favoriteTargets, setFavoriteTargets] = useState(initialFavoriteTargets);
  const [routeFocus, setRouteFocus] = useState<RouteFocusState | null>(null);
  const [activeSentence, setActiveSentence] = useState<SentenceModel | null>(null);
  const [textSelection, setTextSelection] = useState<ReaderTextSelection | null>(null);
  const [lowerPanelMode, setLowerPanelMode] = useState<LowerPanelMode>("sentence");
  const [contextPanelOpen, setContextPanelOpen] = useState(false);
  const [expandedSentenceId, setExpandedSentenceId] = useState<string | null>(null);
  const [activeEntryId, setActiveEntryId] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [selectionNoteOpen, setSelectionNoteOpen] = useState(false);
  const [selectionNoteDraft, setSelectionNoteDraft] = useState("");
  const [selectionFavorited, setSelectionFavorited] = useState(false);
  const [selectionFavoriteLoading, setSelectionFavoriteLoading] = useState(false);
  const [annotationColor, setAnnotationColor] = useState<UserAnnotationColorDto>("warm_yellow");
  const [annotationSaveState, setAnnotationSaveState] = useState<AnnotationSaveState>({ kind: "idle" });
  const [showTranslation, setShowTranslation] = useState(true);
  const [fontSize, setFontSize] = useState<ReaderFontSize>("normal");
  const [density, setDensity] = useState<ReadingDensity>("calm");
  const [theme, setTheme] = useState<ReaderTheme>("paper");
  const [markVisibility, setMarkVisibility] = useState<MarkVisibility>("quiet");
  const [aiOpen, setAiOpen] = useState(false);
  const [dictionaryPinned, setDictionaryPinned] = useState(false);
  const [dictionaryQuery, setDictionaryQuery] = useState("");
  const articleRef = useRef<HTMLElement | null>(null);
  const focusedRouteTargetKeyRef = useRef<string | null>(null);

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
    open: Boolean(activeLookup && lookupPreviewOpen && lookupPreviewAnchorRect),
    placement: "top",
    offsetPx: 12,
    strategy: "fixed",
  });

  const translationBySentence = useMemo(
    () => new Map(reader.translations.map((item) => [item.sentenceId, item.translationZh])),
    [reader.translations],
  );

  const sentenceById = useMemo(
    () => new Map(reader.article.sentences.map((sentence) => [sentence.sentenceId, sentence])),
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

  const annotationsBySentence = useMemo(() => {
    const map = new Map<string, WebAnnotationVm[]>();
    annotations
      .filter((item) => item.recordId === record.id || item.recordId === null)
      .forEach((item) => {
        if (item.anchorType === "multi_text" && item.segments.length > 0) {
          item.segments.forEach((segment) => {
            const current = map.get(segment.sentenceId) ?? [];
            map.set(segment.sentenceId, [...current, item]);
          });
          return;
        }
        if (!item.sentenceId) {
          return;
        }
        const current = map.get(item.sentenceId) ?? [];
        map.set(item.sentenceId, [...current, item]);
      });
    return map;
  }, [annotations, record.id]);

  const favoriteTargetsBySentence = useMemo(() => {
    const map = new Map<string, WebFavoriteTargetVm[]>();
    favoriteTargets
      .filter((item) => item.recordId === record.id || item.recordId === null)
      .forEach((item) => {
        if (item.anchorType === "multi_text" && item.segments.length > 0) {
          item.segments.forEach((segment) => {
            const current = map.get(segment.sentenceId) ?? [];
            map.set(segment.sentenceId, [...current, item]);
          });
          return;
        }
        if (!item.sentenceId) {
          return;
        }
        const current = map.get(item.sentenceId) ?? [];
        map.set(item.sentenceId, [...current, item]);
      });
    return map;
  }, [favoriteTargets, record.id]);

  const activeSentenceAnnotations = activeSentence
    ? annotationsBySentence.get(activeSentence.sentenceId) ?? []
    : [];
  const routeFocusBySentence = useMemo(() => {
    const map = new Map<string, RouteFocusSegment[]>();
    routeFocus?.segments.forEach((segment) => {
      const current = map.get(segment.sentenceId) ?? [];
      map.set(segment.sentenceId, [...current, segment]);
    });
    return map;
  }, [routeFocus]);
  const routeFocusSentenceIds = useMemo(
    () => new Set(routeFocus?.sentenceIds ?? []),
    [routeFocus],
  );

  const selectedAnnotation = useMemo(() => {
    if (!textSelection) {
      return null;
    }
    return (
      annotations.find(
        (item) =>
          (item.recordId === record.id || item.recordId === null) &&
          annotationMatchesSelection(item, textSelection),
      ) ?? null
    );
  }, [annotations, record.id, textSelection]);

  useEffect(() => {
    const targetKey = searchParams.get("targetKey");
    if (!targetKey || focusedRouteTargetKeyRef.current === targetKey) {
      return;
    }

    const annotation = annotations.find((item) => item.targetKey === targetKey) ?? null;
    const favorite = favoriteTargets.find((item) => item.targetKey === targetKey) ?? null;
    const target = annotation ?? favorite;
    if (!target) {
      return;
    }
    setRouteFocus(annotation ? null : favorite ? routeFocusStateFromFavoriteTarget(favorite) : null);

    const targetSentenceId =
      target.anchorType === "multi_text"
        ? target.segments[0]?.sentenceId ?? target.sentenceId
        : target.sentenceId;
    if (!targetSentenceId) {
      focusedRouteTargetKeyRef.current = targetKey;
      return;
    }

    const targetSentence = sentenceById.get(targetSentenceId);
    if (targetSentence) {
      setActiveSentence(targetSentence);
      window.requestAnimationFrame(() => {
        articleRef.current
          ?.querySelector<HTMLElement>(`#reader-sentence-${CSS.escape(targetSentenceId)}`)
          ?.scrollIntoView({ block: "center", behavior: "smooth" });
      });
    }

    focusedRouteTargetKeyRef.current = targetKey;
  }, [annotations, favoriteTargets, searchParams, sentenceById]);

  useEffect(() => {
    if (!routeFocus) {
      return;
    }

    const targetKey = routeFocus.targetKey;
    const timer = window.setTimeout(() => {
      setRouteFocus((current) => (current?.targetKey === targetKey ? null : current));
    }, 4200);
    return () => window.clearTimeout(timer);
  }, [routeFocus]);

  const dismissLookupPreview = useCallback(() => {
    setLookupPreviewOpen(false);
  }, []);

  const revealLookupPreview = useCallback(() => {
    setLookupPreviewOpen(true);
  }, []);

  const clearLookup = useCallback(() => {
    setLookupPreviewOpen(false);
    setLookupPreviewAnchorRect(null);
    setActiveLookup(null);
  }, []);

  const closeDictionaryPanel = useCallback(() => {
    setDictionaryPinned(false);
    clearLookup();
  }, [clearLookup]);

  useEffect(() => {
    function handleCreated(event: Event) {
      const item = (event as CustomEvent<WebAnnotationVm>).detail;
      if (item.recordId === record.id || item.recordId === null) {
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
    if (!lookupPreviewOpen || !lookupPreviewAnchorRect) {
      setLookupPreviewReference(null);
      return;
    }

    setLookupPreviewReference({
      getBoundingClientRect: () => lookupPreviewAnchorRect,
      contextElement: articleRef.current ?? undefined,
    });
  }, [lookupPreviewAnchorRect, lookupPreviewOpen, setLookupPreviewReference]);

  useEffect(() => {
    if (!textSelection) {
      setSelectionToolbarReference(null);
      return;
    }

    setSelectionToolbarReference({
      getBoundingClientRect: () => {
        if (textSelection.range) {
          const rangeRect = firstUsableRangeRect(textSelection.range);
          if (rangeRect) {
            return rangeRect;
          }
        }

        if (textSelection.anchorType === "sentence") {
          const sentenceElement = articleRef.current?.querySelector<HTMLElement>(
            `[data-reader-anchor="sentence"][data-sentence-id="${CSS.escape(textSelection.sentence.sentenceId)}"] [data-reader-sentence-text="true"]`,
          );
          if (sentenceElement) {
            return copyDomRect(sentenceElement.getBoundingClientRect());
          }
        }

        return textSelection.rect;
      },
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
    async (base: LookupBase, options?: { showPreview?: boolean; anchorRect?: DOMRect | null }) => {
      setLookupPreviewOpen(options?.showPreview ?? true);
      setLookupPreviewAnchorRect(options?.anchorRect ? copyDomRect(options.anchorRect) : null);
      const loadingState = { kind: "loading" } satisfies DictionaryLookupSnapshot["state"];
      handleLookupSnapshot({ ...base, state: loadingState });

      try {
        const params = new URLSearchParams({
          word: base.query,
          type: base.lookupType,
          context: base.contextSentence,
          sentenceId: base.sentenceId,
        });
        if (base.occurrence !== undefined) {
          params.set("occurrence", String(base.occurrence));
        }
        const response = await fetch(`/api/web/dict/lookup?${params.toString()}`);
        const payload = (await response.json()) as WebDictResult;
        handleLookupSnapshot({ ...base, state: { kind: "ready", result: payload } });

        if (!response.ok && payload.kind !== "error") {
          handleLookupSnapshot({
            ...base,
            state: { kind: "error", message: "词典查询失败。" },
          });
        }
      } catch (error) {
        handleLookupSnapshot({
          ...base,
          state: {
            kind: "error",
            message: error instanceof Error ? error.message : "词典查询失败。",
          },
        });
      }
    },
    [handleLookupSnapshot],
  );

  const lookupDictionaryQuery = useCallback((query: string) => {
    const trimmed = query.trim();
    if (!trimmed) {
      return;
    }

    const sentence = activeSentence;
    void lookupPlainText(
      {
        query: trimmed,
        lookupType: trimmed.includes(" ") ? "phrase" : "word",
        contextSentence: sentence?.text ?? "",
        sourceContext: sentence ? translationBySentence.get(sentence.sentenceId) : undefined,
        recordId: record.id,
        sentenceId: sentence?.sentenceId ?? "__manual__",
        anchorText: trimmed,
        title: "手动查词",
        label: sentence ? "当前句查词" : "手动查词",
      },
      { showPreview: false },
    );
  }, [activeSentence, lookupPlainText, record.id, translationBySentence]);

  const selectLookupFromTrail = useCallback((lookup: DictionaryLookupSnapshot) => {
    setActiveLookup(lookup);
    setDictionaryQuery(lookup.query);
    setDictionarySaveState({ kind: "idle" });
    setLookupPreviewAnchorRect(null);
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

  const updateTextSelectionFromDom = useCallback(() => {
    const nextSelection = readReaderSelection(articleRef.current, sentenceById);
    setTextSelection(nextSelection);

    if (nextSelection) {
      setActiveSentence(nextSelection.sentence);
      setLowerPanelMode("sentence");
      setContextPanelOpen(false);
      setAiOpen(false);
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
    const { targetType, targetKey } = favoriteTargetForSelection(record.id, textSelection, selectedAnnotation);

    const params = new URLSearchParams({
      targetType,
      targetKey,
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
  }, [record.id, selectedAnnotation, textSelection]);

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

    const selectedText = targetSelection?.selectedText ?? targetSentence.text;
    const anchorType = targetSelection?.anchorType ?? "sentence";
    const isTextRange = anchorType === "text_range";
    const isMultiText = anchorType === "multi_text";
    const color = options?.color ?? annotationColor;
    const noteText = options?.noteText ?? note;
    const sentenceAnnotations = annotationsBySentence.get(targetSentence.sentenceId) ?? [];
    const existingTargetAnnotation = targetSelection
      ? annotations.find(
          (item) =>
            (item.recordId === record.id || item.recordId === null) &&
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

    const body: WebAnnotationCreateRequest = {
      recordId: record.id,
      paragraphId: targetSentence.paragraphId,
      sentenceId: targetSentence.sentenceId,
      selectedText,
      anchorType,
      startOffset: isTextRange ? targetSelection?.startOffset ?? null : null,
      endOffset: isTextRange ? targetSelection?.endOffset ?? null : null,
      textHash: isTextRange ? targetSelection?.textHash ?? null : null,
      segments: isMultiText
        ? targetSelection?.segments.map((segment) => ({
            paragraphId: segment.paragraphId,
            sentenceId: segment.sentenceId,
            selectedText: segment.selectedText,
            startOffset: segment.startOffset,
            endOffset: segment.endOffset,
            textHash: segment.textHash,
          }))
        : undefined,
      color,
      note: useNote ? noteText.trim() : undefined,
      payloadJson: isTextRange
        ? {
            offset_unit: TEXT_RANGE_OFFSET_UNIT,
            text_hash_algorithm: TEXT_RANGE_HASH_ALGORITHM,
            selected_text_hash: targetSelection?.textHash ?? null,
            sentence_text_hash: hashAnchorText(targetSentence.text),
            prefix: targetSentence.text.slice(0, targetSelection?.startOffset ?? 0).slice(-32),
            suffix: targetSentence.text.slice(targetSelection?.endOffset ?? 0, (targetSelection?.endOffset ?? 0) + 32),
            created_client: "web",
            translation: translationBySentence.get(targetSentence.sentenceId) ?? null,
          }
        : isMultiText
          ? {
              offset_unit: TEXT_RANGE_OFFSET_UNIT,
              text_hash_algorithm: TEXT_RANGE_HASH_ALGORITHM,
              created_client: "web",
              translation: translationBySentence.get(targetSentence.sentenceId) ?? null,
              segments: targetSelection?.segments.map((segment) => ({
                paragraph_id: segment.paragraphId,
                sentence_id: segment.sentenceId,
                selected_text: segment.selectedText,
                start_offset: segment.startOffset,
                end_offset: segment.endOffset,
                text_hash: segment.textHash,
              })),
            }
        : undefined,
    };

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
    setLowerPanelMode("sentence");
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

    const { targetType, targetKey } = favoriteTargetForSelection(record.id, textSelection, selectedAnnotation);
    const targetTypeForState: WebFavoriteTargetVm["targetType"] =
      targetType === "sentence" ? "sentence" : targetType === "multi_text" ? "multi_text" : "text_range";
    setSelectionFavoriteLoading(true);

    try {
      const response = selectionFavorited
        ? await fetch(
            `/api/web/favorites/target?${new URLSearchParams({
              targetType,
              targetKey,
            }).toString()}`,
            { method: "DELETE" },
          )
        : await fetch("/api/web/favorites/target", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              recordId: record.id,
              targetType,
              targetKey,
              payloadJson: {
                source: "web_reader_selection_toolbar",
                anchor_type: textSelection.anchorType,
                paragraph_id: textSelection.sentence.paragraphId,
                sentence_id: textSelection.sentence.sentenceId,
                selected_text: textSelection.selectedText,
                start_offset: textSelection.anchorType === "text_range" ? textSelection.startOffset : null,
                end_offset: textSelection.anchorType === "text_range" ? textSelection.endOffset : null,
                text_hash: textSelection.anchorType === "text_range" ? textSelection.textHash : null,
                segments:
                  textSelection.anchorType === "multi_text"
                    ? textSelection.segments.map((segment) => ({
                        paragraph_id: segment.paragraphId,
                        sentence_id: segment.sentenceId,
                        selected_text: segment.selectedText,
                        start_offset: segment.startOffset,
                        end_offset: segment.endOffset,
                        text_hash: segment.textHash,
                      }))
                    : undefined,
                offset_unit: TEXT_RANGE_OFFSET_UNIT,
                text_hash_algorithm: TEXT_RANGE_HASH_ALGORITHM,
                translation: translationBySentence.get(textSelection.sentence.sentenceId) ?? null,
              },
            }),
          });
      const payload = (await response.json()) as { ok: boolean; favorited?: boolean; message?: string };

      if (!response.ok || !payload.ok) {
        setAnnotationSaveState({ kind: "error", message: payload.message ?? "收藏操作失败。" });
        return;
      }

      setSelectionFavorited(Boolean(payload.favorited));
      setFavoriteTargets((current) => {
        const withoutTarget = current.filter(
          (item) => !(item.targetType === targetType && item.targetKey === targetKey),
        );
        if (!payload.favorited) {
          return withoutTarget;
        }
        return [
          {
            id: targetKey,
            targetType: targetTypeForState,
            targetKey,
            recordId: record.id,
            anchorType: textSelection.anchorType,
            sentenceId: textSelection.sentence.sentenceId,
            selectedText: textSelection.selectedText,
            startOffset: textSelection.anchorType === "text_range" ? textSelection.startOffset : null,
            endOffset: textSelection.anchorType === "text_range" ? textSelection.endOffset : null,
            textHash: textSelection.anchorType === "text_range" ? textSelection.textHash : null,
            segments:
              textSelection.anchorType === "multi_text"
                ? textSelection.segments.map((segment) => ({
                    paragraphId: segment.paragraphId,
                    sentenceId: segment.sentenceId,
                    selectedText: segment.selectedText,
                    startOffset: segment.startOffset,
                    endOffset: segment.endOffset,
                    textHash: segment.textHash,
                  }))
                : [],
          },
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
    setLowerPanelMode("sentence");
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

    const query = textSelection.selectedText.trim();
    if (!query) {
      return;
    }

    setActiveSentence(textSelection.sentence);
    void lookupPlainText(
      {
        query,
        lookupType: /\s/.test(query) ? "phrase" : "word",
        contextSentence: textSelection.sentence.text,
        sourceContext: translationBySentence.get(textSelection.sentence.sentenceId),
        recordId: record.id,
        sentenceId: textSelection.sentence.sentenceId,
        anchorText: query,
        title: "选区查词",
        label: "选区查词",
      },
      { showPreview: false },
    );
  }

  function showSelectionActionPending(message: string) {
    if (textSelection) {
      setActiveSentence(textSelection.sentence);
    }
    setLowerPanelMode("sentence");
    setContextPanelOpen(false);
    setAnnotationSaveState({ kind: "error", message });
  }

  function selectSentence(sentence: SentenceModel) {
    setActiveSentence(sentence);
    setTextSelection(null);
    setLowerPanelMode("sentence");
    setContextPanelOpen(true);
    setExpandedSentenceId(null);
    setActiveEntryId(null);
    const sentenceAnnotation =
      (annotationsBySentence.get(sentence.sentenceId) ?? []).find((item) => item.anchorType === "sentence") ?? null;
    setNote(sentenceAnnotation?.note ?? "");
    setAnnotationColor(sentenceAnnotation?.color ?? "warm_yellow");
    setAnnotationSaveState({ kind: "idle" });
  }

  function openSettingsPanel() {
    setAiOpen(false);
    setLowerPanelMode("settings");
    setContextPanelOpen(true);
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
    if (lowerPanelMode === "sentence") {
      setActiveSentence(null);
      setExpandedSentenceId(null);
      setActiveEntryId(null);
      setTextSelection(null);
    }
  }

  const canvasThemeClass =
    theme === "white"
      ? "bg-surface"
      : theme === "green"
        ? "bg-[#F3F7EF]"
        : "reading-paper";
  const readingClass =
    `reader-serif text-ink ${
      fontSize === "compact"
        ? "text-[1.04rem] sm:text-[1.16rem]"
        : fontSize === "large"
          ? "text-[1.24rem] sm:text-[1.42rem]"
          : "text-[1.12rem] sm:text-[1.28rem]"
    } ${density === "roomy" ? "leading-[2.08]" : "leading-[1.88]"}`;
  const contextPanelVisible = Boolean(
    contextPanelOpen && (lowerPanelMode === "settings" || activeSentence),
  );
  const dictionaryPanelVisible = Boolean(activeLookup || dictionaryPinned);

  return (
    <main className="paper-grain min-h-screen px-3 pb-24 pt-3 text-ink sm:px-4 md:pb-6 lg:px-5">
      <div className="relative">
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
            <div className="mx-auto flex max-w-[96ch] flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
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
              <div className="flex shrink-0 flex-wrap items-start gap-2">
                <FavoriteButton recordId={record.id} />
                <button
                  type="button"
                  className={`focus-ring inline-flex h-10 items-center rounded-pill border px-3.5 text-xs font-semibold transition-colors ${
                    showTranslation
                      ? "border-lens-blue/30 bg-lens-blue-soft text-lens-blue"
                      : "border-hairline bg-surface-warm text-muted hover:border-muted hover:text-ink"
                  }`}
                  onClick={() => setShowTranslation((value) => !value)}
                >
                  {showTranslation ? "原文+译文" : "原文"}
                </button>
                <button
                  type="button"
                  className="focus-ring inline-flex h-10 items-center gap-1 rounded-pill border border-hairline bg-surface-warm px-3.5 text-xs font-semibold text-ink-soft transition-colors hover:border-muted hover:text-ink"
                  onClick={openSettingsPanel}
                >
                  <Type aria-hidden="true" className="h-3.5 w-3.5" />
                  Aa
                </button>
                <button
                  type="button"
                  className="focus-ring inline-flex h-10 w-10 items-center justify-center rounded-pill border border-hairline bg-surface-warm text-muted transition-colors hover:border-muted hover:text-ink"
                  aria-label="更多阅读操作"
                >
                  <MoreHorizontal aria-hidden="true" className="h-4 w-4" />
                </button>
              </div>
            </div>

            {message ? (
              <div className="mx-auto mt-5 max-w-[96ch] rounded-[10px] border border-lens-blue/20 bg-lens-blue-soft px-4 py-3 text-sm leading-6 text-ink-soft">
                {message}
              </div>
            ) : null}
          </header>

          <ReaderCanvas
            reader={reader}
            renderSentence={(sentence, sentenceIndex) => {
              const marks = sentenceMarks(sentence.sentenceId, reader.inlineMarks);
              const entries = entriesBySentence.get(sentence.sentenceId) ?? [];
              const sentenceAnnotations = annotationsBySentence.get(sentence.sentenceId) ?? [];
              const sentenceFavoriteTargets = favoriteTargetsBySentence.get(sentence.sentenceId) ?? [];
              const textRangeAnnotations = projectAnnotationRangesForSentence(sentenceAnnotations, sentence.sentenceId);
              const textRangeHighlightAnnotations = textRangeAnnotations.filter(
                (item) => item.type === "highlight",
              );
              const sentenceLevelAnnotations = sentenceAnnotations.filter(
                (item) => item.anchorType === "sentence",
              );
              const slipAnnotations = sentenceAnnotations.filter(
                (item) => item.anchorType !== "multi_text" || item.sentenceId === sentence.sentenceId,
              );
              const isActive = activeSentence?.sentenceId === sentence.sentenceId;
              const activeSentenceEntry =
                activeEntry?.sentenceId === sentence.sentenceId ? activeEntry : null;
              const highlight = sentenceLevelAnnotations.find((item) => item.type === "highlight");
              const routeFocusRanges = routeFocusBySentence.get(sentence.sentenceId) ?? [];
              const hasRouteFocus =
                routeFocusSentenceIds.has(sentence.sentenceId) || routeFocusRanges.length > 0;
              const sentenceFrameClass = [
                isActive ? "rounded-[8px] bg-surface/42" : "rounded-[8px] hover:bg-surface/28",
                hasRouteFocus ? "reader-route-focus-frame" : "",
              ]
                .filter(Boolean)
                .join(" ");
              const textHighlightClass = highlight
                ? `reader-user-highlight ${sentenceHighlightToneClass[highlight.color]}`
                : "";
              const entryActiveClass =
                activeSentenceEntry?.entryType === "sentence_analysis"
                  ? "reader-sentence-entry-active reader-sentence-entry-active--analysis"
                  : activeSentenceEntry?.entryType === "grammar_note"
                    ? "reader-sentence-entry-active reader-sentence-entry-active--grammar"
                    : "";
              const routeFocusSentenceClass = routeFocusSentenceIds.has(sentence.sentenceId)
                ? "reader-route-focus-sentence"
                : "";

              return (
                <ReaderSentenceRow
                  key={sentence.sentenceId}
                  sentence={sentence}
                  activeIndex={isActive ? sentenceIndex + 1 : null}
                  annotations={sentenceAnnotations}
                  slipAnnotations={slipAnnotations}
                  favoriteTargets={sentenceFavoriteTargets}
                  frameClassName={sentenceFrameClass}
                  readingClassName={readingClass}
                  textClassName={`${textHighlightClass} ${entryActiveClass} ${routeFocusSentenceClass}`.trim()}
                  translation={translationBySentence.get(sentence.sentenceId)}
                  showTranslation={showTranslation}
                  sentenceText={renderSentenceText({
                    sentence,
                    marks,
                    recordId: record.id,
                    sourceContext: translationBySentence.get(sentence.sentenceId),
                    markVisibility,
                    userTextRangeAnnotations: textRangeHighlightAnnotations,
                    activeEntry: activeSentenceEntry,
                    activeLookup,
                    lookupPreviewOpen,
                    onLookup: lookupPlainText,
                    onDismissLookup: dismissLookupPreview,
                    onRevealLookup: revealLookupPreview,
                    routeFocusRanges,
                  })}
                  entryControls={
                    expandedSentenceId === sentence.sentenceId ? (
                      entries.map((entry) => (
                        <SentenceEntryCard
                          key={entry.id}
                          entry={entry}
                          active={activeEntryId === entry.id}
                          onActivate={activateEntry}
                        />
                      ))
                    ) : (
                      <SentenceEntrySummary
                        entries={entries}
                        onOpen={(entry) => openEntry(sentence, entry)}
                      />
                    )
                  }
                  onSelectSentence={selectSentence}
                  onSentenceTextClick={(event, targetSentence) => {
                    const target = event.target instanceof HTMLElement ? event.target : null;
                    if (target?.closest("button")) {
                      return;
                    }

                    const currentSelection = window.getSelection();
                    if (currentSelection && !currentSelection.isCollapsed) {
                      return;
                    }

                    const lookupBase = lookupBaseFromSentencePoint({
                      element: event.currentTarget,
                      sentence: targetSentence,
                      recordId: record.id,
                      sourceContext: translationBySentence.get(targetSentence.sentenceId),
                      clientX: event.clientX,
                      clientY: event.clientY,
                    });
                    if (!lookupBase) {
                      return;
                    }

                    event.stopPropagation();
                    void lookupPlainText(lookupBase.base, { anchorRect: lookupBase.anchorRect });
                  }}
                />
              );
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
              onFeedback={() => showSelectionActionPending("选区反馈稍后接入；当前可先用笔记记录问题。")}
              onMore={() => showSelectionActionPending("更多选区操作稍后接入。")}
            />
          </div>
        ) : null}
        {lookupPreviewOpen && activeLookup && lookupPreviewAnchorRect ? (
          <InlineLookupPreview
            lookup={activeLookup}
            floatingRef={setLookupPreviewFloating}
            style={lookupPreviewStyles}
            onDismiss={dismissLookupPreview}
          />
        ) : null}

      </div>

      {dictionaryPanelVisible ? (
        <div className="fixed left-[calc(84px+5rem)] top-[clamp(7rem,13vh,9rem)] z-40 hidden w-[clamp(27rem,calc((100vw-84px-7rem-96ch)/2+3rem),32.5rem)] flex-col gap-3 2xl:flex">
          <div className="h-[min(54vh,36rem)] overflow-hidden rounded-panel">
            <DictionaryDetailPanel
              lookup={activeLookup}
              saveState={dictionarySaveState}
              searchQuery={dictionaryQuery}
              onSave={saveVocabularyFromDictionary}
              onSearchQueryChange={setDictionaryQuery}
              onSearchSubmit={lookupDictionaryQuery}
              onSelectCandidate={selectDictionaryCandidate}
              onDismiss={closeDictionaryPanel}
              pinned={dictionaryPinned}
              onTogglePinned={() => setDictionaryPinned((value) => !value)}
              variant="card"
              canSaveVocabulary={Boolean(activeLookup?.contextSentence.trim())}
            />
          </div>
          <LookupTrail
            history={lookupHistory}
            activeLookup={activeLookup}
            onSelect={selectLookupFromTrail}
          />
        </div>
      ) : null}

      {activeLookup && !aiOpen && !contextPanelVisible ? (
        <div className="fixed inset-x-3 bottom-[5.25rem] z-50 flex max-h-[62vh] flex-col gap-2 md:bottom-6 2xl:hidden">
          <div className="h-[min(44vh,29rem)] overflow-hidden rounded-panel md:h-[min(48vh,31rem)]">
            <DictionaryDetailPanel
              lookup={activeLookup}
              saveState={dictionarySaveState}
              searchQuery={dictionaryQuery}
              onSave={saveVocabularyFromDictionary}
              onSearchQueryChange={setDictionaryQuery}
              onSearchSubmit={lookupDictionaryQuery}
              onSelectCandidate={selectDictionaryCandidate}
              onDismiss={clearLookup}
              canSaveVocabulary={Boolean(activeLookup?.contextSentence.trim())}
            />
          </div>
          <LookupTrail
            history={lookupHistory}
            activeLookup={activeLookup}
            onSelect={selectLookupFromTrail}
          />
        </div>
      ) : null}

      {contextPanelVisible ? (
        <div className="fixed inset-x-3 bottom-[5.25rem] z-50 md:bottom-6 md:left-1/2 md:right-auto md:w-[min(460px,calc(100vw-8rem))] md:-translate-x-1/2">
          <ReaderContextPanel
            mode={lowerPanelMode}
            sentence={activeSentence}
            selectedText={textSelection?.selectedText ?? null}
            annotationScope={textSelection ? "text_range" : "sentence"}
            note={note}
            color={annotationColor}
            saveState={annotationSaveState}
            sentenceAnnotations={activeSentenceAnnotations}
            showTranslation={showTranslation}
            fontSize={fontSize}
            density={density}
            theme={theme}
            markVisibility={markVisibility}
            onModeChange={(mode) => {
              if (mode === "sentence" && !activeSentence) {
                return;
              }
              setLowerPanelMode(mode);
            }}
            onNoteChange={setNote}
            onColorChange={setAnnotationColor}
            onSaveAnnotation={saveSentenceAnnotation}
            onAsk={() => {
              setAiOpen(true);
              setContextPanelOpen(false);
            }}
            onShowTranslationChange={setShowTranslation}
            onFontSizeChange={setFontSize}
            onDensityChange={setDensity}
            onThemeChange={setTheme}
            onMarkVisibilityChange={setMarkVisibility}
            onClose={closeContextPanel}
          />
        </div>
      ) : null}

      {!contextPanelVisible || aiOpen ? (
        <AiWorkspacePanel
          open={aiOpen}
          activeSentence={activeSentence}
          hideLauncherOnMobile={Boolean(activeLookup)}
          hideLauncherInCompactLayout={Boolean(activeLookup)}
          onToggle={toggleAiWorkspace}
        />
      ) : null}
    </main>
  );
}
