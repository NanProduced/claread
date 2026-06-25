"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { AiWorkspacePanel } from "@/components/reader/AiWorkspacePanel";
import type { DictLookupTypeDto, WebDictResult } from "@/types/api/dict";
import {
  projectReaderPlateSnapshotToReaderRecordPlateDocument,
  type ReaderRecordPlateBlockquoteBlock,
  type ReaderRecordPlateCalloutBlock,
  type ReaderRecordPlateMark,
  type ReaderRecordPlateParagraphBlock,
  type ReaderRecordPlateProgress,
  type ReaderRecordPlateTextLeaf,
  type ReaderRecordPlateTextAnchor,
  type ReaderRecordPlateUserHighlightMark,
  type ReaderRecordPlateUserNoteMark,
  type ReaderRecordPlateVocabularyMark,
} from "@/lib/reader-plate/projection/reader-record-plate-document";
import {
  readReaderRecordSelectionAnchorDrafts,
  type ReaderRecordSelectionAnchorBridgeResult,
} from "@/lib/reader-plate/projection/reader-record-dom-selection";
import {
  askAttachmentKey,
  type ReaderAskAttachment,
  type ReaderAskPageIdentity,
} from "@/lib/reader-plate";
import type { ReaderRecordAnchorDraft } from "@/lib/reader-plate/projection/reader-record-anchor-draft";
import type { ReaderPlateSnapshotDto, ReaderSnapshotUserAssetDto } from "@/types/api/reader-plate";
import type { ReaderAskActionConfirmResponseDto } from "@/types/api/reader-ask";
import type { ThemeName } from "@/lib/appearance";
import { FavoriteButton } from "@/components/reader/FavoriteButton";
import {
  ReaderSettingsPanel,
  createDefaultReaderSettings,
  readStoredReaderSettings,
  persistReaderSettings,
  readerModeTypography,
  readerThemeClassName,
  type ReaderSettingsState,
} from "@/components/reader/settings";

import { SelectionToolbar } from "../SelectionToolbar";
import {
  ReaderFloatingSurface,
  useReaderFloatingLayer,
} from "../ReaderFloatingLayer";
import { ReaderQuickPeek } from "../dictionary/ReaderQuickPeek";
import { ReaderDictionaryRail } from "../dictionary/ReaderDictionaryRail";
import type { DictionaryLookupSnapshot, SaveState } from "../dictionary/contracts";
import { firstMeaning, meaningsJson } from "../dictionary/contracts";
import type { DictionaryAIViewState } from "@/types/api/dict-ai";

export interface ReaderRecordPlateSurfaceProps {
  snapshot: ReaderPlateSnapshotDto;
  className?: string;
  columnClassName?: string;
  readingClassName?: string;
  onRequestSnapshotReload?: () => void | Promise<void>;
}

type ReaderRecordLookupState =
  | { kind: "idle" }
  | { kind: "loading"; query: string; context: ReaderRecordLookupContext }
  | { kind: "ready"; query: string; context: ReaderRecordLookupContext; result: WebDictResult }
  | { kind: "error"; query: string; context: ReaderRecordLookupContext; message: string };

interface ReaderRecordLookupContext {
  contextSentence: string;
  sentenceId: string;
  anchorText: string;
  lookupType: DictLookupTypeDto;
  source: "vocabulary" | "selection";
}

type ReaderRecordCopyStatus = "idle" | "copied" | "error";

type ReaderRecordWriteAction = "highlight" | "note";

type ReaderRecordWriteState =
  | { kind: "idle" }
  | { kind: "saving"; action: ReaderRecordWriteAction }
  | { kind: "saved"; action: ReaderRecordWriteAction; message: string }
  | { kind: "error"; action: ReaderRecordWriteAction; message: string };

const HIGHLIGHT_COLOR_OPTIONS: Array<{
  value: string;
  label: string;
  swatchClassName: string;
}> = [
  { value: "soft_green", label: "灰绿", swatchClassName: "bg-structure-green/45 ring-structure-green/25" },
  { value: "warm_yellow", label: "暖黄", swatchClassName: "bg-vocab-amber/75 ring-vocab-amber/25" },
  { value: "soft_blue", label: "雾青", swatchClassName: "bg-context-blue/65 ring-context-blue/25" },
  { value: "soft_purple", label: "淡紫", swatchClassName: "bg-violet-200/70 ring-violet-300/50" },
  { value: "sage_green", label: "草绿", swatchClassName: "bg-emerald-200/70 ring-emerald-300/50" },
];

function overallProgressLabel(status: ReaderRecordPlateProgress["overallStatus"]) {
  switch (status) {
    case "ready":
      return "解析完成";
    case "failed":
      return "部分解析失败";
    case "action_required":
      return "需要确认";
    case "processing":
    case "readable_enhancing":
      return "解析生成中";
    default:
      return "正文可读";
  }
}

function lookupTypeForSelection(text: string): DictLookupTypeDto {
  return /\s/.test(text.trim()) ? "phrase" : "word";
}

function singleRangeDraft(
  selection: ReaderRecordSelectionAnchorBridgeResult | null,
): ReaderRecordAnchorDraft | null {
  return selection?.supportedSingleRange ? (selection.drafts[0] ?? null) : null;
}

function readingRecordAskAnchorFromDraft(
  draft: ReaderRecordAnchorDraft,
): Record<string, unknown> {
  return {
    record_id: draft.record_id,
    base_id: draft.base_id,
    generation: draft.generation,
    unit_id: draft.unit_id,
    anchor_segment_id: draft.anchor_segment_id,
    start_offset: draft.start_offset,
    end_offset: draft.end_offset,
    offset_unit: draft.offset_unit,
    selected_text: draft.selected_text,
    text_hash: draft.text_hash,
    hash_algorithm: draft.hash_algorithm,
    scope: draft.scope,
  };
}

function readingRecordAskAnchorFromTextAnchor(
  recordId: string,
  generation: number,
  anchor: ReaderRecordPlateTextAnchor,
): Record<string, unknown> {
  return {
    record_id: recordId,
    base_id: anchor.baseId,
    generation,
    unit_id: anchor.unitId,
    anchor_segment_id: anchor.anchorSegmentId,
    start_offset: anchor.unitStartOffset,
    end_offset: anchor.unitEndOffset,
    offset_unit: anchor.offsetUnit,
    selected_text: anchor.selectedText,
    text_hash: anchor.textHash,
    hash_algorithm: anchor.hashAlgorithm,
    scope: "stable_source",
  };
}

function actionButtonClassName(enabled: boolean) {
  const base =
    "rounded-full border px-2.5 py-1 transition-colors focus:outline-none focus:ring-2 focus:ring-lens-blue/30";
  return enabled
    ? `${base} border-border/80 bg-background/80 text-foreground hover:border-lens-blue/40 hover:bg-lens-blue/5`
    : `${base} border-transparent bg-transparent text-muted/60`;
}

function writeStateLabel(writeState: ReaderRecordWriteState): string {
  switch (writeState.kind) {
    case "saving":
      return writeState.action === "highlight" ? "正在保存高亮" : "正在保存笔记";
    case "saved":
    case "error":
      return writeState.message;
    default:
      return "";
  }
}

function writeStateClassName(writeState: ReaderRecordWriteState) {
  if (writeState.kind === "error") {
    return "text-rose-700";
  }
  if (writeState.kind === "saved") {
    return "text-emerald-700";
  }
  return "text-muted";
}

function buildTempUserAsset(
  snapshot: ReaderPlateSnapshotDto,
  draft: ReaderRecordAnchorDraft,
  options: { kind: "highlight"; color: string } | { kind: "note"; noteText: string },
): ReaderSnapshotUserAssetDto {
  const segment = snapshot.anchor_segments.find(
    (s) => s.anchor_segment_id === draft.anchor_segment_id,
  );
  const now = new Date().toISOString();
  const tempId = `temp-${now}-${Math.random().toString(36).slice(2, 8)}`;
  return {
    asset_id: tempId,
    asset_type: options.kind === "highlight" ? "user_highlight" : "reader_note",
    owner: "user",
    reading_record_id: draft.record_id,
    generation: draft.generation,
    anchor: {
      anchor_type: "text_range",
      base_id: draft.base_id,
      unit_id: draft.unit_id,
      anchor_segment_id: draft.anchor_segment_id,
      sentence_id: segment?.sentence_id ?? null,
      segment_type: segment?.segment_type ?? "sentence",
      offset_unit: draft.offset_unit,
      start_offset: draft.start_offset,
      end_offset: draft.end_offset,
      selected_text: draft.selected_text,
      text_hash: draft.text_hash,
      hash_algorithm: draft.hash_algorithm,
    },
    note_text: options.kind === "note" ? options.noteText : null,
    color: options.kind === "highlight" ? options.color : null,
    created_at: now,
    updated_at: now,
  };
}

function buildDictionaryLookupSnapshot(
  snapshot: ReaderPlateSnapshotDto,
  state: ReaderRecordLookupState,
): DictionaryLookupSnapshot | null {
  if (state.kind === "idle") {
    return null;
  }
  const lookupState: DictionaryLookupSnapshot["state"] =
    state.kind === "loading"
      ? { kind: "loading" }
      : state.kind === "ready"
        ? { kind: "ready", result: state.result }
        : { kind: "error", message: state.message };
  return {
    query: state.query,
    lookupType: state.context.lookupType,
    contextSentence: state.context.contextSentence,
    recordId: snapshot.record_id,
    sentenceId: state.context.sentenceId,
    anchorText: state.context.anchorText,
    title: state.query,
    label: state.context.source === "vocabulary" ? "词汇查询" : "选区查词",
    state: lookupState,
  };
}

async function postReadingRecordUserAsset(
  endpoint: "/api/web/reading-record/highlights" | "/api/web/reading-record/notes",
  body: Record<string, unknown>,
): Promise<void> {
  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "application/json",
    },
    body: JSON.stringify(body),
  });
  const payload = (await response.json().catch(() => null)) as
    | { ok?: boolean; message?: string }
    | null;

  if (!response.ok || payload?.ok === false) {
    throw new Error(payload?.message ?? "阅读资产保存失败。");
  }
}

function isVocabularyMark(
  mark: ReaderRecordPlateMark,
): mark is ReaderRecordPlateVocabularyMark {
  return mark.kind !== "grammar_note" && mark.kind !== "user_highlight" && mark.kind !== "user_note";
}

function isUserHighlightMark(
  mark: ReaderRecordPlateMark,
): mark is ReaderRecordPlateUserHighlightMark {
  return mark.kind === "user_highlight";
}

