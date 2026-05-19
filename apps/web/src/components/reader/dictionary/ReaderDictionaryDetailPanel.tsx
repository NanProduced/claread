"use client";

import type { FormEvent } from "react";
import { useEffect, useRef, useState } from "react";
import {
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Pin,
  Search,
  Sparkles,
  Volume2,
  X,
} from "lucide-react";
import type { ReaderStructuredInspectIntent } from "@/lib/reader-plate";
import type { WebDictAIRequest, DictionaryAIViewState } from "@/types/api/dict-ai";
import type { SaveState, DictionaryLookupSnapshot } from "./contracts";
import { firstMeaning } from "./contracts";
import {
  type DictionaryContentTab,
  type DictionaryExampleGroup,
  type DictionarySenseItem,
  contextualGlossaryText,
  dictionaryAIActionLabel,
  dictionaryAIClassificationLabel,
  dictionaryAIConfidenceLabel,
  dictionaryAIClassificationLabel as dictionaryAIClassificationBadgeLabel,
  dictionaryAIRequestForLookup,
  dictionaryAITranslationVisible,
  dictionaryDisplayTags,
  dictionaryEntrySummary,
  dictionaryExampleGroups,
  dictionaryIsManualLookup,
  dictionarySenseItems,
  groupDisambiguationCandidates,
  isDictionaryAIErrorResult,
  normalizeDictionaryText,
} from "./shared";
import { ReaderStructuredInspectCard } from "./ReaderStructuredInspectCard";

interface ReaderDictionaryDetailPanelProps {
  lookup: DictionaryLookupSnapshot | null;
  inspect?: ReaderStructuredInspectIntent | null;
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
  onLookupPhraseFromInspect?: (intent: ReaderStructuredInspectIntent) => void;
  onAttachToAsk?: (intent: ReaderStructuredInspectIntent) => void;
}

