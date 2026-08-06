"use client";

import {
  TEXT_RANGE_HASH_ALGORITHM,
  TEXT_RANGE_OFFSET_UNIT,
  buildTextRangeTargetKey,
} from "@claread/contracts";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type {
  ClipboardEvent as ReactClipboardEvent,
  CSSProperties,
  HTMLAttributes,
  MouseEvent,
  PointerEvent as ReactPointerEvent,
  ReactNode,
  Ref,
} from "react";

import {
  AiWorkspacePanel,
  type AiWorkspaceSurface,
} from "@/components/reader/AiWorkspacePanel";
import {
  useAskComposerContext,
  type ReaderAskQuickActionRequest,
} from "@/components/reader/ask/composer-context";
import { useAppShellLayout } from "@/components/layout/app-shell";
import { ReaderRecordNavigationRail } from "@/components/reader/plate/ReaderRecordNavigationRail";
import {
  readerAskPresentationCssVars,
  useReaderAskPresentation,
} from "@/components/reader/plate/useReaderAskPresentation";
import type { DictLookupTypeDto, WebDictResult } from "@/types/api/dict";
import {
  projectReaderPlateSnapshotToReaderRecordPlateDocument,
  type ReaderRecordPlateBlock,
  type ReaderRecordPlateParagraphBlock,
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
  hashAnchorText,
  type ReaderAskAttachment,
  type ReaderAskPageIdentity,
  type ReaderStructuredInspectIntent,
} from "@/lib/reader-plate";
import type { ReaderAnchorPayload } from "@/lib/reader-plate/bridges/assets";
import type { ReaderRecordAnchorDraft } from "@/lib/reader-plate/projection/reader-record-anchor-draft";
import {
  readingRecordStatusKey,
  readingRecordStatusLabel,
  type ReadingRecordStatusKey,
} from "@/lib/reader-record-status";
import {
  READER_TEXT_RANGE_HASH_ALGORITHM,
  READER_TEXT_RANGE_OFFSET_UNIT,
  type ReaderPlateSnapshotDto,
  type ReaderSectionTranslationOutcomeDto,
  type ReaderSnapshotUserAssetDto,
} from "@/types/api/reader-plate";
import type {
  ReaderAskEntryActionDto,
} from "@/types/api/reader-ask";
import type { ThemePreference } from "@/lib/appearance";
import { useAppearance } from "@/components/providers/appearance-provider";
import { createNavigateAgenticSource } from "@/lib/reader-orchestration/agentic-source-navigation/agentic-source-navigation";
import { createCurrentPageIdentityLoader } from "@/lib/reader-orchestration/agentic-source-navigation/current-page-identity-loader";
import {
  BookOpen,
  Check,
  Copy,
  Eye,
  Globe,
  MoreVertical,
  Palette,
  Sparkles,
  Trash2,
} from "lucide-react";
import { FavoriteButton } from "@/components/reader/FavoriteButton";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  readerCommandControl,
  readerInlineFocusRing,
  readerTopBarAction,
  readerTransitionFast,
} from "@/components/reader/interaction";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  readStoredReaderSettings,
  persistReaderSettings,
  readerRecordPlateTypography,
  type ReaderFontFamily,
  type ReaderFontScale,
  type ReaderSettingsState,
} from "@/components/reader/settings";
import {
  READING_GOAL_OPTIONS,
  READING_VARIANT_OPTIONS,
} from "@/lib/reading-defaults";
import { cn } from "@/lib/cn";

