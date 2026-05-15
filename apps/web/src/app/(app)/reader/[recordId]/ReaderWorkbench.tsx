"use client";

import type { CSSProperties, FormEvent, ReactNode } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { autoUpdate, flip, offset, shift, useFloating } from "@floating-ui/react";
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

import type { ReaderRecordVm } from "@/adapters/records.adapter";
import {
  AiWorkspacePanel,
  AnnotationGutter,
  AnnotationSlip,
  ReaderContextPanel,
  SentenceEntryCard,
} from "@/components/reader";
import type { UserAnnotationColorDto, WebAnnotationCreateRequest, WebAnnotationVm } from "@/types/api/annotations";
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

interface ReaderWorkbenchProps {
  record: ReaderRecordVm;
  dataSource: ReaderDataSource;
  message?: string;
  initialAnnotations: WebAnnotationVm[];
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
  soft_green: "from-structure-green/18",
  soft_blue: "from-context-blue/18",
  soft_purple: "from-phrase-lavender/22",
  warm_yellow: "from-vocab-amber/24",
  sage_green: "from-structure-green/14",
};

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

type LookupBase = Omit<DictionaryLookupSnapshot, "state">;

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
    <span
      ref={floatingRef}
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
    </span>
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
  focusable = false,
}: {
  children: ReactNode;
  className: string;
  base: LookupBase;
  activeLookup: DictionaryLookupSnapshot | null;
  previewOpen: boolean;
  onLookup: (snapshot: LookupBase) => void;
  onDismiss: () => void;
  onReveal: () => void;
  focusable?: boolean;
}) {
  const activeTarget = sameLookupTarget(activeLookup, base);
  const active = previewOpen && activeTarget;
  const {
    refs: { setReference, setFloating },
    floatingStyles,
  } = useFloating({
    open: active,
    placement: "bottom-start",
    middleware: [offset(8), flip({ padding: 16 }), shift({ padding: 16 })],
    whileElementsMounted: autoUpdate,
  });

  return (
    <span className="relative inline">
      <button
        ref={setReference}
        type="button"
        tabIndex={focusable ? 0 : -1}
        className={`reader-lookup-token ${className} ${active ? "reader-mark--active" : ""}`}
        onClick={(event) => {
          event.stopPropagation();
          if (active) {
            onDismiss();
            return;
          }
          if (activeTarget) {
            onReveal();
            return;
          }
          onLookup(base);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape" && active) {
            event.stopPropagation();
            onDismiss();
          }
        }}
        title={base.title}
        aria-label={`查询 ${base.query}`}
        aria-expanded={active}
      >
        {children}
      </button>
      {activeLookup && active ? (
        <InlineLookupPreview
          lookup={activeLookup}
          floatingRef={setFloating}
          style={floatingStyles}
          onDismiss={onDismiss}
        />
      ) : null}
    </span>
  );
}

