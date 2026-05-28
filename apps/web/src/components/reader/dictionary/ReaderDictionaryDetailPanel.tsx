"use client";

import type { FormEvent } from "react";
import { useEffect, useRef, useState } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Bot,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Flag,
  Pin,
  Search,
  Sparkles,
  Tag,
  Volume2,
  X,
} from "lucide-react";
import type { ReaderStructuredInspectIntent } from "@/lib/reader-plate";
import type { WebDictAIRequest, DictionaryAIViewState } from "@/types/api/dict-ai";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/primitives/tooltip";
import { cn } from "@/lib/cn";
import { readerCommandControl, readerIconAction, readerPanelItem, readerTransitionFast } from "../interaction";
import type { SaveState, DictionaryLookupSnapshot } from "./contracts";
import { getSaveActionCopy, type LookupSaveState, type ReaderVocabularyLookupMatch } from "./lookupSaveState";
import {
  type DictionaryContentTab,
  type DictionarySenseItem,
  contextualGlossaryTitle,
  contextualGlossaryText,
  dictionaryAIActionLabel,
  dictionaryAIClassificationLabel as dictionaryAIClassificationBadgeLabel,
  dictionaryAIRequestForLookup,
  dictionaryAITranslationVisible,
  dictionaryDisplayTags,
  dictionaryEntrySummary,
  dictionaryIsManualLookup,
  dictionarySenseItems,
  groupDisambiguationCandidates,
  isDictionaryAIErrorResult,
  normalizeDictionaryText,
  dictionaryLookupHistoryKey,
  dictionaryLookupHistorySummary,
} from "./shared";
import { ReaderStructuredInspectCard } from "./ReaderStructuredInspectCard";

function ScrollWrapper({ scrollable, className, children }: { scrollable: boolean, className: string, children: React.ReactNode }) {
  if (scrollable) {
    return (
      <ScrollArea className={className.replace('overflow-y-auto', '').replace('overscroll-contain', '')}>
        <div className="h-full pb-4">{children}</div>
      </ScrollArea>
    );
  }
  return <div className={className}>{children}</div>;
}

function DictionaryIconAction({
  active = false,
  children,
  disabled = false,
  label,
  onClick,
  pressed,
  tone = "default",
}: {
  active?: boolean;
  children: React.ReactNode;
  disabled?: boolean;
  label: string;
  onClick: () => void;
  pressed?: boolean;
  tone?: "default" | "accent" | "saved" | "error";
}) {
  const toneClass =
    tone === "accent"
      ? "text-vocab-amber hover:text-vocab-amber/90"
      : tone === "saved"
        ? "text-structure-green hover:bg-transparent hover:text-structure-green/90"
        : tone === "error"
          ? "text-error-red hover:text-error-red/90"
          : "text-muted hover:text-ink";

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          className={cn(
            readerIconAction,
            "inline-flex h-8 w-8 justify-center rounded-[0.7rem] p-0",
            active
              ? "border-hairline/85 bg-ink/[0.02]"
              : "border-transparent",
            toneClass,
            disabled && "cursor-not-allowed",
          )}
          onClick={onClick}
          disabled={disabled}
          aria-label={label}
          aria-pressed={pressed}
          title={label}
        >
          {children}
        </button>
      </TooltipTrigger>
      <TooltipContent side="top">{label}</TooltipContent>
    </Tooltip>
  );
}

interface ReaderDictionaryDetailPanelProps {
  lookup: DictionaryLookupSnapshot | null;
  inspect?: ReaderStructuredInspectIntent | null;
  readingGoal: string;
  saveState: SaveState;
  lookupSaveState?: LookupSaveState;
  savedVocabularyMatch?: ReaderVocabularyLookupMatch | null;
  dictionaryAI: DictionaryAIViewState;
  dictionaryAIPanelOpen: boolean;
  dictionaryAINoteState: SaveState;
  searchQuery: string;
  searchExpanded: boolean;
  onSave: () => void;
  onRequestAI: (mode: WebDictAIRequest["mode"]) => void;
  onCreateAINote: () => void;
  onSelectAISuggestedQuery: (query: string) => void;
  onSearchQueryChange: (value: string) => void;
  onSearchSubmit: (query: string) => void;
  onSelectCandidate: (entryId: number) => void;
  onToggleAIPanel: () => void;
  onToggleSearchExpanded: () => void;
  onDismiss?: () => void;
  onFeedback?: () => void;
  onNotFoundFeedback?: () => void;
  onInspectFeedback?: (inspect: ReaderStructuredInspectIntent) => void;
  pinned?: boolean;
  onTogglePinned?: () => void;
  variant?: "card" | "sheet";
  canSaveVocabulary?: boolean;
  canCreateAINote?: boolean;
  onLookupPhraseFromInspect?: (intent: ReaderStructuredInspectIntent) => void;
  onAttachToAsk?: (intent: ReaderStructuredInspectIntent) => void;
  // History integration
  history?: DictionaryLookupSnapshot[];
  onSelectHistory?: (lookup: DictionaryLookupSnapshot) => void;
}

