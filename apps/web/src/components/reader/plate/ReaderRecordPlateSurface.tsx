"use client";

import {
  TEXT_RANGE_HASH_ALGORITHM,
  TEXT_RANGE_OFFSET_UNIT,
  buildTextRangeTargetKey,
} from "@claread/contracts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  ClipboardEvent as ReactClipboardEvent,
  CSSProperties,
  HTMLAttributes,
  MouseEvent,
  PointerEvent as ReactPointerEvent,
  Ref,
} from "react";

import { AiWorkspacePanel } from "@/components/reader/AiWorkspacePanel";
import type { DictLookupTypeDto, WebDictResult } from "@/types/api/dict";
import {
  projectReaderPlateSnapshotToReaderRecordPlateDocument,
  type ReaderRecordPlateBlock,
  type ReaderRecordPlateParagraphBlock,
  type ReaderRecordPlateProgress,
  type ReaderRecordPlateTextAnchor,
  type ReaderRecordPlateGrammarMark,
  type ReaderRecordPlateUserHighlightMark,
  type ReaderRecordPlateUserNoteMark,
  type ReaderRecordPlateVocabularyMark,
} from "@/lib/reader-plate/projection/reader-record-plate-document";
import {
  type ReaderRecordSelectionAnchorBridgeResult,
} from "@/lib/reader-plate/projection/reader-record-dom-selection";
import {
  askAttachmentKey,
  type ReaderAskAttachment,
  type ReaderAskPageIdentity,
  type ReaderStructuredInspectIntent,
} from "@/lib/reader-plate";
import type { ReaderAnchorPayload } from "@/lib/reader-plate/bridges/assets";
import type { ReaderRecordAnchorDraft } from "@/lib/reader-plate/projection/reader-record-anchor-draft";
import {
  READER_TEXT_RANGE_HASH_ALGORITHM,
  READER_TEXT_RANGE_OFFSET_UNIT,
  type ReaderPlateSnapshotDto,
  type ReaderSnapshotUserAssetDto,
} from "@/types/api/reader-plate";
import type {
  ReaderAskActionConfirmResponseDto,
  ReaderAskEntryActionDto,
} from "@/types/api/reader-ask";
import type { ThemeName } from "@/lib/appearance";
import { BookOpen, Eye, Globe, SlidersHorizontal, Sparkles } from "lucide-react";
import { FavoriteButton } from "@/components/reader/FavoriteButton";
import { readerCommandControl } from "@/components/reader/interaction";
import {
  ReaderSettingsPanel,
  readStoredReaderSettings,
  persistReaderSettings,
  readerRecordPlateTypography,
  readerThemeClassName,
  type ReaderSettingsState,
} from "@/components/reader/settings";
import {
  READING_GOAL_OPTIONS,
  READING_VARIANT_OPTIONS,
} from "@/lib/reading-defaults";
import { cn } from "@/lib/cn";

import {
  ReaderToolbarActionsProvider,
  type ReaderToolbarActions,
} from "@/components/editor/plugins/reader-floating-toolbar-buttons";
import {
  ReaderFloatingSurface,
  useReaderFloatingLayer,
} from "../ReaderFloatingLayer";
import { ReaderQuickPeek } from "../dictionary/ReaderQuickPeek";
import { ReaderDictionaryRail } from "../dictionary/ReaderDictionaryRail";
import type { DictionaryLookupSnapshot, SaveState } from "../dictionary/contracts";
import { firstMeaning, meaningsJson } from "../dictionary/contracts";
import type { DictionaryAIViewState } from "@/types/api/dict-ai";
import { Plate, usePlateEditor, type RenderLeaf } from "platejs/react";
import { Editor, EditorContainer } from "@/components/ui/editor";
import { ReaderRecordPlateKit } from "@/components/editor/plugins/reader-plate-kit";
import {
  resolveReaderMarkVisual,
  sentenceChunkDomId,
} from "@/components/editor/plugins/reader-leaf-kit";
import {
  READER_CALLOUT_GROUP_TYPE,
  ReaderCalloutActionContext,
  ReaderGrammarInteractionContext,
  ReaderSentenceAnalysisInteractionContext,
  type ReaderCalloutActionTarget,
} from "@/components/editor/plugins/reader-blocks-kit";
import {
  CommentPluginBridge,
  InlineCommentPanel,
  type CommentPluginApi,
} from "@/components/reader/plate/InlineCommentPanel";
import { SelectionAnchorBridge } from "@/components/reader/plate/SelectionAnchorBridge";
import {
  READER_CALLOUT_TYPE,
  projectReaderRecordPlateToPlateValue,
  type ReaderCalloutElement,
  type PlateTextNode,
} from "@/lib/reader-plate/projection/reader-record-plate-to-plate-value";

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
  { value: "warm_yellow", label: "重点", swatchClassName: "bg-vocab-amber/75 ring-vocab-amber/25" },
  { value: "soft_mint", label: "疑问", swatchClassName: "bg-emerald-200/80 ring-emerald-300/50" },
  { value: "soft_rose", label: "难点", swatchClassName: "bg-rose-200/80 ring-rose-300/50" },
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
  return selection?.surfaceKind === "source" && selection.supportedSingleRange
    ? (selection.drafts[0] ?? null)
    : null;
}

function hasNonSourceDocumentSelection(
  selection: ReaderRecordSelectionAnchorBridgeResult | null,
): boolean {
  return Boolean(
    selection &&
      selection.surfaceKind !== "source" &&
      selection.selectedText.trim().length > 0,
  );
}

function hasSourceMultiTextSelection(
  selection: ReaderRecordSelectionAnchorBridgeResult | null,
): boolean {
  return Boolean(
    selection &&
      selection.surfaceKind === "source" &&
      selection.anchorType === "multi_text" &&
      selection.selectedText.trim().length > 0 &&
      selection.drafts.length >= 2 &&
      selection.segments.length >= 2,
  );
}

function canCopySelection(
  selection: ReaderRecordSelectionAnchorBridgeResult | null,
): boolean {
  return Boolean(selection?.selectedText.trim().length);
}

function canAskSelection(
  selection: ReaderRecordSelectionAnchorBridgeResult | null,
): boolean {
  return Boolean(singleRangeDraft(selection));
}

function sourceOnlyDisabledReason(
  selection: ReaderRecordSelectionAnchorBridgeResult | null,
  action: "lookup" | "write",
): string | undefined {
  if (!selection) {
    return "请选择稳定原文后再操作";
  }
  if (selection.surfaceKind !== "source") {
    return action === "lookup"
      ? "当前仅支持原文查词"
      : "当前仅支持原文高亮/笔记";
  }
  if (hasSourceMultiTextSelection(selection)) {
    return action === "lookup"
      ? "跨句选区暂不支持查词"
      : "跨句选区暂不支持高亮/笔记";
  }
  return "暂不支持跨段或非稳定原文选区";
}

function sourceSelectionAnchorPayload(
  recordId: string,
  selection: ReaderRecordSelectionAnchorBridgeResult | null,
): ReaderAnchorPayload | null {
  if (!selection || selection.surfaceKind !== "source") {
    return null;
  }

  const primarySegment = selection.segments[0] ?? null;
  if (!primarySegment) {
    return null;
  }

  const draft = singleRangeDraft(selection);
  if (!draft) {
    return null;
  }

  return {
    anchorType: "text_range",
    targetKey: buildTextRangeTargetKey(
      recordId,
      primarySegment.sentenceId,
      draft.start_offset,
      draft.end_offset,
      draft.text_hash,
    ),
    recordId,
    paragraphId: primarySegment.paragraphId,
    sentenceId: primarySegment.sentenceId,
    selectedText: draft.selected_text,
    startOffset: draft.start_offset,
    endOffset: draft.end_offset,
    textHash: draft.text_hash,
    metadata: {
      offsetUnit: TEXT_RANGE_OFFSET_UNIT,
      textHashAlgorithm: TEXT_RANGE_HASH_ALGORITHM,
      source: "reader_selection",
      originType: "text_range",
    },
  };
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

function noteAnchorMatchesDraft(
  anchor: ReaderRecordPlateTextAnchor,
  draft: ReaderRecordAnchorDraft,
): boolean {
  return (
    anchor.baseId === draft.base_id &&
    anchor.unitId === draft.unit_id &&
    anchor.anchorSegmentId === draft.anchor_segment_id &&
    anchor.unitStartOffset === draft.start_offset &&
    anchor.unitEndOffset === draft.end_offset &&
    anchor.offsetUnit === draft.offset_unit &&
    anchor.selectedText === draft.selected_text
  );
}

function findDuplicateNoteMark(
  blocks: ReaderRecordPlateBlock[],
  draft: ReaderRecordAnchorDraft | null,
): ReaderRecordPlateUserNoteMark | null {
  if (!draft) {
    return null;
  }
  for (const block of blocks) {
    if (block.type !== "paragraph") {
      continue;
    }
    for (const leaf of block.children) {
      for (const mark of leaf.marks) {
        if (
          mark.kind === "user_note" &&
          noteAnchorMatchesDraft(mark.anchor, draft)
        ) {
          return mark;
        }
      }
    }
  }
  return null;
}

function readerNoteAssetIdSelector(assetId: string): string {
  // ~= matches whitespace-separated list values
  return `[data-reader-record-user-note-asset-ids~="${assetId.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"]`;
}

function dataAttributeEqualsSelector(attribute: string, value: string): string {
  return `[${attribute}="${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"]`;
}

function eventTargetElement(target: EventTarget | null): Element | null {
  if (!target) {
    return null;
  }
  if (typeof Element !== "undefined" && target instanceof Element) {
    return target;
  }
  if (
    typeof Node !== "undefined" &&
    target instanceof Node &&
    target.parentElement
  ) {
    return target.parentElement;
  }
  return null;
}

function relatedTargetInsideGrammarItem(
  relatedTarget: EventTarget | null,
  grammarItemId: string,
): boolean {
  const element = eventTargetElement(relatedTarget);
  return Boolean(
    element?.closest(
      dataAttributeEqualsSelector(
        "data-reader-record-grammar-item-id",
        grammarItemId,
      ),
    ),
  );
}

function hasNonCollapsedNativeSelection(): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  const domSelection = window.getSelection();
  return Boolean(
    domSelection &&
      domSelection.rangeCount > 0 &&
      !domSelection.isCollapsed &&
      domSelection.toString().trim().length > 0,
  );
}

function hasNonCollapsedReaderSelection(
  selection: ReaderRecordSelectionAnchorBridgeResult | null,
): boolean {
  if (!selection?.selectedText.trim()) {
    return false;
  }
  if (typeof window === "undefined") {
    return true;
  }
  const domSelection = window.getSelection();
  if (!domSelection) {
    return true;
  }
  return !domSelection.isCollapsed && domSelection.toString().trim().length > 0;
}

const READER_RECORD_COPY_EXCLUDE_SELECTOR =
  '[data-reader-record-copy-exclude="true"], [hidden], svg[aria-hidden="true"]';