export function ReaderDictionaryDetailPanel({
  canSaveVocabulary = true,
  dictionaryAI,
  dictionaryAIPanelOpen,
  inspect = null,
  lookup,
  onAttachToAsk,
  onDismiss,
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
  searchExpanded,
  searchQuery,
  variant = "sheet",
}: ReaderDictionaryDetailPanelProps) {
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
  const saveDisabled = saveState.kind === "saving" || saveState.kind === "saved" || !canSaveVocabulary;
  const primaryMeaning =
    (entryResult ? dictionaryEntrySummary(entryResult, lookup) : "") ||
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
  }, [activeTab, dictionaryAI.kind, dictionaryAIMode, dictionaryAIPanelOpen, inspect?.markId, lookup?.query, lookupResult?.kind]);

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
    const classificationLabel = dictionaryAIClassificationBadgeLabel(missingFallbackResult.classification);

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
              <span key={item} className="reader-dictionary-meta-tag">
                {item}
              </span>
            ))}
            {aiEntryTags.length > 3 ? (
              <span className="reader-dictionary-meta-tag reader-dictionary-meta-tag--count">+{aiEntryTags.length - 3}</span>
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
                  <p className="text-sm font-semibold leading-6 text-ink-soft">{group.meaning}</p>
                </section>
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
                {meaningsExpanded ? <ChevronUp aria-hidden="true" className="h-3.5 w-3.5" /> : <ChevronDown aria-hidden="true" className="h-3.5 w-3.5" />}
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
                    className={`focus-ring block w-full px-3.5 py-2.5 text-left transition-colors ${hasExamples ? "hover:bg-reader-paper/60" : "cursor-default"}`}
                    onClick={hasExamples ? () => toggleMeaningExpanded(sense.key) : undefined}
                    aria-expanded={hasExamples ? expanded : undefined}
                  >
                    <div className="flex items-start gap-3">
                      <span className="reader-dictionary-meta-tag min-w-[3rem] justify-center px-2 py-1">{sense.partOfSpeech || "词性"}</span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-start justify-between gap-2">
                          <p className="text-sm leading-6 text-ink-soft">{sense.meaning}</p>
                          <div className="flex shrink-0 items-center gap-2 pl-2">
                            {hasExamples ? (
                              <span className="reader-dictionary-meta-tag reader-dictionary-meta-tag--count px-2 py-0.5">{sense.examples.length} 例句</span>
                            ) : null}
                            <span className="text-[0.68rem] font-semibold text-subtle">{sense.number}</span>
                          </div>
                        </div>
                        {expanded ? (
                          <div className="mt-3 space-y-2 border-t border-hairline/60 pt-3">
                            {sense.examples.slice(0, 2).map((example) => (
                              <figure key={example.key} className="space-y-1">
                                <blockquote className="reader-serif text-[0.9rem] leading-6 text-ink-soft">{example.example}</blockquote>
                                {example.exampleTranslation ? (
                                  <figcaption className="text-xs leading-5 text-muted">{example.exampleTranslation}</figcaption>
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
              <p className="text-sm font-semibold leading-6 text-ink-soft">{group.meaning}</p>
              <ol className="mt-3 space-y-2.5">
                {group.examples.map((example) => (
                  <li key={example.key} className="rounded-[12px] bg-reader-paper/74 px-3 py-2.5">
                    <blockquote className="reader-serif text-[0.92rem] leading-6 text-ink-soft">{example.example}</blockquote>
                    {example.exampleTranslation ? <p className="mt-1 text-xs leading-5 text-muted">{example.exampleTranslation}</p> : null}
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
                {phrasesExpanded ? <ChevronUp aria-hidden="true" className="h-3.5 w-3.5" /> : <ChevronDown aria-hidden="true" className="h-3.5 w-3.5" />}
              </button>
            </div>
          ) : null}
          <div className="overflow-hidden rounded-[16px] border border-hairline/85 bg-surface/60">
            {visibleItems.map((phrase) => (
              <div key={phrase.phrase} className="flex items-start justify-between gap-3 border-t border-hairline/70 px-3.5 py-3 first:border-t-0">
                <p className="min-w-0 text-sm font-semibold leading-6 text-ink">{phrase.phrase}</p>
                {phrase.meaning ? <p className="max-w-[60%] text-right text-xs leading-5 text-muted">{phrase.meaning}</p> : <span className="text-[0.72rem] text-subtle">搭配</span>}
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
              {formsExpanded ? <ChevronUp aria-hidden="true" className="h-3.5 w-3.5" /> : <ChevronDown aria-hidden="true" className="h-3.5 w-3.5" />}
            </button>
          </div>
        ) : null}
        <div className="rounded-[14px] border border-hairline/80 bg-surface/62 px-3.5 py-3">
          <div className="flex flex-wrap gap-1.5">
            {visibleItems.map((form) => (
              <span key={form} className="rounded-pill border border-hairline bg-surface px-2.5 py-1 text-xs font-semibold text-ink-soft">
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

  const inspectVisible = Boolean(inspect && !lookup);

  return (
    <section className={`reader-tool-panel reader-dictionary-panel ${isCard ? "reader-dictionary-card" : ""} flex flex-col overflow-hidden ${panelSizing}`}>
      <div className="border-b border-hairline px-5 py-3.5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-ink">{inspectVisible ? "对象详情" : "词典"}</h2>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              className={`focus-ring reader-dictionary-toolbar-button inline-flex h-11 w-11 items-center justify-center rounded-full border md:h-9 md:w-9 ${searchExpanded ? "reader-dictionary-toolbar-button--active" : ""}`}
              onClick={onToggleSearchExpanded}
              aria-expanded={searchExpanded}
              aria-label={searchExpanded ? "收起手动搜索" : "展开手动搜索"}
            >
              <Search aria-hidden="true" className="h-4 w-4" />
            </button>
            {onTogglePinned ? (
              <button
                type="button"
                className={`focus-ring reader-dictionary-toolbar-button inline-flex h-11 w-11 items-center justify-center rounded-full border md:h-9 md:w-9 ${pinned ? "reader-dictionary-toolbar-button--active" : ""}`}
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
        {!lookup && !inspectVisible ? (
          <div className="flex min-h-40 flex-col justify-center">
            <p className="text-[0.72rem] font-semibold tracking-[0.08em] text-muted">默认状态</p>
            <h3 className="mt-2 reader-serif text-[1.8rem] leading-tight text-ink">先从正文点一个词</h3>
            <p className="mt-3 max-w-[24ch] text-sm leading-6 text-muted">点正文中的词，或直接搜索。</p>
          </div>
        ) : null}

        {inspectVisible && inspect ? (
          <ReaderStructuredInspectCard
            intent={inspect}
            onAttachToAsk={onAttachToAsk ? () => onAttachToAsk(inspect) : undefined}
            onLookupPhrase={onLookupPhraseFromInspect ? () => onLookupPhraseFromInspect(inspect) : undefined}
            variant="rail"
          />
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
                    <h3 className="reader-serif text-[clamp(1.95rem,3vw,2.45rem)] leading-[0.98] tracking-[-0.025em] text-ink">{entryResult.entry.word}</h3>
                    {lemmaLabel || phoneticLabel || homographLabel ? (
                      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.79rem] leading-5 text-muted">
                        {lemmaLabel ? <span className="rounded-pill border border-hairline/90 bg-reader-paper/84 px-2 py-0.5 text-[0.7rem] font-semibold text-muted">{lemmaLabel}</span> : null}
                        {phoneticLabel ? <span>{phoneticLabel}</span> : null}
                        {homographLabel ? <span className="rounded-pill border border-hairline/90 bg-reader-paper/84 px-2 py-0.5 text-[0.7rem] font-semibold text-muted">{homographLabel}</span> : null}
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
                          className={`focus-ring reader-dictionary-inline-action inline-flex h-10 w-10 items-center justify-center rounded-full border md:h-9 md:w-9 ${dictionaryAI.kind === "loading" && dictionaryAI.mode === "context_explain" ? "reader-dictionary-inline-action--accent cursor-wait" : dictionaryAIPanelOpen && dictionaryAI.kind === "ready" && dictionaryAI.mode === "context_explain" ? "reader-dictionary-inline-action--accent" : ""}`}
                          onClick={() => onRequestAI("context_explain")}
                          disabled={dictionaryAI.kind === "loading" && dictionaryAI.mode === "context_explain"}
                          aria-label={dictionaryAIActionLabel("context_explain", dictionaryAI, dictionaryAIPanelOpen)}
                          aria-pressed={dictionaryAIPanelOpen && dictionaryAI.kind === "ready" && dictionaryAI.mode === "context_explain"}
                          title={dictionaryAIActionLabel("context_explain", dictionaryAI, dictionaryAIPanelOpen)}
                        >
                          <Sparkles aria-hidden="true" className="h-4 w-4" />
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className={`focus-ring reader-dictionary-inline-action inline-flex h-10 w-10 items-center justify-center rounded-full border md:h-9 md:w-9 ${saveState.kind === "saved" ? "reader-dictionary-inline-action--saved" : saveState.kind === "error" ? "reader-dictionary-inline-action--error" : ""}`}
                        onClick={onSave}
                        disabled={saveDisabled}
                      >
                        {saveState.kind === "saved" ? <Check aria-hidden="true" className="h-4 w-4" /> : <BookOpen aria-hidden="true" className="h-4 w-4" />}
                      </button>
                    </div>
                  </div>
                </div>
                {(displayTags.length > 0 || saveState.kind === "saved" || saveState.kind === "error") ? (
                  <div className="space-y-2">
                    {displayTags.length > 0 ? (
                      <div className="flex flex-wrap items-center gap-1.5">
                        {visibleTags.map((item) => <span key={item} className="reader-dictionary-meta-tag">{item}</span>)}
                        {hiddenTagCount > 0 ? <span className="reader-dictionary-meta-tag reader-dictionary-meta-tag--count">+{hiddenTagCount}</span> : null}
                      </div>
                    ) : null}
                    {saveState.kind === "saved" ? <p className="text-[0.76rem] font-semibold text-structure-green">{saveState.message}</p> : null}
                    {saveState.kind === "error" ? <p className="text-[0.76rem] font-semibold text-error-red">{saveState.message}</p> : null}
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
                        className={`focus-ring reader-dictionary-tab inline-flex min-h-10 min-w-[4.85rem] flex-1 items-center justify-center gap-1.5 rounded-[11px] border border-transparent px-3 py-1.5 text-[0.76rem] font-semibold md:min-h-8 ${item.id === activeTab ? "reader-dictionary-tab--active" : ""}`}
                        onClick={() => setActiveTab(item.id)}
                        aria-pressed={item.id === activeTab}
                      >
                        <span>{item.label}</span>
                        <span className={`reader-dictionary-tab-count text-[0.66rem] ${item.id === activeTab ? "reader-dictionary-tab-count--active" : ""}`}>{item.count}</span>
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
                    <span className="rounded-pill bg-reader-paper px-2.5 py-1 text-[0.68rem] font-semibold text-muted">{group.candidates.length}</span>
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
                            {candidate.preview ? <p className="mt-2 text-sm leading-6 text-muted">{candidate.preview}</p> : null}
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
                    className={`focus-ring reader-dictionary-secondary-button inline-flex min-h-10 items-center gap-2 rounded-[12px] border px-3.5 text-xs font-semibold md:min-h-9 ${dictionaryAI.kind === "loading" && dictionaryAI.mode === "missing_fallback" ? "reader-dictionary-secondary-button--active cursor-wait" : ""}`}
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