function isUserNoteMark(
  mark: ReaderRecordPlateMark,
): mark is ReaderRecordPlateUserNoteMark {
  return mark.kind === "user_note";
}

function vocabularyTitle(mark: ReaderRecordPlateVocabularyMark) {
  if (mark.vocabulary.itemType === "vocab_highlight") {
    return mark.vocabulary.headword;
  }
  if (mark.vocabulary.itemType === "phrase_gloss") {
    return mark.vocabulary.phrase;
  }
  return mark.vocabulary.display;
}

function markClassName(mark: ReaderRecordPlateTextLeaf["marks"][number]) {
  if (mark.kind === "user_highlight") {
    return "rounded-sm bg-amber-100/80 ring-1 ring-amber-200/80";
  }
  if (mark.kind === "user_note") {
    return "rounded-sm bg-blue-50/60 underline decoration-blue-500/80 decoration-dashed underline-offset-4";
  }
  if (mark.kind === "grammar_note") {
    return "rounded-sm underline decoration-emerald-600/80 decoration-[1.5px] underline-offset-4";
  }
  if (mark.kind === "phrase_gloss") {
    return "rounded-sm bg-violet-50 underline decoration-violet-500/70 underline-offset-4";
  }
  if (mark.kind === "context_gloss") {
    return "rounded-sm bg-sky-50 underline decoration-sky-500/70 underline-offset-4";
  }
  return "rounded-sm bg-amber-50";
}

function markLabel(mark: ReaderRecordPlateTextLeaf["marks"][number]) {
  if (mark.kind === "user_highlight") {
    return "用户高亮";
  }
  if (mark.kind === "user_note") {
    return "用户笔记";
  }
  if (mark.kind === "grammar_note") {
    return `语法 · ${mark.grammarPoint}`;
  }
  if (mark.vocabulary.itemType === "vocab_highlight") {
    return `词汇 · ${mark.vocabulary.headword}`;
  }
  if (mark.vocabulary.itemType === "phrase_gloss") {
    return `短语 · ${mark.vocabulary.gloss}`;
  }
  return `语境 · ${mark.vocabulary.gloss}`;
}

function markPriority(mark: ReaderRecordPlateMark) {
  if (mark.kind === "grammar_note") {
    return 10;
  }
  if (mark.kind === "phrase_gloss") {
    return 20;
  }
  if (mark.kind === "context_gloss") {
    return 30;
  }
  if (mark.kind === "vocab_highlight") {
    return 40;
  }
  if (mark.kind === "user_note") {
    return 45;
  }
  return 50;
}

function sortedMarkStack(marks: ReaderRecordPlateMark[]) {
  return [...marks].sort((left, right) => {
    const priorityDelta = markPriority(left) - markPriority(right);
    return priorityDelta === 0 ? left.id.localeCompare(right.id) : priorityDelta;
  });
}

function markStackLabel(marks: ReaderRecordPlateMark[]) {
  return marks.map(markLabel).join("；");
}

function markStackClassName(marks: ReaderRecordPlateMark[]) {
  return sortedMarkStack(marks).map(markClassName).join(" ");
}

function renderMarkedLeaf(
  leaf: ReaderRecordPlateTextLeaf,
  children: ReactNode,
  onActivateVocabulary: (mark: ReaderRecordPlateVocabularyMark, anchor: HTMLElement) => void,
  onActivateHighlight: (mark: ReaderRecordPlateUserHighlightMark, anchor: HTMLElement) => void,
  onActivateNote: (mark: ReaderRecordPlateUserNoteMark, anchor: HTMLElement) => void,
) {
  if (leaf.marks.length === 0) {
    return children;
  }

  const markStack = sortedMarkStack(leaf.marks);
  const primaryMark = markStack[0];
  const vocabularyMark = markStack.find(isVocabularyMark);
  const userHighlightMark = markStack.find(isUserHighlightMark);
  const userNoteMark = markStack.find(isUserNoteMark);

  // Render nested spans for non-primary marks so each mark is locatable
  // by data-reader-record-mark-id, even when multiple marks overlap.
  const innerContent = markStack.slice(1).reduce<ReactNode>(
    (acc, mark) => (
      <span
        data-reader-record-mark-entry="stack"
        data-reader-record-mark-id={mark.id}
        data-reader-record-mark-kind={mark.kind}
      >
        {acc}
      </span>
    ),
    children,
  );

  return (
    <span
      data-reader-record-mark-entry="stack"
      data-reader-record-mark-id={primaryMark.id}
      data-reader-record-mark-kind={primaryMark.kind}
      className={markStackClassName(markStack)}
      aria-label={markStackLabel(markStack)}
      title={markStackLabel(markStack)}
      onClick={
        userNoteMark
          ? (event) => {
              event.stopPropagation();
              onActivateNote(userNoteMark, event.currentTarget as HTMLElement);
            }
          : userHighlightMark
            ? (event) => {
                event.stopPropagation();
                onActivateHighlight(userHighlightMark, event.currentTarget as HTMLElement);
              }
            : vocabularyMark
              ? (event) => {
                  event.stopPropagation();
                  onActivateVocabulary(vocabularyMark, event.currentTarget as HTMLElement);
                }
              : undefined
      }
    >
      {innerContent}
    </span>
  );
}

function renderParagraphLeaf(
  leaf: ReaderRecordPlateTextLeaf,
  index: number,
  onActivateVocabulary: (mark: ReaderRecordPlateVocabularyMark, anchor: HTMLElement) => void,
  onActivateHighlight: (mark: ReaderRecordPlateUserHighlightMark, anchor: HTMLElement) => void,
  onActivateNote: (mark: ReaderRecordPlateUserNoteMark, anchor: HTMLElement) => void,
): ReactNode {
  return (
    <span
      key={leaf.anchorSegmentId + index}
      data-reader-record-leaf="segment_text"
      data-anchor-segment-id={leaf.anchorSegmentId}
      data-segment-start-utf16={leaf.segmentRange.startUtf16}
      data-segment-end-utf16={leaf.segmentRange.endUtf16}
    >
      {renderMarkedLeaf(leaf, leaf.text, onActivateVocabulary, onActivateHighlight, onActivateNote)}
    </span>
  );
}

function ParagraphBlock({
  block,
  readingClassName,
  onActivateVocabulary,
  onActivateHighlight,
  onActivateNote,
}: {
  block: ReaderRecordPlateParagraphBlock;
  readingClassName: string;
  onActivateVocabulary: (mark: ReaderRecordPlateVocabularyMark, anchor: HTMLElement) => void;
  onActivateHighlight: (mark: ReaderRecordPlateUserHighlightMark, anchor: HTMLElement) => void;
  onActivateNote: (mark: ReaderRecordPlateUserNoteMark, anchor: HTMLElement) => void;
}) {
  return (
    <p
      data-reader-record-node="paragraph"
      data-anchor-segment-id={block.data.anchorSegmentId}
      data-sentence-id={block.data.sentenceId}
      data-unit-id={block.data.unitId}
      className={`reader-record-plate-paragraph ${readingClassName}`.trim()}
    >
      {block.children.map((leaf, index) =>
        renderParagraphLeaf(leaf, index, onActivateVocabulary, onActivateHighlight, onActivateNote),
      )}
    </p>
  );
}

function BlockquoteBlock({
  block,
}: {
  block: ReaderRecordPlateBlockquoteBlock;
}) {
  return (
    <blockquote
      data-reader-record-node="blockquote"
      data-unit-id={block.data.unitId}
      className="reader-record-plate-blockquote mt-3 border-l-2 border-emerald-300/60 bg-emerald-50/40 py-2 pl-4 pr-3 font-sans text-[0.95rem] leading-7 text-ink-soft"
    >
      <span className="mb-1 block text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-emerald-700/80">
        译文
      </span>
      {block.children.map((leaf, index) => (
        <span key={index}>{leaf.text}</span>
      ))}
    </blockquote>
  );
}

function CalloutBlock({
  block,
  onFeedback,
}: {
  block: ReaderRecordPlateCalloutBlock;
  onFeedback: (block: ReaderRecordPlateCalloutBlock, anchor: HTMLElement) => void;
}) {
  const isGrammar = block.variant === "grammar";
  const isSupplement = block.variant === "supplement";
  const containerClass = isGrammar
    ? "reader-record-plate-callout reader-record-plate-callout--grammar mt-3 rounded-md border border-emerald-200/70 bg-emerald-50/60 px-4 py-3 text-sm leading-6 text-ink-soft"
    : isSupplement
      ? "reader-record-plate-callout reader-record-plate-callout--supplement mt-3 rounded-md border border-amber-200/70 bg-amber-50/60 px-4 py-3 text-sm leading-6 text-ink-soft"
      : "reader-record-plate-callout reader-record-plate-callout--analysis mt-3 rounded-md border border-sky-200/70 bg-sky-50/60 px-4 py-3 text-sm leading-6 text-ink-soft";
  const labelClass = isGrammar
    ? "mb-1 block text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-emerald-700/80"
    : isSupplement
      ? "mb-1 block text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-amber-700/80"
      : "mb-1 block text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-sky-700/80";
  const label = isGrammar
    ? "语法讲解"
    : isSupplement
      ? "AI 补充"
      : "句子结构";
  const title = isGrammar
    ? block.data.grammarPoint ?? ""
    : isSupplement
      ? block.data.supplementTitle ?? ""
      : block.data.label ?? "";

  return (
    <div
      data-reader-record-node="callout"
      data-callout-variant={block.variant}
      data-anchor-segment-id={block.data.anchorSegmentId}
      className={containerClass}
    >
      <div className="flex items-start gap-2">
        <span className="text-base leading-none" aria-hidden="true">
          {block.icon}
        </span>
        <div className="min-w-0 flex-1">
          <span className={labelClass}>{label}</span>
          {title ? (
            <span className="reader-serif block text-[0.95rem] font-semibold leading-snug text-ink">
              {title}
            </span>
          ) : null}
          <div className="mt-1">
            {block.children.map((leaf, index) => (
              <span key={index}>{leaf.text}</span>
            ))}
          </div>
        </div>
        <button
          type="button"
          className="shrink-0 rounded-sm px-1.5 py-0.5 text-xs text-muted/70 hover:bg-muted/40 hover:text-muted"
          aria-label="反馈"
          data-reader-record-feedback-trigger={block.id}
          onClick={(event) => {
            event.stopPropagation();
            onFeedback(block, event.currentTarget as HTMLElement);
          }}
        >
          <span aria-hidden="true">⚑</span>
        </button>
      </div>
    </div>
  );
}