function normalizedClipboardTextFromElement(element: HTMLElement): string {
  return (element.textContent ?? "")
    .replace(/\u200B/g, "")
    .replace(/[ \t\r\f\v]+/g, " ")
    .replace(/\n[ \t]+/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function sanitizedSelectionClipboardPayload(
  root: HTMLElement,
): { text: string; html: string } | null {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) {
    return null;
  }

  const range = selection.getRangeAt(0);
  const commonAncestor =
    range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
      ? (range.commonAncestorContainer as Element)
      : range.commonAncestorContainer.parentElement;
  if (!commonAncestor || !root.contains(commonAncestor)) {
    return null;
  }

  const container = document.createElement("div");
  container.appendChild(range.cloneContents());
  container
    .querySelectorAll(READER_RECORD_COPY_EXCLUDE_SELECTOR)
    .forEach((node) => node.remove());

  const text = normalizedClipboardTextFromElement(container);
  if (!text) {
    return null;
  }
  return {
    text,
    html: container.innerHTML,
  };
}

function isGrammarCalloutElement(value: unknown): value is ReaderCalloutElement {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as { type?: unknown; variant?: unknown };
  return candidate.type === READER_CALLOUT_TYPE && candidate.variant === "grammar";
}

function groupConsecutiveGrammarCallouts(nodes: unknown[]): unknown[] {
  const grouped: unknown[] = [];
  let pending: ReaderCalloutElement[] = [];

  function flushPending() {
    if (pending.length === 0) {
      return;
    }
    const first = pending[0];
    grouped.push({
      type: READER_CALLOUT_GROUP_TYPE,
      id: `callout-group:${first.data.unitId}:${first.data.anchorSegmentId}:${grouped.length}`,
      children: pending,
    });
    pending = [];
  }

  nodes.forEach((node) => {
    if (isGrammarCalloutElement(node)) {
      pending.push(node);
      return;
    }
    flushPending();
    grouped.push(node);
  });
  flushPending();

  return grouped;
}

function userNoteMarksFromLeaf(
  leaf: PlateTextNode,
): ReaderRecordPlateUserNoteMark[] {
  const data = leaf.user_note_data;
  if (!data) {
    return [];
  }
  const marks = Array.isArray(data) ? data : [data];
  return [...marks].sort((a, b) => {
    const aLength = a.anchor.segmentEndOffset - a.anchor.segmentStartOffset;
    const bLength = b.anchor.segmentEndOffset - b.anchor.segmentStartOffset;
    if (aLength !== bLength) {
      return aLength - bLength;
    }
    return a.assetId.localeCompare(b.assetId);
  });
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

type ReadingRecordUserAssetWritePayload = {
  ok?: boolean;
  message?: string;
  item?: unknown;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function readString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0
    ? value
    : null;
}

function readNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function readStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function isHighlightUserAsset(asset: ReaderSnapshotUserAssetDto): boolean {
  return (
    asset.asset_type === "highlight" ||
    asset.asset_type === "quick_highlight" ||
    asset.asset_type === "user_highlight"
  );
}

function userHighlightAssetMatchesDraft(
  asset: ReaderSnapshotUserAssetDto,
  draft: ReaderRecordAnchorDraft,
): boolean {
  const anchor = asset.anchor;
  return (
    !asset.deleted_at &&
    isHighlightUserAsset(asset) &&
    asset.reading_record_id === draft.record_id &&
    asset.generation === draft.generation &&
    anchor.anchor_type === "text_range" &&
    anchor.base_id === draft.base_id &&
    anchor.unit_id === draft.unit_id &&
    anchor.anchor_segment_id === draft.anchor_segment_id &&
    anchor.offset_unit === draft.offset_unit &&
    anchor.start_offset === draft.start_offset &&
    anchor.end_offset === draft.end_offset &&
    anchor.selected_text === draft.selected_text &&
    anchor.text_hash === draft.text_hash
  );
}

function findExactUserHighlightAsset(
  assets: ReaderSnapshotUserAssetDto[],
  draft: ReaderRecordAnchorDraft,
): ReaderSnapshotUserAssetDto | null {
  return assets.find((asset) => userHighlightAssetMatchesDraft(asset, draft)) ?? null;
}

function canonicalHighlightAssetFromWritePayload(
  snapshot: ReaderPlateSnapshotDto,
  payload: ReadingRecordUserAssetWritePayload | null,
): { asset: ReaderSnapshotUserAssetDto; supersededIds: string[] } | null {
  if (!payload || !isRecord(payload.item)) {
    return null;
  }

  const item = payload.item;
  const assetId = readString(item.id);
  const readingRecordId = readString(item.reading_record_id) ?? snapshot.record_id;
  const baseId = readString(item.base_id);
  const generation = readNumber(item.generation) ?? snapshot.record.generation;
  const unitId = readString(item.unit_id);
  const anchorSegmentId = readString(item.anchor_segment_id);
  const startOffset = readNumber(item.unit_start_utf16);
  const endOffset = readNumber(item.unit_end_utf16);
  const selectedText = readString(item.selected_text);
  const textHash = readString(item.text_hash);

  if (
    !assetId ||
    readingRecordId !== snapshot.record_id ||
    generation !== snapshot.record.generation ||
    !baseId ||
    baseId !== snapshot.base.base_id ||
    !unitId ||
    !anchorSegmentId ||
    startOffset === null ||
    endOffset === null ||
    startOffset >= endOffset ||
    !selectedText ||
    !textHash
  ) {
    return null;
  }

  const segment = snapshot.anchor_segments.find(
    (candidate) =>
      candidate.anchor_segment_id === anchorSegmentId &&
      candidate.unit_id === unitId,
  );
  if (!segment) {
    return null;
  }

  return {
    asset: {
      asset_id: assetId,
      asset_type: "user_highlight",
      owner: "user",
      reading_record_id: readingRecordId,
      generation,
      anchor: {
        anchor_type: "text_range",
        base_id: baseId,
        unit_id: unitId,
        anchor_segment_id: anchorSegmentId,
        sentence_id: readString(item.sentence_id) ?? segment.sentence_id,
        segment_type: segment.segment_type,
        offset_unit: READER_TEXT_RANGE_OFFSET_UNIT,
        start_offset: startOffset,
        end_offset: endOffset,
        selected_text: selectedText,
        text_hash: textHash,
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      },
      note_text: null,
      color: typeof item.color === "string" ? item.color : null,
      created_at: readString(item.created_at) ?? new Date().toISOString(),
      updated_at: readString(item.updated_at) ?? new Date().toISOString(),
    },
    supersededIds: readStringArray(item.superseded_ids),
  };
}

function reconcileCanonicalHighlightAsset(
  current: ReaderSnapshotUserAssetDto[],
  canonical: ReaderSnapshotUserAssetDto,
  supersededIds: string[],
  extraRemovedIds: string[] = [],
): ReaderSnapshotUserAssetDto[] {
  const removedIds = new Set([...supersededIds, ...extraRemovedIds]);
  removedIds.delete(canonical.asset_id);

  const next: ReaderSnapshotUserAssetDto[] = [];
  let inserted = false;
  for (const asset of current) {
    if (removedIds.has(asset.asset_id)) {
      continue;
    }
    if (asset.asset_id === canonical.asset_id) {
      if (!inserted) {
        next.push(canonical);
        inserted = true;
      }
      continue;
    }
    next.push(asset);
  }

  if (!inserted) {
    next.push(canonical);
  }
  return next;
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
): Promise<ReadingRecordUserAssetWritePayload | null> {
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
  return payload;
}

async function patchReadingRecordHighlightColor(
  highlightId: string,
  color: string,
): Promise<ReadingRecordUserAssetWritePayload | null> {
  const response = await fetch(
    `/api/web/reading-record/highlights/${encodeURIComponent(highlightId)}`,
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
    | ReadingRecordUserAssetWritePayload
    | null;
  if (!response.ok || payload?.ok === false) {
    throw new Error(payload?.message ?? "高亮更新失败。");
  }
  return payload;
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

function phraseTypeForInspect(
  phraseType: string | undefined,
): NonNullable<ReaderStructuredInspectIntent["glossary"]>["phraseType"] {
  if (
    phraseType === "collocation" ||
    phraseType === "phrasal_verb" ||
    phraseType === "idiom" ||
    phraseType === "proper_noun" ||
    phraseType === "compound"
  ) {
    return phraseType;
  }
  return undefined;
}

function sourceTextForAnchorSegment(
  blocks: ReaderRecordPlateBlock[],
  anchorSegmentId: string,
): string {
  const paragraph = blocks.find(
    (block): block is ReaderRecordPlateParagraphBlock =>
      block.type === "paragraph" &&
      block.data.coveredAnchorSegmentIds.includes(anchorSegmentId),
  );

  if (!paragraph) {
    return "";
  }

  return paragraph.children
    .filter((leaf) => leaf.anchorSegmentId === anchorSegmentId)
    .map((leaf) => leaf.text)
    .join("");
}

function structuredInspectIntentFromVocabularyMark(
  mark: ReaderRecordPlateVocabularyMark,
  contextSentence: string,
): ReaderStructuredInspectIntent | null {
  if (mark.vocabulary.itemType === "vocab_highlight") {
    return null;
  }

  if (mark.vocabulary.itemType === "phrase_gloss") {
    return {
      kind: "structured_annotation_inspect",
      sentenceId: mark.anchor.sentenceId,
      contextSentence,
      markId: mark.id,
      annotationType: "phrase_gloss",
      visualTone: "phrase",
      anchorText: mark.anchor.selectedText,
      lookupText: mark.vocabulary.phrase,
      lookupKind: "phrase",
      glossary: {
        gloss: mark.vocabulary.gloss,
        phraseType: phraseTypeForInspect(mark.vocabulary.phraseType),
        reason: mark.vocabulary.example
          ? `例句：${mark.vocabulary.example}`
          : undefined,
      },
      anchorOffsets: {
        startOffset: mark.anchor.segmentStartOffset,
        endOffset: mark.anchor.segmentEndOffset,
      },
      title: "短语说明",
      label: "短语说明",
    };
  }

  return {
    kind: "structured_annotation_inspect",
    sentenceId: mark.anchor.sentenceId,
    contextSentence,
    markId: mark.id,
    annotationType: "context_gloss",
    visualTone: "context",
    anchorText: mark.anchor.selectedText,
    lookupText: mark.vocabulary.display,
    lookupKind: "phrase",
    glossary: {
      gloss: mark.vocabulary.gloss,
      reason: mark.vocabulary.reason,
    },
    anchorOffsets: {
      startOffset: mark.anchor.segmentStartOffset,
      endOffset: mark.anchor.segmentEndOffset,
    },
    title: "语境义",
    label: "语境义",
  };
}

function askAttachmentFromVocabularyInspect(
  pageIdentity: ReaderAskPageIdentity,
  intent: ReaderStructuredInspectIntent,
): ReaderAskAttachment {
  const displayText = intent.lookupText ?? intent.anchorText;
  return {
    kind: "analysis_ref",
    subtype: "sentence",
    label: `结构化解释：${displayText}`,
    selectedText: displayText,
    targetKey: `record:${pageIdentity.recordId}:analysis:structured_inspect:${intent.markId}`,
    metadata: {
      pageIdentity,
      sourceSurface: "reader_record_vocabulary_inspect",
      entryAction: "lookup_in_context",
      markId: intent.markId,
      sentenceId: intent.sentenceId,
      entryId: intent.markId,
      entryType: "structured_inspect",
      annotationType: intent.annotationType,
      startOffset: intent.anchorOffsets?.startOffset ?? null,
      endOffset: intent.anchorOffsets?.endOffset ?? null,
      title: intent.title,
      query: displayText,
      lookupText: displayText,
      visualTone: intent.visualTone,
      sourceContext: {
        sentenceId: intent.sentenceId,
        sourceText: intent.contextSentence,
      },
    },
  };
}

type PendingReaderRecordAskRequest = {
  content: string;
  entryAction: ReaderAskEntryActionDto;
  attachments: ReaderAskAttachment[];
  submissionMode?: "chat" | "quick_action";
};

type ReaderLeafClickResolver = (
  leaf: PlateTextNode,
  anchor: HTMLElement,
  event: MouseEvent<HTMLElement>,
) => void;

type ReaderLeafSpanAttributes = HTMLAttributes<HTMLSpanElement> & {
  ref?: Ref<HTMLSpanElement>;
  "data-slate-leaf"?: true;
};

function immersiveParagraphBlock(
  block: ReaderRecordPlateParagraphBlock,
): ReaderRecordPlateParagraphBlock {
  return {
    ...block,
    children: block.children.map((leaf) => ({
      ...leaf,
      marks: leaf.marks.filter((mark) => mark.kind !== "grammar_note"),
    })),
  };
}

function visibleBlockForMode(
  block: ReaderRecordPlateBlock,
  surfaceMode: "intensive" | "immersive",
): ReaderRecordPlateBlock | null {
  if (surfaceMode === "intensive") {
    return block;
  }
  if (block.type !== "paragraph") {
    return null;
  }
  return immersiveParagraphBlock(block);
}

/**
 * Traverse snapshot.value and collect only stable source text leaves
 * (segment_text), skipping translation, grammar note, sentence_analysis
 * and Ask supplement surfaces.
 */
function extractStableSourceText(
  snapshot: ReaderPlateSnapshotDto,
): string {
  const parts: string[] = [];
  for (const unit of snapshot.value) {
    for (const child of unit.children) {
      if (child.type !== "reader_source_block") {
        continue;
      }
      for (const sourceChild of child.children) {
        // ReaderAnchorSegmentNodeDto has `children`; ReaderStableSeparatorLeafDto does not.
        if (!("children" in sourceChild)) {
          // separator leaf 保留了 segment 间的空格/换行，必须纳入才能保证词数正确。
          parts.push(sourceChild.text);
          continue;
        }
        for (const leaf of sourceChild.children) {
          parts.push(leaf.text);
        }
      }
    }
  }
  return parts.join("");
}

/**
 * Compute source-only English word count from stable source text leaves.
 * Returns null when source text cannot be reliably obtained or is empty,
 * so the header can omit the word count rather than falling back to sentence count.
 */
function computeSourceOnlyWordCount(
  snapshot: ReaderPlateSnapshotDto,
): number | null {
  const sourceText = extractStableSourceText(snapshot);
  const trimmed = sourceText.trim();
  if (!trimmed) {
    return null;
  }
  const words = trimmed.split(/\s+/).filter(Boolean);
  return words.length > 0 ? words.length : null;
}

/**
 * Resolve `goal · variant` label using reading-defaults options.
 * Returns null when either field is missing or cannot be mapped,
 * so the header omits the strategy label rather than fabricating it.
 */
function resolveReadingGoalVariantLabel(
  goal: string | null | undefined,
  variant: string | null | undefined,
): string | null {
  if (!goal || !variant) {
    return null;
  }
  const goalOption = READING_GOAL_OPTIONS.find((option) => option.value === goal);
  if (!goalOption) {
    return null;
  }
  const variantOptions = READING_VARIANT_OPTIONS[goalOption.value];
  if (!variantOptions) {
    return null;
  }
  const variantOption = variantOptions.find(
    (option) => option.value === variant,
  );
  if (!variantOption) {
    return null;
  }
  return `${goalOption.label} · ${variantOption.label}`;
}

function formatReaderRecordDate(createdAt: string | undefined): string {
  if (!createdAt) return "今日";
  const d = new Date(createdAt);
  if (Number.isNaN(d.getTime())) return "今日";
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
}

function readerRecordSourceTypeLabel(sourceType: string): string {
  switch (sourceType) {
    case "text":
    case "plain_text":
    case "user_input":
      return "粘贴导入";
    default:
      return sourceType;
  }
}

function ReaderRecordHeader({
  snapshot,
  progress,
  surfaceMode,
  onModeChange,
  onOpenSettings,
}: {
  snapshot: ReaderPlateSnapshotDto;
  progress: ReaderRecordPlateProgress;
  surfaceMode: "intensive" | "immersive";
  onModeChange: (mode: "intensive" | "immersive") => void;
  onOpenSettings: (anchor: HTMLElement) => void;
}) {
  const record = snapshot.record;
  const displayTitleZh = record.display_title_zh?.trim() || "";
  const recordTitle = record.title?.trim() || "";
  const titleGenerationStatus = record.title_generation_status ?? null;
  const hasChineseTitle = displayTitleZh.length > 0;
  const titlePending =
    !hasChineseTitle && titleGenerationStatus === "pending";
  const titleFailed =
    !hasChineseTitle && titleGenerationStatus === "failed_retryable";
  // 迁移期防崩溃降级：只有旧 snapshot 完全没有 title_generation_status 时，
  // 才允许用 record.title 作为兼容 H1。如果后端明确返回 succeeded/pending/failed_retryable，
  // 则必须遵守对应合同，不能将英文源标题提升为中文 masthead。
  const titleMigrationFallback =
    !hasChineseTitle &&
    titleGenerationStatus === null &&
    recordTitle.length > 0;

  const createdAt = record.created_at;
  const sourceType = record.source_type;
  const sourceMetadata = record.source_metadata ?? {};
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
  const formattedDate = formatReaderRecordDate(createdAt);
  const modeLabel = surfaceMode === "immersive" ? "沉浸模式" : "精读模式";

  // source-only word count：仅基于 snapshot.value 的稳定原文 segment_text 叶子计算，
  // 不包含 translation / grammar note / sentence_analysis / Ask supplement。
  // 无法可靠得到原文时不显示词数，绝不 fallback 到 sentence count。
  const sourceWordCount = computeSourceOnlyWordCount(snapshot);
  // reading_goal · reading_variant 标签：仅当两个字段都有真实值且能映射到
  // reading-defaults.ts 的 options 时才展示，否则不展示，避免伪造。
  const readingGoalVariantLabel = resolveReadingGoalVariantLabel(
    record.reading_goal,
    record.reading_variant,
  );

  const actionButtonBaseClassName = cn(
    readerCommandControl,
    "relative flex flex-1 justify-center rounded-none px-3.5 py-2.5 text-left sm:py-3.5 md:px-5",
  );
  const actionButtonActiveClassName =
    "text-vocab-amber after:absolute after:bottom-0 after:left-0 after:right-0 after:h-[2px] after:bg-vocab-amber";
  const actionButtonIdleClassName = "text-ink hover:text-ink-soft";

  const sourceLabel = readerRecordSourceTypeLabel(sourceType);
  const hasExternalSource = Boolean(sourceName || sourceDomain || sourceUrl);

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

      {/* Zone 2: H1 editorial masthead / title state */}
      {hasChineseTitle ? (
        <h1
          data-reader-record-reading-title
          data-reader-record-title-state="succeeded"
          className="mt-4 font-headline text-[clamp(2rem,4vw,3.25rem)] font-bold leading-[1.08] tracking-normal text-ink"
        >
          {displayTitleZh}
        </h1>
      ) : titlePending ? (
        <h1
          data-reader-record-reading-title
          data-reader-record-title-state="pending"
          className="mt-4 font-headline text-[clamp(2rem,4vw,3.25rem)] font-bold leading-[1.08] tracking-normal text-muted"
        >
          标题生成中…
        </h1>
      ) : titleFailed ? (
        <div className="mt-4">
          <h1
            data-reader-record-reading-title
            data-reader-record-title-state="failed_retryable"
            className="font-headline text-[clamp(2rem,4vw,3.25rem)] font-bold leading-[1.08] tracking-normal text-muted"
          >
            标题生成失败
          </h1>
          {recordTitle ? (
            <p
              data-reader-record-source-title="true"
              className="mt-1.5 text-[0.8rem] font-medium text-subtle"
            >
              源标题：{recordTitle}
            </p>
          ) : null}
        </div>
      ) : titleMigrationFallback ? (
        <h1
          data-reader-record-reading-title
          data-reader-record-title-state="migration_fallback"
          className="mt-4 font-headline text-[clamp(2rem,4vw,3.25rem)] font-bold leading-[1.08] tracking-normal text-ink"
        >
          {recordTitle}
        </h1>
      ) : null}

      {/* Zone 3: Action bar — hairline shell, left metadata + right action buttons */}
      <div
        className="mt-6 w-full border-t border-b border-hairline bg-transparent py-0 flex flex-col sm:flex-row items-stretch justify-between min-h-[56px]"
        data-reader-record-action-bar="true"
      >
        {/* Left metadata / status block */}
        <div className="flex items-center gap-3.5 px-3 py-3 sm:py-0">
          <span
            data-reader-record-progress-status={progress.overallStatus}
            className="px-3 py-1 text-[0.75rem] font-semibold text-ink-soft bg-surface-warm border border-hairline/80 rounded-[0.5rem] flex items-center gap-1.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.8),0_1px_2px_rgba(0,0,0,0.03)] select-none"
          >
            <Sparkles className="h-3.5 w-3.5 text-vocab-amber fill-vocab-amber/10" />
            <span>{statusLabel}</span>
          </span>
          {sourceWordCount !== null ? (
            <>
              <div className="h-3.5 w-[1px] bg-hairline" />
              <span
                className="text-[0.8rem] font-semibold text-muted"
                data-reader-record-source-word-count={String(sourceWordCount)}
              >
                {sourceWordCount} 词
              </span>
            </>
          ) : null}
          {readingGoalVariantLabel ? (
            <>
              <div className="h-3.5 w-[1px] bg-hairline" />
              <span
                className="text-[0.8rem] font-semibold text-muted"
                data-reader-record-reading-goal-variant="true"
              >
                {readingGoalVariantLabel}
              </span>
            </>
          ) : null}
        </div>

        {/* Right action buttons */}
        <div className="flex items-stretch divide-x divide-hairline border-t border-hairline sm:border-t-0 select-none">
          <FavoriteButton recordId={snapshot.record_id} variant="action-bar" />
          <button
            type="button"
            aria-pressed={surfaceMode === "intensive"}
            aria-label="切换到精读模式"
            data-reader-record-mode-switch={surfaceMode}
            data-reader-record-mode-option="intensive"
            onClick={() => onModeChange("intensive")}
            className={cn(
              actionButtonBaseClassName,
              surfaceMode === "intensive"
                ? actionButtonActiveClassName
                : actionButtonIdleClassName,
            )}
          >
            <BookOpen
              aria-hidden="true"
              className="h-[18px] w-[18px] shrink-0"
              strokeWidth={1.5}
            />
            <span className="flex min-w-0 flex-col items-start leading-none whitespace-nowrap">
              <span className="text-[0.85rem] font-semibold whitespace-nowrap">
                精读
              </span>
              <span className="hidden sm:block mt-1 text-[0.65rem] font-medium text-subtle whitespace-nowrap">
                逐句解析
              </span>
            </span>
          </button>
          <button
            type="button"
            aria-pressed={surfaceMode === "immersive"}
            aria-label="切换到沉浸模式"
            data-reader-record-mode-switch={surfaceMode}
            data-reader-record-mode-option="immersive"
            onClick={() => onModeChange("immersive")}
            className={cn(
              actionButtonBaseClassName,
              surfaceMode === "immersive"
                ? actionButtonActiveClassName
                : actionButtonIdleClassName,
            )}
          >
            <Eye
              aria-hidden="true"
              className="h-[18px] w-[18px] shrink-0"
              strokeWidth={1.5}
            />
            <span className="flex min-w-0 flex-col items-start leading-none whitespace-nowrap">
              <span className="text-[0.85rem] font-semibold whitespace-nowrap">
                沉浸
              </span>
              <span className="hidden sm:block mt-1 text-[0.65rem] font-medium text-subtle whitespace-nowrap">
                专注阅读
              </span>
            </span>
          </button>
          <button
            type="button"
            aria-label="打开阅读设置"
            data-reader-record-action="open-settings"
            onClick={(event) => onOpenSettings(event.currentTarget)}
            className={cn(
              actionButtonBaseClassName,
              actionButtonIdleClassName,
            )}
          >
            <SlidersHorizontal
              aria-hidden="true"
              className="h-[18px] w-[18px] shrink-0"
              strokeWidth={1.5}
            />
            <span className="flex min-w-0 flex-col items-start leading-none whitespace-nowrap">
              <span className="text-[0.85rem] font-semibold whitespace-nowrap">
                阅读设置
              </span>
              <span className="hidden sm:block mt-1 text-[0.65rem] font-medium text-subtle whitespace-nowrap">
                版式与偏好
              </span>
            </span>
          </button>
        </div>
      </div>

      {/* Zone 4: Bottom metadata — source / date / word count / import type */}
      <div className="mt-3 flex flex-col sm:flex-row sm:items-center justify-between gap-3 sm:gap-0 text-[0.78rem] text-muted tracking-wide leading-normal sm:leading-none select-none">
        <div className="flex flex-wrap items-center gap-1.5 font-medium">
          <span>
            {hasExternalSource
              ? `来源 ${sourceName ?? sourceDomain}`
              : `来源 ${sourceLabel}`}
          </span>
          {formattedDate && (
            <>
              <span className="text-muted/60">·</span>
              <span>{formattedDate}</span>
            </>
          )}
          {sourceWordCount !== null && (
            <>
              <span className="text-muted/60">·</span>
              <span>{sourceWordCount} 词</span>
            </>
          )}
        </div>

        <div>
          {sourceUrl ? (
            <a
              href={sourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="focus-ring inline-flex items-center gap-1.5 font-semibold text-muted transition-colors hover:text-ink"
            >
              <Globe className="h-4 w-4 shrink-0" strokeWidth={1.75} />
              <span>英文原文</span>
            </a>
          ) : (
            <span className="inline-flex items-center gap-1.5 text-muted/60">
              <Globe className="h-4 w-4 shrink-0" strokeWidth={1.75} />
              <span>{sourceLabel}</span>
            </span>
          )}
        </div>
      </div>
    </header>
  );
}

function SelectionActionState({
  copyStatus,
  selection,
  writeState,
}: {
  copyStatus: ReaderRecordCopyStatus;
  selection: ReaderRecordSelectionAnchorBridgeResult | null;
  writeState: ReaderRecordWriteState;
}) {
  const draft = singleRangeDraft(selection);
  const selectionActionable =
    canCopySelection(selection) || canAskSelection(selection);
  const writeStatus = writeStateLabel(writeState);
  const actionMode = selectionActionable ? "selection" : selection ? "unsupported" : "idle";
  const actionHint = selectionActionable
    ? `已选：${selection?.selectedText ?? draft?.selected_text ?? ""}`
    : selection
      ? "当前选区暂不支持操作"
      : "划取原文后可查词、复制、标记或记录笔记";

  return (
    <div
      data-testid="reader-record-plate-selection-state"
      data-reader-record-actions="selection-state"
      data-reader-record-action-mode={actionMode}
      data-reader-record-selection-draft-count={selection?.drafts.length ?? 0}
      data-reader-record-selection-supported={selectionActionable ? "true" : "false"}
      data-reader-record-selection-surface-kind={selection?.surfaceKind}
      data-reader-record-selection-block-type={selection?.blockType}
      data-reader-record-selection-block-id={selection?.blockId}
      data-reader-record-selection-anchor-segment-id={
        draft?.anchor_segment_id ?? selection?.blockContext.anchorSegmentId ?? undefined
      }
      data-reader-record-selection-unit-id={
        draft?.unit_id ?? selection?.blockContext.unitId ?? undefined
      }
      data-reader-record-selection-layer-id={
        selection?.blockContext.layerId ?? undefined
      }
      data-reader-record-selection-analysis-id={
        selection?.blockContext.analysisId ?? undefined
      }
      data-reader-record-selection-supplement-id={
        selection?.blockContext.supplementId ?? undefined
      }
      data-reader-record-selection-start-offset={
        draft ? String(draft.start_offset) : undefined
      }
      data-reader-record-selection-end-offset={
        draft ? String(draft.end_offset) : undefined
      }
      data-reader-record-write-state={writeState.kind}
      className="sr-only"
      aria-label="Reader Record Plate 选区状态"
      aria-live="polite"
    >
      <span data-reader-record-action-hint>{actionHint}</span>
      {copyStatus !== "idle" ? (
        <span data-testid="reader-record-plate-copy-status">
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
    </div>
  );
}

export function ReaderRecordPlateSurface({
  snapshot,
  className = "px-5 py-8 sm:px-8 lg:px-10",
  columnClassName,
  readingClassName = "",
  onRequestSnapshotReload,
}: ReaderRecordPlateSurfaceProps) {
  const surfaceRef = useRef<HTMLElement | null>(null);
  const commentApiRef = useRef<CommentPluginApi | null>(null);
  const [commentApiReady, setCommentApiReady] = useState(false);
  const [activeSelection, setActiveSelection] =
    useState<ReaderRecordSelectionAnchorBridgeResult | null>(null);
  const [copyStatus, setCopyStatus] = useState<ReaderRecordCopyStatus>("idle");
  const [writeState, setWriteState] = useState<ReaderRecordWriteState>({
    kind: "idle",
  });
  const [noteDraft, setNoteDraft] = useState("");
  const [noteAnchorDraft, setNoteAnchorDraft] =
    useState<ReaderRecordAnchorDraft | null>(null);
  const [noteDuplicateAcknowledged, setNoteDuplicateAcknowledged] =
    useState(false);
  const [lookupState, setLookupState] = useState<ReaderRecordLookupState>({
    kind: "idle",
  });
  const [inspectState, setInspectState] =
    useState<ReaderStructuredInspectIntent | null>(null);
  const [activeSentenceChunkId, setActiveSentenceChunkId] = useState<string | null>(null);
  const [activeGrammarItemId, setActiveGrammarItemId] = useState<string | null>(null);
  const [grammarExpandRequest, setGrammarExpandRequest] =
    useState<{ itemId: string; requestId: number } | null>(null);
  const [hoverNoteAssetId, setHoverNoteAssetId] = useState<string | null>(null);
  const [noteMenu, setNoteMenu] = useState<{
    mark: ReaderRecordPlateUserNoteMark;
    anchor: HTMLElement;
    mode: "view" | "edit";
    draft: string;
  } | null>(null);
  const grammarPulseTimerRef = useRef<number | null>(null);
  const grammarExpandRequestIdRef = useRef(0);
  const leafClickResolverRef = useRef<ReaderLeafClickResolver | null>(null);
  const markPointerRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    dragged: boolean;
  } | null>(null);
  const suppressNextMarkClickRef = useRef(false);
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
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setActiveGrammarItemId(null);
      }
    }

    window.document.addEventListener("keydown", handleKeyDown);
    return () => {
      window.document.removeEventListener("keydown", handleKeyDown);
      if (grammarPulseTimerRef.current !== null) {
        window.clearTimeout(grammarPulseTimerRef.current);
        grammarPulseTimerRef.current = null;
      }
    };
  }, []);
  const [settingsPanelOpen, setSettingsPanelOpen] = useState(false);
  const settingsAnchorRef = useRef<HTMLElement | null>(null);
  const surfaceMode = readerSettings.mode;
  const [localUserAssets, setLocalUserAssets] = useState<
    ReaderPlateSnapshotDto["user_assets"]
  >(snapshot.user_assets);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- snapshot reload resets optimistic user-asset projections.
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
  const duplicateNoteForDraft = useMemo(
    () => findDuplicateNoteMark(plateDocument.children, noteAnchorDraft),
    [noteAnchorDraft, plateDocument.children],
  );
  const typography = readerRecordPlateTypography(readerSettings);
  const themeClassName = readerThemeClassName(themeName);
  const contentColumnClassName =
    columnClassName ?? `mx-auto w-full ${typography.columnClassName}`;
  const visibleBlocks = useMemo(() => {
    return plateDocument.children.flatMap((block) => {
      const visibleBlock = visibleBlockForMode(block, surfaceMode);
      return visibleBlock ? [visibleBlock] : [];
    });
  }, [plateDocument.children, surfaceMode]);

  // Plate editor value: 把 visibleBlocks 投影为 Plate Descendant[]。
  // visibleBlocks 过滤在 projection 层完成，保证 editor 只渲染当前 surfaceMode 需要的 blocks。
  const plateValue = useMemo(
    () =>
      groupConsecutiveGrammarCallouts(
        projectReaderRecordPlateToPlateValue({
          ...plateDocument,
          children: visibleBlocks,
        }),
      ),
    [plateDocument, visibleBlocks],
  );
  const editor = usePlateEditor(
    {
      plugins: [...ReaderRecordPlateKit],
      value: plateValue as never[],
    },
    [],
  );
  // plateValue 变化时同步 editor 内容，避免重新创建 editor 实例。
  useEffect(() => {
    if (editor.children !== plateValue) {
      editor.tf.setValue(plateValue as never[]);
    }
  }, [plateValue, editor]);

  // renderLeaf：为每个 paragraph text leaf 输出选区锚点 data 属性，
  // 同时承载 vocabulary / grammar / user_highlight / user_note 的视觉和交互。
  //
  // 单一外层 span 设计：
  // - 不再注册 ReaderLeafKit leaf plugin，避免嵌套 mark-hit wrapper 干扰
  //   浏览器原生 selection 落点。
  // - 所有 mark 视觉通过 reader-record-mark-stack--* class 控制。
  // - 点击优先级由 leaf 上各 mark data 字段决定，handleLeafClickIntent
  //   按 vocabulary > grammar_note > user_note > user_highlight 顺序派发。
  const renderLeaf = useCallback(
    (props: Parameters<RenderLeaf>[0]) => {
      const leaf = props.leaf as unknown as PlateTextNode;
      const attributes =
        props.attributes as unknown as ReaderLeafSpanAttributes;
      const anchorSegmentId = leaf.anchor_segment_id;
      const vocabularyMark = leaf.vocabulary_data;
      const grammarMark = leaf.grammar_data;
      const userHighlightMark = leaf.user_highlight_data;
      const noteMarks = userNoteMarksFromLeaf(leaf);
      const hasUserAsset = Boolean(userHighlightMark) || noteMarks.length > 0;
      const downgradeVocabulary = Boolean(vocabularyMark) && hasUserAsset;
      const activeNoteAssetIds = new Set<string>();
      if (noteMenu?.mark.assetId) {
        activeNoteAssetIds.add(noteMenu.mark.assetId);
      }
      if (hoverNoteAssetId) {
        activeNoteAssetIds.add(hoverNoteAssetId);
      }
      const visual = resolveReaderMarkVisual(leaf, {
        activeSentenceChunkId,
        activeGrammarItemId,
        downgradeVocabulary,
        activeNoteAssetIds,
      });
      const sentenceChunk = visual.sentenceChunk;
      const sentenceChunkId = sentenceChunk ? sentenceChunkDomId(sentenceChunk) : null;
      const grammarItemId = grammarMark?.itemId ?? null;
      const grammarActive =
        grammarItemId !== null && activeGrammarItemId === grammarItemId;
      const noteAssetIds = noteMarks.length > 0
        ? noteMarks.map((mark) => mark.assetId).join(" ")
        : null;
      const firstNoteAssetId = noteMarks[0]?.assetId ?? null;
      const mergedClassName = [
        visual.kinds.length > 0 ? visual.className : null,
        props.attributes.className,
      ]
        .filter(Boolean)
        .join(" ");
      const markClickProps =
        visual.kinds.length > 0
          ? ({
              onPointerDown: (event: ReactPointerEvent<HTMLSpanElement>) => {
                markPointerRef.current = {
                  pointerId: event.pointerId,
                  startX: event.clientX,
                  startY: event.clientY,
                  dragged: false,
                };
                suppressNextMarkClickRef.current = false;
              },
              onPointerMove: (event: ReactPointerEvent<HTMLSpanElement>) => {
                const pointer = markPointerRef.current;
                if (!pointer || pointer.pointerId !== event.pointerId) {
                  return;
                }
                const dx = event.clientX - pointer.startX;
                const dy = event.clientY - pointer.startY;
                if (dx * dx + dy * dy >= 16) {
                  pointer.dragged = true;
                  suppressNextMarkClickRef.current = true;
                }
              },
              onPointerUp: (event: ReactPointerEvent<HTMLSpanElement>) => {
                const pointer = markPointerRef.current;
                if (pointer?.pointerId === event.pointerId && pointer.dragged) {
                  suppressNextMarkClickRef.current = true;
                }
                if (hasNonCollapsedNativeSelection()) {
                  suppressNextMarkClickRef.current = true;
                }
                markPointerRef.current = null;
              },
              onPointerCancel: () => {
                markPointerRef.current = null;
              },
              onMouseDown: (event: MouseEvent<HTMLSpanElement>) => {
                if (markPointerRef.current !== null) {
                  return;
                }
                markPointerRef.current = {
                  pointerId: -1,
                  startX: event.clientX,
                  startY: event.clientY,
                  dragged: false,
                };
                suppressNextMarkClickRef.current = false;
              },
              onMouseMove: (event: MouseEvent<HTMLSpanElement>) => {
                const pointer = markPointerRef.current;
                if (!pointer || pointer.pointerId !== -1) {
                  return;
                }
                const dx = event.clientX - pointer.startX;
                const dy = event.clientY - pointer.startY;
                if (dx * dx + dy * dy >= 16) {
                  pointer.dragged = true;
                  suppressNextMarkClickRef.current = true;
                }
              },
              onMouseUp: () => {
                const pointer = markPointerRef.current;
                if (pointer?.pointerId === -1 && pointer.dragged) {
                  suppressNextMarkClickRef.current = true;
                }
                if (hasNonCollapsedNativeSelection()) {
                  suppressNextMarkClickRef.current = true;
                }
                if (pointer?.pointerId === -1) {
                  markPointerRef.current = null;
                }
              },
              onClick: (event: MouseEvent<HTMLSpanElement>) => {
                if (
                  suppressNextMarkClickRef.current ||
                  hasNonCollapsedNativeSelection()
                ) {
                  suppressNextMarkClickRef.current = false;
                  return;
                }
                leafClickResolverRef.current?.(
                  leaf,
                  event.currentTarget,
                  event as unknown as MouseEvent<HTMLElement>,
                );
              },
            } satisfies HTMLAttributes<HTMLSpanElement>)
          : {};
      // 合并 grammar hover 和 note hover 到同一组 handler，避免多 span 嵌套。
      const hoverProps: HTMLAttributes<HTMLSpanElement> = {};
      if (grammarItemId) {
        hoverProps.onMouseEnter = () => {
          setActiveGrammarItemId(grammarItemId);
        };
        hoverProps.onMouseLeave = (event) => {
          if (relatedTargetInsideGrammarItem(event.relatedTarget, grammarItemId)) {
            return;
          }
          setActiveGrammarItemId((current) =>
            current === grammarItemId ? null : current,
          );
        };
        hoverProps.onFocus = () => {
          setActiveGrammarItemId(grammarItemId);
        };
        hoverProps.onBlur = (event) => {
          if (relatedTargetInsideGrammarItem(event.relatedTarget, grammarItemId)) {
            return;
          }
          setActiveGrammarItemId((current) =>
            current === grammarItemId ? null : current,
          );
        };
      }
      if (firstNoteAssetId) {
        const prevMouseEnter = hoverProps.onMouseEnter;
        const prevMouseLeave = hoverProps.onMouseLeave;
        hoverProps.onMouseEnter = (event) => {
          prevMouseEnter?.(event);
          setHoverNoteAssetId(firstNoteAssetId);
        };
        hoverProps.onMouseLeave = (event) => {
          prevMouseLeave?.(event);
          setHoverNoteAssetId(null);
        };
      }
      const vocabularyDataAttrs = vocabularyMark
        ? {
            "data-reader-record-vocabulary-mark-id": vocabularyMark.id,
            "data-reader-record-vocabulary-kind": vocabularyMark.kind,
            "data-reader-record-vocabulary-starts-here": vocabularyMark.startsHere
              ? "true"
              : "false",
          }
        : {};
      const grammarDataAttrs = grammarMark
        ? {
            "data-reader-record-grammar-mark-id": grammarMark.id,
            "data-reader-record-grammar-starts-here": grammarMark.startsHere
              ? "true"
              : "false",
          }
        : {};
      const userHighlightDataAttrs = userHighlightMark
        ? {
            "data-reader-record-user-highlight-asset-id": userHighlightMark.assetId,
          }
        : {};
      const userNoteDataAttrs = noteAssetIds
        ? {
            "data-reader-record-user-note-asset-ids": noteAssetIds,
            "data-reader-record-note-active":
              noteMarks.some((mark) => activeNoteAssetIds.has(mark.assetId)) ||
              undefined,
          }
        : {};
      if (anchorSegmentId) {
        return (
          <span
            {...attributes}
            {...markClickProps}
            {...hoverProps}
            {...vocabularyDataAttrs}
            {...grammarDataAttrs}
            {...userHighlightDataAttrs}
            {...userNoteDataAttrs}
            className={mergedClassName || undefined}
            aria-label={visual.ariaLabel}
            title={visual.title}
            data-reader-record-leaf="segment_text"
            data-anchor-segment-id={anchorSegmentId}
            data-segment-start-utf16={leaf.segment_start_utf16}
            data-segment-end-utf16={leaf.segment_end_utf16}
            data-reader-record-mark-stack-kinds={
              visual.kinds.length > 0 ? visual.kinds.join(" ") : undefined
            }
            data-reader-record-sentence-analysis-chunk-source={sentenceChunkId ?? undefined}
            data-reader-record-sentence-analysis-chunk-active={
              sentenceChunkId && activeSentenceChunkId === sentenceChunkId
                ? "true"
                : undefined
            }
            data-reader-record-grammar-item-id={grammarItemId ?? undefined}
            data-reader-record-grammar-active={grammarActive ? "true" : undefined}
          >
            {props.children}
          </span>
        );
      }
      return (
        <span
          {...attributes}
          {...markClickProps}
          {...hoverProps}
          {...vocabularyDataAttrs}
          {...grammarDataAttrs}
          {...userHighlightDataAttrs}
          {...userNoteDataAttrs}
          className={mergedClassName || undefined}
          aria-label={visual.ariaLabel}
          title={visual.title}
          data-reader-record-mark-stack-kinds={
            visual.kinds.length > 0 ? visual.kinds.join(" ") : undefined
          }
          data-reader-record-grammar-item-id={grammarItemId ?? undefined}
          data-reader-record-grammar-active={grammarActive ? "true" : undefined}
        >
          {props.children}
        </span>
      );
    },
    [activeGrammarItemId, activeSentenceChunkId, hoverNoteAssetId, noteMenu],
  );

  const settingsFloating = useReaderFloatingLayer({
    open: settingsPanelOpen,
    placement: "bottom-end",
    offsetPx: 10,
    collisionPadding: 16,
    strategy: "fixed",
  });

  const handleSettingsChange = useCallback((next: ReaderSettingsState) => {
    setReaderSettings(next);
    persistReaderSettings(next);
  }, []);

  const handleOpenSettingsPanel = useCallback(
    (anchor: HTMLElement) => {
      settingsAnchorRef.current = anchor;
      settingsFloating.refs.setPositionReference?.({
        getBoundingClientRect: () => anchor.getBoundingClientRect(),
        contextElement: anchor,
      });
      setSettingsPanelOpen(true);
    },
    [settingsFloating.refs],
  );

  const { refs: settingsFloatingRefs, update: settingsFloatingUpdate } =
    settingsFloating;
  useEffect(() => {
    if (!settingsPanelOpen) {
      return;
    }

    function updateReference() {
      const anchor = settingsAnchorRef.current;
      if (!anchor) {
        return;
      }
      settingsFloatingRefs.setPositionReference?.({
        getBoundingClientRect: () => anchor.getBoundingClientRect(),
        contextElement: anchor,
      });
      settingsFloatingUpdate?.();
    }

    updateReference();
    window.addEventListener("resize", updateReference);
    window.addEventListener("scroll", updateReference, true);
    return () => {
      window.removeEventListener("resize", updateReference);
      window.removeEventListener("scroll", updateReference, true);
    };
  }, [settingsFloatingRefs, settingsFloatingUpdate, settingsPanelOpen]);

  const handleModeChange = useCallback(
    (mode: "intensive" | "immersive") => {
      const next = { ...readerSettings, mode };
      setReaderSettings(next);
      persistReaderSettings(next);
      setActiveSelection(null);
      setInspectState(null);
      setActiveSentenceChunkId(null);
    },
    [readerSettings],
  );
  // SelectionToolbar 已迁移为 Plate FloatingToolbar（由 FloatingToolbarKit 在 render.afterEditable 渲染），
  // toolbarOpen / toolbarFloating 不再需要，FloatingToolbar 通过 Plate editor selection 自动管理显示。
  const [highlightMenu, setHighlightMenu] = useState<{
    mark: ReaderRecordPlateUserHighlightMark;
    anchor: HTMLElement;
  } | null>(null);
  const highlightMenuFloating = useReaderFloatingLayer({
    open: highlightMenu !== null,
    placement: "bottom",
    offsetPx: 8,
  });
  const quickPeekOpen = lookupState.kind !== "idle" || inspectState !== null;
  const quickPeekAnchorRef = useRef<
    | { kind: "element"; element: HTMLElement }
    | { kind: "range"; getRect: () => DOMRectReadOnly | DOMRect }
    | null
  >(null);
  const quickPeekFloating = useReaderFloatingLayer({
    open: quickPeekOpen,
    placement: "bottom-start",
    offsetPx: 8,
    collisionPadding: 12,
    strategy: "fixed",
  });

  const { refs: quickPeekFloatingRefs, update: quickPeekFloatingUpdate } =
    quickPeekFloating;
  useEffect(() => {
    if (!quickPeekOpen) {
      return;
    }

    function updateReference() {
      const anchor = quickPeekAnchorRef.current;
      if (!anchor) {
        return;
      }
      if (anchor.kind === "element") {
        quickPeekFloatingRefs.setPositionReference?.({
          getBoundingClientRect: () => anchor.element.getBoundingClientRect(),
          contextElement: anchor.element,
        });
      } else {
        quickPeekFloatingRefs.setPositionReference?.({
          getBoundingClientRect: anchor.getRect,
        });
      }
      quickPeekFloatingUpdate?.();
    }

    updateReference();
    window.addEventListener("resize", updateReference);
    window.addEventListener("scroll", updateReference, true);
    return () => {
      window.removeEventListener("resize", updateReference);
      window.removeEventListener("scroll", updateReference, true);
    };
  }, [quickPeekFloatingRefs, quickPeekFloatingUpdate, quickPeekOpen]);

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
  const [pendingAskRequest, setPendingAskRequest] =
    useState<PendingReaderRecordAskRequest | null>(null);
  const [feedbackState, setFeedbackState] = useState<SaveState>({ kind: "idle" });
  const [feedbackTarget, setFeedbackTarget] = useState<{
    blockId: string;
    variant: "grammar" | "supplement" | "vocabulary" | "sentence_analysis";
    feedbackScope: "annotation" | "dictionary";
    analysisRecordId?: string;
    anchorSegmentId: string;
    title: string;
    annotationType?: string;
  } | null>(null);
  const feedbackFloating = useReaderFloatingLayer({
    open: feedbackTarget !== null,
    placement: "bottom-end",
    offsetPx: 4,
  });

  // InlineCommentPanel 浮动层 — 锚定到当前选区 rect（draft）或笔记 mark DOM（existing）。
  // 通过 FloatingPortal 渲染到 body，避免在文档流中挤压正文。
  const commentPanelOpen =
    noteAnchorDraft !== null || noteMenu !== null;
  const commentFloating = useReaderFloatingLayer({
    open: commentPanelOpen,
    placement: "bottom",
    offsetPx: 8,
  });

  // 选区或激活笔记变化时，更新浮动层的 reference 元素
  useEffect(() => {
    if (!commentPanelOpen) return;

    // draft 模式：用选区 rect
    if (noteAnchorDraft && activeSelection?.rect) {
      const rect = activeSelection.rect;
      commentFloating.refs.setReference({
        getBoundingClientRect: () => rect,
      });
      return;
    }
    // existing note 模式：用笔记 mark 的 DOM element
    if (noteMenu?.anchor) {
      const anchor = noteMenu.anchor;
      commentFloating.refs.setReference({
        getBoundingClientRect: () => anchor.getBoundingClientRect(),
      });
    }
  }, [
    commentPanelOpen,
    noteAnchorDraft,
    noteMenu,
    activeSelection,
    commentFloating.refs,
  ]);

  // SelectionAnchorBridge 在 <Plate> 内通过 useEditorSelection 订阅选区，
  // 拿到 Plate editor.selection → ReaderRecordSelectionAnchorBridgeResult。
  // 替代旧的 selectionchange DOM 监听 + readReaderRecordSelectionAnchorDrafts。
  const handleSelectionChange = useCallback(
    (nextSelection: ReaderRecordSelectionAnchorBridgeResult | null) => {
      setActiveSelection(nextSelection);
      setCopyStatus("idle");
      setWriteState((current) => (current.kind === "saving" ? current : { kind: "idle" }));
    },
    [],
  );

  useEffect(() => {
    if (lookupState.kind === "idle" && inspectState === null) {
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
      setInspectState(null);
    }
    window.document.addEventListener("pointerdown", handlePointerDown);
    return () => {
      window.document.removeEventListener("pointerdown", handlePointerDown);
    };
  }, [inspectState, lookupState.kind, quickPeekFloating.refs.floating]);

  useEffect(() => {
    if (!settingsPanelOpen) {
      return;
    }

    function handlePointerDown(event: PointerEvent) {
      const target = event.target as Node | null;
      if (!target) {
        return;
      }
      if (settingsFloating.refs.floating.current?.contains(target)) {
        return;
      }
      if (settingsAnchorRef.current?.contains(target)) {
        return;
      }
      setSettingsPanelOpen(false);
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setSettingsPanelOpen(false);
      }
    }

    window.document.addEventListener("pointerdown", handlePointerDown);
    window.document.addEventListener("keydown", handleKeyDown);
    return () => {
      window.document.removeEventListener("pointerdown", handlePointerDown);
      window.document.removeEventListener("keydown", handleKeyDown);
    };
  }, [settingsFloating.refs.floating, settingsPanelOpen]);

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
      if (hasNonCollapsedNativeSelection()) {
        return;
      }
      const query = vocabularyTitle(mark).trim();
      if (!query) {
        return;
      }
      const contextSentence =
        sourceTextForAnchorSegment(
          plateDocument.children,
          mark.anchor.anchorSegmentId,
        ) || mark.anchor.selectedText;
      const context: ReaderRecordLookupContext = {
        contextSentence,
        sentenceId: mark.anchor.sentenceId,
        anchorText: mark.anchor.selectedText,
        lookupType: lookupTypeForSelection(query),
        source: "vocabulary",
      };
      const rect = anchor.getBoundingClientRect();
      quickPeekAnchorRef.current = { kind: "range", getRect: () => rect };
      quickPeekFloating.refs.setPositionReference?.({
        getBoundingClientRect: () => rect,
      });

      const inspectIntent = structuredInspectIntentFromVocabularyMark(
        mark,
        contextSentence,
      );
      if (inspectIntent) {
        setLookupState({ kind: "idle" });
        setInspectState(inspectIntent);
        return;
      }

      setInspectState(null);
      setLookupState({ kind: "loading", query, context });
      void (async () => {
        try {
          const params = new URLSearchParams({
            word: query,
            type: context.lookupType,
            context: contextSentence,
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
    [plateDocument.children, quickPeekFloating.refs],
  );

  const handleCopy = useCallback(async () => {
    const text = singleRangeDraft(activeSelection)?.selected_text
      ?? activeSelection?.selectedText
      ?? "";
    if (!text.trim()) {
      return;
    }

    try {
      await navigator.clipboard.writeText(text);
      setCopyStatus("copied");
    } catch {
      setCopyStatus("error");
    }
  }, [activeSelection]);

  const handleDocumentCopyCapture = useCallback(
    (event: ReactClipboardEvent<HTMLElement>) => {
      const payload = sanitizedSelectionClipboardPayload(event.currentTarget);
      if (!payload) {
        return;
      }
      event.preventDefault();
      event.clipboardData.setData("text/plain", payload.text);
      if (payload.html) {
        event.clipboardData.setData("text/html", payload.html);
      }
    },
    [],
  );

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
      const getRect = () => liveRange?.getBoundingClientRect() ?? selection.rect!;
      quickPeekAnchorRef.current = { kind: "range", getRect };
      quickPeekFloating.refs.setPositionReference?.({
        getBoundingClientRect: getRect,
      });
    }

    setInspectState(null);
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

  const handleLookupFromInspect = useCallback(async () => {
    const intent = inspectState;
    if (!intent) {
      return;
    }
    const query = (intent.lookupText ?? intent.anchorText).trim();
    if (!query) {
      return;
    }
    const lookupType =
      intent.lookupKind === "phrase" || /\s/.test(query) ? "phrase" : "word";
    const context: ReaderRecordLookupContext = {
      contextSentence: intent.contextSentence,
      sentenceId: intent.sentenceId,
      anchorText: intent.anchorText,
      lookupType,
      source: "vocabulary",
    };

    setInspectState(null);
    setLookupState({ kind: "loading", query, context });

    try {
      const params = new URLSearchParams({
        word: query,
        type: lookupType,
        context: intent.contextSentence,
        sentenceId: intent.sentenceId,
      });
      const response = await fetch(`/api/web/dict/lookup?${params.toString()}`);
      const payload = (await response.json().catch(() => null)) as
        | WebDictResult
        | null;
      if (!payload || (!response.ok && payload.kind !== "error")) {
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
      console.warn("[ReaderRecordPlateSurface] inspect phrase lookup failed", error);
      setLookupState({
        kind: "error",
        query,
        context,
        message: "词典查询失败，请稍后重试。",
      });
    }
  }, [inspectState]);

  const activeLookupSnapshot = useMemo(
    () => buildDictionaryLookupSnapshot(snapshot, lookupState),
    [snapshot, lookupState],
  );
  const currentAskSelectionAttachment = useMemo<ReaderAskAttachment | null>(() => {
    const selection = activeSelection;
    const draft = singleRangeDraft(selection);
    const segment = selection?.supportedSingleRange ? (selection.segments[0] ?? null) : null;
    const anchorPayload = sourceSelectionAnchorPayload(snapshot.record_id, selection);
    if (!selection) {
      return null;
    }

    if (draft && segment && anchorPayload) {
      return {
        kind: "text_selection",
        subtype: selection.anchorType,
        label: draft.selected_text,
        selectedText: draft.selected_text,
        targetKey: draft.anchor_segment_id,
        anchorPayload,
        metadata: {
          pageIdentity: askPageIdentity,
          sourceSurface: "selection_toolbar",
          entryAction: "ask_about_this",
          surfaceKind: "source",
          blockType: selection.blockType,
          blockId: selection.blockId,
          anchorSegmentId: draft.anchor_segment_id,
          unitId: draft.unit_id,
          sourceContext: selection.blockContext.source as Record<string, unknown> | undefined,
          sentenceId: segment.sentenceId,
          paragraphId: segment.paragraphId,
          startOffset: draft.start_offset,
          endOffset: draft.end_offset,
          readingRecordAnchor: readingRecordAskAnchorFromDraft(draft),
        },
      };
    }

    if (hasNonSourceDocumentSelection(selection)) {
      return null;
    }

    return null;
  }, [activeSelection, askPageIdentity, snapshot.record_id]);

  const openDictionaryRail = useCallback(() => {
    setDictionaryOpen(true);
    setLookupState({ kind: "idle" });
    setInspectState(null);
    quickPeekAnchorRef.current = null;
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

  const openAskPanel = useCallback((
    attachment?: ReaderAskAttachment | null,
    pendingRequest?: PendingReaderRecordAskRequest | null,
  ) => {
    if (attachment === null) {
      setAskAttachments([]);
    } else if (attachment) {
      setAskAttachments([attachment]);
    }
    setPendingAskRequest(pendingRequest ?? null);
    setAskOpen(true);
    setDictionaryOpen(false);
    setDictionaryAIPanelOpen(false);
    setDictionaryAI({ kind: "idle" });
    setLookupState({ kind: "idle" });
    setInspectState(null);
    setHighlightMenu(null);
    setNoteMenu(null);
    setFeedbackTarget(null);
  }, []);

  const handleAttachInspectToAsk = useCallback(() => {
    if (!inspectState) {
      return;
    }
    openAskPanel(askAttachmentFromVocabularyInspect(askPageIdentity, inspectState));
  }, [askPageIdentity, inspectState, openAskPanel]);

  const handleAskFromSelection = useCallback(() => {
    if (!currentAskSelectionAttachment) {
      return;
    }
    openAskPanel(currentAskSelectionAttachment);
  }, [currentAskSelectionAttachment, openAskPanel]);

  const handleAskPromptFromSelection = useCallback(
    (request: {
      content: string;
      entryAction?: ReaderAskEntryActionDto;
      submissionMode?: "chat" | "quick_action";
    }) => {
      const content = request.content.trim();
      if (!content || !currentAskSelectionAttachment) {
        return;
      }
      const pendingRequest: PendingReaderRecordAskRequest = {
        content,
        entryAction: request.entryAction ?? "ask_about_this",
        attachments: [currentAskSelectionAttachment],
        submissionMode: request.submissionMode ?? "chat",
      };
      openAskPanel(currentAskSelectionAttachment, pendingRequest);
    },
    [currentAskSelectionAttachment, openAskPanel],
  );

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

  const handleFeedbackFromCallout = useCallback(
    (target: ReaderCalloutActionTarget, anchor: HTMLElement) => {
      if (target.kind !== "sentence_analysis" || !target.analysisId) {
        return;
      }
      feedbackFloating.refs.setReference({
        getBoundingClientRect: () => anchor.getBoundingClientRect(),
      });
      setFeedbackTarget({
        blockId: target.blockId,
        variant: "sentence_analysis",
        feedbackScope: "annotation",
        analysisRecordId: target.analysisId,
        anchorSegmentId: target.anchorSegmentId,
        title: target.title,
        annotationType: "sentence_analysis",
      });
    },
    [feedbackFloating.refs],
  );

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
      setInspectState(null);
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
      setInspectState(null);
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

  const handleSubmitFeedback = useCallback(
    async (sentiment: "positive" | "negative") => {
      const target = feedbackTarget;
      if (!target) {
        return;
      }
      setFeedbackTarget(null);
      setFeedbackState({ kind: "saving" });
      try {
        if (target.feedbackScope === "annotation" && !target.analysisRecordId) {
          setFeedbackState({
            kind: "error",
            message: "当前标注反馈暂不可用。",
          });
          return;
        }
        const feedbackType =
          target.feedbackScope === "dictionary"
            ? sentiment === "negative"
              ? "wrong_definition"
              : null
            : sentiment === "positive"
              ? "helpful"
              : "inaccurate";
        if (!feedbackType) {
          setFeedbackState({
            kind: "error",
            message: "当前反馈类型暂不可用。",
          });
          return;
        }
        const response = await fetch("/api/web/feedback", {
          method: "POST",
          headers: {
            "content-type": "application/json",
            accept: "application/json",
          },
          body: JSON.stringify({
            feedbackScope: target.feedbackScope,
            targetId: target.blockId,
            sentiment,
            feedbackType,
            ...(target.analysisRecordId
              ? { analysisRecordId: target.analysisRecordId }
              : {}),
            ...(target.feedbackScope === "annotation"
              ? {
                  annotationType:
                    target.annotationType ??
                    (target.variant === "grammar"
                      ? "grammar_note"
                      : "ask_supplement"),
                }
              : {}),
            entryPoint:
              target.variant === "vocabulary"
                ? "reader_record_vocabulary_mark"
                : "reader_record_callout",
            contextJson: {
              readingRecordId: snapshot.record_id,
              annotationType: target.annotationType,
              targetVariant: target.variant,
            },
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
    [feedbackTarget, snapshot.record_id],
  );

  const handleInspectFeedback = useCallback(() => {
    const intent = inspectState;
    if (!intent) {
      return;
    }
    const floatingNode = quickPeekFloating.refs.floating.current;
    if (floatingNode) {
      feedbackFloating.refs.setReference({
        getBoundingClientRect: () => floatingNode.getBoundingClientRect(),
      });
    }
    setFeedbackTarget({
      blockId: intent.markId,
      variant: "vocabulary",
      feedbackScope: "dictionary",
      anchorSegmentId: "",
      title: intent.title,
      annotationType: intent.annotationType,
    });
  }, [feedbackFloating.refs, inspectState, quickPeekFloating.refs.floating]);

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

  const saveHighlightColorById = useCallback(
    async (
      targetAssetId: string,
      color: string,
      options: { closeMenu?: boolean } = {},
    ) => {
      if (writeState.kind === "saving") {
        return;
      }

      const previousAssets = localUserAssets;
      if (options.closeMenu) {
        setHighlightMenu(null);
      }
      setLocalUserAssets((current) =>
        current.map((asset) =>
          asset.asset_id === targetAssetId
            ? { ...asset, color, updated_at: new Date().toISOString() }
            : asset,
        ),
      );
      setWriteState({ kind: "saving", action: "highlight" });

      try {
        const payload = await patchReadingRecordHighlightColor(
          targetAssetId,
          color,
        );
        const canonical = canonicalHighlightAssetFromWritePayload(
          snapshot,
          payload,
        );
        if (canonical) {
          setLocalUserAssets((current) =>
            reconcileCanonicalHighlightAsset(
              current,
              canonical.asset,
              canonical.supersededIds,
              canonical.asset.asset_id === targetAssetId ? [] : [targetAssetId],
            ),
          );
        } else {
          await onRequestSnapshotReload?.();
        }
        setWriteState({
          kind: "saved",
          action: "highlight",
          message: "高亮颜色已更新",
        });
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
    [localUserAssets, onRequestSnapshotReload, snapshot, writeState.kind],
  );

  const handleHighlight = useCallback(async (color: string = "warm_yellow") => {
    const draft = singleRangeDraft(activeSelection);
    if (!draft || writeState.kind === "saving") {
      return;
    }

    const exactHighlight = findExactUserHighlightAsset(localUserAssets, draft);
    if (exactHighlight) {
      await saveHighlightColorById(exactHighlight.asset_id, color);
      return;
    }

    const tempAsset = buildTempUserAsset(snapshot, draft, {
      kind: "highlight",
      color,
    });
    setLocalUserAssets((current) => [...current, tempAsset]);
    setWriteState({ kind: "saving", action: "highlight" });

    try {
      const payload = await postReadingRecordUserAsset("/api/web/reading-record/highlights", {
        anchor: draft,
        selectedText: draft.selected_text,
        color,
      });
      const canonical = canonicalHighlightAssetFromWritePayload(snapshot, payload);
      if (canonical) {
        setLocalUserAssets((current) =>
          reconcileCanonicalHighlightAsset(
            current,
            canonical.asset,
            canonical.supersededIds,
            [tempAsset.asset_id],
          ),
        );
      } else {
        await onRequestSnapshotReload?.();
      }
      setWriteState({
        kind: "saved",
        action: "highlight",
        message: "高亮已保存",
      });
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
  }, [
    activeSelection,
    localUserAssets,
    onRequestSnapshotReload,
    saveHighlightColorById,
    snapshot,
    writeState.kind,
  ]);

  const handleOpenNoteComposer = useCallback(() => {
    const draft = singleRangeDraft(activeSelection);
    if (!draft || writeState.kind === "saving" || !commentApiReady) {
      return;
    }

    setNoteAnchorDraft(draft);
    setNoteDraft("");
    setNoteDuplicateAcknowledged(false);
    setWriteState({ kind: "idle" });
    // 通过 CommentKit 的 setDraft 创建 draft comment mark 并设置 activeId，
    // InlineCommentPanel 读取 activeId 后显示 composer。
    commentApiRef.current?.setDraft();
  }, [activeSelection, commentApiReady, writeState.kind]);

  const handleCancelNote = useCallback(() => {
    if (writeState.kind === "saving") {
      return;
    }
    setNoteAnchorDraft(null);
    setNoteDraft("");
    setNoteDuplicateAcknowledged(false);
    // 移除 draft comment mark 并清除 activeId，关闭 InlineCommentPanel。
    commentApiRef.current?.removeMark();
    commentApiRef.current?.setActiveId(null);
  }, [writeState.kind]);

  const handleViewDuplicateNote = useCallback(() => {
    const duplicateNote = duplicateNoteForDraft;
    if (!duplicateNote) {
      return;
    }
    const anchor =
      surfaceRef.current?.querySelector<HTMLElement>(
        readerNoteAssetIdSelector(duplicateNote.assetId),
      ) ??
      surfaceRef.current ??
      window.document.body;

    setNoteAnchorDraft(null);
    setNoteDraft("");
    setNoteDuplicateAcknowledged(false);
    commentApiRef.current?.removeMark();
    setNoteMenu({
      mark: duplicateNote,
      anchor,
      mode: "view",
      draft: duplicateNote.noteText,
    });
    commentApiRef.current?.setActiveId(duplicateNote.assetId);
  }, [duplicateNoteForDraft]);

  const handleContinueDuplicateNote = useCallback(() => {
    setNoteDuplicateAcknowledged(true);
  }, []);

  const handleSaveNote = useCallback(async () => {
    const draft = noteAnchorDraft;
    const noteText = noteDraft.trim();
    if (
      !draft ||
      !noteText ||
      writeState.kind === "saving" ||
      (duplicateNoteForDraft && !noteDuplicateAcknowledged)
    ) {
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
      setNoteDuplicateAcknowledged(false);
      // 保存成功后清除 activeId 关闭 InlineCommentPanel；
      // draft comment mark 会在 snapshot reload 后通过 editor.tf.setValue 自然清除。
      commentApiRef.current?.setActiveId(null);
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
  }, [
    duplicateNoteForDraft,
    noteAnchorDraft,
    noteDraft,
    noteDuplicateAcknowledged,
    onRequestSnapshotReload,
    snapshot,
    writeState.kind,
  ]);

  const handleActivateHighlight = useCallback(
    (mark: ReaderRecordPlateUserHighlightMark, anchor: HTMLElement) => {
      if (hasNonCollapsedNativeSelection()) {
        return;
      }
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

      await saveHighlightColorById(activeMenu.mark.assetId, color, {
        closeMenu: true,
      });
    },
    [highlightMenu, saveHighlightColorById, writeState.kind],
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
      if (hasNonCollapsedNativeSelection()) {
        return;
      }
      setNoteAnchorDraft(null);
      setNoteDraft("");
      setNoteDuplicateAcknowledged(false);
      setNoteMenu({ mark, anchor, mode: "view", draft: mark.noteText });
      // 设置 CommentKit activeId 为笔记 assetId，InlineCommentPanel 读取后显示 view 模式。
      if (commentApiReady) {
        commentApiRef.current?.setActiveId(mark.assetId);
      }
    },
    [commentApiReady],
  );

  useEffect(() => {
    if (!commentApiReady || !noteMenu) {
      return;
    }
    commentApiRef.current?.setActiveId(noteMenu.mark.assetId);
  }, [commentApiReady, noteMenu?.mark.assetId, noteMenu]);

  const pulseGrammarItemId = useCallback((itemId: string) => {
    if (grammarPulseTimerRef.current !== null) {
      window.clearTimeout(grammarPulseTimerRef.current);
      grammarPulseTimerRef.current = null;
    }
    setActiveGrammarItemId(itemId);
    grammarPulseTimerRef.current = window.setTimeout(() => {
      setActiveGrammarItemId((current) => (current === itemId ? null : current));
      grammarPulseTimerRef.current = null;
    }, 1600);
  }, []);

  const requestExpandGrammarItem = useCallback((itemId: string) => {
    grammarExpandRequestIdRef.current += 1;
    setGrammarExpandRequest({
      itemId,
      requestId: grammarExpandRequestIdRef.current,
    });
  }, []);

  const handleActivateGrammar = useCallback(
    (mark: ReaderRecordPlateGrammarMark) => {
      if (hasNonCollapsedNativeSelection()) {
        return;
      }
      setLookupState({ kind: "idle" });
      setInspectState(null);
      quickPeekAnchorRef.current = null;
      setActiveGrammarItemId(mark.itemId);
      requestExpandGrammarItem(mark.itemId);

      const callout = surfaceRef.current?.querySelector<HTMLElement>(
        `[data-callout-variant="grammar"]${dataAttributeEqualsSelector(
          "data-reader-record-grammar-item-id",
          mark.itemId,
        )}`,
      );
      if (callout) {
        callout.scrollIntoView({ behavior: "smooth", block: "center" });
        callout.focus({ preventScroll: true });
      }
    },
    [requestExpandGrammarItem],
  );

  const handleLeafClickIntent = useCallback<ReaderLeafClickResolver>(
    (leaf, anchor, event) => {
      if (suppressNextMarkClickRef.current || hasNonCollapsedNativeSelection()) {
        suppressNextMarkClickRef.current = false;
        return;
      }

      if (hasNonCollapsedReaderSelection(activeSelection)) {
        return;
      }

      if (leaf.vocabulary_data) {
        event.preventDefault();
        handleActivateVocabulary(leaf.vocabulary_data, anchor);
        return;
      }

      if (leaf.grammar_data) {
        event.preventDefault();
        handleActivateGrammar(leaf.grammar_data);
        return;
      }

      const noteMark = userNoteMarksFromLeaf(leaf)[0];
      if (noteMark) {
        event.preventDefault();
        handleActivateNote(noteMark, anchor);
        return;
      }

      if (leaf.user_highlight_data) {
        event.preventDefault();
        handleActivateHighlight(leaf.user_highlight_data, anchor);
      }
    },
    [
      activeSelection,
      handleActivateGrammar,
      handleActivateHighlight,
      handleActivateNote,
      handleActivateVocabulary,
    ],
  );

  leafClickResolverRef.current = handleLeafClickIntent;

  const sentenceAnalysisInteraction = useMemo(
    () => ({
      activeChunkId: activeSentenceChunkId,
      setActiveChunkId: setActiveSentenceChunkId,
    }),
    [activeSentenceChunkId],
  );

  const grammarInteraction = useMemo(
    () => ({
      activeGrammarItemId,
      expandGrammarItemRequest: grammarExpandRequest,
      setActiveGrammarItemId,
      pulseGrammarItemId,
      requestExpandGrammarItem,
    }),
    [
      activeGrammarItemId,
      grammarExpandRequest,
      pulseGrammarItemId,
      requestExpandGrammarItem,
    ],
  );

  const calloutActions = useMemo(
    () => ({
      onFeedbackFromCallout: handleFeedbackFromCallout,
    }),
    [handleFeedbackFromCallout],
  );

  const toolbarActionState = useMemo<ReaderToolbarActions["state"]>(() => {
    const draft = singleRangeDraft(activeSelection);
    const sourceSingleRangeReady = Boolean(draft);
    const copyReady = canCopySelection(activeSelection);
    const askReady =
      canAskSelection(activeSelection) && currentAskSelectionAttachment !== null;
    const sourceLookupReason = sourceOnlyDisabledReason(activeSelection, "lookup");
    const sourceWriteReason = sourceOnlyDisabledReason(activeSelection, "write");
    const copyReason = !activeSelection
      ? "请选择稳定原文后再操作"
      : copyReady
        ? undefined
        : "暂不支持跨段或非稳定原文选区";
    const askReason = !activeSelection
      ? "请选择稳定原文后再操作"
      : hasSourceMultiTextSelection(activeSelection)
        ? "跨句选区暂不支持 Ask"
      : activeSelection.surfaceKind !== "source"
        ? "当前仅支持原文 Ask"
      : askReady
        ? undefined
        : "暂不支持跨段或非稳定原文选区";
    const savingReason =
      writeState.kind === "saving" ? writeStateLabel(writeState) : undefined;

    return {
      lookup: {
        disabled: !sourceSingleRangeReady || lookupState.kind === "loading",
        reason:
          lookupState.kind === "loading" ? "正在查询词典" : sourceLookupReason,
      },
      copy: {
        disabled: !copyReady,
        reason: copyReason,
      },
      ask: {
        disabled: !askReady,
        reason: askReason,
      },
      highlight: {
        disabled: !sourceSingleRangeReady || writeState.kind === "saving",
        reason: savingReason ?? sourceWriteReason,
      },
      note: {
        disabled:
          !sourceSingleRangeReady ||
          !commentApiReady ||
          writeState.kind === "saving" ||
          noteAnchorDraft !== null,
        reason:
          !sourceSingleRangeReady
            ? sourceWriteReason
            : !commentApiReady
            ? "笔记工具初始化中"
            : noteAnchorDraft !== null
            ? "笔记面板已打开"
            : savingReason ?? sourceWriteReason,
      },
    };
  }, [
    activeSelection,
    commentApiReady,
    currentAskSelectionAttachment,
    lookupState.kind,
    noteAnchorDraft,
    writeState,
  ]);

  // 把选区工具栏回调打包为 Context value，供 ReaderFloatingToolbarButtons 消费。
  const toolbarActions = useMemo<ReaderToolbarActions>(
    () => ({
      onAsk: () => handleAskFromSelection(),
      onAskSubmit: (request) => handleAskPromptFromSelection(request),
      onCopy: () => handleCopy(),
      onHighlight: () => handleHighlight(),
      onNote: () => handleOpenNoteComposer(),
      onLookup: () => handleLookup(),
      suppressToolbar: quickPeekOpen,
      state: toolbarActionState,
    }),
    [
      handleAskFromSelection,
      handleAskPromptFromSelection,
      handleCopy,
      handleHighlight,
      handleOpenNoteComposer,
      handleLookup,
      quickPeekOpen,
      toolbarActionState,
    ],
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

  // InlineCommentPanel 的笔记编辑草稿变更回调。
  const handleNoteEditDraftChange = useCallback((value: string) => {
    setNoteMenu((current) =>
      current ? { ...current, draft: value } : null,
    );
  }, []);

  // InlineCommentPanel 关闭回调（X 按钮）。
  const handleCloseCommentPanel = useCallback(() => {
    if (noteAnchorDraft) {
      // draft 模式：取消新建笔记。
      handleCancelNote();
    } else if (noteMenu) {
      // existing note 模式：退出编辑 + 清除 noteMenu。
      setNoteDuplicateAcknowledged(false);
      setNoteMenu(null);
    }
  }, [noteAnchorDraft, noteMenu, handleCancelNote]);

  // InlineCommentPanel 的状态消息。
  const commentStatusMessage =
    writeState.kind === "saved" && writeState.action === "note"
      ? writeState.message
      : writeState.kind === "error" && writeState.action === "note"
        ? writeState.message
        : null;
  const commentIsSaving =
    writeState.kind === "saving" && writeState.action === "note";

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
      // 编辑保存后清除 activeId 关闭 InlineCommentPanel。
      commentApiRef.current?.setActiveId(null);
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
    // 删除后清除 activeId 关闭 InlineCommentPanel。
    commentApiRef.current?.setActiveId(null);
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

  return (
    <section
      ref={surfaceRef}
      data-testid="reader-record-plate-surface"
      data-reader-record-surface="plate-readonly-reading"
      className={`${className} ${themeClassName}`}
    >
      {/* Header sits in its own wider editorial column, decoupled from the reading column. */}
      <div className="reader-header-band-inner mx-auto w-full max-w-[82ch]">
        <ReaderRecordHeader
          snapshot={snapshot}
          progress={plateDocument.progress}
          surfaceMode={surfaceMode}
          onModeChange={handleModeChange}
          onOpenSettings={handleOpenSettingsPanel}
        />
        {settingsPanelOpen ? (
          <ReaderFloatingSurface
            chrome="bare"
            floatingRef={settingsFloating.refs.setFloating}
            style={settingsFloating.floatingStyles as CSSProperties}
            data-reader-record-settings-panel="open"
            data-testid="reader-record-settings-popover"
            onClick={(event) => event.stopPropagation()}
            onPointerDown={(event) => event.stopPropagation()}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.stopPropagation();
                setSettingsPanelOpen(false);
              }
            }}
          >
            <ReaderSettingsPanel
              variant="floating"
              themeName={themeName}
              value={readerSettings}
              onChange={handleSettingsChange}
              onThemeChange={setThemeName}
              onClose={() => setSettingsPanelOpen(false)}
            />
          </ReaderFloatingSurface>
        ) : null}
      </div>

      <div className={contentColumnClassName}>
        <SelectionActionState
          copyStatus={copyStatus}
          selection={activeSelection}
          writeState={writeState}
        />
        {highlightMenu ? (
          <ReaderFloatingSurface
            floatingRef={highlightMenuFloating.refs.setFloating}
            style={highlightMenuFloating.floatingStyles as CSSProperties}
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
        {/* noteMenu 浮层已迁移到 InlineCommentPanel（CommentKit activeId 驱动） */}
        {quickPeekOpen ? (
          <ReaderQuickPeek
            lookup={activeLookupSnapshot}
            inspect={inspectState}
            className="reader-tool-float"
            floatingRef={(node) => {
              quickPeekFloating.refs.setFloating(node);
              if (node) {
                node.setAttribute("data-reader-record-quick-peek", lookupState.kind);
                node.setAttribute("data-testid", "reader-record-plate-lookup-panel");
              }
            }}
            style={quickPeekFloating.floatingStyles as CSSProperties}
            onDismiss={() => {
              setLookupState({ kind: "idle" });
              setInspectState(null);
              quickPeekAnchorRef.current = null;
            }}
            onOpenDetail={activeLookupSnapshot ? openDictionaryRail : undefined}
            onLookupPhrase={inspectState ? handleLookupFromInspect : undefined}
            onAttachToAsk={inspectState ? handleAttachInspectToAsk : undefined}
            onFeedback={inspectState ? handleInspectFeedback : undefined}
          />
        ) : null}
        {feedbackTarget ? (
          <ReaderFloatingSurface
            floatingRef={feedbackFloating.refs.setFloating}
            style={feedbackFloating.floatingStyles as CSSProperties}
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
            {feedbackTarget.feedbackScope === "annotation" ? (
              <button
                type="button"
                className="block w-full rounded-sm px-3 py-1.5 text-left text-sm text-foreground hover:bg-structure-green/10 disabled:cursor-not-allowed disabled:text-muted disabled:hover:bg-transparent"
                disabled={!feedbackTarget.analysisRecordId}
                data-reader-record-disabled-reason={
                  feedbackTarget.analysisRecordId
                    ? undefined
                    : "当前标注反馈需要 analysisRecordId"
                }
                onClick={() => handleSubmitFeedback("positive")}
              >
                有帮助
              </button>
            ) : null}
            <button
              type="button"
              className="mt-0.5 block w-full rounded-sm px-3 py-1.5 text-left text-sm text-foreground hover:bg-error-red/10 disabled:cursor-not-allowed disabled:text-muted disabled:hover:bg-transparent"
              disabled={
                feedbackTarget.feedbackScope === "annotation" &&
                !feedbackTarget.analysisRecordId
              }
              data-reader-record-disabled-reason={
                feedbackTarget.feedbackScope === "annotation" &&
                !feedbackTarget.analysisRecordId
                  ? "当前标注反馈需要 analysisRecordId"
                  : undefined
              }
              onClick={() => handleSubmitFeedback("negative")}
            >
              {feedbackTarget.feedbackScope === "dictionary" ? "释义有问题" : "有问题"}
            </button>
            {feedbackTarget.feedbackScope === "annotation" &&
            !feedbackTarget.analysisRecordId ? (
              <p className="mt-1 px-3 py-1 text-xs leading-5 text-muted" role="status">
                当前标注反馈暂不可用
              </p>
            ) : null}
          </ReaderFloatingSurface>
        ) : null}
        <ReaderGrammarInteractionContext.Provider value={grammarInteraction}>
          <ReaderCalloutActionContext.Provider value={calloutActions}>
            <ReaderSentenceAnalysisInteractionContext.Provider
              value={sentenceAnalysisInteraction}
            >
              <ReaderToolbarActionsProvider value={toolbarActions}>
                <Plate editor={editor} readOnly>
                  <CommentPluginBridge
                    apiRef={commentApiRef}
                    onReadyChange={setCommentApiReady}
                  />
                  <SelectionAnchorBridge
                    snapshot={snapshot}
                    onChange={handleSelectionChange}
                  />
                  <EditorContainer
                    className={`reader-record-plate-document reader-record-plate-document--notion px-0 py-0 outline-none cursor-default overflow-visible bg-transparent ${readingClassName} ${typography.bodyClassName} ${typography.paragraphDensityClassName}`.trim()}
                    data-reader-record-mode={surfaceMode}
                    onCopyCapture={handleDocumentCopyCapture}
                  >
                    <Editor readOnly disableDefaultStyles renderLeaf={renderLeaf as never} />
                  </EditorContainer>
                  <InlineCommentPanel
                    draftText={noteDraft}
                    draftQuoteText={noteAnchorDraft?.selected_text ?? null}
                    onDraftTextChange={setNoteDraft}
                    onSaveDraft={handleSaveNote}
                    onCancelDraft={handleCancelNote}
                    duplicateNote={duplicateNoteForDraft}
                    duplicateAcknowledged={noteDuplicateAcknowledged}
                    onViewDuplicateNote={handleViewDuplicateNote}
                    onContinueDuplicateNote={handleContinueDuplicateNote}
                    activeNote={noteMenu?.mark ?? null}
                    noteEditMode={noteMenu?.mode ?? "view"}
                    noteEditDraft={noteMenu?.draft ?? ""}
                    onNoteEditDraftChange={handleNoteEditDraftChange}
                    onStartEditNote={handleStartEditNote}
                    onCancelEditNote={handleCancelEditNote}
                    onSaveNoteEdit={handleSaveNoteEdit}
                    onDeleteNote={handleDeleteNote}
                    onAskFromNote={handleAskFromNote}
                    isSaving={commentIsSaving}
                    statusMessage={commentStatusMessage}
                    onClose={handleCloseCommentPanel}
                    floatingRef={commentFloating.refs.setFloating}
                    floatingStyles={commentFloating.floatingStyles as CSSProperties}
                  />
                </Plate>
              </ReaderToolbarActionsProvider>
            </ReaderSentenceAnalysisInteractionContext.Provider>
          </ReaderCalloutActionContext.Provider>
        </ReaderGrammarInteractionContext.Provider>
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
        pendingQuickActionRequest={pendingAskRequest}
        onRemoveAttachment={handleRemoveAskAttachment}
        onClearAttachments={() => setAskAttachments([])}
        onPendingQuickActionConsumed={() => setPendingAskRequest(null)}
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
