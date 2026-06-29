"use client";

import { useId } from "react";
import type { CSSProperties, ReactNode } from "react";
import { BookOpen, Bot, Flag, Search, Sparkles, X } from "lucide-react";
import { readerIconAction } from "@/components/reader/interaction";
import { cn } from "@/lib/cn";
import type { ReaderStructuredInspectIntent } from "@/lib/reader-plate";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/primitives/tooltip";
import { ReaderFloatingSurface } from "../ReaderFloatingLayer";
import type { DictionaryLookupSnapshot } from "./contracts";
import { firstMeaning } from "./contracts";
import { contextualGlossaryText, contextualGlossaryTitle, dictionaryAIActionLabel, structuredInspectLabel } from "./shared";
import type { DictionaryAIViewState, WebDictAIRequest } from "@/types/api/dict-ai";
import { ReaderStructuredInspectCard } from "./ReaderStructuredInspectCard";

function getInspectColorClass(annotationType: string) {
  if (annotationType === "phrase_gloss") {
    return "text-phrase-lavender";
  }
  if (annotationType === "context_gloss") {
    return "text-context-blue";
  }
  return "text-muted";
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
          title={label}
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
        <div className="mt-3 border-t border-hairline/60 pt-2.5">
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
  onRequestAI,
  dictionaryAI,
  style,
}: ReaderQuickPeekProps) {
  const inspectTitleId = useId();
  const lookupTitleId = useId();
  const lookupBodyId = useId();

  if (inspect) {
    const inspectDisplayText = inspect.lookupText ?? inspect.anchorText;
    return (
      <TooltipProvider>
        <ReaderQuickPeekShell
          className={className}
          floatingRef={floatingRef}
          titleId={inspectTitleId}
          eyebrow={structuredInspectLabel(inspect.annotationType, inspect.glossary?.phraseType)}
          eyebrowClassName={getInspectColorClass(inspect.annotationType)}
          title={inspectDisplayText}
          body={
            <ReaderStructuredInspectCard
              intent={inspect}
              onFeedback={onFeedback}
              onLookupPhrase={onLookupPhrase}
              onAttachToAsk={onAttachToAsk}
              variant="peek"
            />
          }
          footer={
            <div className="flex items-center gap-1">
              {onLookupPhrase ? (
                <PeekIconAction label="查短语" onClick={onLookupPhrase}>
                  <Search className="h-3.5 w-3.5" />
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
  const result = lookup.state.kind === "ready" ? lookup.state.result : null;
  const entryResult = result?.kind === "entry" ? result : null;
  const disambiguationResult = result?.kind === "disambiguation" ? result : null;
  const notFoundResult = result?.kind === "not_found" ? result : null;
  const previewMeaning = entryResult ? firstMeaning(entryResult) : "";
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
  const compactPreview =
    Boolean(
      entryResult &&
        !glossaryText &&
        !disambiguationResult &&
        !notFoundResult &&
        !errorMessage &&
        lookup.lookupType === "word" &&
        previewMeaning &&
        previewMeaning.length <= 26 &&
        (entryResult.entry.word ?? lookup.query).length <= 18,
    );
  const mergedClassName = [className, compactPreview ? "reader-lookup-preview--compact" : null]
    .filter(Boolean)
    .join(" ");

  return (
    <TooltipProvider>
      <ReaderQuickPeekShell
        className={mergedClassName}
        floatingRef={floatingRef}
        titleId={lookupTitleId}
        eyebrow={lookup.label ?? (lookup.lookupType === "phrase" ? "短语" : "词典")}
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
            {glossaryTitle && glossaryText ? (
              <span className="block rounded-[8px] border border-hairline/70 bg-ink/[0.015] px-3 py-2.5">
                <span
                  className={`block text-[0.68rem] font-semibold tracking-[0.08em] ${
                    lookup.annotationType === "phrase_gloss" || lookup.lookupType === "phrase"
                      ? "text-phrase-lavender"
                      : lookup.annotationType === "context_gloss"
                        ? "text-context-blue"
                        : "text-vocab-amber"
                  }`}
                >
                  {glossaryTitle}
                </span>
                <span className="mt-1.5 block text-[0.86rem] leading-6 text-ink-soft">{glossaryText}</span>
              </span>
            ) : null}

            {lookup.state.kind === "loading" ? (
              <span className="mt-3 block text-sm leading-6 text-muted">正在查词...</span>
            ) : null}

            {entryResult && !glossaryText ? (
              <span className="mt-3 block text-sm leading-6 text-ink-soft">
                {previewMeaning || "当前词条暂无简短释义，打开详情可查看完整信息。"}
              </span>
            ) : null}

            {disambiguationResult ? (
              <span className="mt-3 block text-sm leading-6 text-muted">多个候选词条，打开详情继续选择。</span>
            ) : null}

            {notFoundResult ? (
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
            <div className="flex items-center gap-1">
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
