"use client";

import type { CSSProperties } from "react";
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
  if (inspect) {
    return (
      <ReaderFloatingSurface
        floatingRef={floatingRef}
        className="reader-lookup-preview"
        role="dialog"
        style={style}
        onClick={(event) => event.stopPropagation()}
        onPointerDown={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <span className="text-[0.72rem] font-semibold tracking-[0.08em] text-muted">结构化解释</span>
          <button
            type="button"
            className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-full border border-hairline bg-surface text-muted transition-colors hover:border-muted hover:text-ink"
            onClick={onDismiss}
            aria-label="关闭结构化解释"
          >
            <X aria-hidden="true" className="h-3.5 w-3.5" />
          </button>
        </div>
        <div className="mt-3">
          <ReaderStructuredInspectCard
            intent={inspect}
            onAttachToAsk={onAttachToAsk}
            onLookupPhrase={onLookupPhrase}
            onOpenDetail={onOpenDetail}
            variant="peek"
          />
        </div>
      </ReaderFloatingSurface>
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
    <ReaderFloatingSurface
      floatingRef={floatingRef}
      className="reader-lookup-preview"
      role="dialog"
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
          className="focus-ring mt-3 inline-flex items-center gap-2 text-xs font-semibold text-lens-blue"
          onClick={onOpenDetail}
        >
          打开详情
          <ChevronRight aria-hidden="true" className="h-3.5 w-3.5" />
        </button>
      ) : null}
    </ReaderFloatingSurface>
  );
}
