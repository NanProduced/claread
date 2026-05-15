"use client";

import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  BookOpen,
  Check,
  ChevronRight,
  Highlighter,
  MessageSquare,
  MoreHorizontal,
  PenLine,
  Pin,
  Search,
  Sparkles,
  Type,
  Volume2,
} from "lucide-react";

import type { ReaderRecordVm } from "@/adapters/records.adapter";
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
import { findTextAnchorPosition, parseSentenceAnalysisContent, tokenizeText } from "./readerText";

type ReaderDataSource = "upstream-render-scene" | "upstream-source-text";
type LowerPanelMode = "sentence" | "settings";
type ReadingDensity = "calm" | "roomy";
type ReaderTheme = "paper" | "white" | "green";
type MarkVisibility = "full" | "quiet";
type ReaderFontSize = "compact" | "normal" | "large";

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

const entryToneClass: Record<string, string> = {
  grammar_note: "border-grammar-violet/35 bg-surface-warm",
  sentence_analysis: "border-structure-green/35 bg-surface-warm",
  term_note: "border-vocab-amber/35 bg-surface-warm",
  logic_note: "border-lens-blue/25 bg-surface-warm",
  interpretation_note: "border-context-blue/30 bg-surface-warm",
  content_summary: "border-hairline bg-surface-warm",
};

const sentenceHighlightToneClass: Record<UserAnnotationColorDto, string> = {
  soft_green: "from-structure-green/18",
  soft_blue: "from-context-blue/18",
  soft_purple: "from-phrase-lavender/22",
  warm_yellow: "from-vocab-amber/24",
  sage_green: "from-structure-green/14",
};

const colorOptions: Array<{ value: UserAnnotationColorDto; label: string; className: string }> = [
  { value: "warm_yellow", label: "暖黄", className: "bg-vocab-amber/55" },
  { value: "soft_green", label: "绿", className: "bg-structure-green/50" },
  { value: "soft_blue", label: "蓝", className: "bg-context-blue/45" },
  { value: "soft_purple", label: "紫", className: "bg-phrase-lavender/70" },
  { value: "sage_green", label: "鼠尾草", className: "bg-structure-green/30" },
];

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

function shortId(id: string) {
  return id.length > 12 ? `${id.slice(0, 8)}...${id.slice(-4)}` : id;
}

