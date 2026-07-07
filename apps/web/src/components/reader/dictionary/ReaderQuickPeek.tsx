"use client";

import { useId } from "react";
import type { CSSProperties, ReactNode } from "react";
import { BookOpen, Bot, ChevronRight, Flag, Sparkles, X } from "lucide-react";
import { readerIconAction } from "@/components/reader/interaction";
import { cn } from "@/lib/cn";
import type { ReaderStructuredInspectIntent } from "@/lib/reader-plate";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/primitives/tooltip";
import { ReaderFloatingSurface } from "../ReaderFloatingLayer";
import type { DictionaryLookupSnapshot } from "./contracts";
import { firstMeaning } from "./contracts";
import {
  contextualGlossaryExample,
  contextualGlossaryExampleTranslation,
  contextualGlossaryReason,
  contextualGlossaryText,
  contextualGlossaryTitle,
  dictionaryAIActionLabel,
  phraseGlossarySubtypeLabel,
  structuredInspectCategoryLabel,
  structuredInspectToneClass,
} from "./shared";
import type { DictionaryAIViewState, WebDictAIRequest } from "@/types/api/dict-ai";
import { ReaderStructuredInspectCard } from "./ReaderStructuredInspectCard";

function getInspectColorClass(annotationType: string) {
  return structuredInspectToneClass(annotationType);
}

function PeekIconAction({
  children,
  label,
  onClick,
}: {
  children: ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          className={cn(readerIconAction, "h-8 w-8 rounded-[0.7rem] cursor-pointer")}
          onClick={onClick}
          aria-label={label}
        >
          {children}
        </button>
      </TooltipTrigger>
      <TooltipContent side="top">{label}</TooltipContent>
    </Tooltip>
  );
}

interface ReaderQuickPeekProps {
  lookup?: DictionaryLookupSnapshot | null;
  inspect?: ReaderStructuredInspectIntent | null;
  className?: string;
  floatingRef?: (node: HTMLDivElement | null) => void;
  style?: CSSProperties;
  onDismiss: () => void;
  onOpenDetail?: () => void;
  onLookupPhrase?: () => void;
  onSelectCandidate?: (entryId: number) => void;
  onAttachToAsk?: () => void;
  onFeedback?: () => void;
  onRequestAI?: (mode: WebDictAIRequest["mode"]) => void;
  dictionaryAI?: DictionaryAIViewState;
}

interface ReaderQuickPeekShellProps {
  className?: string;
  titleId: string;
  title: ReactNode;
  eyebrow?: ReactNode;
  eyebrowClassName?: string;
  aside?: ReactNode;
  bodyId?: string;
  body?: ReactNode;
  footer?: ReactNode;
  floatingRef?: (node: HTMLDivElement | null) => void;
  style?: CSSProperties;
  onDismiss: () => void;
}

function ReaderQuickPeekShell({
  aside,
  body,
  bodyId,
  className,
  footer,
  eyebrow,
  eyebrowClassName = "text-muted",
  floatingRef,
  onDismiss,
  style,
  title,
  titleId,
}: ReaderQuickPeekShellProps) {
  return (
    <ReaderFloatingSurface
      floatingRef={floatingRef}
      className={`reader-lookup-preview ${className ?? ""}`.trim()}
      role="dialog"
      aria-modal="false"
      aria-labelledby={titleId}
      aria-describedby={bodyId}
      style={style}
      onClick={(event) => event.stopPropagation()}
      onPointerDown={(event) => event.stopPropagation()}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          onDismiss();
        }
      }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          {eyebrow ? (
            <div className={`text-[0.7rem] font-semibold tracking-[0.12em] ${eyebrowClassName}`}>
              {eyebrow}
            </div>
          ) : null}
          <div id={titleId} className="mt-1 text-[1.12rem] font-semibold leading-tight text-foreground">
            {title}
          </div>
        </div>
        <div className="flex shrink-0 items-start gap-2">
          {aside}
          <button
            type="button"
            className={cn(readerIconAction, "h-6 w-6 rounded-[0.55rem] text-subtle")}
            onClick={onDismiss}
            aria-label="关闭预览卡片"
          >
            <X aria-hidden="true" className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
      {body ? (
        <div id={bodyId} className="mt-3">
          {body}
        </div>
      ) : null}
      {footer ? (
        <div className="mt-2 flex items-center justify-between gap-2 border-t border-hairline/45 pt-2">
          {footer}
        </div>
      ) : null}
    </ReaderFloatingSurface>
  );
}