function ReaderRecordHeader({
  snapshot,
  progress,
  surfaceMode,
  readerSettings,
  onModeChange,
  onOpenSettings,
}: {
  snapshot: ReaderPlateSnapshotDto;
  progress: ReaderRecordPlateProgress;
  surfaceMode: "intensive" | "immersive";
  readerSettings: ReaderSettingsState;
  onModeChange: (mode: "intensive" | "immersive") => void;
  onOpenSettings: () => void;
}) {
  const title = snapshot.record.title;
  const createdAt = snapshot.record.created_at;
  const sentenceCount = snapshot.anchor_segments.length;
  const sourceType = snapshot.record.source_type;
  const sourceMetadata = snapshot.record.source_metadata ?? {};
  const sourceUrl =
    typeof sourceMetadata.source_url === "string" &&
    (sourceMetadata.source_url.startsWith("http:") ||
      sourceMetadata.source_url.startsWith("https:"))
      ? sourceMetadata.source_url
      : null;
  const sourceName =
    typeof sourceMetadata.source_name === "string"
      ? sourceMetadata.source_name
      : null;
  const sourceDomain =
    typeof sourceMetadata.source_domain === "string"
      ? sourceMetadata.source_domain
      : null;
  const statusLabel = overallProgressLabel(progress.overallStatus);
  const formattedDate = createdAt
    ? new Date(createdAt).toLocaleDateString("zh-CN", {
        year: "numeric",
        month: "long",
        day: "numeric",
      })
    : "今日";
  const readingMinutes = Math.max(1, Math.ceil(sentenceCount / 5));
  const modeLabel = surfaceMode === "immersive" ? "沉浸模式" : "精读模式";
  const sourceBadge = sourceName ?? sourceDomain ?? (sourceType === "plain_text" ? "粘贴导入" : sourceType);

  return (
    <header
      data-testid="reader-record-plate-header"
      data-reader-record-reading-header={surfaceMode}
      className="reader-header-band reader-header-band--clean mb-8 border-b border-border/60 pb-6"
    >
      {/* Zone 1: Eyebrow — mode label + date */}
      <div className="flex items-center gap-1.5 text-[0.8rem] font-semibold tracking-wide leading-none">
        <span className="text-lens-blue">{modeLabel}</span>
        <span className="text-muted/60">·</span>
        <span className="text-muted font-medium">{formattedDate}</span>
      </div>

      {/* Zone 2: H1 + overview */}
      {title ? (
        <h1
          data-reader-record-reading-title
          className="font-headline mt-4 text-[clamp(2rem,4vw,3.25rem)] font-bold leading-[1.08] text-ink tracking-tight"
        >
          {title}
        </h1>
      ) : null}

      {/* Zone 3: Action bar — left (badges) + right (favorite + mode + settings) */}
      <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3 text-[0.78rem] text-muted tracking-wide">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-lens-blue" />
            <span
              data-reader-record-progress-status={progress.overallStatus}
              className="font-medium text-foreground"
            >
              {statusLabel}
            </span>
          </span>
          <span className="text-muted/60">·</span>
          <span className="font-medium">{sentenceCount} 句</span>
          <span className="text-muted/60">·</span>
          <span className="font-medium">约 {readingMinutes} 分钟阅读</span>
          {sourceBadge ? (
            <>
              <span className="text-muted/60">·</span>
              <span className="font-medium">来源 {sourceBadge}</span>
            </>
          ) : null}
        </div>

        <div className="flex items-center gap-1.5">
          <FavoriteButton recordId={snapshot.record_id} variant="action-bar" />
          <div
            className="flex items-center rounded-full border border-border/70 bg-background/80 p-0.5"
            role="group"
            aria-label="阅读模式切换"
            data-reader-record-mode-switch={surfaceMode}
          >
            <button
              type="button"
              aria-pressed={surfaceMode === "intensive"}
              aria-label="切换到精读模式"
              data-reader-record-mode-option="intensive"
              onClick={() => onModeChange("intensive")}
              className={
                surfaceMode === "intensive"
                  ? "rounded-full bg-lens-blue/10 px-3 py-1 text-[0.75rem] font-semibold text-lens-blue"
                  : "rounded-full px-3 py-1 text-[0.75rem] font-medium text-muted hover:text-ink"
              }
            >
              精读
            </button>
            <button
              type="button"
              aria-pressed={surfaceMode === "immersive"}
              aria-label="切换到沉浸模式"
              data-reader-record-mode-option="immersive"
              onClick={() => onModeChange("immersive")}
              className={
                surfaceMode === "immersive"
                  ? "rounded-full bg-lens-blue/10 px-3 py-1 text-[0.75rem] font-semibold text-lens-blue"
                  : "rounded-full px-3 py-1 text-[0.75rem] font-medium text-muted hover:text-ink"
              }
            >
              沉浸
            </button>
          </div>
          <button
            type="button"
            aria-label="打开阅读设置"
            data-reader-record-action="open-settings"
            onClick={onOpenSettings}
            className="rounded-full border border-border/70 bg-background/80 p-1.5 text-muted transition-colors hover:text-ink"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </button>
        </div>
      </div>

      {/* Zone 4: Bottom metadata — source + date + reading time + original link */}
      <div className="mt-3 flex flex-wrap items-center gap-2 text-[0.72rem] text-muted/80 tracking-wide">
        {sourceName || sourceDomain ? (
          <span className="font-medium">来源 {sourceName ?? sourceDomain}</span>
        ) : null}
        {sourceUrl ? (
          <>
            <span className="text-muted/40">·</span>
            <a
              href={sourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="font-semibold text-muted transition-colors hover:text-ink"
            >
              原文链接
            </a>
          </>
        ) : null}
        <span className="text-muted/40">·</span>
        <span>数据来源 {sourceType === "plain_text" ? "粘贴导入" : sourceType}</span>
      </div>
    </header>
  );
}

function SelectionActionStrip({
  copyStatus,
  lookupState,
  selection,
  writeState,
  noteComposerOpen,
  onAsk,
  onCopy,
  onHighlight,
  onLookup,
  onOpenNoteComposer,
}: {
  copyStatus: ReaderRecordCopyStatus;
  lookupState: ReaderRecordLookupState;
  selection: ReaderRecordSelectionAnchorBridgeResult | null;
  writeState: ReaderRecordWriteState;
  noteComposerOpen: boolean;
  onAsk: () => void;
  onCopy: () => void;
  onHighlight: () => void;
  onLookup: () => void;
  onOpenNoteComposer: () => void;
}) {
  const draft = singleRangeDraft(selection);
  const singleRangeReady = Boolean(selection?.supportedSingleRange && draft);
  const isSaving = writeState.kind === "saving";
  const copyDisabled = !singleRangeReady;
  const lookupDisabled = !singleRangeReady || lookupState.kind === "loading";
  const highlightDisabled = !singleRangeReady || isSaving;
  const noteDisabled = !singleRangeReady || isSaving || noteComposerOpen;
  const askDisabled = !singleRangeReady;
  const disabledReason = !selection
    ? "请选择稳定原文以启用此操作"
    : singleRangeReady
      ? "操作当前不可用"
      : "暂不支持跨段选区";
  const writeStatus = writeStateLabel(writeState);
  const actionMode = singleRangeReady ? "selection" : selection ? "unsupported" : "idle";
  const actionHint = singleRangeReady
    ? `已选：${draft?.selected_text ?? ""}`
    : selection
      ? "当前选区暂不支持操作"
      : "划取原文后可查词、复制、标记或记录笔记";

  return (
    <div
      data-testid="reader-record-plate-disabled-actions"
      data-reader-record-actions="selection-context"
      data-reader-record-action-mode={actionMode}
      data-reader-record-selection-draft-count={selection?.drafts.length ?? 0}
      data-reader-record-selection-supported={singleRangeReady ? "true" : "false"}
      data-reader-record-selection-anchor-segment-id={
        draft?.anchor_segment_id ?? undefined
      }
      data-reader-record-selection-start-offset={
        draft ? String(draft.start_offset) : undefined
      }
      data-reader-record-selection-end-offset={
        draft ? String(draft.end_offset) : undefined
      }
      data-reader-record-write-state={writeState.kind}
      className="sr-only"
      aria-hidden="true"
      inert
      aria-label="Reader Record Plate 操作"
    >
      <span
        data-reader-record-action-hint
        className={`mr-1 max-w-full truncate sm:max-w-[40ch] ${
          singleRangeReady ? "font-medium text-foreground" : ""
        }`}
      >
        {actionHint}
      </span>
      {singleRangeReady ? (
        <>
          <button
            type="button"
            disabled={lookupDisabled}
            data-reader-record-action="lookup"
            className={actionButtonClassName(!lookupDisabled)}
            title={lookupDisabled ? disabledReason : "查词所选文本"}
            onPointerDown={(event) => event.preventDefault()}
            onClick={onLookup}
          >
            {lookupState.kind === "loading" ? "查询中" : "查词"}
          </button>
          <button
            type="button"
            disabled={copyDisabled}
            data-reader-record-action="copy"
            className={actionButtonClassName(!copyDisabled)}
            title={copyDisabled ? disabledReason : "复制所选文本"}
            onPointerDown={(event) => event.preventDefault()}
            onClick={onCopy}
          >
            复制
          </button>
          <button
            type="button"
            disabled={highlightDisabled}
            data-reader-record-action="highlight"
            className={actionButtonClassName(!highlightDisabled)}
            title={highlightDisabled ? disabledReason : "保存高亮"}
            onPointerDown={(event) => event.preventDefault()}
            onClick={onHighlight}
          >
            {writeState.kind === "saving" && writeState.action === "highlight"
              ? "保存中"
              : "高亮"}
          </button>
          <button
            type="button"
            disabled={noteDisabled}
            data-reader-record-action="note"
            className={actionButtonClassName(!noteDisabled)}
            title={noteDisabled ? disabledReason : "创建笔记"}
            onPointerDown={(event) => event.preventDefault()}
            onClick={onOpenNoteComposer}
          >
            笔记
          </button>
          <button
            type="button"
            disabled={askDisabled}
            data-reader-record-action="ask"
            className={actionButtonClassName(!askDisabled)}
            title={askDisabled ? disabledReason : "Ask 关于所选内容"}
            onPointerDown={(event) => event.preventDefault()}
            onClick={onAsk}
          >
            Ask
          </button>
          <span data-reader-record-coming-soon-actions="feedback" className="text-muted/70">
            反馈稍后开放
          </span>
          {copyStatus !== "idle" ? (
            <span
              data-testid="reader-record-plate-copy-status"
              className={
                copyStatus === "copied" ? "text-emerald-700" : "text-rose-700"
              }
            >
              {copyStatus === "copied" ? "已复制" : "复制失败"}
            </span>
          ) : null}
          {writeStatus ? (
            <span
              data-testid="reader-record-plate-write-status"
              className={writeStateClassName(writeState)}
            >
              {writeStatus}
            </span>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

function ReaderRecordNoteComposer({
  noteDraft,
  saving,
  onCancel,
  onChange,
  onSave,
}: {
  noteDraft: string;
  saving: boolean;
  onCancel: () => void;
  onChange: (value: string) => void;
  onSave: () => void;
}) {
  const saveDisabled = saving || noteDraft.trim().length === 0;

  return (
    <div
      data-testid="reader-record-plate-note-composer"
      className="mb-5 rounded-md border border-border border-l-2 border-l-amber-300/70 bg-background px-3.5 py-3 text-sm shadow-sm"
    >
      <label
        htmlFor="reader-record-plate-note-input"
        className="text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-muted"
      >
        笔记
      </label>
      <textarea
        id="reader-record-plate-note-input"
        data-testid="reader-record-plate-note-input"
        value={noteDraft}
        rows={3}
        className="mt-2 w-full resize-y rounded-md border border-border bg-background px-3 py-2 text-sm leading-6 text-ink outline-none focus:border-lens-blue"
        onChange={(event) => onChange(event.currentTarget.value)}
      />
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          disabled={saveDisabled}
          className={actionButtonClassName(!saveDisabled)}
          onPointerDown={(event) => event.preventDefault()}
          onClick={onSave}
        >
          {saving ? "保存中" : "保存"}
        </button>
        <button
          type="button"
          disabled={saving}
          className={actionButtonClassName(!saving)}
          onPointerDown={(event) => event.preventDefault()}
          onClick={onCancel}
        >
          取消
        </button>
      </div>
    </div>
  );
}

export function ReaderRecordPlateSurface({
  snapshot,
  className = "px-5 py-8 sm:px-8 lg:px-10",
  columnClassName = "mx-auto max-w-[72ch]",
  readingClassName = "reader-serif text-ink text-[1.2rem] leading-[1.9]",
  onRequestSnapshotReload,
}: ReaderRecordPlateSurfaceProps) {
  const surfaceRef = useRef<HTMLElement | null>(null);
  const [activeSelection, setActiveSelection] =
    useState<ReaderRecordSelectionAnchorBridgeResult | null>(null);
  const [copyStatus, setCopyStatus] = useState<ReaderRecordCopyStatus>("idle");
  const [writeState, setWriteState] = useState<ReaderRecordWriteState>({
    kind: "idle",
  });
  const [noteDraft, setNoteDraft] = useState("");
  const [noteAnchorDraft, setNoteAnchorDraft] =
    useState<ReaderRecordAnchorDraft | null>(null);
  const [lookupState, setLookupState] = useState<ReaderRecordLookupState>({
    kind: "idle",
  });
  const [readerSettings, setReaderSettings] = useState<ReaderSettingsState>(
    () => readStoredReaderSettings(),
  );
  const [themeName, setThemeName] = useState<ThemeName>(() => {
    if (typeof window === "undefined") {
      return "paper";
    }
    try {
      const stored = window.localStorage.getItem("claread.reader.themeName");
      return stored === "light" || stored === "dark" ? stored : "paper";
    } catch {
      return "paper";
    }
  });

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    try {
      window.localStorage.setItem("claread.reader.themeName", themeName);
    } catch {
      // Ignore storage errors (e.g. private browsing, test env)
    }
  }, [themeName]);
  const [settingsPanelOpen, setSettingsPanelOpen] = useState(false);
  const surfaceMode = readerSettings.mode;
  const [localUserAssets, setLocalUserAssets] = useState<
    ReaderPlateSnapshotDto["user_assets"]
  >(snapshot.user_assets);

  useEffect(() => {
    setLocalUserAssets(snapshot.user_assets);
  }, [snapshot.user_assets]);

  const projectedSnapshot = useMemo<ReaderPlateSnapshotDto>(
    () => ({ ...snapshot, user_assets: localUserAssets }),
    [snapshot, localUserAssets],
  );
  const askPageIdentity = useMemo<ReaderAskPageIdentity>(
    () => ({
      recordId: snapshot.record_id,
      recordTitle: snapshot.record.title,
      surface: "reader",
      source: "reader_2_0",
      availableContextCapabilities: ["record_context"],
      hasArticleOverview: false,
      hasSentenceEntries: snapshot.anchor_segments.length > 0,
      hasAnnotations: snapshot.enhancement_layers.some(
        (layer) => layer.layer_type !== "translation",
      ),
      hasReaderNotes: projectedSnapshot.user_assets.some(
        (asset) =>
          asset.asset_type === "note" ||
          asset.asset_type === "reader_note" ||
          asset.asset_type === "comment",
      ),
    }),
    [
      projectedSnapshot.user_assets,
      snapshot.anchor_segments.length,
      snapshot.enhancement_layers,
      snapshot.record.title,
      snapshot.record_id,
    ],
  );

  const plateDocument = useMemo(
    () => projectReaderPlateSnapshotToReaderRecordPlateDocument(projectedSnapshot),
    [projectedSnapshot],
  );
  const typography = readerModeTypography(readerSettings);
  const themeClassName = readerThemeClassName(themeName);
  const visibleBlocks = useMemo(() => {
    if (surfaceMode === "intensive") {
      return plateDocument.children;
    }
    // Immersive mode: hide callout (grammar/analysis) and blockquote (translation)
    return plateDocument.children.filter(
      (block) => block.type === "paragraph",
    );
  }, [plateDocument.children, surfaceMode]);

  const handleSettingsChange = useCallback((next: ReaderSettingsState) => {
    setReaderSettings(next);
    persistReaderSettings(next);
  }, []);

  const handleModeChange = useCallback(
    (mode: "intensive" | "immersive") => {
      const next = { ...readerSettings, mode };
      setReaderSettings(next);
      persistReaderSettings(next);
    },
    [readerSettings],
  );
  const [toolbarOpen, setToolbarOpen] = useState(false);
  const toolbarFloating = useReaderFloatingLayer({
    open: toolbarOpen,
    placement: "top",
    offsetPx: 12,
  });
  const [highlightMenu, setHighlightMenu] = useState<{
    mark: ReaderRecordPlateUserHighlightMark;
    anchor: HTMLElement;
  } | null>(null);
  const highlightMenuFloating = useReaderFloatingLayer({
    open: highlightMenu !== null,
    placement: "bottom",
    offsetPx: 8,
  });
  const [noteMenu, setNoteMenu] = useState<{
    mark: ReaderRecordPlateUserNoteMark;
    anchor: HTMLElement;
    mode: "view" | "edit";
    draft: string;
  } | null>(null);
  const noteMenuFloating = useReaderFloatingLayer({
    open: noteMenu !== null,
    placement: "bottom",
    offsetPx: 8,
  });
  const quickPeekOpen = lookupState.kind !== "idle";
  const quickPeekFloating = useReaderFloatingLayer({
    open: quickPeekOpen,
    placement: "bottom",
    offsetPx: 8,
  });
  const [dictionaryOpen, setDictionaryOpen] = useState(false);
  const [dictionaryHistory, setDictionaryHistory] = useState<
    DictionaryLookupSnapshot[]
  >([]);
  const [dictionarySearchQuery, setDictionarySearchQuery] = useState("");
  const [dictionarySearchExpanded, setDictionarySearchExpanded] = useState(false);
  const [dictionarySaveState, setDictionarySaveState] = useState<SaveState>({
    kind: "idle",
  });
  const [dictionaryAI, setDictionaryAI] = useState<DictionaryAIViewState>({
    kind: "idle",
  });
  const [dictionaryAIPanelOpen, setDictionaryAIPanelOpen] = useState(false);
  const [dictionaryAINoteState, setDictionaryAINoteState] = useState<SaveState>({
    kind: "idle",
  });
  const [askOpen, setAskOpen] = useState(false);
  const [askAttachments, setAskAttachments] = useState<ReaderAskAttachment[]>([]);
  const [feedbackState, setFeedbackState] = useState<SaveState>({ kind: "idle" });
  const [feedbackTarget, setFeedbackTarget] = useState<{
    blockId: string;
    variant: "grammar" | "analysis" | "supplement";
    anchorSegmentId: string;
    title: string;
  } | null>(null);
  const feedbackFloating = useReaderFloatingLayer({
    open: feedbackTarget !== null,
    placement: "bottom-end",
    offsetPx: 4,
  });

  useEffect(() => {
    function handleSelectionChange() {
      const nextSelection = readReaderRecordSelectionAnchorDrafts(
        surfaceRef.current,
        snapshot,
      );
      setActiveSelection(nextSelection);
      setCopyStatus("idle");
      setWriteState((current) => (current.kind === "saving" ? current : { kind: "idle" }));

      const selection = window.getSelection();
      if (
        nextSelection?.supportedSingleRange &&
        selection &&
        selection.rangeCount > 0
      ) {
        const range = selection.getRangeAt(0);
        toolbarFloating.refs.setReference({
          getBoundingClientRect: () => range.getBoundingClientRect(),
        });
        setToolbarOpen(true);
      } else {
        setToolbarOpen(false);
      }
    }

    window.document.addEventListener("selectionchange", handleSelectionChange);
    return () => {
      window.document.removeEventListener("selectionchange", handleSelectionChange);
    };
  }, [snapshot, toolbarFloating.refs]);

  useEffect(() => {
    if (lookupState.kind === "idle") {
      return;
    }
    function handlePointerDown(event: PointerEvent) {
      const target = event.target as Node | null;
      if (!target) {
        return;
      }
      if (quickPeekFloating.refs.floating.current?.contains(target)) {
        return;
      }
      setLookupState({ kind: "idle" });
    }
    window.document.addEventListener("pointerdown", handlePointerDown);
    return () => {
      window.document.removeEventListener("pointerdown", handlePointerDown);
    };
  }, [lookupState.kind, quickPeekFloating.refs.floating]);

  useEffect(() => {
    if (writeState.kind !== "saved" && writeState.kind !== "error") {
      return;
    }
    const timer = window.setTimeout(() => {
      setWriteState({ kind: "idle" });
    }, 4000);
    return () => {
      window.clearTimeout(timer);
    };
  }, [writeState]);

  useEffect(() => {
    if (feedbackTarget === null) {
      return;
    }
    function handlePointerDown(event: PointerEvent) {
      const target = event.target as Node | null;
      if (!target) {
        return;
      }
      if (feedbackFloating.refs.floating.current?.contains(target)) {
        return;
      }
      setFeedbackTarget(null);
    }
    window.document.addEventListener("pointerdown", handlePointerDown);
    return () => {
      window.document.removeEventListener("pointerdown", handlePointerDown);
    };
  }, [feedbackTarget, feedbackFloating.refs.floating]);

  useEffect(() => {
    if (feedbackState.kind !== "saved" && feedbackState.kind !== "error") {
      return;
    }
    const timer = window.setTimeout(() => {
      setFeedbackState({ kind: "idle" });
    }, 4000);
    return () => {
      window.clearTimeout(timer);
    };
  }, [feedbackState]);

  const handleActivateVocabulary = useCallback(
    (mark: ReaderRecordPlateVocabularyMark, anchor: HTMLElement) => {
      const query = vocabularyTitle(mark).trim();
      if (!query) {
        return;
      }
      const context: ReaderRecordLookupContext = {
        contextSentence: mark.anchor.selectedText,
        sentenceId: mark.anchor.sentenceId,
        anchorText: mark.anchor.selectedText,
        lookupType: lookupTypeForSelection(query),
        source: "vocabulary",
      };
      quickPeekFloating.refs.setReference({
        getBoundingClientRect: () => anchor.getBoundingClientRect(),
      });
      setLookupState({ kind: "loading", query, context });
      void (async () => {
        try {
          const params = new URLSearchParams({
            word: query,
            type: context.lookupType,
            context: mark.anchor.selectedText,
            sentenceId: mark.anchor.sentenceId,
          });
          const response = await fetch(`/api/web/dict/lookup?${params.toString()}`);
          const payload = (await response.json().catch(() => null)) as
            | WebDictResult
            | null;

          if (!payload) {
            setLookupState({
              kind: "error",
              query,
              context,
              message: "词典查询失败。",
            });
            return;
          }

          if (!response.ok && payload.kind !== "error") {
            setLookupState({
              kind: "error",
              query,
              context,
              message: "词典查询失败。",
            });
            return;
          }

          setLookupState({ kind: "ready", query, context, result: payload });
        } catch (error) {
          console.warn("[ReaderRecordPlateSurface] vocabulary lookup failed", error);
          setLookupState({
            kind: "error",
            query,
            context,
            message: "词典查询失败，请稍后重试。",
          });
        }
      })();
    },
    [quickPeekFloating.refs],
  );

  const handleCopy = useCallback(async () => {
    const draft = singleRangeDraft(activeSelection);
    if (!draft) {
      return;
    }

    try {
      await navigator.clipboard.writeText(draft.selected_text);
      setCopyStatus("copied");
    } catch {
      setCopyStatus("error");
    }
  }, [activeSelection]);

  const handleLookup = useCallback(async () => {
    const selection = activeSelection;
    const draft = singleRangeDraft(selection);
    if (!selection || !draft) {
      return;
    }

    const query = draft.selected_text.trim();
    if (!query) {
      return;
    }

    const context: ReaderRecordLookupContext = {
      contextSentence: selection.contextSentence,
      sentenceId: selection.sentenceId,
      anchorText: draft.selected_text,
      lookupType: lookupTypeForSelection(query),
      source: "selection",
    };

    if (selection.rect) {
      const liveSelection = window.getSelection();
      const liveRange =
        liveSelection && liveSelection.rangeCount > 0
          ? liveSelection.getRangeAt(0)
          : null;
      quickPeekFloating.refs.setReference({
        getBoundingClientRect: () =>
          liveRange?.getBoundingClientRect() ?? selection.rect!,
      });
    }

    setLookupState({ kind: "loading", query, context });

    try {
      const params = new URLSearchParams({
        word: query,
        type: context.lookupType,
        context: selection.contextSentence,
        sentenceId: selection.sentenceId,
      });
      const response = await fetch(`/api/web/dict/lookup?${params.toString()}`);
      const payload = (await response.json().catch(() => null)) as
        | WebDictResult
        | null;

      if (!payload) {
        setLookupState({
          kind: "error",
          query,
          context,
          message: "词典查询失败。",
        });
        return;
      }

      if (!response.ok && payload.kind !== "error") {
        setLookupState({
          kind: "error",
          query,
          context,
          message: "词典查询失败。",
        });
        return;
      }

      setLookupState({ kind: "ready", query, context, result: payload });
    } catch (error) {
      console.warn("[ReaderRecordPlateSurface] dictionary lookup failed", error);
      setLookupState({
        kind: "error",
        query,
        context,
        message: "词典查询失败，请稍后重试。",
      });
    }
  }, [activeSelection, quickPeekFloating.refs]);

  const activeLookupSnapshot = useMemo(
    () => buildDictionaryLookupSnapshot(snapshot, lookupState),
    [snapshot, lookupState],
  );
  const currentAskSelectionAttachment = useMemo<ReaderAskAttachment | null>(() => {
    const selection = activeSelection;
    const draft = singleRangeDraft(selection);
    const segment = selection?.supportedSingleRange ? (selection.segments[0] ?? null) : null;
    if (!selection || !draft || !segment) {
      return null;
    }

    return {
      kind: "text_selection",
      subtype: selection.anchorType,
      label: draft.selected_text,
      selectedText: draft.selected_text,
      targetKey: draft.anchor_segment_id,
      metadata: {
        pageIdentity: askPageIdentity,
        sourceSurface: "selection_toolbar",
        entryAction: "ask_about_this",
        sentenceId: segment.sentenceId,
        paragraphId: segment.paragraphId,
        startOffset: draft.start_offset,
        endOffset: draft.end_offset,
        readingRecordAnchor: readingRecordAskAnchorFromDraft(draft),
      },
    };
  }, [activeSelection, askPageIdentity]);

  const openDictionaryRail = useCallback(() => {
    setDictionaryOpen(true);
    setLookupState({ kind: "idle" });
    if (activeLookupSnapshot) {
      setDictionaryHistory((current) => {
        const filtered = current.filter(
          (item) => item.query !== activeLookupSnapshot.query,
        );
        return [activeLookupSnapshot, ...filtered].slice(0, 20);
      });
      setDictionarySearchQuery(activeLookupSnapshot.query);
    }
  }, [activeLookupSnapshot]);

  const closeDictionaryRail = useCallback(() => {
    setDictionaryOpen(false);
  }, []);

  const handleRemoveAskAttachment = useCallback((attachmentKey: string) => {
    setAskAttachments((current) =>
      current.filter((attachment) => askAttachmentKey(attachment) !== attachmentKey),
    );
  }, []);

  const handleAskActionExecuted = useCallback(
    (result: ReaderAskActionConfirmResponseDto["result"]) => {
      if (
        result.annotation_id ||
        result.note_id ||
        result.persisted_supplement
      ) {
        void onRequestSnapshotReload?.();
      }
    },
    [onRequestSnapshotReload],
  );

  const handleAskSupplementDeleted = useCallback(() => {
    void onRequestSnapshotReload?.();
  }, [onRequestSnapshotReload]);

  const openAskPanel = useCallback((attachment?: ReaderAskAttachment | null) => {
    if (attachment === null) {
      setAskAttachments([]);
    } else if (attachment) {
      setAskAttachments([attachment]);
    }
    setAskOpen(true);
    setToolbarOpen(false);
    setDictionaryOpen(false);
    setDictionaryAIPanelOpen(false);
    setDictionaryAI({ kind: "idle" });
    setLookupState({ kind: "idle" });
    setHighlightMenu(null);
    setNoteMenu(null);
    setFeedbackTarget(null);
    window.getSelection()?.removeAllRanges();
  }, []);

  const handleAskFromSelection = useCallback(() => {
    if (!currentAskSelectionAttachment) {
      return;
    }
    openAskPanel(currentAskSelectionAttachment);
  }, [currentAskSelectionAttachment, openAskPanel]);

  const handleAskFromNote = useCallback(() => {
    const activeMenu = noteMenu;
    if (!activeMenu) {
      return;
    }
    const { anchor, assetId, assetType, noteText } = activeMenu.mark;
    openAskPanel({
      kind: "annotation_ref",
      subtype: "reader_note",
      label: noteText.trim() || anchor.selectedText,
      selectedText: anchor.selectedText,
      targetKey: anchor.anchorSegmentId,
      metadata: {
        pageIdentity: askPageIdentity,
        sourceSurface: "note_menu",
        entryAction: "ask_about_this",
        assetId,
        annotationType: assetType,
        sentenceId: anchor.sentenceId,
        paragraphId: anchor.unitId,
        note: noteText,
        title: "笔记",
        readingRecordAnchor: readingRecordAskAnchorFromTextAnchor(
          snapshot.record_id,
          snapshot.record.generation,
          anchor,
        ),
      },
    });
  }, [askPageIdentity, noteMenu, openAskPanel, snapshot.record.generation, snapshot.record_id]);

  const handleRequestAI = useCallback(() => {
    openAskPanel(currentAskSelectionAttachment);
  }, [currentAskSelectionAttachment, openAskPanel]);

  const handleDictionarySearch = useCallback(
    async (query: string) => {
      const trimmed = query.trim();
      if (!trimmed) {
        return;
      }
      setDictionarySearchExpanded(false);
      setDictionaryOpen(true);
      const lookupType = lookupTypeForSelection(trimmed);
      const context: ReaderRecordLookupContext = {
        contextSentence: "",
        sentenceId: "__manual__",
        anchorText: trimmed,
        lookupType,
        source: "selection",
      };
      setLookupState({ kind: "loading", query: trimmed, context });
      try {
        const params = new URLSearchParams({
          word: trimmed,
          type: lookupType,
          context: "",
          sentenceId: "__manual__",
        });
        const response = await fetch(
          `/api/web/dict/lookup?${params.toString()}`,
        );
        const payload = (await response.json().catch(() => null)) as
          | WebDictResult
          | null;
        if (!payload || (!response.ok && payload.kind !== "error")) {
          setLookupState({
            kind: "error",
            query: trimmed,
            context,
            message: "词典查询失败。",
          });
          return;
        }
        setLookupState({ kind: "ready", query: trimmed, context, result: payload });
      } catch (error) {
        console.warn("[ReaderRecordPlateSurface] dictionary search failed", error);
        setLookupState({
          kind: "error",
          query: trimmed,
          context,
          message: "词典查询失败，请稍后重试。",
        });
      }
    },
    [],
  );

  const handleSelectHistory = useCallback(
    (historyLookup: DictionaryLookupSnapshot) => {
      setDictionarySearchQuery(historyLookup.query);
      setDictionarySaveState({ kind: "idle" });
      setDictionaryAI({ kind: "idle" });
      setDictionaryAIPanelOpen(false);
      setDictionaryAINoteState({ kind: "idle" });
      const context: ReaderRecordLookupContext = {
        contextSentence: historyLookup.contextSentence,
        sentenceId: historyLookup.sentenceId,
        anchorText: historyLookup.anchorText,
        lookupType: historyLookup.lookupType,
        source: "selection",
      };
      if (historyLookup.state.kind === "ready") {
        setLookupState({
          kind: "ready",
          query: historyLookup.query,
          context,
          result: historyLookup.state.result,
        });
      } else if (historyLookup.state.kind === "error") {
        setLookupState({
          kind: "error",
          query: historyLookup.query,
          context,
          message: historyLookup.state.message,
        });
      } else {
        setLookupState({ kind: "loading", query: historyLookup.query, context });
      }
    },
    [],
  );

  const handleSaveVocabulary = useCallback(async () => {
    if (!activeLookupSnapshot) {
      return;
    }
    if (lookupState.kind !== "ready" || lookupState.result.kind !== "entry") {
      setDictionarySaveState({
        kind: "error",
        message: "当前词条暂不支持保存，请先完成词典查询。",
      });
      return;
    }
    const entry = lookupState.result.entry;
    const shortMeaning = firstMeaning(lookupState.result);
    if (!shortMeaning) {
      setDictionarySaveState({
        kind: "error",
        message: "当前词条暂无简短释义，无法保存。",
      });
      return;
    }
    if (!activeLookupSnapshot.contextSentence.trim()) {
      setDictionarySaveState({
        kind: "error",
        message: "请先选中包含该词的句子后再保存。",
      });
      return;
    }
    setDictionarySaveState({ kind: "saving" });
    try {
      const response = await fetch("/api/web/vocabulary", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          accept: "application/json",
        },
        body: JSON.stringify({
          lemma: entry.word,
          display_word: entry.word,
          phonetic: entry.phonetic ?? null,
          short_meaning: shortMeaning,
          meanings_json: meaningsJson(lookupState.result),
          source_provider: "reader_record",
          dict_entry_id: entry.id,
          source_sentence: activeLookupSnapshot.contextSentence,
          source_context: activeLookupSnapshot.contextSentence,
          payload_json: {
            source_refs: [
              {
                reading_record_id: snapshot.record_id,
                source_sentence_id: activeLookupSnapshot.sentenceId,
                source_anchor_text: activeLookupSnapshot.anchorText,
                collected_at: new Date().toISOString(),
              },
            ],
          },
        }),
      });
      const payload = (await response.json().catch(() => null)) as
        | { ok?: boolean; message?: string }
        | null;
      if (!response.ok || payload?.ok === false) {
        throw new Error(payload?.message ?? "词汇保存失败。");
      }
      setDictionarySaveState({
        kind: "saved",
        message: "已加入生词本",
      });
    } catch (error) {
      console.warn("[ReaderRecordPlateSurface] vocabulary save failed", error);
      setDictionarySaveState({
        kind: "error",
        message: "词汇保存失败，请稍后重试。",
      });
    }
  }, [activeLookupSnapshot, lookupState, snapshot.record_id]);

  const handleOpenFeedback = useCallback(
    (
      block: ReaderRecordPlateCalloutBlock,
      anchor: HTMLElement,
    ) => {
      setFeedbackTarget({
        blockId: block.id,
        variant: block.variant,
        anchorSegmentId: block.data.anchorSegmentId,
        title:
          block.variant === "grammar"
            ? (block.data.grammarPoint ?? "")
            : block.variant === "supplement"
              ? (block.data.supplementTitle ?? "")
              : (block.data.label ?? ""),
      });
      feedbackFloating.refs.setReference({
        getBoundingClientRect: () => anchor.getBoundingClientRect(),
      });
    },
    [feedbackFloating.refs],
  );

  const handleSubmitFeedback = useCallback(
    async (sentiment: "positive" | "negative") => {
      const target = feedbackTarget;
      if (!target) {
        return;
      }
      setFeedbackTarget(null);
      setFeedbackState({ kind: "saving" });
      try {
        const response = await fetch("/api/web/feedback", {
          method: "POST",
          headers: {
            "content-type": "application/json",
            accept: "application/json",
          },
          body: JSON.stringify({
            feedbackScope: "annotation",
            targetId: target.blockId,
            sentiment,
            feedbackType: sentiment === "positive" ? "helpful" : "other",
            annotationType:
              target.variant === "grammar"
                ? "grammar_note"
                : target.variant === "supplement"
                  ? "ask_supplement"
                  : "sentence_analysis",
            entryPoint: "reader_record_callout",
            contextSummary: target.title,
            clientPlatform: "web",
            clientSurface: "reader_record",
          }),
        });
        const payload = (await response.json().catch(() => null)) as
          | { ok?: boolean; message?: string }
          | null;
        if (!response.ok || payload?.ok === false) {
          throw new Error(payload?.message ?? "反馈提交失败。");
        }
        setFeedbackState({
          kind: "saved",
          message: sentiment === "positive" ? "感谢反馈" : "已记录问题",
        });
      } catch (error) {
        console.warn("[ReaderRecordPlateSurface] feedback submit failed", error);
        setFeedbackState({
          kind: "error",
          message: "反馈提交失败，请稍后重试。",
        });
      }
    },
    [feedbackTarget],
  );

  const handleSelectCandidate = useCallback(
    async (entryId: number) => {
      if (!activeLookupSnapshot) {
        return;
      }
      const baseContext: ReaderRecordLookupContext = {
        contextSentence: activeLookupSnapshot.contextSentence,
        sentenceId: activeLookupSnapshot.sentenceId,
        anchorText: activeLookupSnapshot.anchorText,
        lookupType: activeLookupSnapshot.lookupType,
        source: "selection",
      };
      setLookupState({
        kind: "loading",
        query: activeLookupSnapshot.query,
        context: baseContext,
      });
      try {
        const response = await fetch(
          `/api/web/dict/entry?id=${encodeURIComponent(entryId)}`,
        );
        const payload = (await response.json().catch(() => null)) as
          | WebDictResult
          | null;
        if (!payload || (!response.ok && payload.kind !== "error")) {
          setLookupState({
            kind: "error",
            query: activeLookupSnapshot.query,
            context: baseContext,
            message: "词典候选加载失败。",
          });
          return;
        }
        setLookupState({
          kind: "ready",
          query: activeLookupSnapshot.query,
          context: baseContext,
          result: payload,
        });
      } catch (error) {
        console.warn("[ReaderRecordPlateSurface] candidate select failed", error);
        setLookupState({
          kind: "error",
          query: activeLookupSnapshot.query,
          context: baseContext,
          message: "词典候选加载失败，请稍后重试。",
        });
      }
    },
    [activeLookupSnapshot],
  );

  const handleHighlight = useCallback(async (color: string = "soft_green") => {
    const draft = singleRangeDraft(activeSelection);
    if (!draft || writeState.kind === "saving") {
      return;
    }

    const tempAsset = buildTempUserAsset(snapshot, draft, {
      kind: "highlight",
      color,
    });
    setLocalUserAssets((current) => [...current, tempAsset]);
    setWriteState({ kind: "saving", action: "highlight" });

    try {
      await postReadingRecordUserAsset("/api/web/reading-record/highlights", {
        anchor: draft,
        selectedText: draft.selected_text,
        color,
      });
      setWriteState({
        kind: "saved",
        action: "highlight",
        message: "高亮已保存",
      });
      await onRequestSnapshotReload?.();
    } catch (error) {
      console.warn("[ReaderRecordPlateSurface] highlight save failed", error);
      setLocalUserAssets((current) =>
        current.filter((asset) => asset.asset_id !== tempAsset.asset_id),
      );
      setWriteState({
        kind: "error",
        action: "highlight",
        message: "高亮保存失败，请稍后重试。",
      });
    }
  }, [activeSelection, onRequestSnapshotReload, snapshot, writeState.kind]);

  const handleOpenNoteComposer = useCallback(() => {
    const draft = singleRangeDraft(activeSelection);
    if (!draft || writeState.kind === "saving") {
      return;
    }

    setNoteAnchorDraft(draft);
    setNoteDraft("");
    setWriteState({ kind: "idle" });
  }, [activeSelection, writeState.kind]);

  const handleCancelNote = useCallback(() => {
    if (writeState.kind === "saving") {
      return;
    }
    setNoteAnchorDraft(null);
    setNoteDraft("");
  }, [writeState.kind]);

  const handleSaveNote = useCallback(async () => {
    const draft = noteAnchorDraft;
    const noteText = noteDraft.trim();
    if (!draft || !noteText || writeState.kind === "saving") {
      return;
    }

    const tempAsset = buildTempUserAsset(snapshot, draft, {
      kind: "note",
      noteText,
    });
    setLocalUserAssets((current) => [...current, tempAsset]);
    setWriteState({ kind: "saving", action: "note" });

    try {
      await postReadingRecordUserAsset("/api/web/reading-record/notes", {
        anchor: draft,
        selectedText: draft.selected_text,
        noteText,
      });
      setNoteAnchorDraft(null);
      setNoteDraft("");
      setWriteState({
        kind: "saved",
        action: "note",
        message: "笔记已保存",
      });
      await onRequestSnapshotReload?.();
    } catch (error) {
      console.warn("[ReaderRecordPlateSurface] note save failed", error);
      setLocalUserAssets((current) =>
        current.filter((asset) => asset.asset_id !== tempAsset.asset_id),
      );
      setWriteState({
        kind: "error",
        action: "note",
        message: "笔记保存失败，请稍后重试。",
      });
    }
  }, [noteAnchorDraft, noteDraft, onRequestSnapshotReload, snapshot, writeState.kind]);

  const handleActivateHighlight = useCallback(
    (mark: ReaderRecordPlateUserHighlightMark, anchor: HTMLElement) => {
      setHighlightMenu({ mark, anchor });
      highlightMenuFloating.refs.setReference({
        getBoundingClientRect: () => anchor.getBoundingClientRect(),
      });
    },
    [highlightMenuFloating.refs],
  );

  const handleDeleteHighlight = useCallback(async () => {
    const activeMenu = highlightMenu;
    if (!activeMenu || writeState.kind === "saving") {
      return;
    }

    const deletedAssetId = activeMenu.mark.assetId;
    const previousAssets = localUserAssets;
    setHighlightMenu(null);
    setLocalUserAssets((current) =>
      current.filter((asset) => asset.asset_id !== deletedAssetId),
    );
    setWriteState({ kind: "saving", action: "highlight" });

    try {
      const response = await fetch(
        `/api/web/reading-record/highlights/${encodeURIComponent(deletedAssetId)}`,
        { method: "DELETE" },
      );
      const payload = (await response.json().catch(() => null)) as
        | { ok?: boolean; message?: string }
        | null;
      if (!response.ok || payload?.ok === false) {
        throw new Error(payload?.message ?? "高亮删除失败。");
      }
      setWriteState({
        kind: "saved",
        action: "highlight",
        message: "高亮已删除",
      });
      await onRequestSnapshotReload?.();
    } catch (error) {
      console.warn("[ReaderRecordPlateSurface] highlight delete failed", error);
      setLocalUserAssets(previousAssets);
      setWriteState({
        kind: "error",
        action: "highlight",
        message: "高亮删除失败，请稍后重试。",
      });
    }
  }, [highlightMenu, localUserAssets, onRequestSnapshotReload, writeState.kind]);

  const handleUpdateHighlightColor = useCallback(
    async (color: string) => {
      const activeMenu = highlightMenu;
      if (!activeMenu || writeState.kind === "saving") {
        return;
      }

      const targetAssetId = activeMenu.mark.assetId;
      const previousAssets = localUserAssets;
      setHighlightMenu(null);
      setLocalUserAssets((current) =>
        current.map((asset) =>
          asset.asset_id === targetAssetId
            ? { ...asset, color, updated_at: new Date().toISOString() }
            : asset,
        ),
      );
      setWriteState({ kind: "saving", action: "highlight" });

      try {
        const response = await fetch(
          `/api/web/reading-record/highlights/${encodeURIComponent(targetAssetId)}`,
          {
            method: "PATCH",
            headers: {
              "content-type": "application/json",
              accept: "application/json",
            },
            body: JSON.stringify({ color }),
          },
        );
        const payload = (await response.json().catch(() => null)) as
          | { ok?: boolean; message?: string }
          | null;
        if (!response.ok || payload?.ok === false) {
          throw new Error(payload?.message ?? "高亮更新失败。");
        }
        setWriteState({
          kind: "saved",
          action: "highlight",
          message: "高亮颜色已更新",
        });
        await onRequestSnapshotReload?.();
      } catch (error) {
        console.warn("[ReaderRecordPlateSurface] highlight update failed", error);
        setLocalUserAssets(previousAssets);
        setWriteState({
          kind: "error",
          action: "highlight",
          message: "高亮更新失败，请稍后重试。",
        });
      }
    },
    [highlightMenu, localUserAssets, onRequestSnapshotReload, writeState.kind],
  );

  useEffect(() => {
    if (highlightMenu === null) {
      return;
    }
    const activeMenu = highlightMenu;
    function handlePointerDown(event: PointerEvent) {
      const target = event.target as Node | null;
      if (!target) {
        return;
      }
      if (highlightMenuFloating.refs.floating.current?.contains(target)) {
        return;
      }
      if (activeMenu.anchor.contains(target)) {
        return;
      }
      setHighlightMenu(null);
    }
    window.document.addEventListener("pointerdown", handlePointerDown);
    return () => {
      window.document.removeEventListener("pointerdown", handlePointerDown);
    };
  }, [highlightMenu, highlightMenuFloating.refs.floating]);

  const handleActivateNote = useCallback(
    (mark: ReaderRecordPlateUserNoteMark, anchor: HTMLElement) => {
      setNoteMenu({ mark, anchor, mode: "view", draft: mark.noteText });
      noteMenuFloating.refs.setReference({
        getBoundingClientRect: () => anchor.getBoundingClientRect(),
      });
    },
    [noteMenuFloating.refs],
  );

  const handleStartEditNote = useCallback(() => {
    setNoteMenu((current) =>
      current ? { ...current, mode: "edit", draft: current.mark.noteText } : null,
    );
  }, []);

  const handleCancelEditNote = useCallback(() => {
    setNoteMenu((current) =>
      current ? { ...current, mode: "view", draft: current.mark.noteText } : null,
    );
  }, []);

  const handleSaveNoteEdit = useCallback(async () => {
    const activeMenu = noteMenu;
    if (!activeMenu || writeState.kind === "saving") {
      return;
    }
    const noteText = activeMenu.draft.trim();
    if (!noteText) {
      return;
    }

    const targetAssetId = activeMenu.mark.assetId;
    const previousAssets = localUserAssets;
    setLocalUserAssets((current) =>
      current.map((asset) =>
        asset.asset_id === targetAssetId
          ? { ...asset, note_text: noteText, updated_at: new Date().toISOString() }
          : asset,
      ),
    );
    setWriteState({ kind: "saving", action: "note" });

    try {
      const response = await fetch(
        `/api/web/reading-record/notes/${encodeURIComponent(targetAssetId)}`,
        {
          method: "PATCH",
          headers: {
            "content-type": "application/json",
            accept: "application/json",
          },
          body: JSON.stringify({ noteText }),
        },
      );
      const payload = (await response.json().catch(() => null)) as
        | { ok?: boolean; message?: string }
        | null;
      if (!response.ok || payload?.ok === false) {
        throw new Error(payload?.message ?? "笔记更新失败。");
      }
      setNoteMenu(null);
      setWriteState({
        kind: "saved",
        action: "note",
        message: "笔记已更新",
      });
      await onRequestSnapshotReload?.();
    } catch (error) {
      console.warn("[ReaderRecordPlateSurface] note update failed", error);
      setLocalUserAssets(previousAssets);
      setWriteState({
        kind: "error",
        action: "note",
        message: "笔记更新失败，请稍后重试。",
      });
    }
  }, [noteMenu, localUserAssets, onRequestSnapshotReload, writeState.kind]);

  const handleDeleteNote = useCallback(async () => {
    const activeMenu = noteMenu;
    if (!activeMenu || writeState.kind === "saving") {
      return;
    }

    const deletedAssetId = activeMenu.mark.assetId;
    const previousAssets = localUserAssets;
    setNoteMenu(null);
    setLocalUserAssets((current) =>
      current.filter((asset) => asset.asset_id !== deletedAssetId),
    );
    setWriteState({ kind: "saving", action: "note" });

    try {
      const response = await fetch(
        `/api/web/reading-record/notes/${encodeURIComponent(deletedAssetId)}`,
        { method: "DELETE" },
      );
      const payload = (await response.json().catch(() => null)) as
        | { ok?: boolean; message?: string }
        | null;
      if (!response.ok || payload?.ok === false) {
        throw new Error(payload?.message ?? "笔记删除失败。");
      }
      setWriteState({
        kind: "saved",
        action: "note",
        message: "笔记已删除",
      });
      await onRequestSnapshotReload?.();
    } catch (error) {
      console.warn("[ReaderRecordPlateSurface] note delete failed", error);
      setLocalUserAssets(previousAssets);
      setWriteState({
        kind: "error",
        action: "note",
        message: "笔记删除失败，请稍后重试。",
      });
    }
  }, [noteMenu, localUserAssets, onRequestSnapshotReload, writeState.kind]);

  useEffect(() => {
    if (noteMenu === null) {
      return;
    }
    const activeMenu = noteMenu;
    function handlePointerDown(event: PointerEvent) {
      const target = event.target as Node | null;
      if (!target) {
        return;
      }
      if (noteMenuFloating.refs.floating.current?.contains(target)) {
        return;
      }
      if (activeMenu.anchor.contains(target)) {
        return;
      }
      setNoteMenu(null);
    }
    window.document.addEventListener("pointerdown", handlePointerDown);
    return () => {
      window.document.removeEventListener("pointerdown", handlePointerDown);
    };
  }, [noteMenu, noteMenuFloating.refs.floating]);

  return (
    <section
      ref={surfaceRef}
      data-testid="reader-record-plate-surface"
      data-reader-record-surface="plate-readonly-reading"
      className={`${className} ${themeClassName}`}
    >
      <div className={columnClassName}>
        <ReaderRecordHeader
          snapshot={snapshot}
          progress={plateDocument.progress}
          surfaceMode={surfaceMode}
          readerSettings={readerSettings}
          onModeChange={handleModeChange}
          onOpenSettings={() => setSettingsPanelOpen(true)}
        />
        {settingsPanelOpen ? (
          <div className="mb-6">
            <ReaderSettingsPanel
              themeName={themeName}
              value={readerSettings}
              onChange={handleSettingsChange}
              onThemeChange={setThemeName}
              onClose={() => setSettingsPanelOpen(false)}
            />
          </div>
        ) : null}
        <SelectionActionStrip
          copyStatus={copyStatus}
          lookupState={lookupState}
          selection={activeSelection}
          writeState={writeState}
          noteComposerOpen={noteAnchorDraft !== null}
          onAsk={handleAskFromSelection}
          onCopy={handleCopy}
          onHighlight={handleHighlight}
          onLookup={handleLookup}
          onOpenNoteComposer={handleOpenNoteComposer}
        />
        {toolbarOpen ? (
          <ReaderFloatingSurface
            floatingRef={toolbarFloating.refs.setFloating}
            style={toolbarFloating.floatingStyles}
            data-reader-record-floating-toolbar="selection"
          >
            <SelectionToolbar
              selectedText={singleRangeDraft(activeSelection)?.selected_text ?? ""}
              disabled={{
                selectSentence: true,
                clear: true,
                feedback: true,
              }}
              statusMessage={
                writeState.kind !== "idle" ? writeStateLabel(writeState) : undefined
              }
              statusKind={
                writeState.kind === "saving"
                  ? "saving"
                  : writeState.kind === "saved"
                    ? "saved"
                    : writeState.kind === "error"
                      ? "error"
                      : undefined
              }
              onAsk={() => handleAskFromSelection()}
              onHighlight={() => handleHighlight()}
              onNote={() => handleOpenNoteComposer()}
              onLookup={() => handleLookup()}
            />
          </ReaderFloatingSurface>
        ) : null}
        {highlightMenu ? (
          <ReaderFloatingSurface
            floatingRef={highlightMenuFloating.refs.setFloating}
            style={highlightMenuFloating.floatingStyles}
            data-reader-record-floating-toolbar="highlight-menu"
          >
            <div className="flex items-center gap-2 rounded-lg border border-border/60 bg-background/95 p-2 shadow-md backdrop-blur-sm">
              <span className="px-1 text-xs text-muted">改色</span>
              {HIGHLIGHT_COLOR_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  aria-label={`切换为${option.label}`}
                  data-reader-record-highlight-color={option.value}
                  onClick={() => handleUpdateHighlightColor(option.value)}
                  className={`h-4 w-4 rounded-[4px] ring-1 ring-inset ring-border/70 transition-transform hover:scale-110 ${option.swatchClassName}`}
                />
              ))}
              <span className="mx-1 h-4 w-px bg-border/40" />
              <button
                type="button"
                aria-label="删除高亮"
                data-reader-record-highlight-action="delete"
                onClick={handleDeleteHighlight}
                className="rounded-md px-2 py-1 text-xs text-rose-600 transition-colors hover:bg-rose-50"
              >
                删除
              </button>
            </div>
          </ReaderFloatingSurface>
        ) : null}
        {noteMenu ? (
          <ReaderFloatingSurface
            floatingRef={noteMenuFloating.refs.setFloating}
            style={noteMenuFloating.floatingStyles}
            data-reader-record-floating-toolbar="note-menu"
          >
            <div className="w-72 rounded-lg border border-border/60 bg-background/95 p-3 shadow-md backdrop-blur-sm">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-muted">
                  笔记
                </span>
                {noteMenu.mode === "view" ? (
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      aria-label="Ask 关于这条笔记"
                      data-reader-record-note-action="ask"
                      onClick={handleAskFromNote}
                      className="rounded-md px-2 py-0.5 text-xs text-lens-blue transition-colors hover:bg-lens-blue/5"
                    >
                      Ask
                    </button>
                    <button
                      type="button"
                      aria-label="编辑笔记"
                      data-reader-record-note-action="edit"
                      onClick={handleStartEditNote}
                      className="rounded-md px-2 py-0.5 text-xs text-lens-blue transition-colors hover:bg-lens-blue/5"
                    >
                      编辑
                    </button>
                    <button
                      type="button"
                      aria-label="删除笔记"
                      data-reader-record-note-action="delete"
                      onClick={handleDeleteNote}
                      className="rounded-md px-2 py-0.5 text-xs text-rose-600 transition-colors hover:bg-rose-50"
                    >
                      删除
                    </button>
                  </div>
                ) : null}
              </div>
              {noteMenu.mode === "view" ? (
                <p
                  data-reader-record-note-content="view"
                  className="whitespace-pre-wrap break-words text-sm leading-6 text-ink"
                >
                  {noteMenu.mark.noteText}
                </p>
              ) : (
                <div className="space-y-2">
                  <textarea
                    aria-label="编辑笔记内容"
                    data-reader-record-note-input="edit"
                    value={noteMenu.draft}
                    rows={3}
                    onChange={(event) =>
                      setNoteMenu((current) =>
                        current ? { ...current, draft: event.currentTarget.value } : null,
                      )
                    }
                    className="w-full resize-y rounded-md border border-border bg-background px-2.5 py-1.5 text-sm leading-6 text-ink outline-none focus:border-lens-blue"
                  />
                  <div className="flex items-center justify-end gap-2">
                    <button
                      type="button"
                      aria-label="取消编辑笔记"
                      data-reader-record-note-action="cancel-edit"
                      onClick={handleCancelEditNote}
                      className={actionButtonClassName(true)}
                    >
                      取消
                    </button>
                    <button
                      type="button"
                      aria-label="保存笔记"
                      data-reader-record-note-action="save"
                      onClick={handleSaveNoteEdit}
                      disabled={
                        writeState.kind === "saving" || noteMenu.draft.trim().length === 0
                      }
                      className={actionButtonClassName(
                        !(writeState.kind === "saving" || noteMenu.draft.trim().length === 0),
                      )}
                    >
                      {writeState.kind === "saving" ? "保存中" : "保存"}
                    </button>
                  </div>
                </div>
              )}
            </div>
          </ReaderFloatingSurface>
        ) : null}
        {quickPeekOpen ? (
          <ReaderQuickPeek
            lookup={activeLookupSnapshot}
            className="reader-tool-float"
            floatingRef={(node) => {
              quickPeekFloating.refs.setFloating(node);
              if (node) {
                node.setAttribute("data-reader-record-quick-peek", lookupState.kind);
                node.setAttribute("data-testid", "reader-record-plate-lookup-panel");
              }
            }}
            style={quickPeekFloating.floatingStyles}
            onDismiss={() => setLookupState({ kind: "idle" })}
            onOpenDetail={openDictionaryRail}
          />
        ) : null}
        {feedbackTarget ? (
          <ReaderFloatingSurface
            floatingRef={feedbackFloating.refs.setFloating}
            style={feedbackFloating.floatingStyles}
            className="w-44 rounded-lg border border-border bg-popover p-1 shadow-lg"
            role="dialog"
            aria-label="反馈选项"
            data-reader-record-feedback-menu="open"
            onClick={(event) => event.stopPropagation()}
            onPointerDown={(event) => event.stopPropagation()}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.stopPropagation();
                setFeedbackTarget(null);
              }
            }}
          >
            <button
              type="button"
              className="block w-full rounded-sm px-3 py-1.5 text-left text-sm text-foreground hover:bg-structure-green/10"
              onClick={() => handleSubmitFeedback("positive")}
            >
              有帮助
            </button>
            <button
              type="button"
              className="mt-0.5 block w-full rounded-sm px-3 py-1.5 text-left text-sm text-foreground hover:bg-error-red/10"
              onClick={() => handleSubmitFeedback("negative")}
            >
              有问题
            </button>
          </ReaderFloatingSurface>
        ) : null}
        {noteAnchorDraft ? (
          <ReaderRecordNoteComposer
            noteDraft={noteDraft}
            saving={writeState.kind === "saving" && writeState.action === "note"}
            onCancel={handleCancelNote}
            onChange={setNoteDraft}
            onSave={handleSaveNote}
          />
        ) : null}
        <div
          className="reader-record-plate-document space-y-3 px-0 py-0 outline-none"
          data-reader-record-mode={surfaceMode}
        >
          {visibleBlocks.map((block) => {
            switch (block.type) {
              case "paragraph":
                return (
                  <ParagraphBlock
                    key={block.id}
                    block={block}
                    readingClassName={`${readingClassName} ${typography.bodyClassName}`}
                    onActivateVocabulary={handleActivateVocabulary}
                    onActivateHighlight={handleActivateHighlight}
                    onActivateNote={handleActivateNote}
                  />
                );
              case "blockquote":
                return <BlockquoteBlock key={block.id} block={block} />;
              case "callout":
                return <CalloutBlock key={block.id} block={block} onFeedback={handleOpenFeedback} />;
              default:
                return null;
            }
          })}
        </div>
        {feedbackState.kind !== "idle" ? (
          <div
            data-reader-record-feedback-status={feedbackState.kind}
            className={`mt-3 text-sm ${
              feedbackState.kind === "error"
                ? "text-rose-700"
                : feedbackState.kind === "saved"
                  ? "text-emerald-700"
                  : "text-muted"
            }`}
            role="status"
            aria-live="polite"
          >
            {feedbackState.kind === "saving"
              ? "正在提交反馈"
              : feedbackState.kind === "saved" || feedbackState.kind === "error"
                ? feedbackState.message
                : ""}
          </div>
        ) : null}
      </div>
      <AiWorkspacePanel
        open={askOpen}
        presentation={surfaceMode}
        pageIdentity={askPageIdentity}
        recordId={snapshot.record_id}
        recordScope="reading_record"
        hideClosedLauncher
        recordTitle={snapshot.record.title}
        attachments={askAttachments}
        onRemoveAttachment={handleRemoveAskAttachment}
        onClearAttachments={() => setAskAttachments([])}
        onToggle={() => setAskOpen(false)}
        onActionExecuted={handleAskActionExecuted}
        onSupplementDeleted={handleAskSupplementDeleted}
      />
      {dictionaryOpen ? (
        <div
          className="reader-tool-surface reader-tool-surface--rail fixed top-3 bottom-3 left-3 z-40 hidden xl:block w-[420px]"
          data-reader-record-dictionary-rail="docked"
        >
          <ReaderDictionaryRail
            className="h-full"
            lookup={activeLookupSnapshot}
            history={dictionaryHistory}
            readingGoal=""
            saveState={dictionarySaveState}
            dictionaryAI={dictionaryAI}
            dictionaryAIPanelOpen={dictionaryAIPanelOpen}
            dictionaryAINoteState={dictionaryAINoteState}
            searchQuery={dictionarySearchQuery}
            searchExpanded={dictionarySearchExpanded}
            onSave={handleSaveVocabulary}
            onRequestAI={handleRequestAI}
            onCreateAINote={() => undefined}
            onSelectAISuggestedQuery={() => undefined}
            onSearchQueryChange={setDictionarySearchQuery}
            onSearchSubmit={handleDictionarySearch}
            onSelectCandidate={handleSelectCandidate}
            onToggleAIPanel={() => setDictionaryAIPanelOpen((v) => !v)}
            onToggleSearchExpanded={() => setDictionarySearchExpanded((v) => !v)}
            onDismiss={closeDictionaryRail}
            variant="card"
            canSaveVocabulary={Boolean(activeLookupSnapshot?.contextSentence.trim())}
            canCreateAINote={false}
            onSelectHistory={handleSelectHistory}
          />
        </div>
      ) : null}
      {dictionaryOpen ? (
        <div
          className="reader-tool-surface reader-tool-surface--compact fixed inset-x-3 bottom-3 z-50 flex max-h-[72vh] flex-col xl:hidden"
          data-reader-record-dictionary-rail="sheet"
        >
          <ReaderDictionaryRail
            lookup={activeLookupSnapshot}
            history={dictionaryHistory}
            readingGoal=""
            saveState={dictionarySaveState}
            dictionaryAI={dictionaryAI}
            dictionaryAIPanelOpen={dictionaryAIPanelOpen}
            dictionaryAINoteState={dictionaryAINoteState}
            searchQuery={dictionarySearchQuery}
            searchExpanded={dictionarySearchExpanded}
            onSave={handleSaveVocabulary}
            onRequestAI={handleRequestAI}
            onCreateAINote={() => undefined}
            onSelectAISuggestedQuery={() => undefined}
            onSearchQueryChange={setDictionarySearchQuery}
            onSearchSubmit={handleDictionarySearch}
            onSelectCandidate={handleSelectCandidate}
            onToggleAIPanel={() => setDictionaryAIPanelOpen((v) => !v)}
            onToggleSearchExpanded={() => setDictionarySearchExpanded((v) => !v)}
            onDismiss={closeDictionaryRail}
            variant="sheet"
            canSaveVocabulary={Boolean(activeLookupSnapshot?.contextSentence.trim())}
            canCreateAINote={false}
            onSelectHistory={handleSelectHistory}
          />
        </div>
      ) : null}
    </section>
  );
}