import {
  ReaderFloatingToolbarButtons,
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
import { dictionaryLookupHistoryKey } from "../dictionary/shared";
import type { DictionaryAIViewState } from "@/types/api/dict-ai";
import { Plate, usePlateEditor, type RenderLeaf } from "platejs/react";
import { Editor, EditorContainer } from "@/components/ui/editor";
import { Toolbar } from "@/components/ui/toolbar";
import { ReaderRecordPlateKit } from "@/components/editor/plugins/reader-plate-kit";
import {
  resolveReaderMarkVisual,
  sentenceChunkDomId,
} from "@/components/editor/plugins/reader-leaf-kit";
import {
  READER_CALLOUT_GROUP_TYPE,
  ReaderCalloutActionContext,
  ReaderGrammarExpansionControlRef,
  ReaderGrammarExpansionProvider,
  ReaderGrammarInteractionContext,
  ReaderSentenceAnalysisInteractionContext,
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
import {
  pathExistsInPlateChildren,
  type PlateDescendantLike,
} from "@/lib/reader-plate-snapshot/progressive-transition";
import { mergeIncrementalProjection } from "@/lib/reader-plate-snapshot/incremental-projection-merger";
import type { ReloadContext } from "@/lib/reader-plate-snapshot/polling";
import type { Descendant } from "platejs";

export interface ReaderRecordPlateSurfaceProps {
  snapshot: ReaderPlateSnapshotDto;
  className?: string;
  columnClassName?: string;
  readingClassName?: string;
  onRequestSnapshotReload?: () => void | Promise<void>;
  /**
   * T4.2a-PUX-R4-R2: Reload context delivered by the page when a polling-
   * triggered reload arrives. The Surface feeds this to the incremental
   * projection merger in its value swap effect: when triggerEvents are
   * present the merger may produce a targeted_apply (replaceNodes batch)
   * instead of a full setValue, preserving non-target interactions
   * (scroll, selection, grammar accordion, Quick Peek, panels).
   *
   * Null on initial mount and after the Surface consumes the context.
   * Manual reloads (toast retry, onRequestSnapshotReload) deliver a
   * synthetic context with empty events → merger returns fallback →
   * existing setValue behavior preserved.
   */
  pendingReloadContext?: ReloadContext | null;
  /**
   * Called by the Surface after it consumes `pendingReloadContext` in the
   * value swap effect. The page clears the prop so the next render's
   * setValue path runs untouched (no stale context re-applies).
   */
  onReloadContextConsumed?: () => void;
}

type ReaderRecordLookupState =
  | { kind: "idle" }
  | { kind: "loading"; query: string; context: ReaderRecordLookupContext }
  | { kind: "ready"; query: string; context: ReaderRecordLookupContext; result: WebDictResult }
  | { kind: "error"; query: string; context: ReaderRecordLookupContext; message: string };

type ReaderRecordTranslationState =
  | { kind: "idle" }
  | { kind: "submitting" }
  | {
      kind: "submitted";
      outcome: ReaderSectionTranslationOutcomeDto;
      detail: string | null;
    }
  | { kind: "error"; message: string };

const READER_SECTION_TRANSLATION_OUTCOMES: readonly ReaderSectionTranslationOutcomeDto[] = [
  "succeeded",
  "retry_later",
  "already_covered_or_inflight",
  "budget_exhausted",
  "rejected",
  "superseded",
];

function isReaderSectionTranslationOutcome(
  value: unknown,
): value is ReaderSectionTranslationOutcomeDto {
  return (
    typeof value === "string" &&
    READER_SECTION_TRANSLATION_OUTCOMES.includes(
      value as ReaderSectionTranslationOutcomeDto,
    )
  );
}

function readerSectionTranslationStatusMessage(
  state: ReaderRecordTranslationState,
): string {
  if (state.kind === "submitting") {
    return "正在提交翻译";
  }
  if (state.kind === "error") {
    return state.message;
  }
  if (state.kind === "submitted") {
    switch (state.outcome) {
      case "succeeded":
        return "翻译已提交";
      case "already_covered_or_inflight":
        return "该段已有译文或正在翻译";
      case "budget_exhausted":
        return "翻译额度已用尽";
      case "retry_later":
        return "翻译暂时排队中，请稍后刷新";
      case "rejected":
      case "superseded":
        return "翻译请求未通过校验，请刷新后重试";
    }
  }
  return "";
}

interface ReaderRecordLookupContext {
  contextSentence: string;
  sentenceId: string;
  anchorText: string;
  lookupType: DictLookupTypeDto;
  source: "vocabulary" | "selection";
  label?: string;
  annotationType?: DictionaryLookupSnapshot["annotationType"];
  visualTone?: DictionaryLookupSnapshot["visualTone"];
  glossary?: DictionaryLookupSnapshot["glossary"];
  sourceContext?: string;
  anchorOffsets?: DictionaryLookupSnapshot["anchorOffsets"];
  occurrence?: DictionaryLookupSnapshot["occurrence"];
  textHash?: DictionaryLookupSnapshot["textHash"];
}

type ReaderRecordLookupPositionReference = {
  getRect: () => DOMRectReadOnly | DOMRect;
  contextElement?: HTMLElement;
};

type ReaderQuickPeekAnchor =
  | { kind: "element"; element: HTMLElement }
  | {
      kind: "range";
      getRect: () => DOMRectReadOnly | DOMRect;
      contextElement?: HTMLElement;
    }
  | null;

function pathIsWithinTarget(path: unknown, targetPath: readonly number[]): boolean {
  return (
    Array.isArray(path) &&
    targetPath.length <= path.length &&
    targetPath.every((segment, index) => path[index] === segment)
  );
}

function quickPeekAnchorBlockIdFromElement(
  element: HTMLElement | undefined,
): string | null {
  const anchorSegmentId = element
    ?.closest<HTMLElement>("[data-anchor-segment-id]")
    ?.dataset.anchorSegmentId;
  return anchorSegmentId ? `paragraph:${anchorSegmentId}` : null;
}

/**
 * T4.2a-PUX-R4-R3-R1: Safe CSS.escape wrapper. Uses native CSS.escape when
 * available (all modern browsers); falls back to simple character escaping
 * for environments that lack it (e.g., jsdom). The fallback handles the
 * characters that matter for attribute value selectors: double-quote and
 * backslash.
 */
function safeCssEscape(value: string): string {
  if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
    return CSS.escape(value);
  }
  return value.replace(/["\\]/g, "\\$&");
}

/**
 * T4.2a-PUX-R4-R3-R1: Resolve a Quick Peek anchor element in the post-setValue
 * DOM using stable business identity — NOT DOM path, HTMLElement ref, or Slate
 * offset.
 *
 * - When `markId` is provided (vocabulary-activated Quick Peek): resolves to
 *   the specific vocabulary mark element via
 *   `[data-anchor-segment-id] [data-reader-record-vocabulary-mark-id]`.
 *   This prevents wrong-anchoring to a different vocabulary on the same segment.
 * - When `markId` is null (selection-based lookup): resolves to the paragraph
 *   element via `[data-anchor-segment-id]`. The panel anchors to the paragraph
 *   rather than the exact text range — acceptable since the range is lost
 *   after full reload.
 * - Returns null when the anchor segment or mark doesn't exist in the new DOM,
 *   signaling fail-safe close.
 */
function resolveQuickPeekAnchorElement(
  anchorSegmentId: string | null,
  markId: string | null,
): HTMLElement | null {
  if (typeof document === "undefined" || !anchorSegmentId) return null;
  const segmentSelector = `[data-anchor-segment-id="${safeCssEscape(anchorSegmentId)}"]`;
  if (markId) {
    return document.querySelector<HTMLElement>(
      `${segmentSelector} [data-reader-record-vocabulary-mark-id="${safeCssEscape(markId)}"]`,
    );
  }
  return document.querySelector<HTMLElement>(segmentSelector);
}

/**
 * T4.2a-PUX-R4-R3-R1: Capture a frozen DOMRect from the current Quick Peek
 * anchor before editor.tf.setValue detaches the old DOM. The frozen rect keeps
 * the floating panel at its last known position during the React commit +
 * rAF restore window, preventing a (0,0) flash.
 */
function captureQuickPeekFrozenRect(
  anchor: NonNullable<ReaderQuickPeekAnchor>,
): DOMRect | null {
  if (anchor.kind === "element") {
    const rect = anchor.element.getBoundingClientRect();
    return rect.width > 0 || rect.height > 0 ? rect : null;
  }
  if (anchor.kind === "range") {
    try {
      const rect = anchor.getRect();
      return rect.width > 0 || rect.height > 0 ? rect : null;
    } catch {
      return null;
    }
  }
  return null;
}

/**
 * T4.2a-PUX-R4-R3-R1: Interaction snapshot captured before editor.tf.setValue
 * on all full-reload paths (with and without merger). Stores stable business
 * identity for post-commit re-anchor — never HTMLElement refs or DOM paths.
 *
 * T4.2a-PUX-R4-R3-R1-P1: `token` is a monotonic request token checked in the
 * rAF callback to ensure stale restores never overwrite the current Quick Peek
 * state. The token is incremented at every invalidation point (new capture,
 * dismiss, mark switch, generation switch).
 */
type QuickPeekInteractionSnapshot = {
  anchorSegmentId: string | null;
  markId: string | null;
  generation: number;
  baseId: string;
  frozenRect: DOMRect | null;
  token: number;
};

const READER_RECORD_DRAFT_COMMENT_SELECTOR =
  '[data-reader-record-comment-draft="true"]';

function boundingRectForElements(elements: HTMLElement[]): DOMRectReadOnly | null {
  const rects = elements
    .map((element) => element.getBoundingClientRect())
    .filter((rect) => rect.width > 0 && rect.height > 0);
  if (rects.length === 0) {
    return null;
  }

  const left = Math.min(...rects.map((rect) => rect.left));
  const top = Math.min(...rects.map((rect) => rect.top));
  const right = Math.max(...rects.map((rect) => rect.right));
  const bottom = Math.max(...rects.map((rect) => rect.bottom));
  return {
    x: left,
    y: top,
    left,
    top,
    right,
    bottom,
    width: right - left,
    height: bottom - top,
    toJSON: () => ({ x: left, y: top, left, top, right, bottom, width: right - left, height: bottom - top }),
  };
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
  { value: "warm_yellow", label: "黄色", swatchClassName: "bg-vocab-amber/75 ring-vocab-amber/25" },
  { value: "soft_mint", label: "绿色", swatchClassName: "bg-emerald-200/80 ring-emerald-300/50" },
  { value: "soft_rose", label: "粉色", swatchClassName: "bg-rose-200/80 ring-rose-300/50" },
];

const ARTICLE_STATUS_DESCRIPTION_BY_KEY: Record<ReadingRecordStatusKey, string> = {
  processing: "正在为你准备阅读内容，完成后即可开始阅读。",
  needs_confirmation: "请在原输入流程继续确认阅读内容。",
  ready_to_read: "正文已就绪，理解信息会在阅读时逐步补充。",
  reading_enhancing: "正在为你准备阅读内容，完成后即可开始阅读。",
  awaiting_continue: "这篇内容还需要完成下一步处理。",
  failed: "这篇内容在准备时遇到了问题。",
  completed: "译文、词汇与语法已准备完成。",
};

function lookupTypeForSelection(text: string): DictLookupTypeDto {
  return /\s/.test(text.trim()) ? "phrase" : "word";
}

function trimDraftForLookup(draft: ReaderRecordAnchorDraft) {
  const selectedText = draft.selected_text;
  const query = selectedText.trim();
  const leadingTrim = selectedText.length - selectedText.trimStart().length;
  const trailingTrim = selectedText.length - selectedText.trimEnd().length;
  const startOffset = draft.start_offset + leadingTrim;
  const endOffset = draft.end_offset - trailingTrim;

  return {
    query,
    startOffset,
    endOffset,
    textHash: leadingTrim > 0 || trailingTrim > 0 ? hashAnchorText(query) : draft.text_hash,
  };
}

function trimNativeSelectionToQuery(container: HTMLElement, query: string): Range | null {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) {
    return null;
  }

  const selectedText = selection.toString();
  if (selectedText === query || selectedText.trim() !== query) {
    return selection.getRangeAt(0);
  }

  const range = selection.getRangeAt(0);
  const rangeElement =
    range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
      ? (range.commonAncestorContainer as Element)
      : range.commonAncestorContainer.parentElement;
  if (!rangeElement || !container.contains(rangeElement)) {
    return range;
  }
  if (range.startContainer !== range.endContainer || range.startContainer.nodeType !== Node.TEXT_NODE) {
    return range;
  }

  const textNode = range.startContainer as Text;
  let startOffset = range.startOffset;
  let endOffset = range.endOffset;
  while (startOffset < endOffset && /\s/.test(textNode.data[startOffset] ?? "")) {
    startOffset += 1;
  }
  while (endOffset > startOffset && /\s/.test(textNode.data[endOffset - 1] ?? "")) {
    endOffset -= 1;
  }
  if (startOffset === range.startOffset && endOffset === range.endOffset) {
    return range;
  }

  const trimmedRange = document.createRange();
  trimmedRange.setStart(textNode, startOffset);
  trimmedRange.setEnd(textNode, endOffset);
  selection.collapse(textNode, startOffset);
  selection.extend(textNode, endOffset);
  return trimmedRange;
}

function singleRangeDraft(
  selection: ReaderRecordSelectionAnchorBridgeResult | null,
): ReaderRecordAnchorDraft | null {
  return selection?.surfaceKind === "source" && selection.supportedSingleRange
    ? (selection.drafts[0] ?? null)
    : null;
}

/**
 * Fail-closed Ask selection identity check.
 *
 * A live `activeSelection` may still carry a draft stamped with a previous
 * record/base/generation for one render after the snapshot identity changes
 * (the clear runs in an effect). That stale draft must never become an Ask
 * selection candidate — otherwise the composer identity fence clears slots
 * and immediately re-ingests the old range.
 */
export function isCurrentAskSelectionDraft(
  draft: Pick<ReaderRecordAnchorDraft, "record_id" | "base_id" | "generation">,
  current: { recordId: string; baseId: string; generation: number },
): boolean {
  return (
    draft.record_id === current.recordId &&
    draft.base_id === current.baseId &&
    draft.generation === current.generation
  );
}

// T4.2a-PUX-R4-R2.1C: extract grammar itemId from a stable blockId of
// the form `callout:grammar:{itemId}`. Returns null for non-grammar or
// malformed blockIds. Used by the targeted remove path to forget the
// expansion state for the removed callout's itemId.
const READER_CALLOUT_GRAMMAR_BLOCK_ID_PREFIX = "callout:grammar:";
function extractGrammarItemIdFromBlockId(blockId: string): string | null {
  if (!blockId.startsWith(READER_CALLOUT_GRAMMAR_BLOCK_ID_PREFIX)) {
    return null;
  }
  const itemId = blockId.slice(READER_CALLOUT_GRAMMAR_BLOCK_ID_PREFIX.length);
  return itemId.length > 0 ? itemId : null;
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

function translationDisabledReason(
  selection: ReaderRecordSelectionAnchorBridgeResult | null,
): string | undefined {
  if (!selection) {
    return "请选择稳定原文后再翻译";
  }
  if (selection.surfaceKind !== "source") {
    return "当前仅支持原文翻译";
  }
  if (hasSourceMultiTextSelection(selection)) {
    return "跨句选区暂不支持翻译";
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

/**
 * Explicit tuple comparison: two grammar callouts enter the same group
 * ONLY if both `unitId` AND `anchorSegmentId` are non-empty AND equal.
 *
 * This is the complete tuple comparison required by Method A2 — it does
 * NOT rely on structural facts like "cross-unit is usually separated by
 * a paragraph". A callout with a different `unitId` but the same
 * `anchorSegmentId` must NOT enter the same group.
 *
 * Missing-identity callouts (empty/undefined unitId or anchorSegmentId)
 * never match anything — not even another missing-identity callout — so
 * each gets its own fallback group.
 */
function sameGroupTarget(
  left: ReaderCalloutElement,
  right: ReaderCalloutElement,
): boolean {
  return (
    Boolean(left.data.unitId) &&
    Boolean(left.data.anchorSegmentId) &&
    Boolean(right.data.unitId) &&
    Boolean(right.data.anchorSegmentId) &&
    left.data.unitId === right.data.unitId &&
    left.data.anchorSegmentId === right.data.anchorSegmentId
  );
}

/**
 * T4.2a-PUX-R4-R2.2-P2a (Method A2): group consecutive grammar callouts
 * by stable identity `callout-group:{unitId}:{anchorSegmentId}`.
 *
 * Exported for direct unit testing of the grouping invariant and
 * fail-closed behavior.
 */
export function groupConsecutiveGrammarCallouts(nodes: unknown[]): unknown[] {
  const grouped: unknown[] = [];
  const seenStableGroupKeys = new Set<string>();
  let pending: ReaderCalloutElement[] = [];

  function flushPending() {
    if (pending.length === 0) {
      return;
    }
    const first = pending[0];
    const unitId = first.data.unitId;
    const anchorSegmentId = first.data.anchorSegmentId;
    const hasStableIdentity = Boolean(unitId && anchorSegmentId);

    // Group ID is stable and derived ONLY from (unitId, anchorSegmentId).
    // It does NOT depend on item identity, group length, array position,
    // or layer_id. This ensures that inserting / removing / reordering
    // grammar items within the same anchor never changes the group's
    // React identity, preserving expansion state and avoiding remounts —
    // a prerequisite for the grammar_note first-publish insert path (2c).
    //
    // Missing unitId / anchorSegmentId: use an explicit non-stable
    // fallback ID to maintain renderability without faking a stable
    // identity. The `fallback` prefix makes the non-stable nature
    // explicit for diagnostics.
    const groupId = hasStableIdentity
      ? `callout-group:${unitId}:${anchorSegmentId}`
      : `callout-group:fallback:${grouped.length}`;

    // A second non-contiguous run for a stable tuple violates the Method A2
    // projection invariant. Do not emit a duplicate group key or throw while
    // rendering the Reader: retain the grammar callouts as standalone blocks.
    // Their own callout IDs stay unique, the article remains readable, and we
    // deliberately make no expansion-state guarantee for this malformed shape.
    if (hasStableIdentity && seenStableGroupKeys.has(groupId)) {
      grouped.push(...pending);
      pending = [];
      return;
    }
    if (hasStableIdentity) {
      seenStableGroupKeys.add(groupId);
    }

    grouped.push({
      type: READER_CALLOUT_GROUP_TYPE,
      id: groupId,
      children: pending,
      // P1-A: carry unitId/anchorSegmentId in data so the incremental
      // projection merger can attribute callout-group nodes to their unit
      // and treat them as target blocks during layer_published revisions.
      data: {
        unitId,
        anchorSegmentId,
      },
    });
    pending = [];
  }

  nodes.forEach((node) => {
    if (isGrammarCalloutElement(node)) {
      // Method A2: only group callouts that share the SAME complete
      // (unitId, anchorSegmentId) tuple. A grammar callout for a
      // different anchor — even if immediately adjacent — starts a new
      // group. This eliminates the cross-segment merge that previously
      // caused group identity to depend on which segments happened to be
      // adjacent.
      if (pending.length > 0) {
        const last = pending[pending.length - 1]!;
        if (!sameGroupTarget(last, node)) {
          flushPending();
        }
      }
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
  return "text-muted-foreground";
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
  return buildDictionaryLookupSnapshotFromContext(
    snapshot,
    state.query,
    state.context,
    lookupState,
  );
}

function lookupLabelFromContext(context: ReaderRecordLookupContext) {
  if (context.label) {
    return context.label;
  }
  if (context.annotationType === "vocab_highlight") {
    return "重点词汇";
  }
  if (context.source === "vocabulary") {
    return "词典查询";
  }
  return "选区查词";
}

function buildDictionaryLookupSnapshotFromContext(
  snapshot: ReaderPlateSnapshotDto,
  query: string,
  context: ReaderRecordLookupContext,
  state: DictionaryLookupSnapshot["state"],
): DictionaryLookupSnapshot {
  return {
    query,
    lookupType: context.lookupType,
    contextSentence: context.contextSentence,
    sourceContext: context.sourceContext,
    recordId: snapshot.record_id,
    sentenceId: context.sentenceId,
    anchorText: context.anchorText,
    anchorOffsets: context.anchorOffsets,
    occurrence: context.occurrence,
    textHash: context.textHash,
    title: query,
    label: lookupLabelFromContext(context),
    annotationType: context.annotationType,
    visualTone: context.visualTone,
    glossary: context.glossary,
    state,
  };
}

function lookupContextFromSnapshot(
  lookup: DictionaryLookupSnapshot,
  source: ReaderRecordLookupContext["source"] = "selection",
): ReaderRecordLookupContext {
  return {
    contextSentence: lookup.contextSentence,
    sentenceId: lookup.sentenceId,
    anchorText: lookup.anchorText,
    lookupType: lookup.lookupType,
    source,
    label: lookup.label,
    annotationType: lookup.annotationType,
    visualTone: lookup.visualTone,
    glossary: lookup.glossary,
    sourceContext: lookup.sourceContext,
    anchorOffsets: lookup.anchorOffsets,
    occurrence: lookup.occurrence,
    textHash: lookup.textHash,
  };
}

async function postReadingRecordUserAsset(
  endpoint: string,
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
  recordId: string,
  highlightId: string,
  color: string,
): Promise<ReadingRecordUserAssetWritePayload | null> {
  const response = await fetch(
    `/api/web/reader/records/${encodeURIComponent(recordId)}/highlights/${encodeURIComponent(highlightId)}`,
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

function vocabularyVisualTone(mark: ReaderRecordPlateVocabularyMark): ReaderRecordLookupContext["visualTone"] {
  if (mark.vocabulary.itemType === "phrase_gloss") {
    return "phrase";
  }
  if (mark.vocabulary.itemType === "context_gloss") {
    return "context";
  }
  return "vocab";
}

function vocabularyLabel(mark: ReaderRecordPlateVocabularyMark): string {
  if (mark.vocabulary.itemType === "vocab_highlight") {
    return "重点词汇";
  }
  if (mark.vocabulary.itemType === "phrase_gloss") {
    return "短语";
  }
  return "语境义";
}

function vocabularyGlossary(mark: ReaderRecordPlateVocabularyMark): ReaderRecordLookupContext["glossary"] {
  if (mark.vocabulary.itemType === "vocab_highlight") {
    return mark.vocabulary.briefExplanation || mark.vocabulary.reason
      ? {
          gloss: mark.vocabulary.briefExplanation ?? undefined,
          reason: mark.vocabulary.reason ?? undefined,
        }
      : undefined;
  }
  if (mark.vocabulary.itemType === "phrase_gloss") {
    return {
      gloss: mark.vocabulary.gloss,
      phraseType: phraseTypeForInspect(mark.vocabulary.phraseType),
      learningNote: mark.vocabulary.learningNote ?? undefined,
      example: mark.vocabulary.example ?? undefined,
    };
  }
  return {
    gloss: mark.vocabulary.gloss,
    reason: mark.vocabulary.reason,
  };
}

function phraseTypeForInspect(
  phraseType: string | undefined,
): NonNullable<ReaderStructuredInspectIntent["glossary"]>["phraseType"] {
  if (
    phraseType === "verb_expression" ||
    phraseType === "fixed_collocation" ||
    phraseType === "name_or_term" ||
    phraseType === "idiom"
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

function lookupContextFromInspectIntent(
  intent: ReaderStructuredInspectIntent,
): ReaderRecordLookupContext {
  const query = intent.lookupText ?? intent.anchorText;
  return {
    contextSentence: intent.contextSentence,
    sentenceId: intent.sentenceId,
    anchorText: intent.anchorText,
    lookupType: intent.lookupKind === "phrase" ? "phrase" : lookupTypeForSelection(query),
    source: "vocabulary",
    label: intent.annotationType === "phrase_gloss" ? "短语" : intent.label ?? "语境义",
    annotationType: intent.annotationType,
    visualTone: intent.visualTone,
    glossary: intent.glossary,
    sourceContext: intent.sourceContext,
    anchorOffsets: intent.anchorOffsets,
    occurrence: intent.occurrence,
  };
}

function sentenceIdForAnchorSegment(
  blocks: ReaderRecordPlateBlock[],
  anchorSegmentId: string,
): string {
  const paragraph = blocks.find(
    (block): block is ReaderRecordPlateParagraphBlock =>
      block.type === "paragraph" &&
      block.data.coveredAnchorSegmentIds.includes(anchorSegmentId),
  );
  return paragraph?.data.sentenceId ?? "";
}

function markStackBlocksDoubleClickLookup(markStackKinds: string | undefined): boolean {
  if (!markStackKinds) {
    return false;
  }
  return markStackKinds
    .split(/\s+/)
    .some((kind) =>
      kind === "vocab_highlight" ||
      kind === "phrase_gloss" ||
      kind === "context_gloss" ||
      kind === "grammar_note" ||
      kind === "user_highlight" ||
      kind === "user_note",
    );
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
        learningNote: mark.vocabulary.learningNote ?? undefined,
        example: mark.vocabulary.example ?? undefined,
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

type PendingReaderRecordAskRequest = ReaderAskQuickActionRequest;

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

type ReaderRecordTitleState =
  | { kind: "succeeded"; title: string }
  | { kind: "pending" }
  | { kind: "failed_retryable"; sourceTitle: string | null }
  | { kind: "migration_fallback"; title: string }
  | { kind: "empty" };

function resolveReaderRecordTitleState(
  record: ReaderPlateSnapshotDto["record"],
): ReaderRecordTitleState {
  const displayTitleZh = record.display_title_zh?.trim() || "";
  const recordTitle = record.title?.trim() || "";
  const titleGenerationStatus = record.title_generation_status ?? null;

  if (displayTitleZh) {
    return { kind: "succeeded", title: displayTitleZh };
  }

  if (titleGenerationStatus === "pending") {
    return { kind: "pending" };
  }

  if (titleGenerationStatus === "failed_retryable") {
    return { kind: "failed_retryable", sourceTitle: recordTitle || null };
  }

  if (titleGenerationStatus === null && recordTitle) {
    return { kind: "migration_fallback", title: recordTitle };
  }

  return { kind: "empty" };
}

/**
 * Single title truth for page-adjacent Ask UI.
 *
 * The reader masthead prefers the generated display title and only falls
 * back to the imported source title for explicit migration/failed states.
 * Composer chips must follow the same contract instead of reading
 * `record.title` directly (which can still be the import placeholder
 * "Untitled Reading").
 */
function resolveReaderRecordAskTitle(
  record: ReaderPlateSnapshotDto["record"],
): string {
  const titleState = resolveReaderRecordTitleState(record);
  if (
    titleState.kind === "succeeded" ||
    titleState.kind === "migration_fallback"
  ) {
    return titleState.title;
  }
  if (titleState.kind === "failed_retryable" && titleState.sourceTitle) {
    return titleState.sourceTitle;
  }
  return "当前文章";
}

function ReaderRecordHeader({
  snapshot,
  surfaceMode,
  onModeChange,
}: {
  snapshot: ReaderPlateSnapshotDto;
  surfaceMode: "intensive" | "immersive";
  onModeChange: (mode: "intensive" | "immersive") => void;
}) {
  const record = snapshot.record;
  const titleState = resolveReaderRecordTitleState(record);

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

  const statusKey = readingRecordStatusKey(
    record.product_state,
    record.readiness_state,
  );
  const statusLabel = readingRecordStatusLabel(statusKey);

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
      className="reader-header-band reader-header-band--clean mb-9 border-b border-border/60 pb-7 pt-0"
    >
      {/* Zone 1: H1 editorial masthead / title state */}
      {titleState.kind === "succeeded" ? (
        <h1
          data-reader-record-reading-title
          data-reader-record-title-state="succeeded"
          className="font-headline text-[clamp(2rem,4vw,3.25rem)] font-bold leading-[1.08] tracking-normal text-ink"
        >
          {titleState.title}
        </h1>
      ) : titleState.kind === "pending" ? (
        <h1
          data-reader-record-reading-title
          data-reader-record-title-state="pending"
          className="font-headline text-[clamp(2rem,4vw,3.25rem)] font-bold leading-[1.08] tracking-normal"
        >
          <span className="block space-y-3" aria-hidden="true">
            <span className="reader-skeleton reader-skeleton--title block h-[1em] w-[min(92%,32ch)] rounded" />
            <span className="reader-skeleton reader-skeleton--title block h-[1em] w-[min(64%,20ch)] rounded" />
          </span>
        </h1>
      ) : titleState.kind === "failed_retryable" ? (
        <div>
          <h1
            data-reader-record-reading-title
            data-reader-record-title-state="failed_retryable"
            className="font-headline text-[clamp(2rem,4vw,3.25rem)] font-bold leading-[1.08] tracking-normal text-muted-foreground"
          >
            标题生成失败
          </h1>
          {titleState.sourceTitle ? (
            <p
              data-reader-record-source-title="true"
              className="mt-1.5 text-[0.8rem] font-medium text-subtle"
            >
              源标题：{titleState.sourceTitle}
            </p>
          ) : null}
        </div>
      ) : titleState.kind === "migration_fallback" ? (
        <h1
          data-reader-record-reading-title
          data-reader-record-title-state="migration_fallback"
          className="font-headline text-[clamp(2rem,4vw,3.25rem)] font-bold leading-[1.08] tracking-normal text-ink"
        >
          {titleState.title}
        </h1>
      ) : null}

      {/* Zone 2: Action bar — hairline shell, left metadata + right mode tabs */}
      <div
        className="mt-6 w-full border-t border-b border-hairline bg-transparent py-0 flex flex-col sm:flex-row items-stretch justify-between min-h-[56px]"
        data-reader-record-action-bar="true"
      >
        {/* Left metadata / status block */}
        <div className="flex items-center gap-3.5 px-3 py-3 sm:py-0">
          <span
            data-reader-record-progress-status={statusKey}
            className="px-3 py-1 text-[0.75rem] font-semibold text-ink-soft bg-surface-raised border border-hairline/80 rounded-[0.5rem] flex items-center gap-1.5 select-none"
          >
            <Sparkles className="h-3.5 w-3.5 text-vocab-amber fill-vocab-amber/10" />
            <span>{statusLabel}</span>
          </span>
          {sourceWordCount !== null ? (
            <>
              <div className="h-3.5 w-[1px] bg-hairline" />
              <span
                className="text-[0.8rem] font-semibold text-muted-foreground"
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
                className="text-[0.8rem] font-semibold text-muted-foreground"
                data-reader-record-reading-goal-variant="true"
              >
                {readingGoalVariantLabel}
              </span>
            </>
          ) : null}
        </div>

        {/* Right mode tabs */}
        <div className="flex items-stretch divide-x divide-hairline border-t border-hairline sm:border-t-0 select-none">
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
        </div>
      </div>

      {/* Zone 3: Bottom metadata — source label on the left, original link on the right only when sourceUrl exists */}
      <div className="mt-3 flex flex-col sm:flex-row sm:items-center justify-between gap-3 sm:gap-0 text-[0.78rem] text-muted-foreground tracking-wide leading-normal sm:leading-none select-none">
        <div className="flex flex-wrap items-center gap-1.5 font-medium">
          <span>
            {hasExternalSource
              ? `来源 ${sourceName ?? sourceDomain}`
              : `来源 ${sourceLabel}`}
          </span>
        </div>

        {sourceUrl ? (
          <a
            href={sourceUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="focus-ring inline-flex items-center gap-1.5 font-semibold text-muted-foreground transition-colors hover:text-ink"
          >
            <Globe className="h-4 w-4 shrink-0" strokeWidth={1.75} />
            <span>英文原文</span>
          </a>
        ) : null}
      </div>
    </header>
  );
}

function ReaderRecordTopBarTitle({
  titleState,
}: {
  titleState: ReaderRecordTitleState;
}) {
  if (titleState.kind === "succeeded") {
    return (
      <span
        className="min-w-0 truncate text-[0.95rem] font-semibold text-ink"
        data-reader-record-top-bar-title-state="succeeded"
      >
        {titleState.title}
      </span>
    );
  }

  if (titleState.kind === "pending") {
    return (
      <span
        className="block h-[1em] w-[min(60%,16ch)] rounded reader-skeleton reader-skeleton--title"
        aria-label="标题生成中"
        data-reader-record-top-bar-title-state="pending"
      />
    );
  }

  if (titleState.kind === "failed_retryable") {
    return (
      <span
        className="min-w-0 truncate text-[0.95rem] font-semibold text-muted-foreground"
        data-reader-record-top-bar-title-state="failed_retryable"
      >
        标题生成失败
      </span>
    );
  }

  if (titleState.kind === "migration_fallback") {
    return (
      <span
        className="min-w-0 truncate text-[0.95rem] font-semibold text-ink"
        data-reader-record-top-bar-title-state="migration_fallback"
      >
        {titleState.title}
      </span>
    );
  }

  return (
    <span
      className="min-w-0 truncate text-[0.95rem] font-semibold text-muted-foreground"
      data-reader-record-top-bar-title-state="empty"
    >
      阅读记录
    </span>
  );
}

function ReaderRecordTopBar({
  snapshot,
  surfaceMode,
  onModeChange,
  readerSettings,
  themePreference,
  onSettingsChange,
  onThemeChange,
}: {
  snapshot: ReaderPlateSnapshotDto;
  surfaceMode: "intensive" | "immersive";
  onModeChange: (mode: "intensive" | "immersive") => void;
  readerSettings: ReaderSettingsState;
  themePreference: ThemePreference;
  onSettingsChange: (next: ReaderSettingsState) => void;
  onThemeChange: (next: ThemePreference) => void;
}) {
  const titleState = resolveReaderRecordTitleState(snapshot.record);

  return (
    <div
      data-testid="reader-record-top-bar"
      data-reader-record-top-bar-layer="surface"
      className="reader-record-top-bar relative flex h-11 w-full items-center justify-between border-b border-hairline/80 bg-surface"
    >
      <div className="min-w-0 max-w-[min(46vw,36rem)] truncate text-left">
        <ReaderRecordTopBarTitle titleState={titleState} />
      </div>
      <div className="ml-auto flex shrink-0 items-center">
        <FavoriteButton recordId={snapshot.record_id} variant="icon-only" />
        <ReaderRecordMoreMenu
          snapshot={snapshot}
          surfaceMode={surfaceMode}
          onModeChange={onModeChange}
          readerSettings={readerSettings}
          themePreference={themePreference}
          onSettingsChange={onSettingsChange}
          onThemeChange={onThemeChange}
        />
      </div>
    </div>
  );
}

function readerRecordPageUrl(recordId: string): string {
  if (typeof window === "undefined") {
    return "";
  }
  return `${window.location.origin}/app/reader/${encodeURIComponent(recordId)}`;
}

async function copyReaderRecordLink(recordId: string): Promise<boolean> {
  const url = readerRecordPageUrl(recordId);
  if (!url) {
    return false;
  }
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(url);
      return true;
    }
    const textArea = document.createElement("textarea");
    textArea.value = url;
    textArea.setAttribute("readonly", "");
    textArea.style.position = "fixed";
    textArea.style.left = "-9999px";
    document.body.appendChild(textArea);
    textArea.select();
    const success = document.execCommand("copy");
    document.body.removeChild(textArea);
    return success;
  } catch {
    return false;
  }
}

const MORE_MENU_THEME_OPTIONS: Array<{ value: ThemePreference; label: string }> = [
  { value: "system", label: "跟随系统" },
  { value: "light", label: "浅色" },
  { value: "dark", label: "深色" },
];

const MORE_MENU_FONT_SCALE_OPTIONS: Array<{ value: ReaderFontScale; label: string }> = [
  { value: "sm", label: "小" },
  { value: "md", label: "中" },
  { value: "lg", label: "大" },
];

const MORE_MENU_FONT_FAMILY_OPTIONS: Array<{ value: ReaderFontFamily; label: string }> = [
  { value: "editorial", label: "Editorial" },
  { value: "book", label: "Book" },
  { value: "sans", label: "Sans" },
];

function ReaderRecordMoreMenu({
  snapshot,
  surfaceMode,
  onModeChange,
  readerSettings,
  themePreference,
  onSettingsChange,
  onThemeChange,
}: {
  snapshot: ReaderPlateSnapshotDto;
  surfaceMode: "intensive" | "immersive";
  onModeChange: (mode: "intensive" | "immersive") => void;
  readerSettings: ReaderSettingsState;
  themePreference: ThemePreference;
  onSettingsChange: (next: ReaderSettingsState) => void;
  onThemeChange: (next: ThemePreference) => void;
}) {
  const [copied, setCopied] = useState(false);
  const record = snapshot.record;
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
  const sourceLabel = readerRecordSourceTypeLabel(sourceType);
  const hasExternalSource = Boolean(sourceName || sourceDomain || sourceUrl);

  const sourceWordCount = computeSourceOnlyWordCount(snapshot);
  const formattedDate = formatReaderRecordDate(record.created_at);
  const articleStatusKey = readingRecordStatusKey(
    record.product_state,
    record.readiness_state,
  );
  const articleStatusLabel = readingRecordStatusLabel(articleStatusKey);
  const articleStatusDescription = ARTICLE_STATUS_DESCRIPTION_BY_KEY[articleStatusKey];

  async function handleCopyLink() {
    const success = await copyReaderRecordLink(snapshot.record_id);
    if (success) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    }
  }

  function updateSettings<K extends keyof ReaderSettingsState>(
    key: K,
    value: ReaderSettingsState[K],
  ) {
    const next = { ...readerSettings, [key]: value };
    onSettingsChange(next);
  }

  const modeLabel = surfaceMode === "immersive" ? "沉浸模式" : "精读模式";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label="更多操作"
          data-testid="reader-record-more-menu-trigger"
          className={cn(readerTopBarAction, "text-muted-foreground/90 hover:text-ink")}
        >
          <MoreVertical className="h-[18px] w-[18px]" strokeWidth={1.5} />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        sideOffset={8}
        className={cn(
          "w-[340px] overflow-hidden rounded-xl border border-hairline/80 p-0 shadow-[var(--app-panel-shadow-quiet)] data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95",
          "bg-surface-raised text-ink",
        )}
        data-testid="reader-record-more-menu-content"
        data-reader-record-more-menu-panel="true"
      >
        {/* Compact header */}
        <div className="flex items-center justify-between border-b border-hairline/60 px-3.5 py-2.5">
          <span className="text-sm font-semibold text-ink">阅读体验</span>
          <span className="text-xs font-medium text-muted-foreground">{modeLabel}</span>
        </div>

        <div className="p-2">
          {/* Article status section */}
          <div
            data-reader-record-more-article-status="true"
            className="space-y-1 px-1 pb-1 pt-0.5"
          >
            <span className="block text-xs font-semibold text-muted-foreground">文章状态</span>
            <span
              className="block text-sm font-semibold text-ink"
              data-reader-record-more-article-status-label={articleStatusKey}
            >
              {articleStatusLabel}
            </span>
            <span className="block text-[0.75rem] leading-relaxed text-muted-foreground">
              {articleStatusDescription}
            </span>
          </div>

          <div className="my-2 h-px bg-hairline/60" />

          {/* Mode section */}
          <div className="space-y-0.5">
            <button
              type="button"
              onClick={() => onModeChange("intensive")}
              data-reader-record-more-mode="intensive"
              className={cn(
                "flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-left text-sm font-medium transition-colors",
                "hover:bg-ink/[0.04] active:bg-ink/[0.08] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-lens-blue/30",
                surfaceMode === "intensive"
                  ? "bg-lens-blue/[0.08] text-ink"
                  : "text-ink/90",
              )}
            >
              <span className="flex items-center gap-2.5">
                <BookOpen className="h-4 w-4 text-muted-foreground" strokeWidth={1.5} />
                <span className="flex flex-col">
                  <span>精读</span>
                  <span className="text-[0.7rem] font-normal text-muted-foreground">逐句解析</span>
                </span>
              </span>
              {surfaceMode === "intensive" ? (
                <Check className="h-4 w-4 text-lens-blue" strokeWidth={2} />
              ) : null}
            </button>
            <button
              type="button"
              onClick={() => onModeChange("immersive")}
              data-reader-record-more-mode="immersive"
              className={cn(
                "flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-left text-sm font-medium transition-colors",
                "hover:bg-ink/[0.04] active:bg-ink/[0.08] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-lens-blue/30",
                surfaceMode === "immersive"
                  ? "bg-lens-blue/[0.08] text-ink"
                  : "text-ink/90",
              )}
            >
              <span className="flex items-center gap-2.5">
                <Eye className="h-4 w-4 text-muted-foreground" strokeWidth={1.5} />
                <span className="flex flex-col">
                  <span>沉浸</span>
                  <span className="text-[0.7rem] font-normal text-muted-foreground">专注阅读</span>
                </span>
              </span>
              {surfaceMode === "immersive" ? (
                <Check className="h-4 w-4 text-lens-blue" strokeWidth={2} />
              ) : null}
            </button>
          </div>

          <div className="my-2 h-px bg-hairline/60" />

          {/* Font preview section */}
          <div className="space-y-2">
            <span className="block px-1 text-xs font-semibold text-muted-foreground">字体</span>
            <div className="grid grid-cols-3 gap-2">
              {MORE_MENU_FONT_FAMILY_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => updateSettings("fontFamily", option.value)}
                  data-reader-record-more-font-family={option.value}
                  className={cn(
                    "flex flex-col items-center gap-1.5 rounded-lg border px-2 py-2.5 text-center transition-colors",
                    "border-hairline/60 hover:border-hairline hover:bg-ink/[0.03] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-lens-blue/30",
                    readerSettings.fontFamily === option.value
                      ? "border-surface-raised bg-surface-raised/60"
                      : "bg-transparent",
                  )}
                >
                  <span
                    className={cn(
                      "text-[1.35rem] leading-none text-ink",
                      option.value === "sans"
                        ? "reader-font-sans"
                        : option.value === "book"
                          ? "reader-font-book"
                          : "reader-font-editorial",
                    )}
                  >
                    Ag
                  </span>
                  <span className="text-[0.7rem] font-medium text-muted-foreground">{option.label}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="my-2 h-px bg-hairline/60" />

          {/* Theme section */}
          <div className="space-y-2">
            <span className="block px-1 text-xs font-semibold text-muted-foreground">主题</span>
            <div className="grid grid-cols-3 gap-2">
              {MORE_MENU_THEME_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => onThemeChange(option.value)}
                  data-reader-record-more-theme={option.value}
                  className={cn(
                    "rounded-lg border px-2 py-1.5 text-xs font-semibold transition-colors",
                    "border-hairline/60 hover:border-hairline hover:bg-ink/[0.03] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-lens-blue/30",
                    themePreference === option.value
                      ? "border-surface-raised bg-surface-raised/60 text-ink"
                      : "bg-transparent text-muted-foreground",
                  )}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <div className="my-2 h-px bg-hairline/60" />

          {/* Font scale section */}
          <div className="space-y-2">
            <span className="block px-1 text-xs font-semibold text-muted-foreground">字号</span>
            <div className="flex rounded-lg border border-hairline/60 p-0.5">
              {MORE_MENU_FONT_SCALE_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => updateSettings("fontScale", option.value)}
                  data-reader-record-more-font-scale={option.value}
                  className={cn(
                    "flex-1 rounded-md py-1.5 text-xs font-semibold transition-colors",
                    "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-lens-blue/30",
                    readerSettings.fontScale === option.value
                      ? "bg-surface-raised/70 text-ink shadow-sm"
                      : "text-muted-foreground hover:bg-ink/[0.03]",
                  )}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <div className="my-2 h-px bg-hairline/60" />

          {/* Article actions */}
          <div className="space-y-0.5">
            <button
              type="button"
              onClick={handleCopyLink}
              disabled={copied}
              data-reader-record-more-action="copy-link"
              className={cn(
                "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm font-medium transition-colors",
                "hover:bg-ink/[0.04] active:bg-ink/[0.08] disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-lens-blue/30",
                copied ? "text-structure-green" : "text-ink/90",
              )}
            >
              {copied ? (
                <Check className="h-4 w-4" strokeWidth={1.5} />
              ) : (
                <Copy className="h-4 w-4 text-muted-foreground" strokeWidth={1.5} />
              )}
              <span>{copied ? "已复制链接" : "复制链接"}</span>
            </button>
            {sourceUrl ? (
              <a
                href={sourceUrl}
                target="_blank"
                rel="noopener noreferrer"
                data-reader-record-more-action="open-source-url"
                className={cn(
                  "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors",
                  "hover:bg-ink/[0.04] active:bg-ink/[0.08] text-ink/90 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-lens-blue/30",
                )}
              >
                <Globe className="h-4 w-4 text-muted-foreground" strokeWidth={1.5} />
                <span>英文原文</span>
              </a>
            ) : null}
            <div
              data-reader-record-more-action="source-info"
              className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium text-muted-foreground/80"
            >
              <Globe className="h-4 w-4 text-muted-foreground/60" strokeWidth={1.5} />
              <span>
                {hasExternalSource
                  ? `来源 ${sourceName ?? sourceDomain ?? sourceLabel}`
                  : `来源 ${sourceLabel}`}
              </span>
            </div>
          </div>

          <div className="my-2 h-px bg-hairline/60" />

          {/* Footer metadata */}
          <div
            className="flex flex-wrap items-center gap-x-2 gap-y-1 px-1 py-1 text-[0.7rem] text-muted-foreground"
            data-reader-record-more-metadata="true"
          >
            {sourceWordCount !== null ? <span>{sourceWordCount} 词</span> : null}
            {formattedDate !== "今日" ? <span>{formattedDate}</span> : null}
            <span>{sourceLabel}</span>
          </div>
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function SelectionActionState({
  copyStatus,
  selection,
  translationState,
  writeState,
}: {
  copyStatus: ReaderRecordCopyStatus;
  selection: ReaderRecordSelectionAnchorBridgeResult | null;
  translationState: ReaderRecordTranslationState;
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
      {translationState.kind !== "idle" ? (
        <span data-testid="reader-record-plate-translation-status-hidden">
          {readerSectionTranslationStatusMessage(translationState)}
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

// T2.1: helpers for preserving scroll + selection across `editor.tf.setValue`.
// A snapshot reload replaces the entire editor children; without these, the
// reader jumps to the top and the caret/selection disappears on every
// layer_published event. Helpers are module-scoped so they keep a stable
// identity across renders.

/**
 * Find the real scroll container for the Reader Record body. The app shell
 * wraps content in a Radix ScrollArea, so `window` is not always the element
 * that scrolls. We walk up from `.reader-record-plate-document` until we find
 * an element with overflow auto/scroll, falling back to `window`. Mirrors the
 * logic in `ReaderRecordNavigationRail.getScrollContainer` so both the
 * navigation rail and the scroll-preservation path agree on the container.
 */
function findReaderRecordScrollContainer(): Window | HTMLElement | null {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return null;
  }
  const body = document.querySelector<HTMLElement>(
    ".reader-record-plate-document",
  );
  if (!body) return window;
  let el: HTMLElement | null = body.parentElement;
  while (el && el !== document.body && el !== document.documentElement) {
    const style = window.getComputedStyle(el);
    if (/(auto|scroll)/.test(style.overflowY + style.overflow)) {
      return el;
    }
    el = el.parentElement;
  }
  return window;
}

// T4.2a-PUX-R4-R3-R2: Capture semantic scroll anchor for compensation.
// Finds the topmost visible block (first [data-reader-record-block-id]
// whose bottom is below the viewport top) and records its viewport offset.
function captureScrollAnchor(
  scrollContainer: Window | HTMLElement | null,
): { blockId: string; viewportOffset: number } | null {
  if (scrollContainer === null) return null;
  const blocks = document.querySelectorAll("[data-reader-record-block-id]");
  for (const block of blocks) {
    const rect = (block as HTMLElement).getBoundingClientRect();
    if (rect.bottom > 0) {
      const viewportOffset = rect.top;
      const blockId = block.getAttribute("data-reader-record-block-id");
      if (blockId) {
        return { blockId, viewportOffset };
      }
    }
  }
  return null;
}

// pathExistsInPlateChildren: shared pure helper from progressive-transition
// (T4.2a-PUX-R1). Slate path `[0,1,2]` = children[0].children[1].children[2].

export function ReaderRecordPlateSurface({
  snapshot,
  className = "px-5 py-8 sm:px-8 lg:px-10",
  columnClassName,
  readingClassName = "",
  onRequestSnapshotReload,
  pendingReloadContext,
  onReloadContextConsumed,
}: ReaderRecordPlateSurfaceProps) {
  const appShell = useAppShellLayout();
  const {
    isWorkspaceShell,
    releaseSidebarForReadingTool,
    sidebarMode,
  } = appShell;
  const surfaceRef = useRef<HTMLElement | null>(null);
  const commentApiRef = useRef<CommentPluginApi | null>(null);
  const [commentApiReady, setCommentApiReady] = useState(false);
  const [activeSelection, setActiveSelection] =
    useState<ReaderRecordSelectionAnchorBridgeResult | null>(null);
  const activeSelectionRef =
    useRef<ReaderRecordSelectionAnchorBridgeResult | null>(null);
  const [copyStatus, setCopyStatus] = useState<ReaderRecordCopyStatus>("idle");
  const [translationState, setTranslationState] =
    useState<ReaderRecordTranslationState>({ kind: "idle" });
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
  const [quickPeekAnchorBlockId, setQuickPeekAnchorBlockId] =
    useState<string | null>(null);
  // T4.2a-PUX-R4-R3-R1: Stable vocabulary mark ID for the currently-open
  // Quick Peek. Set when the user activates a vocabulary mark; auto-cleared
  // when Quick Peek closes (via the ref-sync effect below). Used as stable
  // business identity for post-setValue re-anchor — never the HTMLElement ref.
  const quickPeekAnchorMarkIdRef = useRef<string | null>(null);
  const quickPeekInteractionRef = useRef({
    blockId: null as string | null,
    isOpen: false,
  });
  // T4.2a-PUX-R4-R3-R1-P1: Monotonic request token for Quick Peek restore.
  // Incremented at every invalidation point (new capture, dismiss, mark
  // switch, generation switch). The rAF callback checks this token before
  // touching any ref — stale restores abort without side effects.
  const quickPeekRestoreTokenRef = useRef(0);
  useEffect(() => {
    const isOpen = lookupState.kind !== "idle" || inspectState !== null;
    quickPeekInteractionRef.current = {
      blockId: quickPeekAnchorBlockId,
      isOpen,
    };
    // T4.2a-PUX-R4-R3-R1: Auto-clear markId ref when Quick Peek closes so
    // stale identity doesn't leak into the next open session.
    // T4.2a-PUX-R4-R3-R1-P1: Invalidate pending restore + clear stale
    // frozenRect ref so it cannot persist as a long-term virtual reference.
    if (!isOpen) {
      quickPeekAnchorMarkIdRef.current = null;
      quickPeekRestoreTokenRef.current += 1;
      quickPeekAnchorRef.current = null;
    }
  }, [inspectState, lookupState.kind, quickPeekAnchorBlockId]);
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
  const { themePreference, setThemePreference } = useAppearance();

  useEffect(() => {
    activeSelectionRef.current = activeSelection;
  }, [activeSelection]);
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setActiveGrammarItemId(null);
        // Dismiss the selection-actions toolbar on Escape. Clearing the
        // native selection cascades through SelectionAnchorBridge →
        // activeSelection → toolbar visibility. We only do this when there
        // is an active text selection; other floating UI (noteMenu /
        // feedbackTarget / AIMenu) handle their own Escape via React
        // stopPropagation, so this document-level handler won't fire for
        // them.
        if (activeSelectionRef.current?.selectedText.trim()) {
          window.getSelection()?.removeAllRanges();
        }
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

  // 空白点击关闭选区工具栏：Chromium 中点击非聚焦的空白区域不会折叠
  // contenteditable 内的原生选区，因此 SelectionAnchorBridge 不会收到
  // selectionchange。这里在 pointerdown 阶段判断：若点击落在工具栏与
  // 正文文档之外，则主动清空原生选区，经 bridge → activeSelection 级联
  // 关闭工具栏（验收“空白点击…关闭”）。点击工具栏按钮（pointerdown
  // preventDefault）与正文内新建选区不受影响。
  useEffect(() => {
    function handleBlankPointerDown(event: PointerEvent) {
      const target = event.target as Node | null;
      if (!target || target.nodeType !== Node.ELEMENT_NODE) {
        return;
      }
      const withinToolbar = (
        target as Element
      ).closest?.('[data-reader-record-floating-toolbar="selection-actions"]');
      const withinDocument = (
        target as Element
      ).closest?.(".reader-record-plate-document");
      if (withinToolbar || withinDocument) {
        return;
      }
      const sel = window.getSelection();
      if (sel && !sel.isCollapsed) {
        sel.removeAllRanges();
      }
    }
    window.document.addEventListener(
      "pointerdown",
      handleBlankPointerDown,
      true,
    );
    return () => {
      window.document.removeEventListener(
        "pointerdown",
        handleBlankPointerDown,
        true,
      );
    };
  }, []);
  const surfaceMode = readerSettings.mode;
  const [localUserAssets, setLocalUserAssets] = useState<
    ReaderPlateSnapshotDto["user_assets"]
  >(snapshot.user_assets);

  // T4.2a-PUX-R4-R2: Sync localUserAssets to the new snapshot.user_assets
  // on snapshot reload. This runs as a layout effect so the re-render (and
  // resulting plateValue recomputation) happens as early as possible.
  //
  // The value swap useEffect below ALSO guards against stale localUserAssets
  // by checking `localUserAssets !== snapshot.user_assets` and skipping when
  // stale. This dual-guard is necessary because setLocalUserAssets inside a
  // layout effect does not always cause a fully synchronous re-render before
  // passive effects in all environments (e.g., jsdom + async act()). The
  // stale guard ensures the merge always uses a plateValue computed with the
  // correct user_assets, and the re-render from this layout effect ensures
  // the effect re-runs with the synced value.
  useLayoutEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- snapshot reload resets optimistic user-asset projections.
    setLocalUserAssets(snapshot.user_assets);
  }, [snapshot.user_assets]);

  const projectedSnapshot = useMemo<ReaderPlateSnapshotDto>(
    () => ({ ...snapshot, user_assets: localUserAssets }),
    [snapshot, localUserAssets],
  );
  // R3C-C: Default Plate is the formal source-navigation path. Rebuild when
  // snapshot identity changes. Construction only — no fetch during render;
  // DOM adapter resolves at click time against `.reader-record-plate-document`.
  const navigateAgenticSource = useMemo(() => {
    const loadCurrentPageIdentity = createCurrentPageIdentityLoader({
      readingRecordId: snapshot.record_id,
      baseId: snapshot.base.base_id,
      recordGeneration: snapshot.record.generation,
    });
    return createNavigateAgenticSource({ loadCurrentPageIdentity });
  }, [
    snapshot.record_id,
    snapshot.base.base_id,
    snapshot.record.generation,
  ]);

  const askRecordTitle = useMemo(
    () => resolveReaderRecordAskTitle(snapshot.record),
    [
      snapshot.record.display_title_zh,
      snapshot.record.title,
      snapshot.record.title_generation_status,
    ],
  );

  const askPageIdentity = useMemo<ReaderAskPageIdentity>(
    () => ({
      recordId: snapshot.record_id,
      recordTitle: askRecordTitle,
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
      askRecordTitle,
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
  const contentColumnClassName =
    columnClassName ?? `mx-auto w-full max-w-[var(--reader-record-main-width)]`;
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

  // T4.2a-PUX-R4-R2: Incremental projection merge state.
  //
  // `prevSnapshotRef` tracks the snapshot from the last successful value
  // swap. Initialized to the initial snapshot so the first reload can
  // attempt a targeted apply. Updated inside the value swap effect after
  // each successful apply (targeted or fallback).
  //
  // `pendingReloadContextRef` mirrors the `pendingReloadContext` prop via a
  // sync effect so the value swap effect (which has [plateValue, editor,
  // snapshot] deps) always reads the latest context without re-running on
  // every context change.
  //
  // `onReloadContextConsumedRef` mirrors the callback to keep it out of
  // the value swap effect deps (avoiding re-runs from inline callbacks).
  const prevSnapshotRef = useRef<ReaderPlateSnapshotDto | null>(snapshot);
  // Prevent the Plate re-render caused by targeted transforms from immediately
  // falling through to a second full setValue for the same accepted snapshot.
  const lastTargetedApplySnapshotIdRef = useRef<string | null>(null);
  const pendingReloadContextRef = useRef<ReloadContext | null>(null);
  const onReloadContextConsumedRef = useRef<(() => void) | null>(null);
  // T4.2a-PUX-R4-R3-R2: Pending restore data persisted across effect runs.
  // editor.tf.setValue schedules a DEFERRED React commit (via MessageChannel).
  // The deferred commit triggers a new effect run that would normally capture
  // a stale anchor from the already-shifted DOM. By persisting the original
  // capture in a ref, the rAF polling loop (which is NOT canceled by the
  // effect cleanup) can use the original anchor to detect the DOM change and
  // run the restore with the correct pre-shift viewport offset.
  //
  // T4.2a-PUX-R4-R3-R2-P1: Restore state machine fence repair.
  // - `restoreTokenRef` is a monotonic owner token. Each new restore captures
  //   the current token; rAF/timeout callbacks abort if the token mismatches.
  //   New restore, different accepted snapshot, source switch, and unmount
  //   all increment the token, invalidating old callbacks.
  // - `sourceIdentity` in the pending record is checked in runRestore to
  //   forbid cross-source scroll-anchor / savedScrollTop restoration.
  // - The early-return guard only skips for the SAME accepted snapshot
  //   (deferred React re-run). A DIFFERENT accepted snapshot invalidates the
  //   old restore and proceeds normally.
  const restoreTokenRef = useRef(0);
  const pendingRestoreRef = useRef<{
    scrollContainer: Window | HTMLElement | null;
    savedScrollTop: number | null;
    capturedScrollAnchor: { blockId: string; viewportOffset: number } | null;
    quickPeekSnapshot: QuickPeekInteractionSnapshot | null;
    capturedExpandedItemIds: ReadonlySet<string> | null;
    snapshot: ReaderPlateSnapshotDto;
    sourceIdentity: { generation: number; baseId: string };
    restoreToken: number;
  } | null>(null);
  useEffect(() => {
    pendingReloadContextRef.current = pendingReloadContext ?? null;
  }, [pendingReloadContext]);
  useEffect(() => {
    onReloadContextConsumedRef.current = onReloadContextConsumed ?? null;
  }, [onReloadContextConsumed]);

  // plateValue 变化时同步 editor 内容，避免重新创建 editor 实例。
  // T2.1: `editor.tf.setValue` replaces the entire editor children, which
  // wipes scroll position, DOM selection, and any in-progress draft marks.
  // Snapshot reloads fire this effect on every layer_published event, so
  // without preservation the reader visibly jumps to the top and the user's
  // caret/selection disappears. We save the scroll container's scrollTop
  // and the editor's selection before the swap, then restore them after.
  // Selection is only restored when its anchor path still exists in the new
  // tree; otherwise we leave it cleared (the user's scroll is the critical
  // UX, and a stale selection path would crash Plate).
  //
  // T4.2a-PUX-R4-R2: Before falling back to setValue, attempt an incremental
  // projection merge. If `pendingReloadContext` carries O4-legitimate
  // representation events (G1/G2/G3) and the prev/next snapshots satisfy
  // the fence, the merger returns a `targeted_apply` with per-block
  // operations. We apply them via `editor.tf.replaceNodes` /
  // `editor.tf.removeNodes` (batch) — NEVER `editor.tf.setValue` on the
  // targeted path. This preserves non-target DOM identity (scroll,
  // grammar accordion, Quick Peek, panels, hover, note draft). On any
  // fallback reason, we fall through to the existing setValue path.
  useEffect(() => {
    // T4.2a-PUX-R4-R3-R2: If there's a pending restore from a previous
    // effect run, this run was triggered by the deferred React commit from
    // that run's editor.tf.setValue. The pending rAF polling loop (not
    // canceled by cleanup) will detect the DOM change and run the restore
    // using the original pre-shift anchor. Skip this run entirely to avoid
    // capturing a stale anchor from the already-shifted DOM.
    //
    // T4.2a-PUX-R4-R3-R2-P1: The skip only applies when this is the SAME
    // accepted snapshot (same snapshot_id) — the deferred React re-run from
    // the same setValue. A DIFFERENT accepted snapshot must invalidate the
    // old restore (token increment + clear pending) and proceed normally
    // through setValue / targeted merge. This prevents the old restore from
    // swallowing a new accepted snapshot.
    if (pendingRestoreRef.current !== null) {
      const isSameAcceptedSnapshot =
        pendingRestoreRef.current.snapshot.snapshot_id === snapshot.snapshot_id;
      if (isSameAcceptedSnapshot) {
        prevSnapshotRef.current = snapshot;
        const ctx = pendingReloadContextRef.current;
        if (ctx !== null) {
          pendingReloadContextRef.current = null;
          onReloadContextConsumedRef.current?.();
        }
        return;
      }
      // Different accepted snapshot: invalidate old restore and fall through.
      pendingRestoreRef.current = null;
      restoreTokenRef.current += 1;
    }

    if (editor.children === plateValue) {
      return;
    }

    // Capture pre-swap state. `editor.selection` is the Plate selection
    // (a Range-like object or null). The scroll container is found by
    // walking up from the plate document element.
    let savedSelection = editor.selection ?? null;
    let suppressSelectionRestore = false;
    const scrollContainer = findReaderRecordScrollContainer();
    const savedScrollTop =
      scrollContainer === null
        ? null
        : scrollContainer === window
          ? window.scrollY
          : (scrollContainer as HTMLElement).scrollTop;
    // T4.2a-PUX-R4-R3-R2: Capture semantic scroll anchor (topmost visible
    // block + viewport offset) for anchor-compensated restore after flushSync.
    const capturedScrollAnchor = captureScrollAnchor(scrollContainer);

    // T4.2a-PUX-R4-R2: Try incremental projection merge first.
    // Only attempt when we have a reload context with trigger events AND
    // a valid prev snapshot that differs from the current one.
    const reloadContext = pendingReloadContextRef.current;
    const prevSnapshot = prevSnapshotRef.current;

    // A targeted transform can cause this effect to run again before the
    // parent clears the reload context. Do not replace the just-applied tree.
    // This guard is sufficient: it covers both "reloadContext still pending"
    // and "reloadContext already consumed" re-run cases after targeted_apply.
    // We MUST NOT also early-return when (reloadContext === null &&
    // prevSnapshot === snapshot) — that would skip the setValue path needed
    // for legitimate plateValue changes such as immersive/intensive mode
    // toggle or localUserAssets re-projection (snapshot unchanged but the
    // projected DOM must still be rebuilt).
    if (lastTargetedApplySnapshotIdRef.current === snapshot.snapshot_id) {
      return;
    }

    let appliedViaTargeted = false;
    // T4.2a-PUX-R4-R2.2-P2c: 标记 merger 是否真的被调用（reloadContext 非 null
    // 且 prevSnapshot !== snapshot）。effect 在 targeted_apply 成功后可能因
    // plateValue 变化而再次运行；此时 reloadContext 可能尚未被 consume，
    // 但 prevSnapshot === snapshot（已更新），merger 不会被调用。用此 flag
    // 区分"真正的 fallback reload"和"effect 重新运行但无 merge 需要"。
    let mergerAttempted = false;
    // T4.2a-PUX-R4-R3-R1: Quick Peek interaction snapshot captured before
    // editor.tf.setValue on all full-reload paths. Used in the rAF callback
    // to re-anchor the floating panel via stable business identity.
    let capturedQuickPeekSnapshot: QuickPeekInteractionSnapshot | null = null;
    // T4.2a-PUX-R4-R3-R2: Captured expanded grammar itemIds before
    // editor.tf.setValue. Used in the rAF callback to only forget items
    // that no longer exist in the new DOM (selective forget on
    // same-source-identity full reload). The semantic scroll anchor is
    // captured earlier in the effect (near savedScrollTop) since it must
    // be captured before any DOM mutation.
    let capturedExpandedItemIds: ReadonlySet<string> | null = null;

    // T4.2a-PUX-R4-R2: When a reload context is present but localUserAssets
    // hasn't been synced to snapshot.user_assets yet, skip this run. The
    // useLayoutEffect that syncs localUserAssets may not cause a synchronous
    // re-render before this useEffect in all environments (e.g., jsdom + act()).
    // Skipping ensures the merge uses a plateValue computed with the correct
    // user_assets. The re-render will produce the correct plateValue and this
    // effect will run again.
    if (
      reloadContext !== null &&
      localUserAssets !== snapshot.user_assets
    ) {
      return;
    }

    if (
      reloadContext !== null &&
      prevSnapshot !== null &&
      prevSnapshot !== snapshot
    ) {
      mergerAttempted = true;
      const mergeResult = mergeIncrementalProjection({
        prevSnapshot,
        nextSnapshot: snapshot,
        triggerEvents: reloadContext.events,
        prevChildren: editor.children as unknown as Descendant[],
        nextChildren: plateValue as unknown as Descendant[],
        snapshotFence: reloadContext.acceptedSnapshotFence,
      });

      if (mergeResult.kind === "targeted_apply") {
        // A targeted replacement invalidates a selection only when its anchor
        // or focus lives below the replaced block. Never restore that range:
        // offsets may remain structurally valid while pointing at new text.
        const selectionTargetsReplacement = mergeResult.operations.some((op) =>
          pathIsWithinTarget(savedSelection?.anchor?.path, op.path) ||
          pathIsWithinTarget(savedSelection?.focus?.path, op.path),
        );
        const quickPeekInteraction = quickPeekInteractionRef.current;
        const quickPeekTargetsReplacement =
          quickPeekInteraction.isOpen &&
          mergeResult.operations.some((op) =>
            quickPeekInteraction.blockId !== null
              ? op.blockId === quickPeekInteraction.blockId
              : op.blockId.startsWith("paragraph:"),
          );

        if (selectionTargetsReplacement || quickPeekTargetsReplacement) {
          suppressSelectionRestore = true;
          savedSelection = null;
          editor.tf.deselect();
          activeSelectionRef.current = null;
          setActiveSelection(null);
        }

        // A Quick Peek whose anchor block is being replaced would otherwise
        // keep a detached DOM/range reference. Close it deterministically;
        // sibling updates retain their stable anchor and remain uninterrupted.
        if (quickPeekTargetsReplacement) {
          setLookupState({ kind: "idle" });
          setInspectState(null);
          setQuickPeekAnchorBlockId(null);
        }

        // Apply each operation via batch replaceNodes / removeNodes.
        // NEVER call editor.tf.setValue on this path — that would wipe
        // non-target DOM identity and defeat the purpose of R2.
        for (const op of mergeResult.operations) {
          if (op.type === "replace" && op.nodes && op.nodes.length > 0) {
            // T4.2a-PUX-R4-R3-R2-P1 (Contract D): when a grammar callout is
            // replaced via targeted op, forget its itemId expansion state —
            // the replaced block may carry different content or a different
            // itemId, and we must NOT let stale expanded state bleed into
            // the new block. Sibling expansions in the same batch are
            // untouched (only the affected itemId is forgotten).
            const replacedGrammarItemId =
              extractGrammarItemIdFromBlockId(op.blockId);
            if (replacedGrammarItemId) {
              grammarExpansionControlRef.current?.forgetItem(
                replacedGrammarItemId,
              );
            }
            editor.tf.replaceNodes(op.nodes as never[], { at: op.path });
          } else if (op.type === "remove") {
            // T4.2a-PUX-R4-R2.1C: when a grammar callout is removed via
            // targeted op, forget its itemId expansion state so the same
            // itemId reappearing in the same generation defaults to
            // collapsed instead of inheriting stale expanded state.
            const grammarItemId =
              extractGrammarItemIdFromBlockId(op.blockId);
            if (grammarItemId) {
              grammarExpansionControlRef.current?.forgetItem(grammarItemId);
            }
            editor.tf.removeNodes({ at: op.path });
          } else if (op.type === "insert" && op.nodes && op.nodes.length > 0) {
            // T4.2a-PUX-R4-R2.2-P2c: grammar callout-group 首次发布定向插入。
            // merger 已按 path 降序排列 operations,按数组顺序应用即可
            // 避免前序插入导致后续 path 偏移。
            editor.tf.insertNodes(op.nodes as never[], { at: op.path });
          }
        }
        lastTargetedApplySnapshotIdRef.current = snapshot.snapshot_id;
        appliedViaTargeted = true;
      }
      // fallback_full_reload: fall through to setValue path below.
    }

    if (!appliedViaTargeted) {
      // T4.2a-PUX-R4-R3-R1: Capture Quick Peek interaction snapshot on ALL
      // setValue paths (with and without merger). The previous code only
      // closed Quick Peek when mergerAttempted was true, leaving the
      // without-merger path (surface-mode toggle, localUserAssets
      // re-projection) with a detached HTMLElement → (0,0) panel.
      //
      // New behavior: capture stable identity → freeze anchor rect →
      // setValue → rAF re-anchor (if anchor exists) or fail-safe close.
      // This never displays a detached (0,0) panel and never wrong-anchors
      // to a different vocabulary on the same segment.
      const quickPeekInteraction = quickPeekInteractionRef.current;
      if (quickPeekInteraction.isOpen) {
        const anchorSegmentId = quickPeekInteraction.blockId?.startsWith(
          "paragraph:",
        )
          ? quickPeekInteraction.blockId.slice("paragraph:".length)
          : null;
        const anchor = quickPeekAnchorRef.current;
        const frozenRect = anchor
          ? captureQuickPeekFrozenRect(anchor)
          : null;
        if (frozenRect) {
          // T4.2a-PUX-R4-R3-R1-P1: Increment token for this restore request.
          // Any previous pending rAF with a lower token will abort on sight.
          const restoreToken = (quickPeekRestoreTokenRef.current += 1);
          capturedQuickPeekSnapshot = {
            anchorSegmentId,
            markId: quickPeekAnchorMarkIdRef.current,
            generation: snapshot.record.generation,
            baseId: snapshot.base.base_id,
            frozenRect,
            token: restoreToken,
          };
          // Freeze the anchor to prevent (0,0) during the restore window.
          // autoUpdate will poll this frozen rect, keeping the panel at its
          // last known position until re-anchor completes in rAF.
          quickPeekAnchorRef.current = {
            kind: "range",
            getRect: () => frozenRect,
          };
          quickPeekFloating.refs.setPositionReference?.({
            getBoundingClientRect: () => frozenRect,
          });
        } else {
          // No rect available — close immediately as fail-safe.
          setLookupState({ kind: "idle" });
          setInspectState(null);
          setQuickPeekAnchorBlockId(null);
          quickPeekAnchorRef.current = null;
        }
      }
      // T4.2a-PUX-R4-R3-R2: Capture expanded itemIds BEFORE setValue so we
      // can selectively forget only items that no longer exist in the new DOM.
      // Same-source-identity full reload preserves expansion for surviving
      // items. Source-identity switch (generation/base_id change) is handled
      // by the generation-scoped effect which calls clear() — that path is
      // unaffected.
      capturedExpandedItemIds =
        grammarExpansionControlRef.current?.getExpandedItemIds() ?? null;
      editor.tf.setValue(plateValue as never[]);
    }

    // Restore selection only if the anchor/focus path still resolves in the
    // new children. We avoid `editor.tf.setSelection` when the path is gone
    // because Plate will throw or clamp unpredictably.
    if (!suppressSelectionRestore && savedSelection) {
      const anchorPath = savedSelection.anchor?.path;
      const focusPath = savedSelection.focus?.path;
      if (Array.isArray(anchorPath) && Array.isArray(focusPath)) {
        try {
          const anchorOk = pathExistsInPlateChildren(
            editor.children as unknown as PlateDescendantLike[],
            anchorPath,
          );
          const focusOk = pathExistsInPlateChildren(
            editor.children as unknown as PlateDescendantLike[],
            focusPath,
          );
          if (anchorOk && focusOk) {
            editor.tf.setSelection(savedSelection);
          }
        } catch {
          // Selection restore is best-effort; never block the reload.
        }
      }
    }

    // T4.2a-PUX-R4-R3-R1: Restore scroll and re-anchor Quick Peek.
    // T4.2a-PUX-R4-R3-R2: editor.tf.setValue schedules a DEFERRED React
    // commit (via MessageChannel). The deferred commit triggers a new
    // effect run. To avoid the race where the new run captures a stale
    // anchor from the already-shifted DOM, we:
    //   1. Store the pre-shift capture in pendingRestoreRef (persists across
    //      effect runs; the new run checks this ref and returns early).
    //   2. Start a rAF polling loop that is NOT canceled by the effect
    //      cleanup. The loop polls the captured block's position each frame;
    //      when it moves (DOM committed), the restore runs with the original
    //      pre-shift viewport offset.
    //   3. A 2s timeout fallback covers no-op reloads where the DOM doesn't
    //      change.
    const needsQuickPeekReanchor = capturedQuickPeekSnapshot !== null;
    const needsScrollRestore = savedScrollTop !== null && savedScrollTop > 0;
    const needsGrammarSelectiveForget =
      capturedExpandedItemIds !== null &&
      capturedExpandedItemIds.size > 0;

    if (
      needsQuickPeekReanchor ||
      needsScrollRestore ||
      needsGrammarSelectiveForget
    ) {
      // T4.2a-PUX-R4-R3-R2-P1: Capture monotonic restore token. Old rAF/
      // timeout callbacks capture this token and abort if it mismatches the
      // current `restoreTokenRef.current` — this prevents old callbacks from
      // consuming a newer pending restore record.
      const myRestoreToken = (restoreTokenRef.current += 1);

      // Store restore data in the ref so it persists across effect re-runs.
      pendingRestoreRef.current = {
        scrollContainer,
        savedScrollTop,
        capturedScrollAnchor,
        quickPeekSnapshot: capturedQuickPeekSnapshot,
        capturedExpandedItemIds,
        snapshot,
        sourceIdentity: { generation, baseId },
        restoreToken: myRestoreToken,
      };

      const runRestore = () => {
        const pending = pendingRestoreRef.current;
        if (!pending) return;
        // T4.2a-PUX-R4-R3-R2-P1: Token check — abort if a newer restore
        // or source switch has invalidated this callback.
        if (restoreTokenRef.current !== pending.restoreToken) return;
        pendingRestoreRef.current = null;

        const qpSnapshot = pending.quickPeekSnapshot;
        // Quick Peek re-anchor: resolve new DOM element via stable identity.
        if (qpSnapshot) {
          if (qpSnapshot.token !== quickPeekRestoreTokenRef.current) {
            // Stale request — scroll restore still runs below.
          } else if (!quickPeekInteractionRef.current.isOpen) {
            quickPeekAnchorRef.current = null;
          } else if (
            pending.snapshot.record.generation !== qpSnapshot.generation ||
            pending.snapshot.base.base_id !== qpSnapshot.baseId
          ) {
            quickPeekAnchorRef.current = null;
          } else if (quickPeekAnchorMarkIdRef.current !== qpSnapshot.markId) {
            // Mark switched — new mark's handler owns the ref.
          } else {
            const newElement = resolveQuickPeekAnchorElement(
              qpSnapshot.anchorSegmentId,
              qpSnapshot.markId,
            );
            if (newElement) {
              quickPeekAnchorRef.current = {
                kind: "element",
                element: newElement,
              };
              quickPeekFloating.refs.setPositionReference?.({
                getBoundingClientRect: () =>
                  newElement.getBoundingClientRect(),
                contextElement: newElement,
              });
              quickPeekFloating.update?.();
            } else {
              setLookupState({ kind: "idle" });
              setInspectState(null);
              setQuickPeekAnchorBlockId(null);
              quickPeekAnchorRef.current = null;
            }
          }
        }
        // T4.2a-PUX-R4-R3-R2: Selective grammar expansion forget.
        if (
          pending.capturedExpandedItemIds !== null &&
          pending.capturedExpandedItemIds.size > 0
        ) {
          for (const itemId of pending.capturedExpandedItemIds) {
            const el = document.querySelector(
              `[data-reader-record-grammar-item-id="${itemId}"]`,
            );
            if (!el) {
              grammarExpansionControlRef.current?.forgetItem(itemId);
            }
          }
        }
        // T4.2a-PUX-R4-R3-R2: Semantic scroll-anchor compensation.
        // T4.2a-PUX-R4-R3-R2-P1: Only run when source identity (generation +
        // base_id) is unchanged. Cross-source scroll restore is forbidden —
        // the old anchor may not exist in the new source, and even a same-
        // name blockId must not be used to force-position into the new source.
        if (
          pending.savedScrollTop !== null &&
          pending.savedScrollTop > 0 &&
          pending.scrollContainer !== null &&
          pending.sourceIdentity.generation === generation &&
          pending.sourceIdentity.baseId === baseId
        ) {
          let restored = false;
          if (pending.capturedScrollAnchor) {
            const newEl = document.querySelector(
              `[data-reader-record-block-id="${pending.capturedScrollAnchor.blockId}"]`,
            ) as HTMLElement | null;
            if (newEl) {
              const newRect = newEl.getBoundingClientRect();
              const currentScrollTop =
                pending.scrollContainer === window
                  ? window.scrollY
                  : (pending.scrollContainer as HTMLElement).scrollTop;
              const targetScrollTop =
                currentScrollTop +
                newRect.top -
                pending.capturedScrollAnchor.viewportOffset;
              if (pending.scrollContainer === window) {
                window.scrollTo(0, targetScrollTop);
              } else {
                (pending.scrollContainer as HTMLElement).scrollTop =
                  targetScrollTop;
              }
              restored = true;
            }
          }
          if (!restored) {
            const targetTop = pending.savedScrollTop ?? 0;
            if (pending.scrollContainer === window) {
              window.scrollTo(0, targetTop);
            } else {
              (pending.scrollContainer as HTMLElement).scrollTop = targetTop;
            }
          }
        }
      };

      // rAF polling loop: detect when the captured block's position changes
      // (indicating the deferred React commit has happened). The loop is
      // intentionally NOT canceled by the effect cleanup so it survives
      // the re-run triggered by the deferred commit.
      //
      // T4.2a-PUX-R4-R3-R2-P1: Each frame checks the restore token. If a
      // newer restore or source switch has incremented the token, the old
      // loop aborts without consuming the new pending record.
      const capturedBlockId = capturedScrollAnchor?.blockId ?? null;
      const originalViewportOffset =
        capturedScrollAnchor?.viewportOffset ?? null;
      const pollFrame = () => {
        if (restoreTokenRef.current !== myRestoreToken) return; // Invalidated.
        if (pendingRestoreRef.current === null) return; // Already restored.
        if (capturedBlockId !== null && originalViewportOffset !== null) {
          const el = document.querySelector(
            `[data-reader-record-block-id="${capturedBlockId}"]`,
          ) as HTMLElement | null;
          if (el) {
            const rect = el.getBoundingClientRect();
            if (Math.abs(rect.top - originalViewportOffset) > 1) {
              runRestore();
              return;
            }
          } else {
            runRestore();
            return;
          }
        } else {
          runRestore();
          return;
        }
        window.requestAnimationFrame(pollFrame);
      };
      window.requestAnimationFrame(pollFrame);

      // Fallback: if DOM doesn't change within 100ms (no-op reload or jsdom
      // where getBoundingClientRect returns zeros), restore anyway.
      // 100ms is enough for React's deferred commit (MessageChannel macrotask)
      // in real browsers, and short enough for Vitest's waitFor timeout.
      //
      // T4.2a-PUX-R4-R3-R2-P1: Token check prevents old timeout from
      // consuming a newer pending restore record.
      window.setTimeout(() => {
        if (restoreTokenRef.current !== myRestoreToken) return; // Invalidated.
        if (pendingRestoreRef.current !== null) {
          runRestore();
        }
      }, 100);
    }

    // Update prevSnapshotRef for the next reload's merge attempt.
    prevSnapshotRef.current = snapshot;
    if (reloadContext !== null) {
      onReloadContextConsumedRef.current?.();
    }
  }, [plateValue, editor, snapshot]);

  // renderLeaf：为每个 paragraph text leaf 输出选区锚点 data 属性，
  // 同时承载 vocabulary / grammar / user_highlight / user_note 的视觉和交互。
  //
  // 单一外层 span 设计：
  // - 不再注册 ReaderLeafKit leaf plugin，避免嵌套 mark-hit wrapper 干扰
  //   浏览器原生 selection 落点。
  // - 所有 mark 视觉通过 reader-record-mark-stack--* class 控制。
  // - 点击优先级由 leaf 上各 mark data 字段决定，handleLeafClickIntent
  //   按 user_note > user_highlight > vocabulary > grammar_note 顺序派发。
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

  // Memoize the <Editor> (PlateContent) element so it does NOT re-render when
  // activeSelection changes. When SelectionAnchorBridge fires onChange →
  // setActiveSelection, ReaderRecordPlateSurface re-renders, but <Editor> is
  // skipped because renderLeaf (its only changing prop) hasn't changed. This
  // prevents Slate's internal restoreDomSelection layout effect from running
  // and clearing the user's native DOM selection in readonly mode.
  // See: P0 fix — toolbar not appearing after text selection.
  const editorElement = useMemo(
    () => (
      <Editor
        readOnly
        disableDefaultStyles
        renderLeaf={renderLeaf as never}
      />
    ),
    [renderLeaf],
  );

  const handleSettingsChange = useCallback((next: ReaderSettingsState) => {
    setReaderSettings(next);
    persistReaderSettings(next);
  }, []);

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
  // SelectionToolbar 现由 selectionToolbarFloating + ReaderFloatingToolbarButtons
  // 渲染（见 showSelectionToolbar），以 activeSelection 为唯一真相，不再使用
  // Plate FloatingToolbarKit（readonly 下 editor.selection 不可靠同步）。
  const [highlightMenu, setHighlightMenu] = useState<{
    mark: ReaderRecordPlateUserHighlightMark;
    anchor: HTMLElement;
  } | null>(null);
  const highlightMenuFloating = useReaderFloatingLayer({
    open: highlightMenu !== null,
    placement: "bottom-start",
    offsetPx: 6,
    collisionPadding: 10,
    strategy: "fixed",
  });
  const [dictionaryOpen, setDictionaryOpen] = useState(false);
  const dictionaryRailVisible =
    dictionaryOpen && !(isWorkspaceShell && sidebarMode === "locked");
  const quickPeekOpen =
    !dictionaryOpen && (lookupState.kind !== "idle" || inspectState !== null);

  // T4.2a-PUX-R2: source-identity-scoped interaction reset. A base_id or
  // generation change invalidates every anchor-bound interaction from the
  // previous source. Scroll is intentionally preserved by the plateValue
  // swap effect and is not source-identity-scoped.
  const generation = snapshot.record.generation;
  const baseId = snapshot.base.base_id;
  const prevSourceIdentityRef = useRef({ generation, baseId });
  useEffect(() => {
    const previous = prevSourceIdentityRef.current;
    if (
      previous.generation === generation &&
      previous.baseId === baseId
    ) {
      return;
    }
    prevSourceIdentityRef.current = { generation, baseId };
    setActiveSelection(null);
    setLookupState({ kind: "idle" });
    setInspectState(null);
    // T4.2a-PUX-R4-R3-R1-P1: Invalidate any pending Quick Peek restore from
    // the previous generation so the old rAF cannot re-anchor into the new
    // generation's DOM.
    quickPeekRestoreTokenRef.current += 1;
    quickPeekAnchorRef.current = null;
    // T4.2a-PUX-R4-R3-R2-P1 (Contract C): Invalidate any pending plate
    // restore (scroll-anchor / savedScrollTop / selective grammar forget)
    // bound to the previous source identity. The new generation's blocks
    // are not positionally comparable to the old ones, and the captured
    // sourceIdentity in pendingRestoreRef would mismatch anyway — but we
    // also increment the token so any in-flight rAF/timeout aborts without
    // touching the new DOM.
    restoreTokenRef.current += 1;
    pendingRestoreRef.current = null;
    setActiveSentenceChunkId(null);
    setActiveGrammarItemId(null);
    setGrammarExpandRequest(null);
    setHoverNoteAssetId(null);
    setNoteMenu(null);
    setHighlightMenu(null);
    setNoteAnchorDraft(null);
    setNoteDraft("");
    setNoteDuplicateAcknowledged(false);
    setDictionaryOpen(false);
    // T4.2a-PUX-R4-R2.1C: drop itemId-keyed grammar expansion state —
    // the new generation's itemIds are not comparable to the old ones.
    grammarExpansionControlRef.current?.clear();
  }, [baseId, generation]);

  // T4.2a-PUX-R4-R3-R2-P1 (Contract C): On unmount, invalidate any pending
  // plate restore so the in-flight rAF/timeout cannot fire against a torn-
  // down editor or read stale refs. The token check alone is sufficient to
  // make the callbacks no-op, but we also null out pendingRestoreRef so a
  // late callback cannot observe stale capture data.
  useEffect(() => {
    return () => {
      restoreTokenRef.current += 1;
      pendingRestoreRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (isWorkspaceShell && sidebarMode === "locked" && dictionaryOpen) {
      setDictionaryOpen(false);
    }
    return undefined;
  }, [dictionaryOpen, isWorkspaceShell, sidebarMode]);

  const quickPeekAnchorRef = useRef<ReaderQuickPeekAnchor>(null);
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

  const [dictionaryLookup, setDictionaryLookup] =
    useState<DictionaryLookupSnapshot | null>(null);
  const [dictionaryInspect, setDictionaryInspect] =
    useState<ReaderStructuredInspectIntent | null>(null);
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
  const commitDictionaryLookup = useCallback((lookup: DictionaryLookupSnapshot) => {
    setDictionaryLookup(lookup);
    setDictionaryInspect(null);
    setDictionarySearchQuery(lookup.query);
    setDictionarySaveState({ kind: "idle" });
    setDictionaryAI({ kind: "idle" });
    setDictionaryAIPanelOpen(false);
    setDictionaryAINoteState({ kind: "idle" });
    setDictionaryHistory((current) => {
      const lookupKey = dictionaryLookupHistoryKey(lookup);
      const filtered = current.filter(
        (item) => dictionaryLookupHistoryKey(item) !== lookupKey,
      );
      return [lookup, ...filtered].slice(0, 20);
    });
  }, []);
  const [askOpen, setAskOpen] = useState(false);
  const [askSurface, setAskSurface] =
    useState<AiWorkspaceSurface>("sidecar");
  const [workspaceEl, setWorkspaceEl] = useState<HTMLElement | null>(null);
  const { effectiveSurface, hasSidecarCapacity } = useReaderAskPresentation({
    requestedSurface: askSurface,
    workspaceEl,
  });
  const [capacityDowngradeDismissed, setCapacityDowngradeDismissed] =
    useState(false);
  useEffect(() => {
    if (hasSidecarCapacity) {
      setCapacityDowngradeDismissed(false);
    }
  }, [hasSidecarCapacity]);
  const [feedbackState, setFeedbackState] = useState<SaveState>({ kind: "idle" });
  const [feedbackTarget, setFeedbackTarget] = useState<{
    blockId: string;
    variant: "grammar" | "supplement" | "vocabulary" | "sentence_analysis";
    feedbackScope: "dictionary";
    anchorSegmentId: string;
    layerId?: string;
    itemId?: string;
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
    collisionPadding: 10,
    strategy: "fixed",
  });

  // --- Selection-actions floating toolbar ---
  // Reader 选区工具栏：以 activeSelection（SelectionAnchorBridge 产出）为唯一
  // 显示与定位真相，不依赖 Plate editor.selection（readonly 下与原生 selection
  // 不可靠同步）。显示条件 = 有稳定选区文本 + rect 可用 + 无其他浮层抢占。
  const showSelectionToolbar =
    activeSelection !== null &&
    activeSelection.selectedText.trim().length > 0 &&
    activeSelection.rect !== null &&
    !quickPeekOpen &&
    highlightMenu === null &&
    noteMenu === null &&
    noteAnchorDraft === null &&
    feedbackTarget === null;
  const selectionToolbarFloating = useReaderFloatingLayer({
    open: showSelectionToolbar,
    placement: "top",
    offsetPx: 8,
    collisionPadding: 12,
    strategy: "fixed",
  });

  // 定位 effect：activeSelection.rect 作为初始定位真相；滚动/缩放时重读
  // 原生 selection 的 live rect（activeSelection.rect 的底层来源）以保持
  // 工具栏跟随选区。flip/shift middleware 保证工具栏在视口内不遮挡选区。
  useEffect(() => {
    if (!showSelectionToolbar) {
      return;
    }
    const initialRect = activeSelection?.rect ?? null;
    if (!initialRect) {
      return;
    }

    function getLiveSelectionRect(): DOMRect | null {
      const sel = window.getSelection();
      if (!sel || sel.rangeCount === 0 || sel.isCollapsed) {
        return null;
      }
      return sel.getRangeAt(0).getBoundingClientRect();
    }

    function updateSelectionToolbarReference() {
      const liveRect = getLiveSelectionRect();
      const rect = liveRect ?? initialRect;
      if (!rect) {
        return;
      }
      selectionToolbarFloating.refs.setPositionReference?.({
        getBoundingClientRect: () => rect,
      });
      selectionToolbarFloating.update?.();
    }

    updateSelectionToolbarReference();
    window.addEventListener("resize", updateSelectionToolbarReference);
    window.addEventListener("scroll", updateSelectionToolbarReference, true);
    return () => {
      window.removeEventListener("resize", updateSelectionToolbarReference);
      window.removeEventListener("scroll", updateSelectionToolbarReference, true);
    };
  }, [
    showSelectionToolbar,
    activeSelection,
    selectionToolbarFloating.refs,
    selectionToolbarFloating.update,
  ]);

  // 选区或激活笔记变化时，更新浮动层的 reference 元素
  useEffect(() => {
    if (!commentPanelOpen) return;

    function updateCommentReference() {
      // draft 模式：优先锚定 Plate CommentKit 已渲染的 draft mark DOM。
      if (noteAnchorDraft) {
        const draftAnchors = Array.from(
          surfaceRef.current?.querySelectorAll<HTMLElement>(
            READER_RECORD_DRAFT_COMMENT_SELECTOR,
          ) ?? [],
        );
        const anchor = draftAnchors[0] ?? null;
        const fallbackRect = activeSelection?.rect ?? null;
        if (anchor) {
          commentFloating.refs.setPositionReference?.({
            getBoundingClientRect: () =>
              boundingRectForElements(draftAnchors) ??
              fallbackRect ??
              anchor.getBoundingClientRect(),
            contextElement: anchor,
          });
          commentFloating.update?.();
          return;
        }
      }
      if (noteAnchorDraft && activeSelection?.rect) {
        const rect = activeSelection.rect;
        commentFloating.refs.setPositionReference?.({
          getBoundingClientRect: () => rect,
        });
        commentFloating.update?.();
        return;
      }
      // existing note 模式：用笔记 mark 的 DOM element
      if (noteMenu?.anchor) {
        const anchor = noteMenu.anchor;
        commentFloating.refs.setPositionReference?.({
          getBoundingClientRect: () => anchor.getBoundingClientRect(),
          contextElement: anchor,
        });
        commentFloating.update?.();
      }
    }

    updateCommentReference();
    window.addEventListener("resize", updateCommentReference);
    window.addEventListener("scroll", updateCommentReference, true);
    return () => {
      window.removeEventListener("resize", updateCommentReference);
      window.removeEventListener("scroll", updateCommentReference, true);
    };
  }, [
    commentPanelOpen,
    noteAnchorDraft,
    noteMenu,
    activeSelection,
    commentFloating.refs,
    commentFloating.update,
  ]);

  // SelectionAnchorBridge 在 <Plate> 内通过 useEditorSelection 订阅选区，
  // 拿到 Plate editor.selection → ReaderRecordSelectionAnchorBridgeResult。
  // 替代旧的 selectionchange DOM 监听 + readReaderRecordSelectionAnchorDrafts。
  const handleSelectionChange = useCallback(
    (nextSelection: ReaderRecordSelectionAnchorBridgeResult | null) => {
      activeSelectionRef.current = nextSelection;
      setActiveSelection(nextSelection);
      setCopyStatus("idle");
      setTranslationState({ kind: "idle" });
      if (nextSelection?.selectedText.trim()) {
        setHighlightMenu(null);
        setNoteMenu(null);
        commentApiRef.current?.setActiveId(null);
      }
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

  const runDictionaryLookupRequest = useCallback(async ({
    query,
    context,
    positionReference,
    anchorBlockId,
    warningLabel = "dictionary",
  }: {
    query: string;
    context: ReaderRecordLookupContext;
    positionReference?: ReaderRecordLookupPositionReference;
    // T4.2a-PUX-R4-R3-R1: Stable block identity fallback for selection-based
    // lookup where positionReference.contextElement is not available (e.g.,
    // runLookupForSelection passes only getRect). Without this, the Quick Peek
    // anchor block ID would be null, and the post-setValue re-anchor would
    // fail to resolve the anchor segment — causing a deterministic close
    // instead of preserving the Quick Peek across full reload.
    anchorBlockId?: string;
    warningLabel?: string;
  }) => {
    if (positionReference) {
      quickPeekAnchorRef.current = {
        kind: "range",
        getRect: positionReference.getRect,
        contextElement: positionReference.contextElement,
      };
      setQuickPeekAnchorBlockId(
        anchorBlockId ??
          quickPeekAnchorBlockIdFromElement(positionReference.contextElement),
      );
      quickPeekFloating.refs.setPositionReference?.({
        getBoundingClientRect: positionReference.getRect,
        contextElement: positionReference.contextElement,
      });
    }

    setInspectState(null);
    setDictionaryInspect(null);
    setLookupState({ kind: "loading", query, context });

    try {
      const params = new URLSearchParams({
        word: query,
        type: context.lookupType,
        context: context.contextSentence,
        sentenceId: context.sentenceId,
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

      const readySnapshot = buildDictionaryLookupSnapshotFromContext(
        snapshot,
        query,
        context,
        { kind: "ready", result: payload },
      );
      setLookupState({ kind: "ready", query, context, result: payload });
      commitDictionaryLookup(readySnapshot);
    } catch (error) {
      console.warn(`[ReaderRecordPlateSurface] ${warningLabel} lookup failed`, error);
      setLookupState({
        kind: "error",
        query,
        context,
        message: "词典查询失败，请稍后重试。",
      });
    }
  }, [commitDictionaryLookup, quickPeekFloating.refs, snapshot]);

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
        label: vocabularyLabel(mark),
        annotationType: mark.vocabulary.itemType,
        visualTone: vocabularyVisualTone(mark),
        glossary: vocabularyGlossary(mark),
        anchorOffsets: {
          startOffset: mark.anchor.segmentStartOffset,
          endOffset: mark.anchor.segmentEndOffset,
        },
        textHash: mark.anchor.textHash,
      };
      quickPeekAnchorRef.current = { kind: "element", element: anchor };
      setQuickPeekAnchorBlockId(quickPeekAnchorBlockIdFromElement(anchor));
      // T4.2a-PUX-R4-R3-R1: Capture stable markId for post-setValue re-anchor.
      // inspectState.markId also carries this, but lookup-based Quick Peek
      // (vocab_highlight path) doesn't set inspectState — the ref is the
      // single source of truth across both paths.
      // T4.2a-PUX-R4-R3-R1-P1: Invalidate any pending restore from a previous
      // mark so the old rAF cannot overwrite this new mark's anchor ref.
      quickPeekAnchorMarkIdRef.current = mark.id;
      quickPeekRestoreTokenRef.current += 1;
      quickPeekFloating.refs.setPositionReference?.({
        getBoundingClientRect: () => anchor.getBoundingClientRect(),
        contextElement: anchor,
      });

      const inspectIntent = structuredInspectIntentFromVocabularyMark(
        mark,
        contextSentence,
      );
      if (inspectIntent) {
        setLookupState({ kind: "idle" });
        if (dictionaryOpen) {
          setInspectState(null);
          setDictionarySaveState({ kind: "idle" });
          setDictionaryAI({ kind: "idle" });
          setDictionaryAIPanelOpen(false);
          setDictionaryAINoteState({ kind: "idle" });
          void runDictionaryLookupRequest({
            query,
            context,
            warningLabel: "vocabulary",
          });
        } else {
          setInspectState(inspectIntent);
        }
        return;
      }

      void runDictionaryLookupRequest({
        query,
        context,
        warningLabel: "vocabulary",
      });
    },
    [dictionaryOpen, plateDocument.children, quickPeekFloating.refs, runDictionaryLookupRequest],
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

  const handleTranslate = useCallback(async () => {
    const selection = activeSelection;
    const draft = singleRangeDraft(selection);
    if (!selection || !draft) {
      setTranslationState({
        kind: "error",
        message: translationDisabledReason(selection) ?? "请选择稳定原文后再翻译",
      });
      return;
    }

    setTranslationState({ kind: "submitting" });
    try {
      const response = await fetch(
        `/api/web/reader/records/${encodeURIComponent(
          snapshot.record_id,
        )}/section-translation`,
        {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            startUnitId: draft.unit_id,
            endUnitId: draft.unit_id,
            startAnchorSegmentId: draft.anchor_segment_id,
            endAnchorSegmentId: draft.anchor_segment_id,
            nodeId: selection.blockId,
            outlineRevision: null,
          }),
        },
      );
      const payload = (await response.json().catch(() => null)) as unknown;
      const payloadRecord =
        payload && typeof payload === "object"
          ? (payload as Record<string, unknown>)
          : null;
      const outcome = payloadRecord?.outcome;
      if (
        !response.ok ||
        payloadRecord?.ok === false ||
        !isReaderSectionTranslationOutcome(outcome)
      ) {
        const message =
          typeof payloadRecord?.message === "string"
            ? payloadRecord.message
            : "翻译请求失败，请稍后重试。";
        setTranslationState({ kind: "error", message });
        return;
      }

      const detail =
        typeof payloadRecord?.detail === "string" ? payloadRecord.detail : null;
      setTranslationState({ kind: "submitted", outcome, detail });
      if (outcome === "succeeded") {
        await onRequestSnapshotReload?.();
      }
    } catch {
      setTranslationState({
        kind: "error",
        message: "翻译请求失败，请稍后重试。",
      });
    }
  }, [activeSelection, onRequestSnapshotReload, snapshot.record_id]);

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

  const runLookupForSelection = useCallback(async (
    selection: ReaderRecordSelectionAnchorBridgeResult | null,
    options: { label?: string; clearSelectionAfterAnchor?: boolean } = {},
  ) => {
    const draft = singleRangeDraft(selection);
    if (!selection || !draft) {
      return false;
    }

    const lookupDraft = trimDraftForLookup(draft);
    const query = lookupDraft.query;
    if (!query) {
      return false;
    }

    const contextSentence =
      sourceTextForAnchorSegment(plateDocument.children, draft.anchor_segment_id) ||
      selection.contextSentence;
    const context: ReaderRecordLookupContext = {
      contextSentence,
      sentenceId: selection.sentenceId,
      anchorText: query,
      lookupType: lookupTypeForSelection(query),
      source: "selection",
      label: options.label ?? "选区查词",
      anchorOffsets: {
        startOffset: lookupDraft.startOffset,
        endOffset: lookupDraft.endOffset,
      },
      textHash: lookupDraft.textHash,
    };

    if (options.clearSelectionAfterAnchor) {
      setActiveSelection(null);
    }

    const liveSelection = window.getSelection();
    const liveRange =
      liveSelection && liveSelection.rangeCount > 0
        ? liveSelection.getRangeAt(0)
        : null;
    const positionReference = selection.rect
      ? {
          getRect: () => liveRange?.getBoundingClientRect() ?? selection.rect!,
        }
      : undefined;

    await runDictionaryLookupRequest({
      query,
      context,
      positionReference,
      // T4.2a-PUX-R4-R3-R1: Pass stable block identity from the selection
      // bridge so that post-setValue re-anchor can resolve the anchor
      // segment even without a contextElement DOM reference.
      anchorBlockId: selection.blockId,
    });
    return true;
  }, [plateDocument.children, runDictionaryLookupRequest]);

  const handleLookup = useCallback(async () => {
    await runLookupForSelection(activeSelection);
  }, [activeSelection, runLookupForSelection]);

  const runLookupFromNativeDoubleClickSelection = useCallback(async (
    sourceLeaf: HTMLElement,
  ) => {
    const nativeSelection = window.getSelection();
    if (
      !nativeSelection ||
      nativeSelection.rangeCount === 0 ||
      nativeSelection.isCollapsed
    ) {
      return false;
    }

    const query = nativeSelection.toString().trim();
    if (!query || lookupTypeForSelection(query) !== "word") {
      return false;
    }
    if (!/^[A-Za-z][A-Za-z'-]*$/.test(query)) {
      return false;
    }

    const range = trimNativeSelectionToQuery(sourceLeaf, query) ?? nativeSelection.getRangeAt(0);
    const rangeElement =
      range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
        ? (range.commonAncestorContainer as Element)
        : range.commonAncestorContainer.parentElement;
    if (!rangeElement || !sourceLeaf.contains(rangeElement)) {
      return false;
    }

    const anchorSegmentId = sourceLeaf.dataset.anchorSegmentId;
    if (!anchorSegmentId) {
      return false;
    }
    const sentenceId = sentenceIdForAnchorSegment(
      plateDocument.children,
      anchorSegmentId,
    );
    if (!sentenceId) {
      return false;
    }
    const contextSentence =
      sourceTextForAnchorSegment(plateDocument.children, anchorSegmentId) ||
      sourceLeaf.textContent?.trim() ||
      query;
    const context: ReaderRecordLookupContext = {
      contextSentence,
      sentenceId,
      anchorText: query,
      lookupType: "word",
      source: "selection",
      label: "查词",
    };
    const getRect = () => {
      const rect = range.getBoundingClientRect();
      return rect.width > 0 || rect.height > 0
        ? rect
        : sourceLeaf.getBoundingClientRect();
    };

    setActiveSelection(null);
    await runDictionaryLookupRequest({
      query,
      context,
      positionReference: {
        getRect,
        contextElement: sourceLeaf,
      },
    });
    return true;
  }, [plateDocument.children, runDictionaryLookupRequest]);

  const handleDocumentDoubleClickTarget = useCallback((eventTarget: EventTarget | null) => {
    const target = eventTarget instanceof Element ? eventTarget : null;
    if (!target) {
      return;
    }

    const sourceLeaf = target.closest<HTMLElement>(
      '[data-reader-record-leaf="segment_text"]',
    );
    if (
      !sourceLeaf ||
      markStackBlocksDoubleClickLookup(
        sourceLeaf.dataset.readerRecordMarkStackKinds,
      )
    ) {
      return;
    }

    window.setTimeout(() => {
      const runNativeFallback = () => {
        void runLookupFromNativeDoubleClickSelection(sourceLeaf);
      };
      const selection = activeSelectionRef.current;
      const draft = singleRangeDraft(selection);
      const query = draft?.selected_text.trim() ?? "";
      if (!selection || !draft || !query || lookupTypeForSelection(query) !== "word") {
        runNativeFallback();
        return;
      }
      if (!/^[A-Za-z][A-Za-z'-]*$/.test(query)) {
        runNativeFallback();
        return;
      }

      trimNativeSelectionToQuery(sourceLeaf, query);
      void runLookupForSelection(selection, {
        label: "查词",
        clearSelectionAfterAnchor: true,
      }).then((handled) => {
        if (!handled) {
          void runLookupFromNativeDoubleClickSelection(sourceLeaf);
        }
      });
    }, 0);
  }, [runLookupForSelection, runLookupFromNativeDoubleClickSelection]);

  const handleSurfaceDoubleClickCapture = useCallback(
    (event: MouseEvent<HTMLDivElement>) => {
      handleDocumentDoubleClickTarget(event.target);
    },
    [handleDocumentDoubleClickTarget],
  );

  const activeLookupSnapshot = useMemo(
    () => buildDictionaryLookupSnapshot(snapshot, lookupState),
    [snapshot, lookupState],
  );
  const dictionaryPanelLookup = activeLookupSnapshot ?? dictionaryLookup;
  const dictionaryPanelInspect = activeLookupSnapshot ? null : dictionaryInspect;
  const currentAskSelectionAttachment = useMemo<ReaderAskAttachment | null>(() => {
    const selection = activeSelection;
    const draft = singleRangeDraft(selection);
    const segment = selection?.supportedSingleRange ? (selection.segments[0] ?? null) : null;
    const anchorPayload = sourceSelectionAnchorPayload(snapshot.record_id, selection);
    if (!selection) {
      return null;
    }

    if (draft && segment && anchorPayload) {
      // Snapshot identity may advance one render before the selection-clear
      // effect runs. Reject drafts stamped for a previous record/base/generation
      // so the composer never re-ingests a stale range after its own identity fence.
      if (
        !isCurrentAskSelectionDraft(draft, {
          recordId: snapshot.record_id,
          baseId: snapshot.base.base_id,
          generation: snapshot.record.generation,
        })
      ) {
        return null;
      }
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
  }, [
    activeSelection,
    askPageIdentity,
    snapshot.base.base_id,
    snapshot.record.generation,
    snapshot.record_id,
  ]);

  // Ask composer send-context: plate adapts the live selection, then the
  // composer module owns slots / draft / quick-action / send merge.
  const askComposerIdentityKey = `${snapshot.record_id}:${snapshot.base.base_id}:${snapshot.record.generation}`;
  const askComposer = useAskComposerContext({
    open: askOpen,
    identityKey: askComposerIdentityKey,
    selectionCandidate: currentAskSelectionAttachment,
  });

  const openDictionaryRail = useCallback(() => {
    releaseSidebarForReadingTool();
    const lookupForRail = activeLookupSnapshot;
    const inspectForRail = inspectState;
    setDictionaryOpen(true);
    quickPeekAnchorRef.current = null;
    if (lookupForRail) {
      setLookupState({ kind: "idle" });
      setInspectState(null);
      setDictionaryInspect(null);
      commitDictionaryLookup(lookupForRail);
      return;
    }
    if (inspectForRail) {
      setInspectState(null);
      setDictionarySaveState({ kind: "idle" });
      setDictionaryAI({ kind: "idle" });
      setDictionaryAIPanelOpen(false);
      setDictionaryAINoteState({ kind: "idle" });
      const query = inspectForRail.lookupText ?? inspectForRail.anchorText;
      void runDictionaryLookupRequest({
        query,
        context: lookupContextFromInspectIntent(inspectForRail),
        warningLabel: "vocabulary",
      });
      return;
    }
    setLookupState({ kind: "idle" });
    setInspectState(null);
  }, [
    activeLookupSnapshot,
    commitDictionaryLookup,
    inspectState,
    releaseSidebarForReadingTool,
    runDictionaryLookupRequest,
  ]);

  const closeDictionaryRail = useCallback(() => {
    setDictionaryOpen(false);
  }, []);

  const openAskPanel = useCallback((
    attachment?: ReaderAskAttachment | null,
    pendingRequest?: PendingReaderRecordAskRequest | null,
  ) => {
    askComposer.enter(attachment, pendingRequest ?? null);
    setAskOpen(true);
    setDictionaryOpen(false);
    setDictionaryAIPanelOpen(false);
    setDictionaryAI({ kind: "idle" });
    setLookupState({ kind: "idle" });
    setInspectState(null);
    setDictionaryInspect(null);
    setHighlightMenu(null);
    setNoteMenu(null);
    setFeedbackTarget(null);
  }, [askComposer]);

  const handleAttachInspectToAsk = useCallback(() => {
    if (!inspectState) {
      return;
    }
    openAskPanel(
      askAttachmentFromVocabularyInspect(askPageIdentity, inspectState),
      null,
    );
  }, [askPageIdentity, inspectState, openAskPanel]);

  const handleAskFromSelection = useCallback(() => {
    if (!currentAskSelectionAttachment) {
      return;
    }
    openAskPanel(currentAskSelectionAttachment, null);
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
    }, null);
  }, [askPageIdentity, noteMenu, openAskPanel, snapshot.record.generation, snapshot.record_id]);

  const handleRequestAI = useCallback(() => {
    openAskPanel(currentAskSelectionAttachment, null);
  }, [currentAskSelectionAttachment, openAskPanel]);

  /**
   * "加入 Ask Claread": pin via the Ask composer context (slot policy
   * lives there), then open the panel.
   */
  const handlePinSelectionToAsk = useCallback(() => {
    askComposer.pinSelection();
    openAskPanel(undefined, null);
  }, [askComposer, openAskPanel]);

  const pinSelectionState = askComposer.pinSelectionState;

  const handleDictionarySearch = useCallback(
    async (query: string) => {
      const trimmed = query.trim();
      if (!trimmed) {
        return;
      }
      releaseSidebarForReadingTool();
      setDictionarySearchExpanded(false);
      setDictionaryOpen(true);
      const lookupType = lookupTypeForSelection(trimmed);
      const context: ReaderRecordLookupContext = {
        contextSentence: "",
        sentenceId: "__manual__",
        anchorText: trimmed,
        lookupType,
        source: "selection",
        label: "手动查词",
      };
      await runDictionaryLookupRequest({
        query: trimmed,
        context,
        warningLabel: "dictionary search",
      });
    },
    [releaseSidebarForReadingTool, runDictionaryLookupRequest],
  );

  const handleSelectHistory = useCallback(
    (historyLookup: DictionaryLookupSnapshot) => {
      setDictionarySearchQuery(historyLookup.query);
      setDictionaryLookup(historyLookup);
      setDictionarySaveState({ kind: "idle" });
      setDictionaryAI({ kind: "idle" });
      setDictionaryAIPanelOpen(false);
      setDictionaryAINoteState({ kind: "idle" });
      const context = lookupContextFromSnapshot(historyLookup);
      setInspectState(null);
      setDictionaryInspect(null);
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
    const lookupForSave = dictionaryPanelLookup;
    if (!lookupForSave) {
      return;
    }
    if (
      lookupForSave.state.kind !== "ready" ||
      lookupForSave.state.result.kind !== "entry"
    ) {
      setDictionarySaveState({
        kind: "error",
        message: "当前词条暂不支持保存，请先完成词典查询。",
      });
      return;
    }
    const result = lookupForSave.state.result;
    const entry = result.entry;
    const shortMeaning = firstMeaning(result);
    if (!shortMeaning) {
      setDictionarySaveState({
        kind: "error",
        message: "当前词条暂无简短释义，无法保存。",
      });
      return;
    }
    if (!lookupForSave.contextSentence.trim()) {
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
          meanings_json: meaningsJson(result),
          source_provider: "reader_record",
          dict_entry_id: entry.id,
          source_sentence: lookupForSave.contextSentence,
          source_context: lookupForSave.contextSentence,
          payload_json: {
            source_refs: [
              {
                reading_record_id: snapshot.record_id,
                source_sentence_id: lookupForSave.sentenceId,
                source_anchor_text: lookupForSave.anchorText,
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
  }, [dictionaryPanelLookup, snapshot.record_id]);

  const handleSubmitFeedback = useCallback(
    async (sentiment: "positive" | "negative") => {
      const target = feedbackTarget;
      if (!target) {
        return;
      }
      setFeedbackTarget(null);
      setFeedbackState({ kind: "saving" });
      try {
        const feedbackType =
          sentiment === "negative" ? "wrong_definition" : null;
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
            entryPoint:
              target.variant === "vocabulary"
                ? "reader_record_vocabulary_mark"
                : "reader_record_callout",
            contextJson: {
              readingRecordId: snapshot.record_id,
              annotationType: target.annotationType,
              targetVariant: target.variant,
              anchorSegmentId: target.anchorSegmentId,
              layerId: target.layerId,
              itemId: target.itemId,
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
      const lookupForCandidate = dictionaryPanelLookup;
      if (!lookupForCandidate) {
        return;
      }
      const snapshotContext = lookupContextFromSnapshot(lookupForCandidate);
      const baseContext: ReaderRecordLookupContext = {
        contextSentence: snapshotContext.contextSentence,
        sentenceId: snapshotContext.sentenceId,
        anchorText: snapshotContext.anchorText,
        lookupType: snapshotContext.lookupType,
        source: snapshotContext.source,
        label: lookupForCandidate.lookupType === "phrase" ? "短语查询" : "词典查询",
        sourceContext: snapshotContext.sourceContext,
        anchorOffsets: snapshotContext.anchorOffsets,
        occurrence: snapshotContext.occurrence,
        textHash: snapshotContext.textHash,
      };
      setLookupState({
        kind: "loading",
        query: lookupForCandidate.query,
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
            query: lookupForCandidate.query,
            context: baseContext,
            message: "词典候选加载失败。",
          });
          return;
        }
        const readySnapshot = buildDictionaryLookupSnapshotFromContext(
          snapshot,
          lookupForCandidate.query,
          baseContext,
          { kind: "ready", result: payload },
        );
        setLookupState({
          kind: "ready",
          query: lookupForCandidate.query,
          context: baseContext,
          result: payload,
        });
        commitDictionaryLookup(readySnapshot);
      } catch (error) {
        console.warn("[ReaderRecordPlateSurface] candidate select failed", error);
        setLookupState({
          kind: "error",
          query: lookupForCandidate.query,
          context: baseContext,
          message: "词典候选加载失败，请稍后重试。",
        });
      }
    },
    [commitDictionaryLookup, dictionaryPanelLookup, snapshot],
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
          snapshot.record_id,
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
      const payload = await postReadingRecordUserAsset(`/api/web/reader/records/${encodeURIComponent(snapshot.record_id)}/highlights`, {
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
    commentApiRef.current?.removeDraftMark();
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
    commentApiRef.current?.removeDraftMark();
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
      await postReadingRecordUserAsset(`/api/web/reader/records/${encodeURIComponent(snapshot.record_id)}/notes`, {
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
      setNoteAnchorDraft(null);
      setNoteMenu(null);
      commentApiRef.current?.setActiveId(null);
      setHighlightMenu({ mark, anchor });
    },
    [],
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
        `/api/web/reader/records/${encodeURIComponent(snapshot.record_id)}/highlights/${encodeURIComponent(deletedAssetId)}`,
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
    function updateHighlightReference() {
      const anchor = activeMenu.anchor;
      highlightMenuFloating.refs.setPositionReference?.({
        getBoundingClientRect: () => anchor.getBoundingClientRect(),
        contextElement: anchor,
      });
      highlightMenuFloating.update?.();
    }
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
    updateHighlightReference();
    window.addEventListener("resize", updateHighlightReference);
    window.addEventListener("scroll", updateHighlightReference, true);
    window.document.addEventListener("pointerdown", handlePointerDown);
    return () => {
      window.removeEventListener("resize", updateHighlightReference);
      window.removeEventListener("scroll", updateHighlightReference, true);
      window.document.removeEventListener("pointerdown", handlePointerDown);
    };
  }, [highlightMenu, highlightMenuFloating.refs, highlightMenuFloating.update]);

  const handleActivateNote = useCallback(
    (mark: ReaderRecordPlateUserNoteMark, anchor: HTMLElement) => {
      if (hasNonCollapsedNativeSelection()) {
        return;
      }
      setHighlightMenu(null);
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

      const noteMark = userNoteMarksFromLeaf(leaf)[0];
      if (noteMark) {
        event.preventDefault();
        handleActivateNote(noteMark, anchor);
        return;
      }

      if (leaf.user_highlight_data) {
        event.preventDefault();
        handleActivateHighlight(leaf.user_highlight_data, anchor);
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

  useEffect(() => {
    leafClickResolverRef.current = handleLeafClickIntent;
    return () => {
      if (leafClickResolverRef.current === handleLeafClickIntent) {
        leafClickResolverRef.current = null;
      }
    };
  }, [handleLeafClickIntent]);

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

  // T4.2a-PUX-R4-R2.1C: ref holding the control handle from
  // ReaderGrammarExpansionProvider. `clear()` is called before
  // editor.tf.setValue (full reload) and on generation change to drop
  // stale itemId-keyed expansion state. `forgetItem(itemId)` is called
  // on targeted remove ops for grammar callout blockIds so the same
  // itemId reappearing in the same generation defaults to collapsed.
  const grammarExpansionControlRef = useRef<ReaderGrammarExpansionControlRef["current"]>(null);

  const calloutActions = useMemo(
    () => ({}),
    [],
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
      translate: {
        disabled: !sourceSingleRangeReady || translationState.kind === "submitting",
        reason:
          translationState.kind === "submitting"
            ? "正在提交翻译"
            : translationDisabledReason(activeSelection),
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
    translationState,
    writeState,
  ]);

  // 把选区工具栏回调打包为 Context value，供 ReaderFloatingToolbarButtons 消费。
  const toolbarActions = useMemo<ReaderToolbarActions>(
    () => ({
      onAsk: () => handleAskFromSelection(),
      onPinSelectionToAsk: () => handlePinSelectionToAsk(),
      pinSelectionState,
      onAskSubmit: (request) => handleAskPromptFromSelection(request),
      onCopy: () => handleCopy(),
      onTranslate: () => handleTranslate(),
      onHighlight: () => handleHighlight(),
      onNote: () => handleOpenNoteComposer(),
      onLookup: () => handleLookup(),
      suppressToolbar: quickPeekOpen,
      state: toolbarActionState,
    }),
    [
      handleAskFromSelection,
      handlePinSelectionToAsk,
      pinSelectionState,
      handleAskPromptFromSelection,
      handleCopy,
      handleTranslate,
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
        `/api/web/reader/records/${encodeURIComponent(snapshot.record_id)}/notes/${encodeURIComponent(targetAssetId)}`,
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
        `/api/web/reader/records/${encodeURIComponent(snapshot.record_id)}/notes/${encodeURIComponent(deletedAssetId)}`,
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

  const askSidecarOpen = askOpen && effectiveSurface === "sidecar";
  const askFloatingOpen = askOpen && effectiveSurface === "floating";
  const capacityNotice =
    askOpen && askSurface === "sidecar" && !hasSidecarCapacity
      ? "当前阅读区较窄，Ask Claread 以浮窗形式展示。"
      : "";
  const showCapacityDowngradeNotice =
    askOpen &&
    askSurface === "sidecar" &&
    !hasSidecarCapacity &&
    !capacityDowngradeDismissed;

  return (
    <div
      data-testid="reader-record-plate-surface"
      data-reader-record-surface="plate-readonly-reading"
      onDoubleClickCapture={handleSurfaceDoubleClickCapture}
    >
      <div
        ref={setWorkspaceEl}
        data-reader-record-workspace="plate"
        style={readerAskPresentationCssVars()}
        className={cn(
          "reader-workspace-shell",
          askSidecarOpen && "reader-workspace-shell--ask-docked",
          askFloatingOpen && "reader-workspace-shell--ask-floating",
        )}
      >
        <div role="status" aria-live="polite" className="sr-only">
          {capacityNotice}
        </div>
        <div className="reader-workspace-shell__topbar">
          {/* Surface-level sticky operation bar: spans the available document viewport. */}
          <ReaderRecordTopBar
        snapshot={snapshot}
        surfaceMode={surfaceMode}
        onModeChange={handleModeChange}
        readerSettings={readerSettings}
        themePreference={themePreference}
        onSettingsChange={handleSettingsChange}
            onThemeChange={setThemePreference}
          />
        </div>
        <section
          ref={surfaceRef}
          className={cn(!askSidecarOpen && className, askSidecarOpen && "contents")}
        >
        <div
          className={cn(
            "reader-record-canvas",
            askSidecarOpen && "reader-record-canvas--ask-open",
          )}
        >
          <div className={cn("reader-record-canvas__body", askSidecarOpen && className)}>
            <div className="reader-record-main reader-record-main--document-rhythm">
              <div className="reader-header-band-inner mx-auto w-full max-w-[var(--reader-record-main-width)]">
                <ReaderRecordHeader
                  snapshot={snapshot}
                  surfaceMode={surfaceMode}
                  onModeChange={handleModeChange}
                />
              </div>

              <div className={contentColumnClassName}>
          <SelectionActionState
            copyStatus={copyStatus}
            selection={activeSelection}
            translationState={translationState}
            writeState={writeState}
          />
          {highlightMenu ? (
            <ReaderFloatingSurface
              floatingRef={highlightMenuFloating.refs.setFloating}
              style={highlightMenuFloating.floatingStyles as CSSProperties}
              chrome="bare"
              data-reader-record-floating-toolbar="highlight-menu"
            >
              <TooltipProvider delayDuration={200}>
                <div className="flex h-10 items-center gap-1 rounded-[7px] border border-border/75 bg-background/95 p-1 shadow-[var(--app-panel-shadow-quiet)] backdrop-blur-md">
                  <span className="inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-xs font-medium text-muted-foreground">
                    <Palette className="h-3.5 w-3.5" aria-hidden="true" />
                    改色
                  </span>
                  {HIGHLIGHT_COLOR_OPTIONS.map((option) => {
                    const isActive = highlightMenu.mark.color === option.value;
                    return (
                      <Tooltip key={option.value}>
                        <TooltipTrigger asChild>
                          <button
                            type="button"
                            aria-label={`切换为${option.label}`}
                            data-reader-record-highlight-color={option.value}
                            onClick={() => handleUpdateHighlightColor(option.value)}
                            className="focus-ring group grid h-8 w-8 place-items-center rounded-md transition-transform hover:bg-transparent active:scale-[0.96]"
                          >
                            <span
                              className={cn(
                                "h-4 w-4 rounded-[4px] ring-1 ring-inset ring-border/70 transition-[box-shadow,transform] group-hover:ring-foreground/30",
                                isActive && "ring-2 ring-foreground/60",
                                option.swatchClassName,
                              )}
                            />
                            <span className="sr-only">{option.label}</span>
                          </button>
                        </TooltipTrigger>
                        <TooltipContent side="top" className="text-xs">
                          {option.label}
                        </TooltipContent>
                      </Tooltip>
                    );
                  })}
                  <span className="mx-1 h-5 w-px bg-border/55" />
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
                        type="button"
                        aria-label="删除高亮"
                        data-reader-record-highlight-action="delete"
                        onClick={handleDeleteHighlight}
                        className="focus-ring grid h-8 w-8 place-items-center rounded-md text-muted-foreground transition-[color,transform] hover:bg-transparent hover:text-rose-600 active:scale-[0.96] focus-visible:text-rose-600"
                      >
                        <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                        <span className="sr-only">删除</span>
                      </button>
                    </TooltipTrigger>
                    <TooltipContent side="top" className="text-xs">
                      删除高亮
                    </TooltipContent>
                  </Tooltip>
                </div>
              </TooltipProvider>
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
              onOpenDetail={activeLookupSnapshot || inspectState ? openDictionaryRail : undefined}
              onSelectCandidate={activeLookupSnapshot ? handleSelectCandidate : undefined}
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
              <button
                type="button"
                className="block w-full rounded-sm px-3 py-1.5 text-left text-sm text-foreground hover:bg-error-red/10"
                onClick={() => handleSubmitFeedback("negative")}
              >
                释义有问题
              </button>
            </ReaderFloatingSurface>
          ) : null}
          <ReaderGrammarInteractionContext.Provider value={grammarInteraction}>
            <ReaderGrammarExpansionProvider controlRef={grammarExpansionControlRef}>
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
                        {editorElement}
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
                    {showSelectionToolbar ? (
                      <ReaderFloatingSurface
                        floatingRef={selectionToolbarFloating.refs.setFloating}
                        style={selectionToolbarFloating.floatingStyles as CSSProperties}
                        chrome="selection-toolbar"
                        className="reader-record-floating-toolbar p-1 [&_[data-slot=separator][data-orientation=vertical]]:h-6 [&_[data-slot=separator][data-orientation=vertical]]:bg-border/80"
                        data-reader-record-floating-toolbar="selection-actions"
                      >
                        <TooltipProvider delayDuration={200}>
                          <Toolbar
                            className="items-center gap-0.5"
                            aria-label="Reader 选区操作"
                          >
                            <ReaderFloatingToolbarButtons />
                          </Toolbar>
                        </TooltipProvider>
                      </ReaderFloatingSurface>
                    ) : null}
                  </ReaderToolbarActionsProvider>
                  {translationState.kind !== "idle" ? (
                    <div
                      data-testid="reader-record-plate-translation-status"
                      data-reader-record-translation-status={translationState.kind}
                      className={`mt-3 text-sm ${
                        translationState.kind === "error"
                          ? "text-rose-700"
                          : translationState.kind === "submitted" &&
                              translationState.outcome === "succeeded"
                            ? "text-emerald-700"
                            : "text-muted-foreground"
                      }`}
                      role="status"
                      aria-live="polite"
                    >
                      {readerSectionTranslationStatusMessage(translationState)}
                    </div>
                  ) : null}
                </ReaderSentenceAnalysisInteractionContext.Provider>
              </ReaderCalloutActionContext.Provider>
            </ReaderGrammarExpansionProvider>
          </ReaderGrammarInteractionContext.Provider>
          {feedbackState.kind !== "idle" ? (
            <div
              data-reader-record-feedback-status={feedbackState.kind}
              className={`mt-3 text-sm ${
                feedbackState.kind === "error"
                  ? "text-rose-700"
                  : feedbackState.kind === "saved"
                    ? "text-emerald-700"
                    : "text-muted-foreground"
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
          </div>
        </div>
      </div>
      <aside className="reader-record-outline-slot">
        <ReaderRecordNavigationRail
          snapshot={snapshot}
          plateDocument={plateDocument}
          askOpen={askSidecarOpen}
          layout="canvas"
        />
      </aside>
      <AiWorkspacePanel
          open={askOpen}
          presentation={surfaceMode}
          surface={askOpen ? effectiveSurface : askSurface}
          layout={askSidecarOpen ? "docked" : "overlay"}
          pageIdentity={askPageIdentity}
          recordId={snapshot.record_id}
          recordTitle={askRecordTitle}
          composer={askComposer}
          onChangeSurface={setAskSurface}
          onToggle={() => setAskOpen((current) => !current)}
          hasSidecarCapacity={hasSidecarCapacity}
          capacityDowngradeNotice={
            showCapacityDowngradeNotice
              ? "当前阅读区较窄，Ask Claread 已暂以浮窗展示；空间恢复后将回到侧边栏。"
              : null
          }
          onDismissCapacityDowngradeNotice={() =>
            setCapacityDowngradeDismissed(true)
          }
          onNavigateAgenticSource={navigateAgenticSource}
        />
        {dictionaryRailVisible ? (
          <div
            className="reader-tool-surface reader-tool-surface--rail reader-record-dictionary-rail--docked fixed top-14 bottom-3 z-40 hidden xl:block"
            data-reader-record-dictionary-rail="docked"
          >
            <ReaderDictionaryRail
              className="h-full"
              lookup={dictionaryPanelLookup}
              inspect={dictionaryPanelInspect}
              history={dictionaryHistory}
              readingGoal={snapshot.record.reading_goal}
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
              canSaveVocabulary={Boolean(dictionaryPanelLookup?.contextSentence.trim())}
              canCreateAINote={false}
              onAttachToAsk={(intent) =>
                openAskPanel(askAttachmentFromVocabularyInspect(askPageIdentity, intent))
              }
              onSelectHistory={handleSelectHistory}
            />
          </div>
        ) : null}
        {dictionaryRailVisible ? (
          <div
            className="reader-tool-surface reader-tool-surface--compact fixed inset-x-3 bottom-3 z-50 flex max-h-[72vh] flex-col xl:hidden"
            data-reader-record-dictionary-rail="sheet"
          >
            <ReaderDictionaryRail
              lookup={dictionaryPanelLookup}
              inspect={dictionaryPanelInspect}
              history={dictionaryHistory}
              readingGoal={snapshot.record.reading_goal}
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
              canSaveVocabulary={Boolean(dictionaryPanelLookup?.contextSentence.trim())}
              canCreateAINote={false}
              onAttachToAsk={(intent) =>
                openAskPanel(askAttachmentFromVocabularyInspect(askPageIdentity, intent))
              }
              onSelectHistory={handleSelectHistory}
            />
          </div>
        ) : null}
        </section>
      </div>
    </div>
  );
}