export function ReaderQuickPeek({
  className,
  floatingRef,
  inspect = null,
  lookup = null,
  onDismiss,
  onAttachToAsk,
  onFeedback,
  onLookupPhrase,
  onOpenDetail,
  onSelectCandidate,
  onRequestAI,
  dictionaryAI,
  style,
}: ReaderQuickPeekProps) {
  const inspectTitleId = useId();
  const lookupTitleId = useId();
  const lookupBodyId = useId();

  if (inspect) {
    const inspectDisplayText = inspect.lookupText ?? inspect.anchorText;
    const inspectSubtype = phraseGlossarySubtypeLabel(inspect.glossary);
    const showLookupPhrase =
      Boolean(onLookupPhrase) &&
      inspect.annotationType !== "phrase_gloss" &&
      inspect.annotationType !== "context_gloss";
    return (
      <TooltipProvider>
        <ReaderQuickPeekShell
          className={className}
          floatingRef={floatingRef}
          titleId={inspectTitleId}
          eyebrow={
            <span className="inline-flex items-center gap-1.5">
              <span>{structuredInspectCategoryLabel(inspect.annotationType)}</span>
              {inspectSubtype ? (
                <span className="rounded-[5px] bg-ink/[0.045] px-1.5 py-0.5 text-[0.62rem] font-semibold tracking-normal text-muted">
                  {inspectSubtype}
                </span>
              ) : null}
            </span>
          }
          eyebrowClassName={getInspectColorClass(inspect.annotationType)}
          title={inspectDisplayText}
          body={
            <ReaderStructuredInspectCard
              intent={inspect}
              onFeedback={onFeedback}
              onLookupPhrase={showLookupPhrase ? onLookupPhrase : undefined}
              onAttachToAsk={onAttachToAsk}
              variant="peek"
            />
          }
          footer={
            <div className="flex w-full items-center justify-end gap-1">
              {showLookupPhrase && onLookupPhrase ? (
                <PeekIconAction label="查短语" onClick={onLookupPhrase}>
                  <BookOpen className="h-3.5 w-3.5" />
                </PeekIconAction>
              ) : null}
              {onAttachToAsk ? (
                <PeekIconAction label="带入 Ask" onClick={onAttachToAsk}>
                  <Sparkles className="h-3.5 w-3.5" />
                </PeekIconAction>
              ) : null}
              {onOpenDetail ? (
                <PeekIconAction label="打开词典" onClick={onOpenDetail}>
                  <BookOpen className="h-3.5 w-3.5" />
                </PeekIconAction>
              ) : null}
              {onFeedback ? (
                <PeekIconAction label="反馈" onClick={onFeedback}>
                  <Flag className="h-3.5 w-3.5" />
                </PeekIconAction>
              ) : null}
            </div>
          }
          style={style}
          onDismiss={onDismiss}
        />
      </TooltipProvider>
    );
  }

  if (!lookup) {
    return null;
  }

  const glossaryTitle = contextualGlossaryTitle(lookup);
  const glossaryText = contextualGlossaryText(lookup.glossary);
  const glossarySubtype = phraseGlossarySubtypeLabel(lookup.glossary);
  const glossaryExample = contextualGlossaryExample(lookup.glossary);
  const glossaryExampleTranslation = contextualGlossaryExampleTranslation(lookup.glossary);
  const glossaryReason = contextualGlossaryReason(lookup.glossary);
  const isVocabHighlight = lookup.annotationType === "vocab_highlight";
  const result = lookup.state.kind === "ready" ? lookup.state.result : null;
  const entryResult = result?.kind === "entry" ? result : null;
  const disambiguationResult = result?.kind === "disambiguation" ? result : null;
  const notFoundResult = result?.kind === "not_found" ? result : null;
  const previewMeaning = entryResult ? firstMeaning(entryResult) : "";
  const glossaryDuplicatesPreview =
    isVocabHighlight &&
    Boolean(glossaryText.trim()) &&
    Boolean(previewMeaning.trim()) &&
    glossaryText.trim().toLowerCase() === previewMeaning.trim().toLowerCase();
  const compactCandidates = disambiguationResult?.candidates.slice(0, 2) ?? [];
  const hiddenCandidateCount = Math.max((disambiguationResult?.candidates.length ?? 0) - compactCandidates.length, 0);
  const hasVocabReadingHint =
    isVocabHighlight &&
    ((!glossaryDuplicatesPreview && Boolean(glossaryText)) || Boolean(glossaryReason));
  const lookupEyebrow = lookup.label ?? (lookup.lookupType === "phrase" ? "短语" : "词典");
  const lookupEyebrowClassName = lookup.annotationType
    ? getInspectColorClass(lookup.annotationType)
    : lookup.lookupType === "phrase"
      ? "text-phrase-lavender"
      : "text-muted";
  const canRequestMissingFallback = Boolean(
    onRequestAI &&
      notFoundResult &&
      lookup.contextSentence.trim() &&
      lookup.sentenceId !== "__manual__" &&
      lookup.label !== "手动查词",
  );
  const errorMessage =
    lookup.state.kind === "error"
      ? lookup.state.message
      : result?.kind === "error"
        ? result.message
        : "";
  const mergedClassName = className ?? "";

  return (
    <TooltipProvider>
      <ReaderQuickPeekShell
        className={mergedClassName}
        floatingRef={floatingRef}
        titleId={lookupTitleId}
        eyebrow={lookupEyebrow}
        eyebrowClassName={lookupEyebrowClassName}
        title={
          <div className="flex max-w-full flex-wrap items-baseline gap-x-2">
            <span className="block max-w-full break-words [overflow-wrap:anywhere] reader-serif text-[1.28rem] leading-tight text-ink">
              {entryResult?.entry.word ?? lookup.query}
            </span>
            {entryResult?.entry.phonetic ? (
              <span className="text-xs text-muted font-sans font-normal tracking-normal">{entryResult.entry.phonetic}</span>
            ) : null}
          </div>
        }
        bodyId={lookupBodyId}
        body={
          <>
            {isVocabHighlight && (entryResult || disambiguationResult || notFoundResult || hasVocabReadingHint) ? (
              <span className="mt-3 block rounded-[8px] border border-hairline/65 bg-ink/[0.01] px-3 py-2.5">
                {entryResult ? (
                  <>
                    <span className="block text-[0.68rem] font-semibold tracking-[0.08em] text-muted">
                      词典释义
                    </span>
                    <span className="mt-1.5 block text-[0.86rem] leading-6 text-ink-soft">
                      {previewMeaning || "当前词条暂无简短释义，打开词典可查看完整信息。"}
                    </span>
                  </>
                ) : null}
                {disambiguationResult ? (
                  <>
                    <span className="block text-[0.68rem] font-semibold tracking-[0.08em] text-muted">
                      选择候选词条
                    </span>
                    <span className="mt-2 block space-y-1">
                      {compactCandidates.map((candidate) => (
                        <button
                          key={candidate.entryId}
                          type="button"
                          className="group flex w-full items-center justify-between gap-3 rounded-[7px] border border-hairline/65 bg-surface px-2.5 py-1.5 text-left transition-colors hover:bg-ink/[0.015] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-vocab-amber/30"
                          onClick={() => onSelectCandidate?.(candidate.entryId)}
                          disabled={!onSelectCandidate}
                        >
                          <span className="min-w-0">
                            <span className="block truncate text-xs font-semibold leading-5 text-ink">
                              {candidate.label}
                            </span>
                            {candidate.preview ? (
                              <span className="block truncate text-[0.68rem] leading-4 text-muted">
                                {candidate.preview}
                              </span>
                            ) : null}
                          </span>
                          <ChevronRight aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-subtle transition-colors group-hover:text-muted" />
                        </button>
                      ))}
                    </span>
                    {hiddenCandidateCount > 0 ? (
                      <span className="mt-1.5 block text-xs leading-5 text-muted">
                        另有 {hiddenCandidateCount} 个候选，打开词典查看完整列表。
                      </span>
                    ) : null}
                  </>
                ) : null}
                {notFoundResult ? (
                  <span className="block text-sm leading-6 text-muted">当前词典暂未收录。</span>
                ) : null}
                {hasVocabReadingHint ? (
                  <span className={`${entryResult || disambiguationResult || notFoundResult ? "mt-2 border-t border-hairline/50 pt-2" : ""} block`}>
                    <span className="block text-[0.68rem] font-semibold tracking-[0.08em] text-vocab-amber">
                      阅读提示
                    </span>
                    {glossaryText && !glossaryDuplicatesPreview ? (
                      <span className="mt-1 block text-[0.82rem] leading-5 text-ink-soft">{glossaryText}</span>
                    ) : null}
                    {glossaryReason ? (
                      <span className="mt-1 block text-xs leading-5 text-muted">{glossaryReason}</span>
                    ) : null}
                  </span>
                ) : null}
              </span>
            ) : null}

            {glossaryTitle && glossaryText && !isVocabHighlight ? (
              <span className="block rounded-[8px] border border-hairline/70 bg-ink/[0.015] px-3 py-2.5">
                <span
                  className={`block text-[0.68rem] font-semibold tracking-[0.08em] ${
                    lookup.annotationType === "context_gloss"
                      ? "text-context-blue"
                      : lookup.annotationType === "phrase_gloss" || lookup.lookupType === "phrase"
                        ? "text-phrase-lavender"
                        : "text-vocab-amber"
                  }`}
                >
                  {lookup.annotationType === "phrase_gloss" ? "短语" : glossaryTitle}
                  {glossarySubtype ? (
                    <span className="ml-1.5 rounded-[5px] bg-ink/[0.04] px-1.5 py-0.5 text-[0.62rem] font-semibold tracking-normal text-muted">
                      {glossarySubtype}
                    </span>
                  ) : null}
                </span>
                <span className="mt-1.5 block text-[0.86rem] leading-6 text-ink-soft">{glossaryText}</span>
                {glossaryExample ? (
                  <span className="mt-2 block border-t border-hairline/50 pt-2">
                    <span className="block text-[0.68rem] font-semibold text-muted">例句</span>
                    <span className="mt-1 block text-xs leading-5 text-ink-soft">{glossaryExample}</span>
                    {glossaryExampleTranslation ? (
                      <span className="mt-0.5 block text-[0.72rem] leading-5 text-muted">
                        {glossaryExampleTranslation}
                      </span>
                    ) : null}
                  </span>
                ) : null}
                {glossaryReason ? (
                  <span className="mt-2 block text-xs leading-5 text-muted">
                    {glossaryReason}
                  </span>
                ) : null}
              </span>
            ) : null}

            {lookup.state.kind === "loading" ? (
              <span className="mt-3 block text-sm leading-6 text-muted">正在查词...</span>
            ) : null}

            {entryResult && !glossaryText && !isVocabHighlight ? (
              <span className="mt-3 block text-sm leading-6 text-ink-soft">
                {previewMeaning || "当前词条暂无简短释义，打开详情可查看完整信息。"}
              </span>
            ) : null}

            {disambiguationResult && !isVocabHighlight ? (
              <span className="mt-3 block">
                <span className="block text-[0.72rem] font-semibold tracking-[0.08em] text-muted">
                  选择候选词条
                </span>
                <span className="mt-2 block space-y-1.5">
                  {compactCandidates.map((candidate) => (
                    <button
                      key={candidate.entryId}
                      type="button"
                      className="group flex w-full items-center justify-between gap-3 rounded-[7px] border border-hairline/65 bg-surface px-2.5 py-1.5 text-left transition-colors hover:bg-ink/[0.015] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-vocab-amber/30"
                      onClick={() => onSelectCandidate?.(candidate.entryId)}
                      disabled={!onSelectCandidate}
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-xs font-semibold leading-5 text-ink">
                          {candidate.label}
                        </span>
                        {candidate.preview ? (
                          <span className="block truncate text-[0.68rem] leading-4 text-muted">
                            {candidate.preview}
                          </span>
                        ) : null}
                      </span>
                      <ChevronRight
                        aria-hidden="true"
                        className="h-3.5 w-3.5 shrink-0 text-subtle transition-colors group-hover:text-muted"
                      />
                    </button>
                  ))}
                </span>
                {hiddenCandidateCount > 0 ? (
                  <span className="mt-2 block text-xs leading-5 text-muted">
                    另有 {hiddenCandidateCount} 个候选，打开词典查看完整列表。
                  </span>
                ) : null}
              </span>
            ) : null}

            {notFoundResult && !isVocabHighlight ? (
              <span className="mt-3 block text-sm leading-6 text-muted">当前词典暂未收录，可用 AI 补充词义。</span>
            ) : null}

            {errorMessage ? (
              <span className="mt-3 block text-sm leading-6 text-error-red">{errorMessage}</span>
            ) : null}

            {canRequestMissingFallback ? (
              <div className="mt-3 flex items-center">
                <PeekIconAction
                  label={dictionaryAI ? dictionaryAIActionLabel("missing_fallback", dictionaryAI, false) : "AI 补充词义"}
                  onClick={() => onRequestAI?.("missing_fallback")}
                >
                  <Bot aria-hidden="true" className="h-3.5 w-3.5 text-vocab-amber" />
                </PeekIconAction>
              </div>
            ) : null}
          </>
        }
        footer={
          onOpenDetail ? (
            <div className="flex w-full items-center justify-end gap-1">
              <PeekIconAction label="打开词典" onClick={onOpenDetail}>
                <BookOpen className="h-3.5 w-3.5" />
              </PeekIconAction>
            </div>
          ) : undefined
        }
        style={style}
        onDismiss={onDismiss}
      />
    </TooltipProvider>
  );
}