function renderSentenceText({
  sentence,
  marks,
  recordId,
  sourceContext,
  markVisibility,
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
  activeLookup: DictionaryLookupSnapshot | null;
  lookupPreviewOpen: boolean;
  onLookup: (snapshot: LookupBase) => void;
  onDismissLookup: () => void;
  onRevealLookup: () => void;
}): ReactNode[] {
  const ranges = buildMarkRanges(sentence, marks);
  const occurrenceByStart = buildOccurrenceByStart(sentence.text);

  const nodes: ReactNode[] = [];
  let cursor = 0;

  function renderPlainSegment(text: string, offset: number) {
    return tokenizeText(text).map((token, index) => {
      if (token.type !== "word") {
        return token.text;
      }

      const absoluteStart = offset + token.start;
      const occurrence = occurrenceByStart.get(absoluteStart);
      const base: LookupBase = {
        query: token.text,
        lookupType: "word",
        contextSentence: sentence.text,
        sourceContext,
        recordId,
        sentenceId: sentence.sentenceId,
        anchorText: token.text,
        occurrence,
        title: "查词",
      };

      return (
        <LookupToken
          key={`plain-${offset}-${index}-${token.text}`}
          className="reader-plain-word"
          base={base}
          activeLookup={activeLookup}
          previewOpen={lookupPreviewOpen}
          onLookup={onLookup}
          onDismiss={onDismissLookup}
          onReveal={onRevealLookup}
        >
          {token.text}
        </LookupToken>
      );
    });
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
          className={markClass}
          base={base}
          activeLookup={activeLookup}
          previewOpen={lookupPreviewOpen}
          onLookup={onLookup}
          onDismiss={onDismissLookup}
          onReveal={onRevealLookup}
          focusable
        >
          {range.anchorText}
        </LookupToken>,
      );
    } else {
      nodes.push(
        <span
          key={range.key}
          className={markClass}
          title={markLabel(range.mark)}
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
  onOpen: () => void;
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
            onOpen();
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
}: ReaderWorkbenchProps) {
  const reader = record.reader;
  const [activeLookup, setActiveLookup] = useState<DictionaryLookupSnapshot | null>(null);
  const [lookupPreviewOpen, setLookupPreviewOpen] = useState(false);
  const [lookupHistory, setLookupHistory] = useState<DictionaryLookupSnapshot[]>([]);
  const [dictionarySaveState, setDictionarySaveState] = useState<SaveState>({ kind: "idle" });
  const [annotations, setAnnotations] = useState(initialAnnotations);
  const [activeSentence, setActiveSentence] = useState<SentenceModel | null>(null);
  const [lowerPanelMode, setLowerPanelMode] = useState<LowerPanelMode>("sentence");
  const [contextPanelOpen, setContextPanelOpen] = useState(false);
  const [expandedSentenceId, setExpandedSentenceId] = useState<string | null>(null);
  const [note, setNote] = useState("");
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

  const annotationsBySentence = useMemo(() => {
    const map = new Map<string, WebAnnotationVm[]>();
    annotations
      .filter((item) => item.recordId === record.id || item.recordId === null)
      .forEach((item) => {
        if (!item.sentenceId) {
          return;
        }
        const current = map.get(item.sentenceId) ?? [];
        map.set(item.sentenceId, [...current, item]);
      });
    return map;
  }, [annotations, record.id]);

  const activeSentenceAnnotations = activeSentence
    ? annotationsBySentence.get(activeSentence.sentenceId) ?? []
    : [];

  const dismissLookupPreview = useCallback(() => {
    setLookupPreviewOpen(false);
  }, []);

  const revealLookupPreview = useCallback(() => {
    setLookupPreviewOpen(true);
  }, []);

  const clearLookup = useCallback(() => {
    setLookupPreviewOpen(false);
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

  const lookupPlainText = useCallback(async (base: LookupBase, options?: { showPreview?: boolean }) => {
    setLookupPreviewOpen(options?.showPreview ?? true);
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
  }, [handleLookupSnapshot]);

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

  async function saveSentenceAnnotation(useNote: boolean) {
    if (!activeSentence) {
      setAnnotationSaveState({ kind: "error", message: "请先选择一个句子。" });
      return;
    }

    setAnnotationSaveState({ kind: "saving" });

    const body: WebAnnotationCreateRequest = {
      recordId: record.id,
      paragraphId: activeSentence.paragraphId,
      sentenceId: activeSentence.sentenceId,
      selectedText: activeSentence.text,
      color: annotationColor,
      note: useNote ? note.trim() : undefined,
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

      setAnnotations((current) => [
        payload.item,
        ...current.filter((existing) => existing.id !== payload.item.id),
      ]);
      window.dispatchEvent(
        new CustomEvent<WebAnnotationVm>(ANNOTATION_CREATED_EVENT, { detail: payload.item }),
      );
      setNote("");
      setAnnotationSaveState({ kind: "saved", message: useNote ? "笔记已保存。" : "高亮已保存。" });
    } catch (error) {
      setAnnotationSaveState({
        kind: "error",
        message: error instanceof Error ? error.message : "批注保存失败。",
      });
    }
  }

  function selectSentence(sentence: SentenceModel) {
    setActiveSentence(sentence);
    setLowerPanelMode("sentence");
    setContextPanelOpen(true);
    setExpandedSentenceId(null);
    setNote("");
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
          className={`min-w-0 overflow-visible rounded-panel border border-hairline shadow-surface-quiet ${canvasThemeClass}`}
          onClick={lookupPreviewOpen ? dismissLookupPreview : undefined}
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
                    if (!sentence) {
                      return null;
                    }

                    const marks = sentenceMarks(sentence.sentenceId, reader.inlineMarks);
                    const entries = entriesBySentence.get(sentence.sentenceId) ?? [];
                    const sentenceAnnotations = annotationsBySentence.get(sentence.sentenceId) ?? [];
                    const isActive = activeSentence?.sentenceId === sentence.sentenceId;
                    const sentenceHasActiveLookup =
                      lookupPreviewOpen && activeLookup?.sentenceId === sentence.sentenceId;
                    const highlight =
                      sentenceAnnotations.find((item) => item.type === "highlight") ?? sentenceAnnotations.at(0);
                    const sentenceFrameClass = isActive
                      ? "rounded-[8px] bg-surface/42"
                      : "rounded-[8px] hover:bg-surface/28";
                    const textHighlightClass = highlight
                      ? `box-decoration-clone bg-gradient-to-t ${sentenceHighlightToneClass[highlight.color]} from-[44%] to-transparent to-[44%]`
                      : "";

                    return (
                      <section
                        key={sentence.sentenceId}
                        className={`group/sentence relative scroll-mt-8 px-2 py-2 transition-colors ${sentenceFrameClass}`}
                        aria-label={`句子 ${sentence.sentenceId}`}
                      >
                        <AnnotationGutter annotations={sentenceAnnotations} />
                        {isActive ? (
                          <span className="reader-active-dot" aria-hidden="true">
                            {sentenceIndex + 1}
                          </span>
                        ) : null}
                        <div
                          tabIndex={0}
                          className="focus-ring block w-full text-left"
                          onClick={() => selectSentence(sentence)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              selectSentence(sentence);
                            }
                          }}
                        >
                          <p className={`${readingClass} ${textHighlightClass}`}>
                            {renderSentenceText({
                              sentence,
                              marks,
                              recordId: record.id,
                              sourceContext: translationBySentence.get(sentence.sentenceId),
                              markVisibility,
                              activeLookup,
                              lookupPreviewOpen,
                              onLookup: lookupPlainText,
                              onDismissLookup: dismissLookupPreview,
                              onRevealLookup: revealLookupPreview,
                            })}
                          </p>
                          {showTranslation ? (
                            <p className="mt-2 text-sm leading-7 text-muted/95">
                              {translationBySentence.get(sentence.sentenceId) ?? null}
                            </p>
                          ) : null}
                        </div>

                        <AnnotationSlip annotations={sentenceAnnotations} />

                        {sentenceHasActiveLookup ? <div className="h-28" aria-hidden="true" /> : null}

                        {expandedSentenceId === sentence.sentenceId ? (
                          entries.map((entry) => (
                            <SentenceEntryCard key={entry.id} entry={entry} />
                          ))
                        ) : (
                          <SentenceEntrySummary
                            entries={entries}
                            onOpen={() => {
                              selectSentence(sentence);
                              setExpandedSentenceId(sentence.sentenceId);
                            }}
                          />
                        )}
                      </section>
                    );
                    })}
                  </div>
                </section>
              ))}
            </div>
          </div>
        </article>

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