function entryLabel(entry: SentenceEntryModel) {
  if (entry.entryType === "grammar_note") {
    return "语法说明";
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

function InlineLookupPreview({ lookup }: { lookup: DictionaryLookupSnapshot }) {
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
      className="reader-lookup-preview"
      role="status"
      aria-live="polite"
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
        {entryResult?.entry.phonetic ? (
          <span className="mt-1 shrink-0 text-xs text-muted">{entryResult.entry.phonetic}</span>
        ) : null}
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

      {entryResult ? (
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
  onLookup,
}: {
  children: ReactNode;
  className: string;
  base: LookupBase;
  activeLookup: DictionaryLookupSnapshot | null;
  onLookup: (snapshot: LookupBase) => void;
}) {
  const active = sameLookupTarget(activeLookup, base);

  return (
    <span className="relative inline">
      <button
        type="button"
        tabIndex={-1}
        className={`reader-lookup-token ${className} ${active ? "reader-mark--active" : ""}`}
        onClick={(event) => {
          event.stopPropagation();
          onLookup(base);
        }}
        title={base.title}
        aria-label={`查询 ${base.query}`}
      >
        {children}
      </button>
      {activeLookup && active ? <InlineLookupPreview lookup={activeLookup} /> : null}
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
  onLookup,
}: {
  sentence: SentenceModel;
  marks: InlineMarkModel[];
  recordId: string;
  sourceContext?: string;
  markVisibility: MarkVisibility;
  activeLookup: DictionaryLookupSnapshot | null;
  onLookup: (snapshot: LookupBase) => void;
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
          onLookup={onLookup}
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
          onLookup={onLookup}
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
  history,
  saveState,
  onSave,
  onSelectHistory,
  onSelectCandidate,
}: {
  lookup: DictionaryLookupSnapshot | null;
  history: DictionaryLookupSnapshot[];
  saveState: SaveState;
  onSave: () => void;
  onSelectHistory: (lookup: DictionaryLookupSnapshot) => void;
  onSelectCandidate: (entryId: number) => void;
}) {
  const lookupResult = lookup?.state.kind === "ready" ? lookup.state.result : null;
  const entryResult = lookupResult?.kind === "entry" ? lookupResult : null;
  const disambiguationResult = lookupResult?.kind === "disambiguation" ? lookupResult : null;
  const notFoundResult = lookupResult?.kind === "not_found" ? lookupResult : null;
  const errorResult = lookupResult?.kind === "error" ? lookupResult : null;
  const glossaryTitle = lookup ? contextualGlossaryTitle(lookup) : null;
  const glossaryText = contextualGlossaryText(lookup?.glossary);

  return (
    <section className="reader-tool-panel reader-dictionary-panel flex min-h-[23rem] flex-col overflow-hidden xl:min-h-0">
      <div className="border-b border-hairline px-5 py-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-ink">词典</h2>
            <p className="mt-1 text-xs leading-5 text-muted">正文轻释义，左侧完整词条</p>
          </div>
          <div className="flex items-center gap-2 text-muted">
            <Pin aria-hidden="true" className="h-4 w-4" />
            <ChevronRight aria-hidden="true" className="h-4 w-4" />
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {!lookup ? (
          <div className="flex min-h-52 flex-col justify-center border-b border-hairline/70 pb-6">
            <Search aria-hidden="true" className="mb-4 h-5 w-5 text-lens-blue" />
            <p className="text-sm font-semibold text-ink">从原文开始查词</p>
            <p className="mt-2 text-sm leading-6 text-muted">
              词汇、短语和语境标注会在原文附近给出轻释义，完整词条保留在这个面板。
            </p>
          </div>
        ) : null}

        {lookup && glossaryTitle && glossaryText ? (
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
          <div className="space-y-5">
            <div>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h3 className="reader-serif text-[2.05rem] font-semibold leading-tight text-ink">
                    {entryResult.entry.word}
                  </h3>
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
                    {entryResult.entry.phonetic ? <span>{entryResult.entry.phonetic}</span> : null}
                    <span>{entryResult.provider}</span>
                    <span>{entryResult.cached ? "缓存" : "实时查询"}</span>
                  </div>
                </div>
                <Volume2 aria-hidden="true" className="mt-2 h-4 w-4 shrink-0 text-muted" />
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs font-semibold">
                <span className="text-vocab-amber">{lookup.label ?? (lookup.lookupType === "phrase" ? "短语" : "词汇")}</span>
                {firstMeaning(entryResult) ? <span className="text-ink-soft">{firstMeaning(entryResult)}</span> : null}
              </div>
            </div>

            <div className="border-t border-hairline py-4">
              <p className="text-sm font-semibold text-ink">详细释义</p>
              <div className="mt-3 space-y-4">
                {entryResult.entry.meanings.slice(0, 3).map((meaning) => (
                  <div key={`${entryResult.entry.id}-${meaning.partOfSpeech}`}>
                    <p className="text-xs font-semibold text-structure-green">{meaning.partOfSpeech}</p>
                    <ol className="mt-2 space-y-2">
                      {meaning.definitions.slice(0, 2).map((definition, index) => (
                        <li
                          key={`${entryResult.entry.id}-${meaning.partOfSpeech}-${definition.meaning}`}
                          className="grid grid-cols-[1.5rem_1fr] gap-2 text-sm leading-6 text-ink-soft"
                        >
                          <span className="text-xs font-semibold text-subtle">{index + 1}</span>
                          <span>{definition.meaning}</span>
                          {definition.example ? (
                            <span className="col-start-2 reader-serif text-[0.9375rem] leading-6 text-muted">
                              {definition.example}
                            </span>
                          ) : null}
                        </li>
                      ))}
                    </ol>
                  </div>
                ))}
              </div>
            </div>

            {entryResult.entry.phrases.length > 0 || entryResult.entry.examples.length > 0 ? (
              <div className="space-y-4">
                {entryResult.entry.phrases.length > 0 ? (
                  <div className="border-t border-hairline pt-4">
                    <p className="text-sm font-semibold text-ink">搭配与派生</p>
                    <div className="mt-2 space-y-2">
                      {entryResult.entry.phrases.slice(0, 4).map((phrase) => (
                        <p key={phrase.phrase} className="text-sm leading-6 text-ink-soft">
                          <span className="font-semibold text-ink">{phrase.phrase}</span>
                          {phrase.meaning ? <span className="ml-2 text-muted">{phrase.meaning}</span> : null}
                        </p>
                      ))}
                    </div>
                  </div>
                ) : null}
                {entryResult.entry.examples.length > 0 ? (
                  <div className="border-t border-hairline pt-4">
                    <p className="text-sm font-semibold text-ink">例句</p>
                    <div className="mt-2 space-y-3">
                      {entryResult.entry.examples.slice(0, 2).map((example) => (
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

            <div className="sticky bottom-0 -mx-5 flex flex-wrap items-center gap-2 border-t border-hairline bg-surface/96 px-5 py-3">
              <button
                type="button"
                className="focus-ring inline-flex min-h-10 flex-1 items-center justify-center gap-2 rounded-[10px] bg-ink px-4 text-sm font-semibold text-surface transition-colors hover:bg-ink-soft disabled:cursor-not-allowed disabled:opacity-60"
                onClick={onSave}
                disabled={saveState.kind === "saving" || saveState.kind === "saved"}
              >
                {saveState.kind === "saving" ? "写入中" : "加入生词本"}
              </button>
              {saveState.kind === "saved" ? (
                <span className="inline-flex items-center gap-1 text-xs font-semibold text-structure-green">
                  <Check aria-hidden="true" className="h-3.5 w-3.5" />
                  {saveState.message}
                </span>
              ) : null}
              {saveState.kind === "error" ? (
                <span className="text-xs font-semibold text-error-red">{saveState.message}</span>
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

        {history.length > 0 ? (
          <div className="mt-6 border-t border-hairline pt-4">
            <p className="text-xs font-semibold text-muted">本次查词</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {history.map((item) => (
                <button
                  key={`${item.query}-${item.sentenceId}-${item.anchorText}`}
                  type="button"
                  className="focus-ring rounded-pill border border-hairline bg-reader-paper px-3 py-1.5 text-xs font-semibold text-ink-soft transition-colors hover:border-muted hover:text-ink"
                  onClick={() => onSelectHistory(item)}
                >
                  {item.query}
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function ContextPanel({
  mode,
  sentence,
  note,
  color,
  saveState,
  sentenceAnnotations,
  showTranslation,
  fontSize,
  density,
  theme,
  markVisibility,
  onModeChange,
  onNoteChange,
  onColorChange,
  onSaveAnnotation,
  onAsk,
  onShowTranslationChange,
  onFontSizeChange,
  onDensityChange,
  onThemeChange,
  onMarkVisibilityChange,
}: {
  mode: LowerPanelMode;
  sentence: SentenceModel | null;
  note: string;
  color: UserAnnotationColorDto;
  saveState: AnnotationSaveState;
  sentenceAnnotations: WebAnnotationVm[];
  showTranslation: boolean;
  fontSize: ReaderFontSize;
  density: ReadingDensity;
  theme: ReaderTheme;
  markVisibility: MarkVisibility;
  onModeChange: (mode: LowerPanelMode) => void;
  onNoteChange: (value: string) => void;
  onColorChange: (value: UserAnnotationColorDto) => void;
  onSaveAnnotation: (useNote: boolean) => void;
  onAsk: () => void;
  onShowTranslationChange: (value: boolean) => void;
  onFontSizeChange: (value: ReaderFontSize) => void;
  onDensityChange: (value: ReadingDensity) => void;
  onThemeChange: (value: ReaderTheme) => void;
  onMarkVisibilityChange: (value: MarkVisibility) => void;
}) {
  return (
    <section className="reader-tool-panel flex min-h-[19.5rem] flex-col overflow-hidden xl:min-h-0">
      <div className="flex items-center justify-between gap-2 border-b border-hairline px-5 py-3.5">
        <div>
          <h2 className="text-base font-semibold text-ink">
            {mode === "settings" ? "阅读设置" : "选中句子"}
          </h2>
          <p className="mt-1 text-xs leading-5 text-muted">
            {mode === "settings" ? "仅调整本地阅读显示。" : "笔记、高亮和追问挂回原文。"}
          </p>
        </div>
        <button
          type="button"
          className="focus-ring inline-flex h-9 w-9 items-center justify-center rounded-full border border-hairline bg-reader-paper text-muted transition-colors hover:border-muted hover:text-ink"
          onClick={() => onModeChange(mode === "settings" ? "sentence" : "settings")}
          aria-label={mode === "settings" ? "切回句子操作" : "打开阅读设置"}
        >
          {mode === "settings" ? <PenLine aria-hidden="true" className="h-4 w-4" /> : <Type aria-hidden="true" className="h-4 w-4" />}
        </button>
      </div>

      {mode === "settings" ? (
          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
          <fieldset>
            <legend className="text-xs font-semibold text-muted">译文</legend>
            <div className="mt-2 grid grid-cols-2 gap-2">
              {[true, false].map((value) => (
                <button
                  key={String(value)}
                  type="button"
                  className={`focus-ring min-h-9 rounded-pill border px-3 text-sm font-semibold transition-colors ${
                    showTranslation === value
                      ? "border-lens-blue bg-lens-blue-soft text-lens-blue"
                      : "border-hairline bg-reader-paper text-ink-soft hover:border-muted"
                  }`}
                  onClick={() => onShowTranslationChange(value)}
                >
                  {value ? "显示译文" : "原文模式"}
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset>
            <legend className="text-xs font-semibold text-muted">字号</legend>
            <div className="mt-2 grid grid-cols-3 gap-2">
              {(["compact", "normal", "large"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  className={`focus-ring min-h-9 rounded-pill border px-3 text-sm font-semibold transition-colors ${
                    fontSize === value
                      ? "border-lens-blue bg-lens-blue-soft text-lens-blue"
                      : "border-hairline bg-reader-paper text-ink-soft hover:border-muted"
                  }`}
                  onClick={() => onFontSizeChange(value)}
                >
                  {value === "compact" ? "小" : value === "normal" ? "中" : "大"}
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset>
            <legend className="text-xs font-semibold text-muted">行距</legend>
            <div className="mt-2 grid grid-cols-2 gap-2">
              {(["calm", "roomy"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  className={`focus-ring min-h-9 rounded-pill border px-3 text-sm font-semibold transition-colors ${
                    density === value
                      ? "border-lens-blue bg-lens-blue-soft text-lens-blue"
                      : "border-hairline bg-reader-paper text-ink-soft hover:border-muted"
                  }`}
                  onClick={() => onDensityChange(value)}
                >
                  {value === "calm" ? "标准" : "舒展"}
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset>
            <legend className="text-xs font-semibold text-muted">阅读背景</legend>
            <div className="mt-2 grid grid-cols-3 gap-2">
              {(["paper", "white", "green"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  className={`focus-ring min-h-9 rounded-pill border px-3 text-sm font-semibold transition-colors ${
                    theme === value
                      ? "border-lens-blue bg-lens-blue-soft text-lens-blue"
                      : "border-hairline bg-reader-paper text-ink-soft hover:border-muted"
                  }`}
                  onClick={() => onThemeChange(value)}
                >
                  {value === "paper" ? "纸张" : value === "white" ? "纯白" : "护眼"}
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset>
            <legend className="text-xs font-semibold text-muted">标注显示</legend>
            <div className="mt-2 grid grid-cols-2 gap-2">
              {(["full", "quiet"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  className={`focus-ring min-h-9 rounded-pill border px-3 text-sm font-semibold transition-colors ${
                    markVisibility === value
                      ? "border-lens-blue bg-lens-blue-soft text-lens-blue"
                      : "border-hairline bg-reader-paper text-ink-soft hover:border-muted"
                  }`}
                  onClick={() => onMarkVisibilityChange(value)}
                >
                  {value === "full" ? "完整" : "安静"}
                </button>
              ))}
            </div>
          </fieldset>
        </div>
      ) : (
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
          {sentence ? (
            <>
              <div className="rounded-[10px] bg-structure-green/8 px-3 py-3 ring-1 ring-structure-green/18" title="当前选中的句子">
                <p className="line-clamp-4 reader-serif text-[1rem] leading-7 text-ink">{sentence.text}</p>
              </div>
              <div className="flex items-center justify-between gap-3">
                <div className="flex flex-wrap gap-2" aria-label="高亮颜色">
                  {colorOptions.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      className={`focus-ring flex h-8 w-8 items-center justify-center rounded-full border ${
                        color === option.value ? "border-lens-blue" : "border-hairline"
                      }`}
                      onClick={() => onColorChange(option.value)}
                      aria-label={`选择${option.label}高亮`}
                    >
                      <span className={`h-5 w-5 rounded-full ${option.className}`} />
                    </button>
                  ))}
                </div>
                <button
                  type="button"
                  className="focus-ring inline-flex h-8 w-8 items-center justify-center rounded-full border border-hairline bg-reader-paper text-muted transition-colors hover:border-muted hover:text-ink"
                  aria-label="更多句子操作"
                >
                  <MoreHorizontal aria-hidden="true" className="h-4 w-4" />
                </button>
              </div>
              <label className="block">
                <span className="text-xs font-semibold text-muted">我的笔记</span>
                <textarea
                  className="mt-2 min-h-28 w-full resize-y rounded-[10px] border border-hairline bg-reader-paper px-3 py-3 text-sm leading-6 text-ink outline-none transition-colors focus:border-muted"
                  placeholder="写一句和这句话绑定的笔记。"
                  value={note}
                  onChange={(event) => onNoteChange(event.target.value)}
                />
              </label>
              <div className="grid grid-cols-[1fr_1fr] gap-2">
                <button
                  type="button"
                  className="focus-ring inline-flex min-h-10 items-center justify-center gap-2 rounded-[10px] bg-ink px-3.5 text-sm font-semibold text-surface disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={saveState.kind === "saving"}
                  onClick={() => onSaveAnnotation(false)}
                >
                  <Highlighter aria-hidden="true" className="h-4 w-4" />
                  高亮本句
                </button>
                <button
                  type="button"
                  className="focus-ring inline-flex min-h-10 items-center justify-center gap-2 rounded-[10px] border border-hairline bg-reader-paper px-3.5 text-sm font-semibold text-ink-soft transition-colors hover:border-muted hover:text-ink disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={saveState.kind === "saving" || note.trim().length === 0}
                  onClick={() => onSaveAnnotation(true)}
                >
                  <PenLine aria-hidden="true" className="h-4 w-4" />
                  保存笔记
                </button>
                <button
                  type="button"
                  className="focus-ring col-span-2 inline-flex min-h-10 items-center justify-center gap-2 rounded-[10px] border border-lens-blue/20 bg-lens-blue-soft/60 px-3.5 text-sm font-semibold text-lens-blue transition-colors hover:border-lens-blue/35"
                  onClick={onAsk}
                >
                  <MessageSquare aria-hidden="true" className="h-4 w-4" />
                  问 Claread
                </button>
              </div>
              {saveState.kind === "saved" ? (
                <p className="text-xs font-semibold text-structure-green">{saveState.message}</p>
              ) : null}
              {saveState.kind === "error" ? (
                <p className="text-xs font-semibold text-error-red">{saveState.message}</p>
              ) : null}
              {sentenceAnnotations.length > 0 ? (
                <div className="border-t border-hairline pt-3">
                  <p className="text-xs font-semibold text-muted">本句已保存</p>
                  <div className="mt-2 space-y-2">
                    {sentenceAnnotations.slice(0, 3).map((item) => (
                      <p key={item.id} className="text-sm leading-6 text-ink-soft">
                        <span className="font-semibold text-ink">{item.type === "note" ? "笔记" : "高亮"}</span>
                        {item.note ? <span className="ml-2 text-muted">{item.note}</span> : null}
                      </p>
                    ))}
                  </div>
                </div>
              ) : null}
            </>
          ) : (
            <p className="text-sm leading-6 text-muted">点击正文中的句子后，可以在这里写笔记或保存高亮。</p>
          )}
        </div>
      )}
    </section>
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
          tabIndex={-1}
          className="reader-entry-chip"
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

function SentenceEntryBlock({ entry }: { entry: SentenceEntryModel }) {
  if (entry.entryType === "sentence_analysis") {
    const parsed = parseSentenceAnalysisContent(entry.content);

    return (
      <section className={`reader-entry-note ${entryToneClass[entry.entryType]}`}>
        <div className="flex items-start gap-3">
          <span className="reader-entry-icon text-structure-green">
            <BookOpen aria-hidden="true" className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <h3 className="text-sm font-semibold leading-6 text-ink">{entry.title ?? entryLabel(entry)}</h3>
            {parsed.summary ? (
              <p className="mt-2 whitespace-pre-line text-sm leading-7 text-ink-soft">{parsed.summary}</p>
            ) : null}
            {parsed.chunks.length > 0 ? (
              <ol className="mt-3 divide-y divide-hairline/80 border-t border-hairline/80">
                {parsed.chunks.map((chunk) => (
                  <li
                    key={`${entry.id}-${chunk.order}`}
                    className="grid grid-cols-[1.75rem_1fr] gap-3 py-2.5 text-sm leading-6"
                  >
                    <span className="mt-0.5 flex h-6 w-6 items-center justify-center rounded-full bg-surface text-xs font-semibold text-muted ring-1 ring-hairline">
                      {chunk.order || "•"}
                    </span>
                    <span>
                      <span className="font-semibold text-ink">{chunk.label}</span>
                      <span className="mx-1 text-muted">:</span>
                      <span className="text-ink-soft">{chunk.text}</span>
                    </span>
                  </li>
                ))}
              </ol>
            ) : null}
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className={`reader-entry-note ${entryToneClass[entry.entryType] ?? entryToneClass.content_summary}`}>
      <div className="flex items-start gap-3">
        <span className="reader-entry-icon text-grammar-violet">
          <Sparkles aria-hidden="true" className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <h3 className="text-sm font-semibold leading-6 text-ink">{entry.title ?? entryLabel(entry)}</h3>
          <p className="mt-2 whitespace-pre-line text-sm leading-7 text-ink-soft">{entry.content}</p>
        </div>
      </div>
    </section>
  );
}

function AiWorkspace({
  open,
  activeSentence,
  onToggle,
}: {
  open: boolean;
  activeSentence: SentenceModel | null;
  onToggle: () => void;
}) {
  if (!open) {
    return (
      <aside className="rounded-panel border border-hairline bg-surface/86 shadow-surface-quiet xl:sticky xl:top-5 xl:min-h-[calc(100vh-2.5rem)]">
        <button
          type="button"
          className="focus-ring flex min-h-20 w-full items-center justify-center gap-2 px-3 py-4 text-sm font-semibold text-ink-soft xl:h-full xl:flex-col"
          onClick={onToggle}
          aria-label="打开 AI 工作区"
        >
          <MessageSquare aria-hidden="true" className="h-4 w-4 text-lens-blue" />
          <span className="xl:[writing-mode:vertical-rl]">Ask Claread</span>
        </button>
      </aside>
    );
  }

  return (
    <aside className="rounded-panel border border-hairline bg-surface shadow-surface-quiet xl:sticky xl:top-5 xl:max-h-[calc(100vh-2.5rem)] xl:min-h-[calc(100vh-2.5rem)] xl:overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b border-hairline px-4 py-4">
        <div>
          <h2 className="text-sm font-semibold text-ink">Ask Claread</h2>
          <p className="mt-1 text-xs leading-5 text-muted">未来 AI 工作区，围绕当前句子或全文上下文。</p>
        </div>
        <button
          type="button"
          className="focus-ring inline-flex h-9 w-9 items-center justify-center rounded-full border border-hairline bg-reader-paper text-muted transition-colors hover:border-muted hover:text-ink"
          onClick={onToggle}
          aria-label="收起 AI 工作区"
        >
          <ChevronRight aria-hidden="true" className="h-4 w-4" />
        </button>
      </div>

      <div className="flex min-h-[24rem] flex-col px-4 py-4 xl:h-[calc(100vh-8.5rem)]">
        <div className="border-b border-hairline pb-4">
          <p className="text-xs font-semibold text-muted">当前上下文</p>
          <p className="mt-2 line-clamp-5 reader-serif text-sm leading-6 text-ink-soft">
            {activeSentence?.text ?? "点击正文句子后，这里会带入上下文。"}
          </p>
        </div>
        <div className="flex flex-1 items-center justify-center py-8">
          <div className="text-center">
            <MessageSquare aria-hidden="true" className="mx-auto h-8 w-8 text-lens-blue" />
            <p className="mt-3 text-sm font-semibold text-ink">AI 对话尚未接入</p>
            <p className="mt-2 text-sm leading-6 text-muted">首版只保留工作区位置，避免和正文批注争抢空间。</p>
          </div>
        </div>
        <label className="block border-t border-hairline pt-4">
          <span className="text-xs font-semibold text-muted">输入框</span>
          <textarea
            className="mt-2 min-h-24 w-full resize-none rounded-md border border-hairline bg-reader-paper px-3 py-2 text-sm leading-6 text-muted outline-none"
            placeholder="后续接入 Ask Claread。"
            disabled
          />
        </label>
      </div>
    </aside>
  );
}

export function ReaderWorkbench({
  record,
  dataSource,
  message,
  initialAnnotations,
}: ReaderWorkbenchProps) {
  const reader = record.reader;
  const firstSentence = reader.article.sentences.at(0) ?? null;
  const [activeLookup, setActiveLookup] = useState<DictionaryLookupSnapshot | null>(null);
  const [lookupHistory, setLookupHistory] = useState<DictionaryLookupSnapshot[]>([]);
  const [dictionarySaveState, setDictionarySaveState] = useState<SaveState>({ kind: "idle" });
  const [annotations, setAnnotations] = useState(initialAnnotations);
  const [activeSentence, setActiveSentence] = useState<SentenceModel | null>(firstSentence);
  const [lowerPanelMode, setLowerPanelMode] = useState<LowerPanelMode>("sentence");
  const [note, setNote] = useState("");
  const [annotationColor, setAnnotationColor] = useState<UserAnnotationColorDto>("warm_yellow");
  const [annotationSaveState, setAnnotationSaveState] = useState<AnnotationSaveState>({ kind: "idle" });
  const [showTranslation, setShowTranslation] = useState(true);
  const [fontSize, setFontSize] = useState<ReaderFontSize>("normal");
  const [density, setDensity] = useState<ReadingDensity>("calm");
  const [theme, setTheme] = useState<ReaderTheme>("paper");
  const [markVisibility, setMarkVisibility] = useState<MarkVisibility>("quiet");
  const [aiOpen, setAiOpen] = useState(false);

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

  const handleLookupSnapshot = useCallback((snapshot: DictionaryLookupSnapshot) => {
    setActiveLookup(snapshot);
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

  const lookupPlainText = useCallback(async (base: LookupBase) => {
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
    setNote("");
    setAnnotationSaveState({ kind: "idle" });
  }

  const canvasThemeClass =
    theme === "white"
      ? "bg-surface"
      : theme === "green"
        ? "bg-[#F3F7EF]"
        : "reading-paper";
  const readingClass =
    `reader-serif text-ink ${
      fontSize === "compact" ? "text-[1.16rem]" : fontSize === "large" ? "text-[1.42rem]" : "text-[1.28rem]"
    } ${density === "roomy" ? "leading-[2.08]" : "leading-[1.88]"}`;

  return (
    <main className="paper-grain min-h-screen px-3 py-3 text-ink sm:px-4 lg:px-5">
      <div
        className={`grid gap-4 ${
          aiOpen
            ? "xl:grid-cols-[390px_minmax(0,1fr)_360px]"
            : "xl:grid-cols-[390px_minmax(0,1fr)_72px]"
        }`}
      >
        <aside className="order-2 grid min-h-0 gap-3 xl:order-1 xl:sticky xl:top-3 xl:h-[calc(100vh-1.5rem)] xl:grid-rows-[minmax(0,1.35fr)_minmax(0,1fr)]">
          <DictionaryDetailPanel
            lookup={activeLookup}
            history={lookupHistory}
            saveState={dictionarySaveState}
            onSave={saveVocabularyFromDictionary}
            onSelectHistory={setActiveLookup}
            onSelectCandidate={selectDictionaryCandidate}
          />
          <ContextPanel
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
            onModeChange={setLowerPanelMode}
            onNoteChange={setNote}
            onColorChange={setAnnotationColor}
            onSaveAnnotation={saveSentenceAnnotation}
            onAsk={() => setAiOpen(true)}
            onShowTranslationChange={setShowTranslation}
            onFontSizeChange={setFontSize}
            onDensityChange={setDensity}
            onThemeChange={setTheme}
            onMarkVisibilityChange={setMarkVisibility}
          />
        </aside>

        <article
          className={`order-1 min-w-0 overflow-visible rounded-panel border border-hairline shadow-surface-quiet xl:order-2 ${canvasThemeClass}`}
        >
          <header className="border-b border-hairline px-5 py-4 sm:px-8 lg:px-10">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
              <div className="min-w-0">
                <p className="font-headline text-xl font-semibold text-ink">Claread Reader v1</p>
                <h1 className="mt-3 max-w-[24ch] font-headline text-3xl font-semibold leading-tight tracking-normal text-ink md:text-[2.45rem]">
                  {record.title}
                </h1>
                <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted">
                  <span>{shortId(record.id)}</span>
                  <span aria-hidden="true" className="h-1 w-1 rounded-full bg-hairline" />
                  <span>{dataSourceLabel[dataSource]}</span>
                  <span aria-hidden="true" className="h-1 w-1 rounded-full bg-hairline" />
                  <span>{reader.article.sentences.length} 句</span>
                </div>
              </div>
              <div className="flex shrink-0 flex-wrap items-start gap-2">
                <FavoriteButton recordId={record.id} />
                <button
                  type="button"
                  className={`focus-ring inline-flex h-9 items-center rounded-pill border px-3 text-xs font-semibold transition-colors ${
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
                  className="focus-ring inline-flex h-9 items-center gap-1 rounded-pill border border-hairline bg-surface-warm px-3 text-xs font-semibold text-ink-soft transition-colors hover:border-muted hover:text-ink"
                  onClick={() => setLowerPanelMode("settings")}
                >
                  <Type aria-hidden="true" className="h-3.5 w-3.5" />
                  Aa
                </button>
                <button
                  type="button"
                  className="focus-ring inline-flex h-9 w-9 items-center justify-center rounded-pill border border-hairline bg-surface-warm text-muted transition-colors hover:border-muted hover:text-ink"
                  aria-label="更多阅读操作"
                >
                  <MoreHorizontal aria-hidden="true" className="h-4 w-4" />
                </button>
              </div>
            </div>

            {message ? (
              <div className="mt-5 border-l border-lens-blue/35 bg-lens-blue-soft px-4 py-3 text-sm leading-6 text-ink-soft">
                {message}
              </div>
            ) : null}
          </header>

          <div className="px-5 py-7 sm:px-8 lg:px-10 lg:py-9">
            <div className="space-y-10">
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
                    const sentenceHasActiveLookup = activeLookup?.sentenceId === sentence.sentenceId;
                    const highlight = sentenceAnnotations.at(0);
                    const noteAnnotations = sentenceAnnotations.filter((item) => item.note);
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
                              onLookup: lookupPlainText,
                            })}
                          </p>
                          {showTranslation ? (
                            <p className="mt-2 text-sm leading-7 text-muted/95">
                              {translationBySentence.get(sentence.sentenceId) ?? null}
                            </p>
                          ) : null}
                        </div>

                        {noteAnnotations.length > 0 ? (
                          <div className="ml-3 mt-3 space-y-2 border-l border-vocab-amber/45 pl-4">
                            {noteAnnotations.map((item) => (
                              <div key={item.id} className="flex items-start gap-2 text-sm leading-6 text-ink-soft">
                                <PenLine aria-hidden="true" className="mt-1 h-3.5 w-3.5 shrink-0 text-vocab-amber" />
                                <p>{item.note}</p>
                              </div>
                            ))}
                          </div>
                        ) : null}

                        {sentenceHasActiveLookup ? <div className="h-28" aria-hidden="true" /> : null}

                        {isActive ? (
                          entries.map((entry) => (
                            <SentenceEntryBlock key={entry.id} entry={entry} />
                          ))
                        ) : (
                          <SentenceEntrySummary entries={entries} onOpen={() => selectSentence(sentence)} />
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

        <div className="order-3 xl:order-3">
          <AiWorkspace
            open={aiOpen}
            activeSentence={activeSentence}
            onToggle={() => setAiOpen((value) => !value)}
          />
        </div>
      </div>
    </main>
  );
}