export function ReaderDictionaryDetailPanel({
  canSaveVocabulary = true,
  canCreateAINote = false,
  dictionaryAI,
  dictionaryAINoteState,
  dictionaryAIPanelOpen,
  inspect = null,
  lookup,
  onAttachToAsk,
  onCreateAINote,
  onDismiss,
  onFeedback,
  onNotFoundFeedback,
  onInspectFeedback,
  onLookupPhraseFromInspect,
  onRequestAI,
  onSave,
  onSearchQueryChange,
  onSearchSubmit,
  onSelectAISuggestedQuery,
  onSelectCandidate,
  onToggleAIPanel,
  onTogglePinned,
  onToggleSearchExpanded,
  pinned = false,
  readingGoal,
  saveState,
  lookupSaveState = "not_saved",
  savedVocabularyMatch = null,
  searchExpanded,
  searchQuery,
  variant = "sheet",
  history = [],
  onSelectHistory,
}: ReaderDictionaryDetailPanelProps) {
  const lookupResult = lookup?.state.kind === "ready" ? lookup.state.result : null;
  const entryResult = lookupResult?.kind === "entry" ? lookupResult : null;
  const disambiguationResult = lookupResult?.kind === "disambiguation" ? lookupResult : null;
  const notFoundResult = lookupResult?.kind === "not_found" ? lookupResult : null;
  const errorResult = lookupResult?.kind === "error" ? lookupResult : null;
  const isCard = variant === "card";

  const [activeTab, setActiveTab] = useState<DictionaryContentTab>("meanings");
  const [showAllTags, setShowAllTags] = useState(false);
  const entryScrollRef = useRef<HTMLDivElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);

  const senseItems = entryResult ? dictionarySenseItems(entryResult.entry) : [];
  const phraseCount = entryResult?.entry.phrases.length ?? 0;
  const formCount = entryResult?.entry.exchange.length ?? 0;

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
  const aiEntryTags = aiEntry ? dictionaryDisplayTags(aiEntry.tags, readingGoal) : [];

  const conciseMeaning = entryResult ? dictionaryEntrySummary(entryResult, lookup) : "";
  const glossaryTitle = lookup ? contextualGlossaryTitle(lookup) : null;
  const glossaryText = contextualGlossaryText(lookup?.glossary);
  const isManualLookup = dictionaryIsManualLookup(lookup);
  const canRequestContextExplain = Boolean(entryResult && lookup?.contextSentence.trim() && !isManualLookup);
  const canRequestMissingFallback = Boolean(notFoundResult && lookup?.contextSentence.trim() && !isManualLookup);
  const canRequestAIFallback =
    canRequestMissingFallback || Boolean(entryResult && !conciseMeaning && lookup?.contextSentence.trim() && !isManualLookup);

  const [historyCollapsed, setHistoryCollapsed] = useState(true);
  const savedContextCount = savedVocabularyMatch?.sourceRefs.length ?? 0;
  const isSavedState = lookupSaveState !== "not_saved";
  const saveDisabled =
    saveState.kind === "saving" ||
    lookupSaveState === "already_saved_here" ||
    lookupSaveState === "multiple_contexts" ||
    lookupSaveState === "mastered" ||
    !canSaveVocabulary;
  const primaryMeaning =
    conciseMeaning ||
    (entryResult ? "当前词条暂无简短释义。" : "") ||
    (notFoundResult ? "当前词典没有匹配到这个词条。" : "");

  const lemmaWord =
    entryResult?.entry.baseWord &&
    entryResult.entry.baseWord.trim().toLowerCase() !== entryResult.entry.word.trim().toLowerCase()
      ? entryResult.entry.baseWord.trim()
      : null;
  const phoneticLabel = entryResult?.entry.phonetic?.trim() || null;
  const lemmaLabel = lemmaWord ? `原形 ${lemmaWord}` : null;
  const homographLabel = entryResult?.entry.homographNo ? `义项 ${entryResult.entry.homographNo}` : null;

  const tabItems = [
    { id: "meanings" as const, label: "释义", count: senseItems.length },
    { id: "phrases" as const, label: "搭配", count: phraseCount },
    { id: "forms" as const, label: "词形", count: formCount },
  ].filter((item) => item.count > 0);

  useEffect(() => {
    queueMicrotask(() => {
      setActiveTab("meanings");
      setShowAllTags(false);
    });
  }, [lookup?.query, lookupResult?.kind]);

  useEffect(() => {
    if (!entryResult || !tabItems.length) {
      return;
    }
    if (!tabItems.some((item) => item.id === activeTab)) {
      queueMicrotask(() => setActiveTab(tabItems[0].id));
    }
  }, [activeTab, entryResult, tabItems]);

  useEffect(() => {
    const viewport = entryScrollRef.current?.querySelector('[data-radix-scroll-area-viewport]');
    if (viewport) {
      viewport.scrollTo({ top: 0 });
    } else {
      entryScrollRef.current?.scrollTo({ top: 0 });
    }
  }, [activeTab, dictionaryAI.kind, dictionaryAIPanelOpen, inspect?.markId, lookup?.query, lookupResult?.kind]);

  useEffect(() => {
    if (!searchExpanded) {
      return;
    }

    const frame = window.requestAnimationFrame(() => {
      searchInputRef.current?.focus();
      searchInputRef.current?.select();
    });

    return () => window.cancelAnimationFrame(frame);
  }, [searchExpanded]);

  function handleSearchSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSearchSubmit(searchQuery);
  }

  function handleSearchClearOrCollapse() {
    if (searchQuery.trim()) {
      onSearchQueryChange("");
      return;
    }

    onToggleSearchExpanded();
  }

  const inspectVisible = Boolean(inspect && !lookup);

  function renderAIStatusCard(mode: WebDictAIRequest["mode"]) {
    if (!dictionaryAIPanelOpen || dictionaryAI.kind === "idle" || dictionaryAI.mode !== mode) {
      return null;
    }
    if (dictionaryAI.kind === "loading") {
      return (
        <div className="rounded-[10px] border border-hairline/80 bg-ink/[0.01] px-4 py-3 select-none">
          <div className="flex items-center gap-2 text-[0.72rem] font-semibold tracking-wider text-vocab-amber">
            <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
            <span>{mode === "context_explain" ? "AI 语境解读" : "AI 补充结果"}</span>
          </div>
          <div className="mt-3 space-y-2 animate-pulse">
            <div className="h-3 w-24 rounded bg-ink/[0.06]" />
            <div className="h-3 w-5/6 rounded bg-ink/[0.04]" />
            <div className="h-3 w-2/3 rounded bg-ink/[0.04]" />
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
      <div className="rounded-[10px] border border-exam-red/20 bg-exam-red/[0.02] px-4 py-3 select-none">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[0.72rem] font-semibold tracking-wider text-exam-red">
              <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
              <span>{mode === "context_explain" ? "AI 语境解读" : "AI 补充结果"}</span>
            </div>
            <p className="mt-2 text-xs leading-5 text-exam-red">{dictionaryAI.error.message}</p>
          </div>
          {canRetry ? (
            <button
              type="button"
              className={cn(readerCommandControl, "inline-flex min-h-8 shrink-0 rounded border border-exam-red/30 bg-surface px-2.5 py-1 text-[0.68rem] text-exam-red hover:bg-exam-red/[0.04]")}
              onClick={() => onRequestAI(mode)}
            >
              重试
            </button>
          ) : null}
        </div>
      </div>
    );
  }

  function renderAINoteAction() {
    if (!canCreateAINote || dictionaryAI.kind !== "ready") {
      return null;
    }

    return (
      <div className="mt-3 border-t border-hairline/60 pt-3">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className={cn(
              readerCommandControl,
              "inline-flex min-h-8 rounded border border-hairline bg-surface px-3 text-[0.72rem] text-ink-soft",
              "hover:bg-ink/[0.02]",
              dictionaryAINoteState.kind === "saving" && "cursor-wait opacity-60",
            )}
            onClick={onCreateAINote}
            disabled={dictionaryAINoteState.kind === "saving"}
          >
            <BookOpen aria-hidden="true" className="h-3.5 w-3.5 text-muted" />
            <span>AI 生成笔记</span>
          </button>
          {dictionaryAINoteState.kind === "saved" ? (
            <span className="text-[0.68rem] font-semibold text-structure-green">{dictionaryAINoteState.message}</span>
          ) : null}
          {dictionaryAINoteState.kind === "error" ? (
            <span className="text-[0.68rem] font-semibold text-exam-red">{dictionaryAINoteState.message}</span>
          ) : null}
        </div>
      </div>
    );
  }

  function renderCollapsedAIStub(mode: WebDictAIRequest["mode"], title: string, summary?: string | null) {
    if (dictionaryAIPanelOpen || dictionaryAI.kind !== "ready" || dictionaryAI.mode !== mode) {
      return null;
    }

    return (
      <button
        type="button"
        className={cn(readerPanelItem, "flex w-full rounded-[10px] border border-hairline/75 bg-ink/[0.015] px-4 py-3 text-left hover:bg-ink/[0.03]")}
        onClick={onToggleAIPanel}
        aria-label={`展开${title}`}
      >
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[0.68rem] font-semibold tracking-[0.08em] text-vocab-amber">
            <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
            <span>{title}</span>
          </div>
          {summary ? <p className="mt-1.5 line-clamp-2 text-[0.82rem] leading-6 text-ink-soft/88 select-none">{summary}</p> : null}
        </div>
        <ChevronDown aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-subtle" />
      </button>
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

    return (
      <div className="overflow-hidden rounded-[10px] border border-hairline/80 bg-ink/[0.015] px-4 py-3.5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 text-[0.68rem] font-semibold tracking-[0.08em] text-vocab-amber">
              <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
              <span>AI 语境解读</span>
            </div>
          </div>
            <button
              type="button"
              className={cn(readerIconAction, "inline-flex h-6 w-6 shrink-0 justify-center rounded-[0.55rem] p-0 text-muted")}
              onClick={onToggleAIPanel}
              aria-label="折叠 AI 语境解读"
            >
            <ChevronUp aria-hidden="true" className="h-3.5 w-3.5" />
          </button>
        </div>
        <div className="mt-3 select-text">
          <p className="max-w-[30rem] text-[0.9rem] leading-7 text-ink-soft font-medium">{contextExplainResult.summary}</p>
          {details.length > 0 ? (
            <div className="mt-4 border-t border-hairline/50 pt-3.5">
              <dl className="grid gap-3.5">
                {details.map((item) => (
                  <div key={item.label} className="grid gap-1">
                    <dt className="text-[0.64rem] font-bold tracking-[0.08em] text-subtle uppercase">{item.label}</dt>
                    <dd className="text-[0.84rem] leading-6 text-ink-soft/92">{item.value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ) : null}
          {renderAINoteAction()}
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

    const classificationLabel = dictionaryAIClassificationBadgeLabel(missingFallbackResult.classification);

    if (missingFallbackResult.kind === "ai_unresolved") {
      return (
        <div className="rounded-[8px] border border-hairline bg-ink/[0.01] px-4 py-3.5">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-1.5 text-[0.7rem] font-semibold text-vocab-amber">
                <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
                <span>未识别结果</span>
                <span className="rounded bg-ink/[0.04] px-1.5 py-0.5 text-[0.62rem] text-muted">
                  未验证
                </span>
                {classificationLabel ? (
                  <span className="rounded bg-ink/[0.04] px-1.5 py-0.5 text-[0.62rem] text-muted">
                    {classificationLabel}
                  </span>
                ) : null}
              </div>
              <p className="mt-2.5 text-xs leading-relaxed text-ink-soft select-text">{missingFallbackResult.summary}</p>
              {missingFallbackResult.reason ? (
                <p className="mt-1.5 text-[0.68rem] leading-normal text-muted select-text">{missingFallbackResult.reason}</p>
              ) : null}
            </div>
            <button
              type="button"
              className={cn(readerIconAction, "inline-flex h-6 w-6 shrink-0 justify-center rounded-[0.55rem] p-0 text-muted")}
              onClick={onToggleAIPanel}
              aria-label="折叠未识别结果"
            >
              <ChevronUp aria-hidden="true" className="h-3.5 w-3.5" />
            </button>
          </div>
          {renderAINoteAction()}
          {missingFallbackResult.suggestedQuery.length > 0 ? (
            <div className="mt-3.5 border-t border-hairline/60 pt-3 select-none">
              <p className="text-[0.66rem] font-bold tracking-wider text-subtle uppercase">换个词再查</p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {missingFallbackResult.suggestedQuery.map((query) => (
                  <button
                    key={query}
                    type="button"
                    className={cn(readerCommandControl, "inline-flex rounded border border-hairline bg-surface px-2.5 py-1 text-[0.68rem] text-ink hover:bg-ink/[0.02]")}
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
      <div className="rounded-[8px] border border-structure-green/20 bg-structure-green/[0.01] px-4 py-3.5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5 text-[0.7rem] font-semibold text-structure-green">
              <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
              <span>未验证词条</span>
              <span className="rounded bg-structure-green/[0.05] px-1.5 py-0.5 text-[0.62rem] text-structure-green/80">
                未验证
              </span>
              {classificationLabel ? (
                <span className="rounded bg-ink/[0.04] px-1.5 py-0.5 text-[0.62rem] text-muted">
                  {classificationLabel}
                </span>
              ) : null}
            </div>
            <h4 className="mt-2.5 font-headline text-[1.4rem] font-bold tracking-tight text-ink leading-none select-text">
              {missingFallbackResult.entry.word}
            </h4>
            {missingFallbackResult.entry.baseWord || missingFallbackResult.entry.phonetic ? (
              <div className="mt-2 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[0.75rem] text-muted font-mono leading-none">
                {missingFallbackResult.entry.baseWord &&
                normalizeDictionaryText(missingFallbackResult.entry.baseWord) !==
                  normalizeDictionaryText(missingFallbackResult.entry.word) ? (
                  <span className="rounded bg-ink/[0.03] px-1.5 py-0.5 text-[0.66rem] text-muted">
                    原形 {missingFallbackResult.entry.baseWord}
                  </span>
                ) : null}
                {missingFallbackResult.entry.phonetic ? <span>{missingFallbackResult.entry.phonetic}</span> : null}
              </div>
            ) : null}
          </div>
          <button
            type="button"
            className={cn(readerIconAction, "inline-flex h-6 w-6 shrink-0 justify-center rounded-[0.55rem] p-0 text-muted")}
            onClick={onToggleAIPanel}
            aria-label="折叠未验证词条"
          >
            <ChevronUp aria-hidden="true" className="h-3.5 w-3.5" />
          </button>
        </div>
        {renderAINoteAction()}

        {aiEntryTags.length > 0 ? (
          <div className="mt-3 flex flex-wrap gap-1 select-none">
            {aiEntryTags.slice(0, 3).map((item) => (
              <span key={item} className="rounded-[4px] bg-ink/[0.03] px-1.5 py-0.5 text-[0.66rem] font-medium text-muted">
                {item}
              </span>
            ))}
            {aiEntryTags.length > 3 ? (
              <span className="rounded-[4px] bg-ink/[0.03] px-1.5 py-0.5 text-[0.66rem] font-medium text-muted">+{aiEntryTags.length - 3}</span>
            ) : null}
          </div>
        ) : null}

        {missingFallbackResult.suggestedQuery.length > 0 ? (
          <div className="mt-3.5 border-t border-hairline/60 pt-3 select-none">
            <p className="text-[0.66rem] font-bold tracking-wider text-subtle uppercase">换个词再查</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {missingFallbackResult.suggestedQuery.map((query) => (
                <button
                  key={query}
                  type="button"
                  className={cn(readerCommandControl, "inline-flex rounded border border-hairline bg-surface px-2.5 py-1 text-[0.68rem] text-ink hover:bg-ink/[0.02]")}
                  onClick={() => onSelectAISuggestedQuery(query)}
                >
                  {query}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        {aiEntrySenseItems.length > 0 ? (
          <div className="mt-3.5 border-t border-hairline/60 pt-3">
            <p className="text-[0.66rem] font-bold tracking-wider text-subtle uppercase select-none">释义</p>
            <ol className="mt-2.5 space-y-3.5">
              {aiEntrySenseItems.slice(0, 4).map((sense, index) => (
                <li key={sense.key} className="flex items-start gap-3">
                  <span className="shrink-0 w-5 h-5 rounded-full border border-hairline/80 bg-ink/[0.02] text-ink-soft text-[0.68rem] font-semibold flex items-center justify-center mt-0.5 select-none">
                    {index + 1}
                  </span>
                  <div className="flex-1 min-w-0 select-text">
                    <div className="flex items-baseline flex-wrap gap-x-2">
                      {sense.partOfSpeech && (
                        <span className="inline-flex px-1.5 py-0.5 rounded-[4px] bg-ink/[0.03] text-muted text-[0.68rem] font-mono leading-none">
                          {sense.partOfSpeech}
                        </span>
                      )}
                      <p className="text-xs font-semibold text-ink leading-snug">
                        {sense.meaning}
                      </p>
                    </div>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        ) : null}
      </div>
    );
  }

  function renderEntryTabContent() {
    if (!entryResult) {
      return null;
    }

    if (activeTab === "meanings") {
      return (
        <div className="px-5 py-4">
          <ol className="space-y-6">
            {senseItems.map((sense) => (
              <li key={sense.key} className="flex flex-col">
                <div className="flex items-start gap-3">
                  <span className="shrink-0 w-5 h-5 rounded-full border border-hairline/80 bg-ink/[0.02] text-ink-soft text-[0.68rem] font-semibold flex items-center justify-center mt-0.5 select-none">
                    {sense.number}
                  </span>
                  <div className="flex-1 min-w-0 select-text">
                    <div className="flex items-baseline flex-wrap gap-x-2">
                      {sense.partOfSpeech && (
                        <span className="inline-flex px-1.5 py-0.5 rounded-[4px] bg-ink/[0.03] text-muted text-[0.68rem] font-mono leading-none">
                          {sense.partOfSpeech}
                        </span>
                      )}
                      <p className="text-[0.92rem] font-semibold text-ink leading-snug">
                        {sense.meaning}
                      </p>
                    </div>
                  </div>
                </div>

                {/* Inline example sentences nested directly below meaning */}
                {sense.examples.length > 0 && (
                  <div className="mt-3 ml-2.5 pl-4 border-l border-dotted border-hairline/90 space-y-3">
                    {sense.examples.map((example) => (
                      <figure key={example.key} className="space-y-0.5">
                        <blockquote className="font-serif italic text-[0.88rem] leading-relaxed text-ink-soft/90 pl-1 select-text">
                          • {example.example}
                        </blockquote>
                        {example.exampleTranslation && (
                          <figcaption className="text-[0.78rem] leading-normal text-muted/95 pl-3 select-text">
                            {example.exampleTranslation}
                          </figcaption>
                        )}
                      </figure>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ol>
        </div>
      );
    }

    if (activeTab === "phrases") {
      return (
        <div className="px-5 py-4 select-text">
          <div className="rounded-[8px] border border-hairline/75 bg-ink/[0.01] px-4">
            {entryResult.entry.phrases.map((phrase) => (
              <div
                key={phrase.phrase}
                className="border-b border-hairline/55 py-3 last:border-b-0"
              >
                <p className="min-w-0 text-[0.9rem] font-semibold leading-6 text-ink">{phrase.phrase}</p>
                {phrase.meaning ? (
                  <p className="mt-1 text-[0.82rem] leading-6 text-ink-soft/82">{phrase.meaning}</p>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      );
    }

    if (activeTab === "forms") {
      return (
        <div className="px-5 py-4 select-text">
          <div className="rounded-[6px] border border-hairline/80 bg-ink/[0.005] p-3.5">
            <div className="flex flex-wrap gap-1.5">
              {entryResult.entry.exchange.map((form) => (
                <span key={form} className="rounded-[4px] border border-hairline bg-surface px-2.5 py-0.5 text-xs font-semibold text-ink-soft select-all hover:border-vocab-amber/40 transition-colors">
                  {form}
                </span>
              ))}
            </div>
          </div>
        </div>
      );
    }

    return null;
  }

  function renderHistoryTimeline() {
    if (!history || history.length === 0) {
      return null;
    }

    return (
      <div className="mt-auto border-t border-hairline/70 bg-ink/[0.01]">
        <div className="flex items-center justify-between px-5 py-3 select-none">
          <button
            type="button"
            className={cn(
              readerPanelItem,
              "inline-flex rounded-[0.6rem] px-1.5 py-1 text-left text-[0.7rem] hover:bg-ink/[0.015] active:bg-ink/[0.03]",
            )}
            onClick={() => setHistoryCollapsed((prev) => !prev)}
            aria-expanded={!historyCollapsed}
          >
            <span className="text-[0.7rem] font-semibold tracking-[0.08em] text-muted">最近查阅</span>
            <span className="rounded-[4px] bg-ink/[0.04] px-1.5 py-0.5 text-[0.62rem] font-mono font-semibold text-muted">
              {history.length}
            </span>
            <ChevronDown
              aria-hidden="true"
              className={`h-3.5 w-3.5 text-muted transition-transform duration-200 ${
                !historyCollapsed ? "rotate-180" : ""
              }`}
            />
          </button>
        </div>
        {!historyCollapsed ? (
          <div className="px-5 pb-4 select-none">
            <div className="space-y-1.5 border-t border-hairline/50 pt-3">
              {history.map((item) => {
                const active =
                  lookup ? dictionaryLookupHistoryKey(lookup) === dictionaryLookupHistoryKey(item) : false;
                const summary = dictionaryLookupHistorySummary(item);

                return (
                  <button
                    key={dictionaryLookupHistoryKey(item)}
                    type="button"
                    className={cn(
                      readerPanelItem,
                      "group flex w-full items-start rounded-[8px] px-2.5 py-2 text-left",
                      active ? "bg-ink/[0.02] text-ink" : "hover:bg-ink/[0.015]",
                    )}
                    onClick={() => onSelectHistory?.(item)}
                    title={`${item.query}: ${summary}`}
                  >
                    <span
                      aria-hidden="true"
                      className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full transition-colors ${
                        active ? "bg-vocab-amber" : "bg-hairline group-hover:bg-muted"
                      }`}
                    />
                    <span className="min-w-0 flex-1">
                      <span className={`block truncate text-[0.8rem] font-semibold leading-none ${active ? "text-vocab-amber" : "text-ink group-hover:text-ink-soft"}`}>
                        {item.query}
                      </span>
                      <span className="mt-1 block line-clamp-1 text-[0.7rem] leading-none text-muted">
                        {summary}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        ) : null}
      </div>
    );
  }

  const scrollWrapperClass = entryResult
    ? "min-h-0 flex-1 overflow-hidden"
    : "min-h-0 flex-1 overflow-y-auto overscroll-contain";

  const collapsedContextExplainStub = canRequestContextExplain
    ? renderCollapsedAIStub("context_explain", "AI 语境解读", contextExplainResult?.summary)
    : null;
  const displayedTags = showAllTags ? displayTags : visibleTags;

  const panelSizing = isCard
    ? "h-full min-h-0"
    : onDismiss
      ? "h-full min-h-0"
      : lookup
        ? "min-h-[14rem] md:min-h-[18rem] xl:max-h-[calc(100vh-1.5rem)]"
        : "min-h-[14rem]";
  const panelWidthClass = isCard ? "w-full" : "w-full md:w-[26rem] xl:w-[28rem] 2xl:w-[30rem]";

  return (
    <TooltipProvider>
      <section className={`reader-tool-panel reader-dictionary-panel ${isCard ? "reader-dictionary-card" : ""} relative flex flex-col overflow-hidden ${panelWidthClass} shadow-surface-quiet bg-paper-warm border border-hairline/80 ${panelSizing}`}>
      
      {/* Absolute Folder Tab Index Handle (Decorative/Affordance) */}
      <div
        aria-hidden="true"
        className="absolute -right-4.5 top-1/2 -translate-y-1/2 hidden md:flex flex-col items-center justify-center w-4.5 h-20 bg-[#e4dcce] dark:bg-[#252422] border-t border-r border-b border-hairline rounded-r-[0.6rem] shadow-[3px_0_6px_rgba(0,0,0,0.04)] group cursor-pointer select-none transition-all duration-300 hover:translate-x-[2px] hover:bg-[#ded5c5] z-10"
      >
        <span className="text-[0.65rem] font-bold text-vocab-amber/80 group-hover:text-vocab-amber transition-colors">
          📖
        </span>
      </div>

      {/* Top Header Section with manual lookup entry & light tools */}
      <div className="flex items-center gap-2 border-b border-hairline/80 px-4.5 py-3 select-none">
        <div className="min-w-0 flex-1">
          {searchExpanded ? (
            <form onSubmit={handleSearchSubmit} className="relative flex min-w-0 items-center w-full">
              <Search className="absolute left-3 h-3.5 w-3.5 text-muted pointer-events-none" />
              <input
                ref={searchInputRef}
                className="w-full bg-surface pl-8.5 pr-8 py-1.5 rounded-[6px] border border-hairline/70 focus:border-vocab-amber/40 focus:ring-1 focus:ring-vocab-amber/20 outline-none text-xs text-ink placeholder:text-subtle/80 transition-all font-medium"
                value={searchQuery}
                onChange={(event) => onSearchQueryChange(event.target.value)}
                placeholder="搜索词典…"
                aria-label="搜索词典"
              />
              <button
                type="button"
                className={cn(readerIconAction, "absolute right-2.5 inline-flex h-5 w-5 justify-center rounded-[0.45rem] p-0 text-muted")}
                onClick={handleSearchClearOrCollapse}
                aria-label={searchQuery.trim() ? "清空搜索" : "收起搜索"}
              >
                <X className="h-3 w-3" />
              </button>
            </form>
          ) : (
            <DictionaryIconAction label="搜索词典" onClick={onToggleSearchExpanded}>
              <Search className="h-3.5 w-3.5" />
            </DictionaryIconAction>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-0.5">
          {onFeedback && (
            <DictionaryIconAction label="词典反馈" onClick={onFeedback}>
              <Flag className="h-3.5 w-3.5" />
            </DictionaryIconAction>
          )}
          {onTogglePinned && (
            <DictionaryIconAction
              label={pinned ? "取消钉住词典" : "钉住词典"}
              onClick={onTogglePinned}
              active={pinned}
              pressed={pinned}
              tone={pinned ? "accent" : "default"}
            >
              <Pin className="h-3.5 w-3.5" />
            </DictionaryIconAction>
          )}
          {onDismiss && (
            <DictionaryIconAction label="收起词典" onClick={onDismiss}>
              <X className="h-4 w-4" />
            </DictionaryIconAction>
          )}
        </div>
      </div>

      <ScrollWrapper scrollable={!entryResult && !isCard} className={scrollWrapperClass}>
        
        {/* Default empty state */}
        {!lookup && !inspectVisible ? (
          <div className="flex min-h-[16rem] flex-col justify-center px-6 text-center select-none">
            <span className="text-[1.8rem] mb-2">📖</span>
            <h3 className="font-headline text-[1.45rem] font-bold text-ink leading-tight">先从正文点一个词</h3>
            <p className="mt-2 text-xs leading-normal text-muted max-w-[24ch] mx-auto">点正文中的任意英文单词，或使用顶部搜索框直接查阅词典。</p>
          </div>
        ) : null}

        {/* Object Inspector Detail Card */}
        {inspectVisible && inspect ? (
          <div className="px-5 py-4">
            <ReaderStructuredInspectCard
              intent={inspect}
              onAttachToAsk={onAttachToAsk ? () => onAttachToAsk(inspect) : undefined}
              onLookupPhrase={onLookupPhraseFromInspect ? () => onLookupPhraseFromInspect(inspect) : undefined}
              onFeedback={onInspectFeedback ? () => onInspectFeedback(inspect) : undefined}
              variant="rail"
            />
          </div>
        ) : null}

        {/* Loading state spinner placeholder */}
        {lookup?.state.kind === "loading" ? (
          <div className="space-y-4 px-5 py-5 select-none">
            <div className="animate-pulse space-y-2">
              <div className="h-3 w-16 rounded bg-ink/[0.05]" />
              <div className="h-7 w-44 rounded bg-ink/[0.05]" />
            </div>
            <div className="rounded-[8px] border border-hairline bg-ink/[0.005] px-4 py-4 animate-pulse">
              <div className="h-3.5 w-24 rounded bg-ink/[0.05]" />
              <div className="mt-3 space-y-2">
                <div className="h-3 w-5/6 rounded bg-ink/[0.04]" />
                <div className="h-3 w-4/5 rounded bg-ink/[0.04]" />
              </div>
            </div>
          </div>
        ) : null}

        {/* Error state */}
        {lookup?.state.kind === "error" ? (
          <div className="space-y-4 px-5 py-5 select-none">
            <div>
              <p className="text-[0.66rem] font-bold tracking-wider text-muted uppercase">查询失败</p>
              <h3 className="mt-1 font-headline text-[1.8rem] font-bold tracking-tight text-ink leading-none">{lookup.query}</h3>
            </div>
            <div className="rounded-[8px] border border-exam-red/20 bg-exam-red/[0.01] px-4 py-3">
              <p className="text-xs leading-normal text-exam-red">{lookup.state.message}</p>
            </div>
          </div>
        ) : null}

        {/* Main word entry resolved state */}
        {lookup && entryResult ? (
          <div className="flex h-full min-h-0 flex-col">
            
            {/* Word Summary Section */}
            <div className="shrink-0">
              <div className="px-5 pt-5 pb-3 select-none">
                <div className="flex items-baseline gap-2.5 flex-wrap">
                  <h3 className="min-w-0 max-w-full break-words [overflow-wrap:anywhere] font-headline text-[2rem] sm:text-[2.15rem] xl:text-[2.3rem] font-bold tracking-tight text-ink leading-[0.98]">
                    {entryResult.entry.word}
                  </h3>
                  {phoneticLabel && (
                    <span className="text-xs text-muted font-mono leading-none tracking-wide select-text">
                      {phoneticLabel}
                    </span>
                  )}
                  <button
                    type="button"
                    className={cn(readerIconAction, "inline-flex items-center justify-center rounded-[0.5rem] p-1 text-muted")}
                    aria-label="发音"
                  >
                    <Volume2 className="h-3.5 w-3.5" />
                  </button>
                  {lemmaLabel || homographLabel ? (
                    <div className="flex flex-wrap items-center gap-1.5 text-[0.68rem] font-mono leading-none">
                      {lemmaLabel ? <span className="rounded bg-ink/[0.03] px-1.5 py-0.5 text-muted">原形 {lemmaWord}</span> : null}
                      {homographLabel ? <span className="rounded bg-ink/[0.03] px-1.5 py-0.5 text-muted">{homographLabel}</span> : null}
                    </div>
                  ) : null}
                </div>

                {glossaryTitle && glossaryText ? (
                  <div className="mt-4 rounded-[8px] border border-hairline/70 bg-ink/[0.015] px-3.5 py-3">
                    <p
                      className={`text-[0.68rem] font-semibold tracking-[0.08em] ${
                        lookup?.annotationType === "phrase_gloss" || lookup?.lookupType === "phrase"
                          ? "text-phrase-lavender"
                          : lookup?.annotationType === "context_gloss"
                            ? "text-context-blue"
                            : "text-vocab-amber"
                      }`}
                    >
                      {glossaryTitle}
                    </p>
                    <p className="mt-1.5 text-[0.86rem] leading-6 text-ink-soft/92 select-text">{glossaryText}</p>
                  </div>
                ) : null}

                <div className="mt-4 flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-t border-hairline/60 pt-3">
                  <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                    {displayedTags.map((item) => (
                      <span key={item} className="rounded-[4px] bg-ink/[0.03] px-1.5 py-0.5 text-[0.66rem] font-medium text-muted">
                        {item}
                      </span>
                    ))}
                    {hiddenTagCount > 0 && !showAllTags ? (
                      <button
                        type="button"
                        className="inline-flex rounded-[4px] border border-transparent bg-ink/[0.03] px-1.5 py-0.5 text-[0.66rem] font-medium text-muted transition-[background-color,color,border-color] duration-[var(--cl-duration-fast)] ease-[var(--cl-ease-standard)] hover:border-hairline/70 hover:bg-ink/[0.045] hover:text-ink"
                        onClick={() => setShowAllTags(true)}
                        aria-expanded={false}
                        aria-label={`展开剩余 ${hiddenTagCount} 个考试标签`}
                      >
                        +{hiddenTagCount}
                      </button>
                    ) : null}
                    {hiddenTagCount > 0 && showAllTags ? (
                      <button
                        type="button"
                        className={cn(readerIconAction, "inline-flex h-5 w-5 items-center justify-center rounded-[0.45rem] p-0 text-muted")}
                        onClick={() => setShowAllTags(false)}
                        aria-expanded
                        aria-label="收起考试标签"
                        title="收起考试标签"
                      >
                        <ChevronUp aria-hidden="true" className="h-3.5 w-3.5" />
                      </button>
                    ) : null}
                  </div>

                  <div className="flex items-center gap-1">
                    <DictionaryIconAction
                      label={
                        saveState.kind === "saved"
                          ? saveState.message || getSaveActionCopy(lookupSaveState, savedContextCount, "已加入生词本")
                          : saveState.kind === "error"
                            ? saveState.message || "加入生词本失败"
                            : getSaveActionCopy(lookupSaveState, savedContextCount)
                      }
                      onClick={onSave}
                      disabled={saveDisabled}
                      tone={
                        saveState.kind === "saved" || isSavedState
                          ? "saved"
                          : saveState.kind === "error"
                            ? "error"
                            : "default"
                      }
                    >
                      {saveState.kind === "saved" || isSavedState ? (
                        <Check className="h-3.5 w-3.5" />
                      ) : saveState.kind === "error" ? (
                        <X className="h-3.5 w-3.5" />
                      ) : (
                        <Tag className="h-3.5 w-3.5" />
                      )}
                    </DictionaryIconAction>

                    {canRequestContextExplain ? (
                      <DictionaryIconAction
                        label={dictionaryAIActionLabel("context_explain", dictionaryAI, dictionaryAIPanelOpen)}
                        onClick={() => onRequestAI("context_explain")}
                        disabled={dictionaryAI.kind === "loading" && dictionaryAI.mode === "context_explain"}
                        active={dictionaryAIPanelOpen && dictionaryAI.kind === "ready" && dictionaryAI.mode === "context_explain"}
                        tone="accent"
                      >
                        <Sparkles className="h-3.5 w-3.5" />
                      </DictionaryIconAction>
                    ) : null}

                    {canRequestAIFallback ? (
                      <DictionaryIconAction
                        label={dictionaryAIActionLabel("missing_fallback", dictionaryAI, dictionaryAIPanelOpen)}
                        onClick={() => onRequestAI("missing_fallback")}
                        disabled={dictionaryAI.kind === "loading" && dictionaryAI.mode === "missing_fallback"}
                        active={dictionaryAIPanelOpen && dictionaryAI.kind === "ready" && dictionaryAI.mode === "missing_fallback"}
                        tone="accent"
                      >
                        <Bot className="h-3.5 w-3.5" />
                      </DictionaryIconAction>
                    ) : null}
                  </div>
                </div>
              </div>

              {/* Main operational tabs selection layout */}
              {tabItems.length > 0 ? (
                <div className="border-b border-hairline/80 px-5 pt-3.5 select-none bg-ink/[0.005]">
                  <div className="flex gap-5">
                    {tabItems.map((item) => {
                      const active = item.id === activeTab;
                      return (
                        <button
                          key={item.id}
                          type="button"
                          className={cn(
                            readerTransitionFast,
                            "relative flex items-baseline gap-1 pb-2 text-[0.78rem] font-bold",
                            active ? "text-ink" : "text-muted hover:text-ink active:text-ink",
                          )}
                          onClick={() => setActiveTab(item.id)}
                          aria-pressed={active}
                        >
                          <span>{item.label}</span>
                          <span className="text-[0.64rem] opacity-75 font-mono font-medium">{item.count}</span>
                          {active && (
                            <div className="absolute bottom-0 left-0 right-0 h-[2px] bg-vocab-amber" />
                          )}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ) : null}
            </div>

            {/* Scrollable area showing tab specific contents */}
            <ScrollArea ref={entryScrollRef} className="min-h-0 flex-1">
              <div className="space-y-3.5 pb-4">
                {collapsedContextExplainStub ? <div className="px-5 pt-4">{collapsedContextExplainStub}</div> : null}
                {canRequestContextExplain && dictionaryAIPanelOpen ? (
                  <div className="px-5 pt-4">
                    {renderContextExplainCard()}
                  </div>
                ) : null}
                {renderEntryTabContent()}
              </div>
            </ScrollArea>
          </div>
        ) : null}

        {/* Disambiguation selection lists */}
        {lookup && disambiguationResult ? (
          <div className="space-y-5 px-5 py-5 select-none">
            <div>
              <p className="text-[0.66rem] font-bold tracking-wider text-muted uppercase">歧义选择</p>
              <h3 className="mt-1 font-headline text-[1.8rem] font-bold tracking-tight text-ink leading-none">{lookup.query}</h3>
            </div>
            <div className="space-y-5">
              {candidateGroups.map((group) => (
                <section key={group.key} className="border-t border-hairline/70 pt-4 first:border-t-0 first:pt-0">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <p className="text-[0.8rem] font-bold text-ink">{group.label}</p>
                      <p className="mt-0.5 text-[0.68rem] text-muted">{group.hint}</p>
                    </div>
                    <span className="rounded-[4px] bg-ink/[0.04] px-1.5 py-0.5 text-[0.62rem] font-mono font-semibold text-muted">{group.candidates.length}</span>
                  </div>
                  <div className="mt-2.5 space-y-2">
                    {group.candidates.map((candidate) => (
                      <button
                        key={candidate.entryId}
                        type="button"
                        className={cn(readerPanelItem, "block w-full rounded border border-hairline bg-surface px-4 py-2.5 text-left hover:bg-ink/[0.01]")}
                        onClick={() => onSelectCandidate(candidate.entryId)}
                      >
                        <div className="flex items-center justify-between gap-3">
                          <div className="min-w-0">
                            <p className="text-xs font-semibold text-ink leading-normal">{candidate.label}</p>
                            {candidate.preview ? <p className="mt-1 line-clamp-1 text-[0.68rem] text-muted">{candidate.preview}</p> : null}
                          </div>
                          <ChevronRight aria-hidden="true" className="h-4 w-4 shrink-0 text-subtle" />
                        </div>
                      </button>
                    ))}
                  </div>
                </section>
              ))}
            </div>
          </div>
        ) : null}

        {/* Not Found lists and AI fallbacks */}
        {lookup && notFoundResult ? (
          <div className="space-y-4 px-5 py-5">
            <div>
              <p className="text-[0.66rem] font-bold tracking-wider text-muted uppercase select-none">未收录结果</p>
              <h3 className="mt-1 font-headline text-[1.8rem] font-bold tracking-tight text-ink leading-none">{lookup.query}</h3>
            </div>
            <div className="space-y-3.5 rounded-[8px] border border-hairline bg-ink/[0.005] px-4 py-4">
              <p className="text-xs font-semibold text-ink select-none">当前词典没有匹配到这个词条。</p>
              {onNotFoundFeedback ? (
                <button
                  type="button"
                  className={cn(readerCommandControl, "mt-3 inline-flex rounded border border-hairline bg-surface px-3 py-1.5 text-[0.72rem] text-ink-soft hover:bg-ink/[0.02]")}
                  onClick={onNotFoundFeedback}
                >
                  反馈缺失
                </button>
              ) : null}
                  {canRequestMissingFallback ? (
                <div className="space-y-3.5 border-t border-hairline/60 pt-3.5">
                  {renderCollapsedAIStub(
                    "missing_fallback",
                    missingFallbackResult?.kind === "ai_unresolved" ? "未识别结果" : "未验证词条",
                    missingFallbackResult?.summary,
                  )}
                  {renderMissingFallbackCard()}
                </div>
              ) : null}
            </div>
          </div>
        ) : null}

        {/* Dictionary API generic errors */}
        {lookup && errorResult ? (
          <div className="space-y-4 px-5 py-5 select-none">
            <div>
              <p className="text-[0.66rem] font-bold tracking-wider text-muted uppercase">词典暂不可用</p>
              <h3 className="mt-1 font-headline text-[1.8rem] font-bold tracking-tight text-ink leading-none">{lookup.query}</h3>
            </div>
            <div className="rounded-[8px] border border-exam-red/20 bg-exam-red/[0.01] px-4 py-3">
              <p className="text-xs leading-normal text-exam-red">{errorResult.message}</p>
            </div>
          </div>
        ) : null}
      </ScrollWrapper>

      {/* integrated collapsible recent lookup timeline archive */}
        {renderHistoryTimeline()}
      </section>
    </TooltipProvider>
  );
}
