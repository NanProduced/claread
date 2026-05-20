"use client";

import { useId } from "react";
import type { CSSProperties, ReactNode } from "react";
import { ChevronRight, X } from "lucide-react";
import type { ReaderStructuredInspectIntent } from "@/lib/reader-plate";
import { ReaderFloatingSurface } from "../ReaderFloatingLayer";
import type { DictionaryLookupSnapshot } from "./contracts";
import { firstMeaning } from "./contracts";
import { contextualGlossaryText, contextualGlossaryTitle } from "./shared";
import { ReaderStructuredInspectCard } from "./ReaderStructuredInspectCard";

interface ReaderQuickPeekProps {
  lookup?: DictionaryLookupSnapshot | null;
  inspect?: ReaderStructuredInspectIntent | null;
  floatingRef?: (node: HTMLDivElement | null) => void;
  style?: CSSProperties;
  onDismiss: () => void;
  onOpenDetail?: () => void;
  onLookupPhrase?: () => void;
  onAttachToAsk?: () => void;
}

interface ReaderQuickPeekShellProps {
  titleId: string;
  title: ReactNode;
  eyebrow?: ReactNode;
  aside?: ReactNode;
  bodyId?: string;
  body?: ReactNode;
  floatingRef?: (node: HTMLDivElement | null) => void;
  style?: CSSProperties;
  onDismiss: () => void;
}

function ReaderQuickPeekShell({
  aside,
  body,
  bodyId,
  eyebrow,
  floatingRef,
  onDismiss,
  style,
  title,
  titleId,
}: ReaderQuickPeekShellProps) {
  return (
    <ReaderFloatingSurface
      floatingRef={floatingRef}
      className="reader-lookup-preview"
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
            <div className="text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-muted">
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
            className="focus-ring inline-flex h-10 w-10 items-center justify-center rounded-full border border-hairline bg-surface text-muted transition-colors hover:border-muted hover:text-ink"
            onClick={onDismiss}
            aria-label="关闭预览卡片"
          >
            <X aria-hidden="true" className="h-4 w-4" />
          </button>
        </div>
      </div>
      {body ? (
        <div id={bodyId} className="mt-3">
          {body}
        </div>
      ) : null}
    </ReaderFloatingSurface>
  );
}

export function ReaderQuickPeek({
  floatingRef,
  inspect = null,
  lookup = null,
  onDismiss,
  onAttachToAsk,
  onLookupPhrase,
  onOpenDetail,
  style,
}: ReaderQuickPeekProps) {
  const inspectTitleId = useId();
  const lookupTitleId = useId();
  const lookupBodyId = useId();

  if (inspect) {
    return (
      <ReaderQuickPeekShell
        floatingRef={floatingRef}
        titleId={inspectTitleId}
        eyebrow="结构化解释"
        title={inspect.anchorText}
        body={
          <ReaderStructuredInspectCard
            intent={inspect}
            onAttachToAsk={onAttachToAsk}
            onLookupPhrase={onLookupPhrase}
            onOpenDetail={onOpenDetail}
            variant="peek"
          />
        }
        style={style}
        onDismiss={onDismiss}
      />
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
  const errorMessage =
    lookup.state.kind === "error"
      ? lookup.state.message
      : result?.kind === "error"
        ? result.message
        : "";

  return (
    <ReaderQuickPeekShell
      floatingRef={floatingRef}
      titleId={lookupTitleId}
      eyebrow={lookup.label ?? (lookup.lookupType === "phrase" ? "短语" : "词典")}
      title={
        <span className="block truncate reader-serif text-[1.35rem] leading-tight text-ink">
          {entryResult?.entry.word ?? lookup.query}
        </span>
      }
      aside={
        entryResult?.entry.phonetic ? (
          <span className="mt-1 text-xs text-muted">{entryResult.entry.phonetic}</span>
        ) : undefined
      }
      bodyId={lookupBodyId}
      body={
        <>
          {glossaryTitle && glossaryText ? (
            <span className="block rounded-[6px] bg-lens-blue-soft/80 px-3 py-2">
              <span className="block text-xs font-semibold text-lens-blue">{glossaryTitle}</span>
              <span className="mt-1 block text-sm leading-6 text-ink-soft">{glossaryText}</span>
            </span>
          ) : null}

          {lookup.state.kind === "loading" ? (
            <span className="mt-3 block text-sm leading-6 text-muted">正在查词...</span>
          ) : null}

          {entryResult && !glossaryText ? (
            <span className="mt-3 block text-sm leading-6 text-ink-soft">
              {firstMeaning(entryResult) || "当前词条暂无简短释义，打开详情可查看完整信息。"}
            </span>
          ) : null}

          {disambiguationResult ? (
            <span className="mt-3 block text-sm leading-6 text-muted">多个候选词条，打开详情继续选择。</span>
          ) : null}

          {notFoundResult ? (
            <span className="mt-3 block text-sm leading-6 text-muted">当前词典暂未收录。</span>
          ) : null}

          {errorMessage ? (
            <span className="mt-3 block text-sm leading-6 text-error-red">{errorMessage}</span>
          ) : null}

          {onOpenDetail ? (
            <button
              type="button"
              className="focus-ring mt-3 inline-flex min-h-10 items-center gap-2 rounded-md px-1 text-xs font-semibold text-lens-blue"
              onClick={onOpenDetail}
            >
              打开详情
              <ChevronRight aria-hidden="true" className="h-3.5 w-3.5" />
            </button>
          ) : null}
        </>
      }
      style={style}
      onDismiss={onDismiss}
    />
  );
}
