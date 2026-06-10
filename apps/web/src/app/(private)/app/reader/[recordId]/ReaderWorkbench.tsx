"use client";

import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  buildMultiTextTargetKey,
  buildSentenceTargetKey,
  buildTextRangeTargetKey,
  TEXT_RANGE_HASH_ALGORITHM,
  TEXT_RANGE_OFFSET_UNIT,
  USER_ANNOTATION_COLORS,
} from "@claread/contracts";
import {
  Type,
  X,
  Heart,
  Eye,
  BookOpen,
  SlidersHorizontal,
  Globe,
  Sparkles,
} from "lucide-react";
import { useSearchParams } from "next/navigation";

import type { ReaderRecordVm } from "@/adapters/records.adapter";
import {
  AiWorkspacePanel,
  ReaderContextPanel,
  ReaderDictionaryRail,
  ReaderGlobalFeedbackPrompt,
  ImmersiveReaderSurface,
  IntensiveReaderSurface,
  ReaderNotePanel,
  ReaderQuickPeek,
  ReaderSettingsPanel,
  SelectionToolbar,
  defaultReaderSettings,
  modeShowsTranslation,
  modeVisibility,
  persistReaderSettings,
  readStoredReaderSettings,
  readerModeTypography,
  readerThemeClassName,
  type ReaderSettingsState,
  textRangeAnchorAttributes,
  useReaderFloatingLayer,
} from "@/components/reader";
import { useAppearance } from "@/components/providers/appearance-provider";
import {
  buildWebPreferencesFromLocal,
  isWebPreferencesSyncReady,
  syncWebPreferencesToCloud,
} from "@/lib/web-preferences-sync";
import {
  type WebPreferences,
  WEB_PREFERENCES_APPLIED_EVENT,
  WEB_PREFERENCES_SYNC_READY_EVENT,
} from "@/lib/web-preferences";
import {
  askAttachmentFromAnnotation,
  askAttachmentFromAnalysisBlock,
  askAttachmentFromContentSummary,
  askAttachmentFromReaderNote,
  askAttachmentFromSelection,
  askAttachmentFromSentence,
  askAttachmentFromStructuredInspect,
  askAttachmentFromTranslation,
  askAttachmentKey,
  annotationMatchesSelection,
  annotationOverlapsSelection,
  annotationRequestFromAnchorPayload,
  annotationToTargetRef,
  anchorPayloadFromSelection,
  anchorPayloadFromSentence,
  copyDomRect,
  hashAnchorText,
  jumpToAnchorPayload,
  jumpTargetFromAskAttachment,
  jumpTargetFromAskCitation,
  jumpToTargetKey,
  jumpToTargetRef,
  lookupIntentFromSelection,
  lookupIntentFromStructuredInspect,
  projectReaderAssets,
  readPlateReaderSelection,
  readerLookupSnapshotFromIntent,
  rectForTextOffsets,
  renderSceneToPlateDocument,
  type ReaderLookupIntent,
  type ReaderLookupPreviewAnchor,
  type ReaderStructuredInspectIntent,
  selectionToolbarRectForReaderSelection,
  sentenceToTargetRef,
  targetKeyForSelection,
  textOffsetWithinElement,
  type ReaderAskAttachment,
  type ReaderAskPageIdentity,
  type ReaderContentSummaryNode,
  type ReaderAssetProjection,
  type ReaderJumpRangeSegment,
  type ReaderSelectionSegment,
  type ReaderJumpTarget,
  type ReaderTextSelection,
} from "@/lib/reader-plate";
import type {
  UserAnnotationColorDto,
  WebAnnotationCreateRequest,
  WebAnnotationVm,
} from "@/types/api/annotations";
import type { ReaderAskCitationDto } from "@/types/api/reader-ask";
import type { WebReaderNoteCreateRequest, WebReaderNoteVm } from "@/types/api/reader-notes";
import type { WebDictResult } from "@/types/api/dict";
import type {
  DictionaryAIViewState,
  WebDictAIErrorResult,
  WebDictAIResult,
  WebDictAIRequest,
} from "@/types/api/dict-ai";
import type {
  ReaderVocabularyLookupResponseDto,
  VocabularyCreateRequestDto,
} from "@/types/api/vocabulary";
import type { SentenceEntryModel, SentenceModel } from "@/types/view/ReaderMockVm";
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
} from "@/components/reader/dictionary/contracts";
import {
  createDictionaryAICacheEntry,
  dictionaryAICacheKey,
  dictionaryAIContextKey,
  dictionaryAIRequestForLookup,
  dictionaryAIRequestKey,
  dictionaryAIViewStateFromCacheEntry,
  dictionaryLookupBase,
  dictionaryLookupHistoryKey,
  dictionaryLookupSupportsExactAINote,
  dictionaryPreferredAIMode,
  persistDictionaryAIArticleCache,
  readStoredDictionaryAIArticleCache,
  type DictionaryAIArticleCache,
} from "@/components/reader/dictionary";
import {
  buildOptimisticLookupMatch,
  getLookupSaveState,
  lookupSaveCacheKey,
  lookupSaveRequestFromSnapshot,
  type LookupSaveState,
  type ReaderVocabularyLookupMatch,
} from "@/components/reader/dictionary/lookupSaveState";
import { readerCommandControl, readerSegmentedOption } from "@/components/reader/interaction";
import { cn } from "@/lib/cn";
import { FavoriteButton } from "./FavoriteButton";
import { FeedbackSheet, FEEDBACK_CONFIG_BY_SCOPE } from "@/components/reader/FeedbackSheet";
import type { FeedbackScopeDto, FeedbackSentimentDto, FeedbackTypeDto } from "@/types/api/feedback";

type ReaderDataSource = "upstream-render-scene" | "upstream-source-text";

interface ReaderWorkbenchProps {
  record: ReaderRecordVm;
  dataSource: ReaderDataSource;
  message?: string;
  initialAnnotations: WebAnnotationVm[];
  initialReaderNotes?: WebReaderNoteVm[];
}

type AnnotationSaveState =
  | { kind: "idle" }
  | { kind: "saving"; message?: string }
  | { kind: "saved"; message: string }
  | { kind: "error"; message: string };

type PendingReaderNoteSource = "selection" | "sentence";
type ReaderSelectionSource = "dom" | "programmatic" | "none";
type ReaderSelectionVisualMode = "selection" | "context" | "annotation_hover";

const dataSourceLabel: Record<ReaderDataSource, string> = {
  "upstream-render-scene": "解析结果",
  "upstream-source-text": "原文回退",
};

const annotationColorValues = [...USER_ANNOTATION_COLORS];

function isUserAnnotationColor(value: string): value is UserAnnotationColorDto {
  return annotationColorValues.includes(value as UserAnnotationColorDto);
}

function isEditableKeyboardTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) {
    return false;
  }

  const tagName = target.tagName;
  return (
    target.isContentEditable ||
    tagName === "INPUT" ||
    tagName === "TEXTAREA" ||
    tagName === "SELECT" ||
    target.closest("[contenteditable='true']") !== null
  );
}

function belongsToCurrentRecord(candidateRecordId: string | null | undefined, targetKey: string, recordId: string) {
  if (candidateRecordId === recordId) {
    return true;
  }

  if (candidateRecordId !== null) {
    return false;
  }

  return targetKey.startsWith(`record:${recordId}:`);
}

function mergedSelectionText(segments: ReaderSelectionSegment[]) {
  return segments
    .map((segment) => segment.selectedText.trim())
    .filter(Boolean)
    .join(" ")
    .trim();
}

function mergeSelectionSegments(
  sentenceById: ReadonlyMap<string, SentenceModel>,
  sentenceOrderById: ReadonlyMap<string, number>,
  segments: ReaderSelectionSegment[],
): ReaderSelectionSegment[] {
  const grouped = new Map<string, Array<{ startOffset: number; endOffset: number }>>();

  segments.forEach((segment) => {
    const current = grouped.get(segment.sentenceId) ?? [];
    current.push({ startOffset: segment.startOffset, endOffset: segment.endOffset });
    grouped.set(segment.sentenceId, current);
  });

  return Array.from(grouped.entries())
    .sort(([leftId], [rightId]) => {
      const leftOrder = sentenceOrderById.get(leftId);
      const rightOrder = sentenceOrderById.get(rightId);
      if (typeof leftOrder === "number" && typeof rightOrder === "number") {
        return leftOrder - rightOrder;
      }
      return leftId.localeCompare(rightId);
    })
    .flatMap(([sentenceId, ranges]) => {
      const sentence = sentenceById.get(sentenceId);
      if (!sentence) {
        return [];
      }

      const mergedRanges = ranges
        .sort((left, right) => left.startOffset - right.startOffset)
        .reduce<Array<{ startOffset: number; endOffset: number }>>((current, range) => {
          const previous = current[current.length - 1];
          if (!previous) {
            current.push({ ...range });
            return current;
          }

          if (range.startOffset <= previous.endOffset) {
            previous.endOffset = Math.max(previous.endOffset, range.endOffset);
            return current;
          }

          current.push({ ...range });
          return current;
        }, []);

      return mergedRanges.map((range) => {
        const selectedText = sentence.text.slice(range.startOffset, range.endOffset);
        return {
          paragraphId: sentence.paragraphId,
          sentenceId,
          sentence,
          selectedText,
          startOffset: range.startOffset,
          endOffset: range.endOffset,
          textHash: hashAnchorText(selectedText),
        } satisfies ReaderSelectionSegment;
      });
    });
}

function noteRequestFromSentence(recordId: string, sentence: SentenceModel): WebReaderNoteCreateRequest {
  return {
    recordId,
    quoteMode: "sentence",
    anchorSentenceId: sentence.sentenceId,
    paragraphId: sentence.paragraphId,
    sentenceId: sentence.sentenceId,
    selectedText: sentence.text,
    noteText: "",
  };
}

function noteRequestFromSelection(recordId: string, selection: ReaderTextSelection): WebReaderNoteCreateRequest {
  if (selection.anchorType === "sentence") {
    return noteRequestFromSentence(recordId, selection.sentence);
  }

  if (selection.anchorType === "multi_text") {
    const firstSegment = selection.segments[0];
    return {
      recordId,
      quoteMode: "multi_text",
      anchorSentenceId: firstSegment?.sentenceId ?? selection.sentence.sentenceId,
      paragraphId: firstSegment?.paragraphId ?? selection.sentence.paragraphId,
      sentenceId: firstSegment?.sentenceId ?? selection.sentence.sentenceId,
      selectedText: selection.selectedText,
      segments: selection.segments.map((segment) => ({
        paragraphId: segment.paragraphId ?? null,
        sentenceId: segment.sentenceId,
        selectedText: segment.selectedText,
        startOffset: segment.startOffset,
        endOffset: segment.endOffset,
        textHash: segment.textHash,
      })),
      noteText: "",
    };
  }

  return {
    recordId,
    quoteMode: "text_range",
    anchorSentenceId: selection.sentence.sentenceId,
    paragraphId: selection.sentence.paragraphId,
    sentenceId: selection.sentence.sentenceId,
    selectedText: selection.selectedText,
    startOffset: selection.startOffset,
    endOffset: selection.endOffset,
    textHash: selection.textHash,
    noteText: "",
  };
}

function noteTargetKeyFromRequest(request: WebReaderNoteCreateRequest) {
  if (request.quoteMode === "sentence") {
    return request.sentenceId ? buildSentenceTargetKey(request.recordId, request.sentenceId) : "";
  }

  if (request.quoteMode === "text_range") {
    if (
      !request.sentenceId ||
      typeof request.startOffset !== "number" ||
      typeof request.endOffset !== "number" ||
      !request.textHash
    ) {
      return "";
    }
    return buildTextRangeTargetKey(
      request.recordId,
      request.sentenceId,
      request.startOffset,
      request.endOffset,
      request.textHash,
    );
  }

  const segments = request.segments ?? [];
  if (segments.length < 2) {
    return "";
  }
  return buildMultiTextTargetKey(
    request.recordId,
    segments.map((segment) => ({
      sentenceId: segment.sentenceId,
      selectedText: segment.selectedText,
      startOffset: segment.startOffset,
      endOffset: segment.endOffset,
      textHash: segment.textHash,
      paragraphId: segment.paragraphId ?? null,
    })),
  );
}

function readerNoteJumpTarget(note: WebReaderNoteVm): ReaderJumpTarget | null {
  return jumpToAnchorPayload({
    anchorType: note.quoteMode,
    targetKey: note.targetKey,
    recordId: note.recordId,
    paragraphId: note.paragraphId,
    sentenceId: note.sentenceId ?? note.anchorSentenceId,
    selectedText: note.selectedText,
    startOffset: note.startOffset,
    endOffset: note.endOffset,
    textHash: note.textHash,
    segments:
      note.quoteMode === "multi_text"
        ? note.segments.map((segment) => ({
            paragraphId: segment.paragraphId ?? null,
            sentenceId: segment.sentenceId,
            selectedText: segment.selectedText,
            startOffset: segment.startOffset,
            endOffset: segment.endOffset,
            textHash: segment.textHash,
          }))
        : undefined,
    metadata: {
      source: "reader_note",
      originType: note.quoteMode,
      offsetUnit: TEXT_RANGE_OFFSET_UNIT,
      textHashAlgorithm: TEXT_RANGE_HASH_ALGORITHM,
    },
  });
}

function readerNoteJumpTargetFromRequest(request: WebReaderNoteCreateRequest): ReaderJumpTarget | null {
  const targetKey = noteTargetKeyFromRequest(request);
  if (!targetKey) {
    return null;
  }
  return jumpToAnchorPayload({
    anchorType: request.quoteMode,
    targetKey,
    recordId: request.recordId,
    paragraphId: request.paragraphId ?? null,
    sentenceId: request.sentenceId ?? request.anchorSentenceId,
    selectedText: request.selectedText,
    startOffset: request.startOffset ?? null,
    endOffset: request.endOffset ?? null,
    textHash: request.textHash ?? null,
    segments:
      request.quoteMode === "multi_text"
        ? (request.segments ?? []).map((segment) => ({
            paragraphId: segment.paragraphId ?? null,
            sentenceId: segment.sentenceId,
            selectedText: segment.selectedText,
            startOffset: segment.startOffset,
            endOffset: segment.endOffset,
            textHash: segment.textHash,
          }))
        : undefined,
    metadata: {
      source: "reader_note_request",
      originType: request.quoteMode,
      offsetUnit: TEXT_RANGE_OFFSET_UNIT,
      textHashAlgorithm: TEXT_RANGE_HASH_ALGORITHM,
    },
  });
}

function entryLabel(entry: SentenceEntryModel) {
  if (entry.entryType === "grammar_note") {
    return "语法旁注";
  }
  if (entry.entryType === "sentence_analysis") {
    return "句子拆解";
  }
  return entry.label || "解析";
}

type LookupBase = Omit<DictionaryLookupSnapshot, "state">;
type LookupPreviewAnchor = ReaderLookupPreviewAnchor;
type DictionaryDockLayout = {
  left: number;
  width: number;
};

function lookupIntentFromSnapshotBase(base: LookupBase): ReaderLookupIntent {
  return {
    kind: base.label === "选区查词" ? "manual_span_lookup" : "lexical_lookup",
    query: base.query,
    lookupType: base.lookupType,
    sentenceId: base.sentenceId,
    contextSentence: base.contextSentence,
    sourceContext: base.sourceContext,
    anchorOffsets: base.anchorOffsets,
    anchorText: base.anchorText,
    occurrence: base.occurrence,
    title: base.title,
    label: base.label,
    annotationType: base.annotationType,
    visualTone: base.visualTone,
    glossary: base.glossary,
  };
}

function shouldShowLookupPreview() {
  if (typeof window === "undefined") {
    return false;
  }

  return window.matchMedia("(min-width: 768px)").matches;
}

function caretRangeFromPoint(clientX: number, clientY: number): Range | null {
  const doc = document as Document & {
    caretRangeFromPoint?: (x: number, y: number) => Range | null;
    caretPositionFromPoint?: (
      x: number,
      y: number,
    ) => { offsetNode: Node; offset: number } | null;
  };

  const legacyRange = doc.caretRangeFromPoint?.(clientX, clientY);
  if (legacyRange) {
    return legacyRange;
  }

  const position = doc.caretPositionFromPoint?.(clientX, clientY);
  if (!position) {
    return null;
  }

  const range = document.createRange();
  range.setStart(position.offsetNode, position.offset);
  range.collapse(true);
  return range;
}

function pushDictionaryAINoteLine(lines: string[], label: string, value?: string | null) {
  const normalized = value?.trim();
  if (!normalized) {
    return;
  }

  lines.push(`${label}：${normalized}`);
}

function dictionaryAINoteText(result: WebDictAIResult) {
  const lines = [result.mode === "context_explain" ? "AI 语境解读" : "AI 未验证词条", result.summary.trim()];

  if (result.mode === "context_explain") {
    pushDictionaryAINoteLine(lines, "词义", result.bestFitSense);
    pushDictionaryAINoteLine(lines, "语境", result.whyHere);
    pushDictionaryAINoteLine(lines, "线索", result.cue);
    pushDictionaryAINoteLine(lines, "译法", result.translation);
    pushDictionaryAINoteLine(lines, "易混", result.contrast);
    pushDictionaryAINoteLine(lines, "记忆点", result.learningTip);
    return lines.filter(Boolean).join("\n");
  }

  pushDictionaryAINoteLine(lines, "分类", result.classification);
  if (result.kind === "ai_entry") {
    pushDictionaryAINoteLine(lines, "建议词条", result.entry.word);
  }
  if (result.kind === "ai_unresolved") {
    pushDictionaryAINoteLine(lines, "原因", result.reason);
  }
  if (result.suggestedQuery.length > 0) {
    lines.push(`建议改查：${result.suggestedQuery.join(" / ")}`);
  }

  return lines.filter(Boolean).join("\n");
}

function dictionaryAINotePayload(lookup: DictionaryLookupSnapshot, result: WebDictAIResult) {
  return {
    source: "dictionary_ai",
    mode: result.mode,
    query: result.query,
    anchorText: lookup.anchorText,
    sentenceId: lookup.sentenceId,
    occurrence: lookup.occurrence ?? null,
    summary: result.summary,
    generatedAt: new Date().toISOString(),
    ...(result.mode === "context_explain"
      ? {
          bestFitSense: result.bestFitSense ?? null,
        }
      : {
          classification: result.classification,
          resultKind: result.kind,
          suggestedQuery: result.suggestedQuery,
        }),
  };
}

function dictionaryAINoteRequestFromLookup(
  lookup: DictionaryLookupSnapshot,
  sentence: SentenceModel,
  result: WebDictAIResult,
): WebReaderNoteCreateRequest | null {
  if (!lookup.anchorOffsets || !lookup.textHash) {
    return null;
  }

  return {
    recordId: lookup.recordId,
    quoteMode: "text_range",
    anchorSentenceId: lookup.sentenceId,
    paragraphId: sentence.paragraphId,
    sentenceId: lookup.sentenceId,
    selectedText: lookup.anchorText,
    startOffset: lookup.anchorOffsets.startOffset,
    endOffset: lookup.anchorOffsets.endOffset,
    textHash: lookup.textHash,
    noteText: dictionaryAINoteText(result),
    payloadJson: dictionaryAINotePayload(lookup, result),
  };
}

function isDictionaryAIErrorResult(value: unknown): value is WebDictAIErrorResult {
  if (!value || typeof value !== "object") {
    return false;
  }

  const payload = value as Record<string, unknown>;
  return (
    payload.kind === "error" &&
    typeof payload.query === "string" &&
    typeof payload.status === "number" &&
    typeof payload.code === "string" &&
    typeof payload.message === "string"
  );
}

export function ReaderWorkbench({
  record,
  dataSource,
  message,
  initialAnnotations,
  initialReaderNotes = [],
}: ReaderWorkbenchProps) {
  const [readerScene, setReaderScene] = useState(record.reader);
  const reader = readerScene;
  const searchParams = useSearchParams();
  const [activeLookup, setActiveLookup] = useState<DictionaryLookupSnapshot | null>(null);
  const [activeInspect, setActiveInspect] = useState<ReaderStructuredInspectIntent | null>(null);
  const [lookupPreviewOpen, setLookupPreviewOpen] = useState(false);
  const [lookupPreviewAnchor, setLookupPreviewAnchor] = useState<ReaderLookupPreviewAnchor | null>(null);
  const [lookupPreviewEpoch, setLookupPreviewEpoch] = useState(0);
  const [lookupHistory, setLookupHistory] = useState<DictionaryLookupSnapshot[]>([]);
  const [dictionaryAICache, setDictionaryAICache] = useState<DictionaryAIArticleCache>(() =>
    readStoredDictionaryAIArticleCache(record.id),
  );
  const [dictionarySaveState, setDictionarySaveState] = useState<SaveState>({ kind: "idle" });
  const [savedVocabularyMatches, setSavedVocabularyMatches] = useState<Record<string, ReaderVocabularyLookupMatch | null>>({});
  const [annotations, setAnnotations] = useState(initialAnnotations);
  const [readerNotes, setReaderNotes] = useState(initialReaderNotes);
  const [jumpTarget, setJumpTarget] = useState<ReaderJumpTarget | null>(null);
  const [focusedReaderNoteTarget, setFocusedReaderNoteTarget] = useState<ReaderJumpTarget | null>(null);
  const [activeSentence, setActiveSentence] = useState<SentenceModel | null>(null);
  const [textSelection, setTextSelection] = useState<ReaderTextSelection | null>(null);
  const [contextPanelOpen, setContextPanelOpen] = useState(false);
  const [sentencePopoverAnchorEl, setSentencePopoverAnchorEl] = useState<HTMLElement | null>(null);
  const [settingsPanelOpen, setSettingsPanelOpen] = useState(false);
  const [expandedAnalysisEntryIds, setExpandedAnalysisEntryIds] = useState<string[]>([]);
  const sentencePopoverPanelRef = useRef<HTMLDivElement | null>(null);
  const lookupPreviewPanelRef = useRef<HTMLDivElement | null>(null);
  const lastSentencePopoverTriggerRef = useRef<HTMLElement | null>(null);
  const lastLookupTriggerRef = useRef<HTMLElement | null>(null);
  const [activeEntryId, setActiveEntryId] = useState<string | null>(null);
  const [annotationColor, setAnnotationColor] = useState<UserAnnotationColorDto>("warm_yellow");
  const [annotationSaveState, setAnnotationSaveState] = useState<AnnotationSaveState>({ kind: "idle" });
  const [highlightPaletteOpen, setHighlightPaletteOpen] = useState(false);
  const [textSelectionSource, setTextSelectionSource] = useState<ReaderSelectionSource>("none");
  const [textSelectionVisualMode, setTextSelectionVisualMode] = useState<ReaderSelectionVisualMode>("selection");
  const [selectionToolbarVisible, setSelectionToolbarVisible] = useState(false);
  const [activeReaderNoteId, setActiveReaderNoteId] = useState<string | null>(null);
  const [pendingReaderNote, setPendingReaderNote] = useState<WebReaderNoteCreateRequest | null>(null);
  const [pendingReaderNoteSource, setPendingReaderNoteSource] = useState<PendingReaderNoteSource | null>(null);
  const [readerNoteDraft, setReaderNoteDraft] = useState("");
  const [readerNoteSaveState, setReaderNoteSaveState] = useState<AnnotationSaveState>({ kind: "idle" });
  const [notePanelOpen, setNotePanelOpen] = useState(false);
  const [hoveredAnnotationTargetKey, setHoveredAnnotationTargetKey] = useState<string | null>(null);
  const [activeAnnotationTargetKey, setActiveAnnotationTargetKey] = useState<string | null>(null);
  const [readerSettings, setReaderSettings] = useState<ReaderSettingsState>(() =>
    readStoredReaderSettings(),
  );
  const [immersiveHeaderHidden, setImmersiveHeaderHidden] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  const [askAttachments, setAskAttachments] = useState<ReaderAskAttachment[]>([]);
  const [liveContextSelection, setLiveContextSelection] = useState<ReaderTextSelection | null>(null);
  const [composerTextareaFocused, setComposerTextareaFocused] = useState(false);
  const [pendingAskQuickAction, setPendingAskQuickAction] = useState<{
    content: string;
    entryAction: "ask_about_this" | "explain_this" | "why_here" | "lookup_in_context";
    attachments: ReaderAskAttachment[];
  } | null>(null);
  const [dictionaryPinned, setDictionaryPinned] = useState(false);
  const [dictionaryRailOpen, setDictionaryRailOpen] = useState(false);
  const [dictionaryQuery, setDictionaryQuery] = useState("");
  const [dictionarySearchExpanded, setDictionarySearchExpanded] = useState(false);
  const [dictionaryAI, setDictionaryAI] = useState<DictionaryAIViewState>({ kind: "idle" });
  const [dictionaryAIPanelOpen, setDictionaryAIPanelOpen] = useState(false);
  const [dictionaryAINoteState, setDictionaryAINoteState] = useState<SaveState>({ kind: "idle" });
  const [feedbackSheet, setFeedbackSheet] = useState<{
    open: boolean;
    scope: FeedbackScopeDto;
    sentiment?: FeedbackSentimentDto;
    feedbackType?: FeedbackTypeDto;
    analysisRecordId?: string;
    targetId: string;
    annotationType?: string;
    contextSummary?: string;
    contextJson?: Record<string, unknown>;
    clientSurface?: string;
    entryPoint?: string;
  } | null>(null);
  const activeAnnotationTargetKeyRef = useRef<string | null>(null);
  const readerSettingsHydratedRef = useRef(false);
  const skipNextReaderSettingsSyncRef = useRef(false);
  const settingsButtonRef = useRef<HTMLButtonElement | null>(null);
  const [settingsFloatingStyle, setSettingsFloatingStyle] = useState<CSSProperties | null>(null);
  const [webPreferencesSyncReady, setWebPreferencesSyncReady] = useState(() => isWebPreferencesSyncReady());

  useEffect(() => {
    queueMicrotask(() => setReaderScene(record.reader));
  }, [record.reader]);
  useEffect(() => {
    queueMicrotask(() => setDictionaryAICache(readStoredDictionaryAIArticleCache(record.id)));
  }, [record.id]);
  useEffect(() => {
    persistDictionaryAIArticleCache(record.id, dictionaryAICache);
  }, [dictionaryAICache, record.id]);
  const articleRef = useRef<HTMLElement | null>(null);
  const readingColumnRef = useRef<HTMLDivElement | null>(null);
  const focusedRouteTargetKeyRef = useRef<string | null>(null);
  const dictionaryAIRequestKeyRef = useRef<string | null>(null);
  const textSelectionSourceRef = useRef<ReaderSelectionSource>("none");
  const pointerSelectionActiveRef = useRef(false);
  const [dictionaryDockLayout, setDictionaryDockLayout] = useState<DictionaryDockLayout | null>(null);
  const dictionaryPanelVisible = Boolean(dictionaryRailOpen || dictionaryPinned);
  const { themeName, setThemeName } = useAppearance();

  const {
    refs: {
      setFloating: setSelectionToolbarFloating,
      setPositionReference: setSelectionToolbarReference,
    },
    floatingStyles: selectionToolbarStyles,
  } = useReaderFloatingLayer({
    open: Boolean(textSelection && selectionToolbarVisible),
    placement: "top-start",
    offsetPx: 14,
    crossAxisOffsetPx: 28,
    strategy: "fixed",
  });
  const {
    refs: {
      setFloating: setLookupPreviewFloating,
      setPositionReference: setLookupPreviewReference,
    },
    floatingStyles: lookupPreviewStyles,
  } = useReaderFloatingLayer({
    open: Boolean((activeLookup || activeInspect) && lookupPreviewOpen && lookupPreviewAnchor),
    placement: "top",
    offsetPx: 12,
    strategy: "fixed",
  });
  const {
    refs: {
      setFloating: setSentencePopoverFloating,
      setPositionReference: setSentencePopoverReference,
    },
    floatingStyles: sentencePopoverStyles,
  } = useReaderFloatingLayer({
    open: Boolean(contextPanelOpen && activeSentence && sentencePopoverAnchorEl),
    placement: "bottom-end",
    offsetPx: 10,
    crossAxisOffsetPx: 8,
    strategy: "fixed",
  });

  useEffect(() => {
    if (!readerSettingsHydratedRef.current || !webPreferencesSyncReady) {
      return;
    }

    if (skipNextReaderSettingsSyncRef.current) {
      skipNextReaderSettingsSyncRef.current = false;
      return;
    }

    persistReaderSettings(readerSettings);
    try {
      const prefs = buildWebPreferencesFromLocal();
      prefs.reader_mode = readerSettings.mode;
      prefs.font_family = readerSettings.fontFamily;
      prefs.font_scale = readerSettings.fontScale;
      prefs.updated_at = new Date().toISOString();
      syncWebPreferencesToCloud(prefs);
    } catch {}
  }, [readerSettings, webPreferencesSyncReady]);

  useEffect(() => {
    readerSettingsHydratedRef.current = true;
  }, []);

  useEffect(() => {
    if (isWebPreferencesSyncReady()) {
      setWebPreferencesSyncReady(true);
      return;
    }

    function handleSyncReady() {
      setWebPreferencesSyncReady(true);
    }

    window.addEventListener(WEB_PREFERENCES_SYNC_READY_EVENT, handleSyncReady);
    return () => {
      window.removeEventListener(WEB_PREFERENCES_SYNC_READY_EVENT, handleSyncReady);
    };
  }, []);

  useEffect(() => {
    function handleApplied(event: Event) {
      const prefs = (event as CustomEvent<WebPreferences>).detail;
      if (!prefs) {
        return;
      }

      skipNextReaderSettingsSyncRef.current = true;
      setReaderSettings({
        mode: prefs.reader_mode,
        fontFamily: prefs.font_family,
        fontScale: prefs.font_scale,
        updatedAt: prefs.updated_at || undefined,
      });
    }

    window.addEventListener(WEB_PREFERENCES_APPLIED_EVENT, handleApplied as EventListener);
    return () => {
      window.removeEventListener(WEB_PREFERENCES_APPLIED_EVENT, handleApplied as EventListener);
    };
  }, []);

  useEffect(() => {
    if (readerSettings.mode !== "immersive") {
      queueMicrotask(() => setImmersiveHeaderHidden(false));
      return;
    }

    let lastScrollY = window.scrollY;

    const updateHeaderState = () => {
      const currentScrollY = window.scrollY;

      // Autohide: hide when scrolling down past 260px, show when scrolling up or at top
      if (currentScrollY > 260 && currentScrollY > lastScrollY) {
        setImmersiveHeaderHidden(true);
      } else if (currentScrollY < lastScrollY || currentScrollY < 60) {
        setImmersiveHeaderHidden(false);
      }

      lastScrollY = currentScrollY;
    };

    updateHeaderState();
    window.addEventListener("scroll", updateHeaderState, { passive: true });
    window.addEventListener("resize", updateHeaderState);
    return () => {
      window.removeEventListener("scroll", updateHeaderState);
      window.removeEventListener("resize", updateHeaderState);
    };
  }, [readerSettings.mode]);

  useEffect(() => {
    if (!settingsPanelOpen) {
      queueMicrotask(() => setSettingsFloatingStyle(null));
      return;
    }

    const updateSettingsPanelPosition = () => {
      if (typeof window === "undefined") {
        return;
      }

      if (window.innerWidth < 768 || !settingsButtonRef.current) {
        setSettingsFloatingStyle(null);
        return;
      }

      const rect = settingsButtonRef.current.getBoundingClientRect();
      const panelWidth = Math.min(360, Math.max(window.innerWidth - 40, 320));
      const maxLeft = Math.max(24, window.innerWidth - panelWidth - 24);
      const left = Math.min(maxLeft, Math.max(24, rect.right - panelWidth));

      setSettingsFloatingStyle({
        left,
        top: rect.bottom + 12,
        bottom: "auto",
        width: panelWidth,
      });
    };

    updateSettingsPanelPosition();
    window.addEventListener("resize", updateSettingsPanelPosition);
    window.addEventListener("scroll", updateSettingsPanelPosition, true);

    return () => {
      window.removeEventListener("resize", updateSettingsPanelPosition);
      window.removeEventListener("scroll", updateSettingsPanelPosition, true);
    };
  }, [settingsPanelOpen]);

  useEffect(() => {
    if (!contextPanelOpen || !sentencePopoverAnchorEl) {
      setSentencePopoverReference(null);
      return;
    }

    const updateReference = () => {
      setSentencePopoverReference({
        contextElement: sentencePopoverAnchorEl,
        getBoundingClientRect: () => sentencePopoverAnchorEl.getBoundingClientRect(),
      });
    };

    updateReference();

    const handleWindowChange = () => updateReference();
    window.addEventListener("resize", handleWindowChange);
    window.addEventListener("scroll", handleWindowChange, true);

    return () => {
      window.removeEventListener("resize", handleWindowChange);
      window.removeEventListener("scroll", handleWindowChange, true);
    };
  }, [contextPanelOpen, sentencePopoverAnchorEl, setSentencePopoverReference]);

  useEffect(() => {
    if (!contextPanelOpen) {
      return;
    }

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target instanceof HTMLElement ? event.target : null;
      if (!target) {
        return;
      }
      if (
        target.closest("[data-reader-sentence-popover='true']") ||
        target.closest("[data-reader-sentence-handle='true']") ||
        target.closest("[data-reader-sentence-rail='true']")
      ) {
        return;
      }
      closeContextPanel();
    };

    window.addEventListener("pointerdown", handlePointerDown);
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown);
    };
  }, [contextPanelOpen]);

  useEffect(() => {
    if (!(contextPanelOpen && activeSentence)) {
      return;
    }

    const frame = window.requestAnimationFrame(() => {
      const firstAction = sentencePopoverPanelRef.current?.querySelector<HTMLElement>(
        "button, textarea, [href], [tabindex]:not([tabindex='-1'])",
      );
      firstAction?.focus();
    });

    return () => window.cancelAnimationFrame(frame);
  }, [activeSentence, contextPanelOpen]);

  useEffect(() => {
    if (!lookupPreviewOpen || (!activeLookup && !activeInspect)) {
      return;
    }

    const frame = window.requestAnimationFrame(() => {
      const closeButton = lookupPreviewPanelRef.current?.querySelector<HTMLElement>("button");
      closeButton?.focus();
    });

    return () => window.cancelAnimationFrame(frame);
  }, [activeInspect, activeLookup, lookupPreviewOpen]);

  const translationBySentence = useMemo(
    () => new Map(reader.translations.map((item) => [item.sentenceId, item.translationZh])),
    [reader.translations],
  );
  const translationModelBySentence = useMemo(
    () => new Map(reader.translations.map((item) => [item.sentenceId, item])),
    [reader.translations],
  );

  const sentenceById = useMemo(
    () => new Map(reader.article.sentences.map((sentence) => [sentence.sentenceId, sentence])),
    [reader.article.sentences],
  );
  const sentenceOrderById = useMemo(
    () => new Map(reader.article.sentences.map((sentence, index) => [sentence.sentenceId, index])),
    [reader.article.sentences],
  );
  const sentenceTextById = useMemo(
    () => new Map(reader.article.sentences.map((sentence) => [sentence.sentenceId, sentence.text])),
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

  const activeEntry = useMemo(() => {
    if (!activeEntryId) {
      return null;
    }
    return reader.sentenceEntries.find((entry) => entry.id === activeEntryId) ?? null;
  }, [activeEntryId, reader.sentenceEntries]);

  const assetProjection: ReaderAssetProjection = useMemo(
    () =>
      projectReaderAssets({
        annotations,
        recordId: record.id,
      }),
    [annotations, record.id],
  );

  const annotationsBySentence = assetProjection.sentenceAssetSummaryBySentence;
  const activeSentenceAnnotations = activeSentence
    ? annotationsBySentence.get(activeSentence.sentenceId)?.annotations ?? []
    : [];
  const readerNotesByTargetKey = useMemo(
    () => new Map(readerNotes.map((note) => [note.targetKey, note])),
    [readerNotes],
  );
  const activeReaderNote = useMemo(
    () => readerNotes.find((note) => note.id === activeReaderNoteId) ?? null,
    [activeReaderNoteId, readerNotes],
  );
  const readerNotesBySentence = useMemo(() => {
    const notesBySentence = new Map<string, WebReaderNoteVm[]>();
    readerNotes.forEach((note) => {
      const current = notesBySentence.get(note.anchorSentenceId) ?? [];
      notesBySentence.set(note.anchorSentenceId, [...current, note]);
    });

    notesBySentence.forEach((notes, sentenceId) => {
      const sentence = sentenceById.get(sentenceId);
      if (!sentence) {
        return;
      }
      const sortedNotes = [...notes].sort((left, right) => {
        const leftStart = left.quoteMode === "sentence" ? 0 : (left.startOffset ?? left.segments[0]?.startOffset ?? 0);
        const rightStart = right.quoteMode === "sentence" ? 0 : (right.startOffset ?? right.segments[0]?.startOffset ?? 0);
        if (leftStart !== rightStart) {
          return leftStart - rightStart;
        }
        const leftLength =
          left.quoteMode === "sentence"
            ? sentence.text.length
            : (left.endOffset ?? left.segments.at(-1)?.endOffset ?? 0) - leftStart;
        const rightLength =
          right.quoteMode === "sentence"
            ? sentence.text.length
            : (right.endOffset ?? right.segments.at(-1)?.endOffset ?? 0) - rightStart;
        if (leftLength !== rightLength) {
          return leftLength - rightLength;
        }
        return left.createdAt.localeCompare(right.createdAt);
      });
      notesBySentence.set(sentenceId, sortedNotes);
    });

    return notesBySentence;
  }, [readerNotes, sentenceById]);
  const noteDraftReaderNote = useMemo(() => pendingReaderNote, [pendingReaderNote]);
  const notePanelSentenceId = activeReaderNote?.anchorSentenceId ?? noteDraftReaderNote?.anchorSentenceId ?? null;
  const notePanelSentence = notePanelSentenceId ? sentenceById.get(notePanelSentenceId) ?? null : null;
  const notePanelSentenceIndex = notePanelSentenceId
    ? reader.article.sentences.findIndex((item) => item.sentenceId === notePanelSentenceId) + 1
    : 0;
  const notePanelNotes = notePanelSentenceId ? readerNotesBySentence.get(notePanelSentenceId) ?? [] : [];
  const {
    refs: {
      setFloating: setNotePanelFloating,
      setPositionReference: setNotePanelReference,
    },
    floatingStyles: notePanelStyles,
  } = useReaderFloatingLayer({
    open: Boolean(notePanelOpen && (activeReaderNote || noteDraftReaderNote)),
    placement: "right-start",
    offsetPx: 12,
    crossAxisOffsetPx: 8,
    collisionPadding: 18,
    strategy: "fixed",
  });
  const plateDocument = useMemo(() => renderSceneToPlateDocument(reader), [reader]);
  const pageIdentity: ReaderAskPageIdentity = useMemo(
    () => ({
      recordId: record.id,
      recordTitle: record.title,
      surface: "reader",
      source: "reader_2_0",
      availableContextCapabilities: [
        "record_context",
        "dictionary",
        ...(reader.contentSummary?.overview?.trim() || reader.sentenceEntries.length > 0 ? ["record_insights"] : []),
        ...(annotations.length > 0 ? ["reader_annotations"] : []),
        ...(readerNotes.length > 0 ? ["reader_notes"] : []),
      ],
      hasArticleOverview: Boolean(reader.contentSummary?.overview?.trim()),
      hasSentenceEntries: reader.sentenceEntries.length > 0,
      hasAnnotations: annotations.length > 0,
      hasReaderNotes: readerNotes.length > 0,
    }),
    [annotations.length, reader.contentSummary?.overview, reader.sentenceEntries.length, readerNotes.length, record.id, record.title],
  );
  const activeLookupAIContextKey = useMemo(() => dictionaryAIContextKey(activeLookup), [activeLookup]);
  const activeLookupSaveRequest = useMemo(() => lookupSaveRequestFromSnapshot(activeLookup), [activeLookup]);
  const activeLookupSaveCacheKey = useMemo(() => lookupSaveCacheKey(activeLookupSaveRequest), [activeLookupSaveRequest]);
  const activeLookupSavedVocabularyMatch =
    activeLookupSaveCacheKey && Object.prototype.hasOwnProperty.call(savedVocabularyMatches, activeLookupSaveCacheKey)
      ? savedVocabularyMatches[activeLookupSaveCacheKey] ?? null
      : null;
  const activeLookupSaveState = useMemo<LookupSaveState>(() => {
    const savedMatch = activeLookupSavedVocabularyMatch;
    return getLookupSaveState(
      Boolean(savedMatch),
      activeLookup?.sentenceId,
      savedMatch?.sourceRefs,
      savedMatch?.masteryStatus === "mastered",
    );
  }, [activeLookup?.sentenceId, activeLookupSavedVocabularyMatch]);
  const activeLookupAICacheEntry = useMemo(() => {
    const preferredMode = dictionaryPreferredAIMode(activeLookup);
    if (!activeLookup || !preferredMode) {
      return null;
    }

    const request = dictionaryAIRequestForLookup(activeLookup, preferredMode);
    if (!request) {
      return null;
    }

    return dictionaryAICache[dictionaryAICacheKey(activeLookup, request)] ?? null;
  }, [activeLookup, dictionaryAICache]);
  const canCreateDictionaryAINote =
    activeLookup &&
    dictionaryAI.kind === "ready" &&
    dictionaryLookupSupportsExactAINote(activeLookup);

  const selectedAnnotation = useMemo(() => {
    if (!textSelection) {
      return null;
    }
    return (
      annotations.find(
        (item) =>
          belongsToCurrentRecord(item.recordId, item.targetKey, record.id) &&
          annotationMatchesSelection(item, textSelection),
      ) ?? null
    );
  }, [annotations, record.id, textSelection]);
  const selectedReaderNote = useMemo(() => {
    if (!textSelection) {
      return null;
    }
    return readerNotesByTargetKey.get(targetKeyForSelection(record.id, textSelection)) ?? null;
  }, [readerNotesByTargetKey, record.id, textSelection]);
  const liveContextAttachment = useMemo(() => {
    if (!liveContextSelection) {
      return null;
    }
    return askAttachmentFromSelection(pageIdentity, liveContextSelection, {
      sourceSurface: "reader_live_selection",
      entryAction: liveContextSelection.anchorType === "sentence" ? "explain_this" : "ask_about_this",
    });
  }, [liveContextSelection, pageIdentity]);
  const selectionTargetKey = useMemo(
    () => (textSelection ? targetKeyForSelection(record.id, textSelection) : null),
    [record.id, textSelection],
  );
  const liveContextSelectionTargetKey = useMemo(
    () => (liveContextSelection ? targetKeyForSelection(record.id, liveContextSelection) : null),
    [liveContextSelection, record.id],
  );
  const activeSelectionMatchesLiveContext = Boolean(
    selectionTargetKey &&
      liveContextSelectionTargetKey &&
      selectionTargetKey === liveContextSelectionTargetKey,
  );
  const selectedHighlight = selectedAnnotation?.type === "highlight" ? selectedAnnotation : null;
  const activeAnnotation =
    activeAnnotationTargetKey
      ? annotations.find(
          (item) =>
            belongsToCurrentRecord(item.recordId, item.targetKey, record.id) &&
            item.targetKey === activeAnnotationTargetKey,
        ) ?? null
      : null;
  const hasExactSelectedHighlight = Boolean(
    selectedHighlight && selectionTargetKey && selectedHighlight.targetKey === selectionTargetKey,
  );
  const multiTextHighlightExtensionBase =
    !hasExactSelectedHighlight && selectedHighlight?.anchorType === "multi_text"
      ? selectedHighlight
      : activeAnnotation?.type === "highlight" && activeAnnotation.anchorType === "multi_text"
        ? activeAnnotation
        : null;
  const selectionToolbarStatus =
    annotationSaveState.kind === "idle"
      ? null
      : {
          kind: annotationSaveState.kind,
          message:
            annotationSaveState.kind === "saving"
              ? annotationSaveState.message ?? "正在处理当前高亮…"
              : annotationSaveState.message,
        };

  useEffect(() => {
    if (selectedAnnotation?.type === "highlight") {
      queueMicrotask(() => setAnnotationColor(selectedAnnotation.color));
    }
  }, [selectedAnnotation]);

  useEffect(() => {
    if (annotationSaveState.kind !== "saved" && annotationSaveState.kind !== "error") {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      setAnnotationSaveState((current) =>
        current.kind === annotationSaveState.kind && current.message === annotationSaveState.message
          ? { kind: "idle" }
          : current,
      );
    }, annotationSaveState.kind === "error" ? 2600 : 1600);

    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [annotationSaveState]);

  useEffect(() => {
    const targetKey = searchParams.get("focusTargetKey") ?? searchParams.get("targetKey");
    if (!targetKey || focusedRouteTargetKeyRef.current === targetKey) {
      return;
    }

    const nextJumpTarget = jumpToTargetKey(targetKey, {
      annotations,
    });
    if (!nextJumpTarget) {
      return;
    }

    queueMicrotask(() => setJumpTarget(nextJumpTarget));
    focusedRouteTargetKeyRef.current = targetKey;
  }, [annotations, searchParams]);

  useEffect(() => {
    if (!jumpTarget) {
      return;
    }

    if (jumpTarget.targetType === "content_summary") {
      if (jumpTarget.scrollStrategy === "center") {
        window.requestAnimationFrame(() => {
          articleRef.current
            ?.querySelector<HTMLElement>("#reader-content-summary")
            ?.scrollIntoView({ block: "center", behavior: "smooth" });
        });
      }

      const targetKey = jumpTarget.targetKey;
      const timer = window.setTimeout(() => {
        setJumpTarget((current) => (current?.targetKey === targetKey ? null : current));
      }, 4200);
      return () => window.clearTimeout(timer);
    }

    const targetSentenceId = jumpTarget.primarySentenceId ?? jumpTarget.sentenceIds[0];
    if (targetSentenceId) {
      const targetSentence = sentenceById.get(targetSentenceId);
      if (targetSentence) {
        queueMicrotask(() => setActiveSentence(targetSentence));
        if (jumpTarget.scrollStrategy === "center") {
          window.requestAnimationFrame(() => {
            articleRef.current
              ?.querySelector<HTMLElement>(`#reader-sentence-${CSS.escape(targetSentenceId)}`)
              ?.scrollIntoView({ block: "center", behavior: "smooth" });
          });
        }
      }
    }

    const targetKey = jumpTarget.targetKey;
    const timer = window.setTimeout(() => {
      setJumpTarget((current) => (current?.targetKey === targetKey ? null : current));
    }, 4200);
    return () => window.clearTimeout(timer);
  }, [jumpTarget, sentenceById]);

  useEffect(() => {
    if (!focusedReaderNoteTarget) {
      return;
    }

    const targetSentenceId =
      focusedReaderNoteTarget.primarySentenceId ?? focusedReaderNoteTarget.sentenceIds[0];
    if (!targetSentenceId) {
      return;
    }

    const targetSentence = sentenceById.get(targetSentenceId);
    if (!targetSentence) {
      return;
    }

    queueMicrotask(() => setActiveSentence(targetSentence));
    if (focusedReaderNoteTarget.scrollStrategy === "center") {
      window.requestAnimationFrame(() => {
        articleRef.current
          ?.querySelector<HTMLElement>(`#reader-sentence-${CSS.escape(targetSentenceId)}`)
          ?.scrollIntoView({ block: "center", behavior: "smooth" });
      });
    }
  }, [focusedReaderNoteTarget, sentenceById]);

  const updateDictionaryAICacheEntry = useCallback(
    (
      lookup: DictionaryLookupSnapshot | null,
      state: Extract<DictionaryAIViewState, { kind: "ready" | "error" }>,
      expanded: boolean,
    ) => {
      if (!lookup) {
        return;
      }

      const request = dictionaryAIRequestForLookup(lookup, state.mode);
      if (!request) {
        return;
      }

      const cacheKey = dictionaryAICacheKey(lookup, request);
      setDictionaryAICache((current) => ({
        ...current,
        [cacheKey]: createDictionaryAICacheEntry(state, expanded),
      }));
    },
    [],
  );

  const dismissLookupPreview = useCallback(() => {
    setLookupPreviewOpen(false);
    const trigger = lastLookupTriggerRef.current;
    if (trigger?.isConnected) {
      window.requestAnimationFrame(() => {
        trigger.focus({ preventScroll: true });
      });
    }
  }, []);

  const clearLookup = useCallback(() => {
    setLookupPreviewOpen(false);
    setLookupPreviewAnchor(null);
    setActiveLookup(null);
    setActiveInspect(null);
    setDictionaryRailOpen(false);
    dictionaryAIRequestKeyRef.current = null;
    setDictionaryAI({ kind: "idle" });
    setDictionaryAIPanelOpen(false);
    setDictionaryAINoteState({ kind: "idle" });
    const trigger = lastLookupTriggerRef.current;
    if (trigger?.isConnected) {
      window.requestAnimationFrame(() => {
        trigger.focus({ preventScroll: true });
      });
    }
  }, []);

  const closeDictionaryPanel = useCallback(() => {
    setDictionaryPinned(false);
    clearLookup();
  }, [clearLookup]);

  const openFeedbackSheet = useCallback(
    (config: {
      scope: FeedbackScopeDto;
      sentiment?: FeedbackSentimentDto;
      feedbackType?: FeedbackTypeDto;
      analysisRecordId?: string;
      targetId: string;
      annotationType?: string;
      contextSummary?: string;
      contextJson?: Record<string, unknown>;
      clientSurface?: string;
      entryPoint?: string;
    }) => {
      setFeedbackSheet({ open: true, ...config });
    },
    [],
  );

  const closeFeedbackSheet = useCallback(() => {
    setFeedbackSheet(null);
  }, []);

  const openAnalysisFeedback = useCallback(
    (entry: {
      entryId: string;
      sentenceId: string;
      entryType: string;
      label?: string;
      title?: string;
      content?: string;
      sourceKind?: string;
    }) => {
      openFeedbackSheet({
        scope: "annotation",
        sentiment: "negative",
        targetId: `record:${record.id}:analysis:${entry.entryType}:${entry.entryId}`,
        analysisRecordId: record.id,
        annotationType: entry.entryType,
        clientSurface: "reader",
        entryPoint: "reader_analysis_card",
        contextSummary: entry.title ?? entry.label ?? "标注反馈",
        contextJson: {
          entry_id: entry.entryId,
          entry_type: entry.entryType,
          sentence_id: entry.sentenceId,
          source_kind: entry.sourceKind ?? "workflow",
          content: entry.content ?? "",
        },
      });
    },
    [openFeedbackSheet, record.id],
  );

  useEffect(() => {
    dictionaryAIRequestKeyRef.current = null;
    queueMicrotask(() => setDictionaryAINoteState({ kind: "idle" }));
    if (activeLookupAICacheEntry) {
      queueMicrotask(() => {
        setDictionaryAI(dictionaryAIViewStateFromCacheEntry(activeLookupAICacheEntry));
        setDictionaryAIPanelOpen(activeLookupAICacheEntry.expanded);
      });
      return;
    }
    queueMicrotask(() => {
      setDictionaryAI({ kind: "idle" });
      setDictionaryAIPanelOpen(false);
    });
  }, [activeLookupAICacheEntry, activeLookupAIContextKey]);

  useEffect(() => {
    if (!activeLookupSaveRequest || !activeLookupSaveCacheKey) {
      return;
    }

    if (Object.prototype.hasOwnProperty.call(savedVocabularyMatches, activeLookupSaveCacheKey)) {
      return;
    }

    let cancelled = false;
    const params = new URLSearchParams();
    if (activeLookupSaveRequest.dictEntryId) {
      params.set("dict_entry_id", String(activeLookupSaveRequest.dictEntryId));
    }
    if (activeLookupSaveRequest.lemma) {
      params.set("lemma", activeLookupSaveRequest.lemma);
    }
    if (activeLookupSaveRequest.form) {
      params.set("form", activeLookupSaveRequest.form);
    }

    void fetch(`/api/web/vocabulary?${params.toString()}`)
      .then(async (response) => {
        const payload = (await response.json().catch(() => null)) as ReaderVocabularyLookupResponseDto | null;
        if (cancelled) {
          return;
        }

        if (!payload || !payload.ok) {
          setSavedVocabularyMatches((current) => ({
            ...current,
            [activeLookupSaveCacheKey]: null,
          }));
          return;
        }

        setSavedVocabularyMatches((current) => ({
          ...current,
          [activeLookupSaveCacheKey]: payload.item
            ? {
                id: payload.item.id,
                lemma: payload.item.lemma,
                displayWord: payload.item.display_word,
                dictEntryId: payload.item.dict_entry_id,
                masteryStatus: payload.item.mastery_status,
                sourceRefs: payload.item.source_refs,
                collectedForms: payload.item.collected_forms,
              }
            : null,
        }));
      })
      .catch(() => {
        if (cancelled) {
          return;
        }
        setSavedVocabularyMatches((current) => ({
          ...current,
          [activeLookupSaveCacheKey]: null,
        }));
      });

    return () => {
      cancelled = true;
    };
  }, [activeLookupSaveCacheKey, activeLookupSaveRequest, savedVocabularyMatches]);

  useEffect(() => {
    function handleCreated(event: Event) {
      const item = (event as CustomEvent<WebAnnotationVm>).detail;
      if (belongsToCurrentRecord(item.recordId, item.targetKey, record.id)) {
        setAnnotations((current) => [item, ...current.filter((existing) => existing.id !== item.id)]);
      }
    }

    window.addEventListener(ANNOTATION_CREATED_EVENT, handleCreated);
    return () => window.removeEventListener(ANNOTATION_CREATED_EVENT, handleCreated);
  }, [record.id]);

  useEffect(() => {
    if (!lookupPreviewOpen) {
      return;
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setLookupPreviewOpen(false);
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [lookupPreviewOpen]);

  useEffect(() => {
    if (!lookupPreviewOpen || !lookupPreviewAnchor || !articleRef.current) {
      return;
    }

    const articleElement = articleRef.current;
    const handleWindowResize = () => {
      setLookupPreviewEpoch((value) => value + 1);
    };
    const observer = new ResizeObserver(() => {
      setLookupPreviewEpoch((value) => value + 1);
    });

    observer.observe(articleElement);
    window.addEventListener("resize", handleWindowResize);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", handleWindowResize);
    };
  }, [lookupPreviewAnchor, lookupPreviewOpen]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const syncPreviewVisibility = () => {
      if (!shouldShowLookupPreview()) {
        setLookupPreviewOpen(false);
        setLookupPreviewAnchor(null);
      }
    };

    syncPreviewVisibility();
    window.addEventListener("resize", syncPreviewVisibility);
    return () => window.removeEventListener("resize", syncPreviewVisibility);
  }, []);

  useEffect(() => {
    if (!lookupPreviewOpen || !lookupPreviewAnchor) {
      setLookupPreviewReference(null);
      return;
    }

    setLookupPreviewReference({
      getBoundingClientRect: () => {
        const sentenceElement = articleRef.current?.querySelector<HTMLElement>(
          `[data-reader-anchor="sentence"][data-sentence-id="${CSS.escape(lookupPreviewAnchor.sentenceId)}"] [data-reader-sentence-text="true"]`,
        );
        const liveRect = sentenceElement
          ? rectForTextOffsets(
              sentenceElement,
              lookupPreviewAnchor.startOffset,
              lookupPreviewAnchor.endOffset,
            )
          : null;

        return liveRect ?? lookupPreviewAnchor.fallbackRect ?? new DOMRect(0, 0, 0, 0);
      },
      contextElement: articleRef.current ?? undefined,
    });
  }, [lookupPreviewAnchor, lookupPreviewEpoch, lookupPreviewOpen, setLookupPreviewReference]);

  useEffect(() => {
    if (!dictionaryPanelVisible || typeof window === "undefined") {
      queueMicrotask(() => setDictionaryDockLayout(null));
      return;
    }

    const articleElement = articleRef.current;
    const readingColumnElement = readingColumnRef.current;
    if (!articleElement || !readingColumnElement) {
      setDictionaryDockLayout(null);
      return;
    }

    const updateDictionaryDockLayout = () => {
      if (window.innerWidth < 1200) {
        setDictionaryDockLayout(null);
        return;
      }

      const articleRect = articleElement.getBoundingClientRect();
      const readingColumnRect = readingColumnElement.getBoundingClientRect();
      const gapToReadingColumn = 28;
      const minLeft = articleRect.left + 18;
      const availableWidth = readingColumnRect.left - minLeft - gapToReadingColumn;

      if (availableWidth < 320) {
        setDictionaryDockLayout(null);
        return;
      }

      const width = Math.min(496, availableWidth);
      const left = Math.max(minLeft, readingColumnRect.left - width - gapToReadingColumn);
      setDictionaryDockLayout({
        left: Math.round(left),
        width: Math.round(width),
      });
    };

    updateDictionaryDockLayout();
    const articleObserver = new ResizeObserver(updateDictionaryDockLayout);
    const readingColumnObserver = new ResizeObserver(updateDictionaryDockLayout);
    articleObserver.observe(articleElement);
    readingColumnObserver.observe(readingColumnElement);
    window.addEventListener("resize", updateDictionaryDockLayout);
    const intervalId = window.setInterval(updateDictionaryDockLayout, 250);

    return () => {
      articleObserver.disconnect();
      readingColumnObserver.disconnect();
      window.removeEventListener("resize", updateDictionaryDockLayout);
      window.clearInterval(intervalId);
    };
  }, [dictionaryPanelVisible]);

  useEffect(() => {
    if (!textSelection) {
      setSelectionToolbarReference(null);
      return;
    }

    setSelectionToolbarReference({
      getBoundingClientRect: () => selectionToolbarRectForReaderSelection(articleRef.current, textSelection),
      contextElement: articleRef.current ?? undefined,
    });
  }, [setSelectionToolbarReference, textSelection]);

  useEffect(() => {
    if (!notePanelOpen || !notePanelSentenceId) {
      setNotePanelReference(null);
      return;
    }

    const sentenceSelector = `[data-reader-anchor="sentence"][data-sentence-id="${CSS.escape(notePanelSentenceId)}"]`;
    const updateReference = () => {
      const sentenceHandle =
        articleRef.current?.querySelector<HTMLElement>(`${sentenceSelector} [data-reader-sentence-rail="true"]`) ??
        articleRef.current?.querySelector<HTMLElement>(`${sentenceSelector} [data-reader-sentence-handle="true"]`) ??
        null;
      const sentenceSection = articleRef.current?.querySelector<HTMLElement>(sentenceSelector) ?? null;
      const anchor = sentenceHandle ?? sentenceSection;
      if (!anchor) {
        setNotePanelReference(null);
        return;
      }
      setNotePanelReference({
        getBoundingClientRect: () => copyDomRect(anchor.getBoundingClientRect()),
        contextElement: articleRef.current ?? undefined,
      });
    };

    updateReference();
    window.addEventListener("resize", updateReference);
    window.addEventListener("scroll", updateReference, true);
    return () => {
      window.removeEventListener("resize", updateReference);
      window.removeEventListener("scroll", updateReference, true);
    };
  }, [notePanelOpen, notePanelSentenceId, setNotePanelReference]);

  useEffect(() => {
    if (!textSelection) {
      return;
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (isEditableKeyboardTarget(event.target) || event.metaKey || event.ctrlKey || event.altKey) {
        return;
      }

      if (event.key === "Escape") {
        setTextSelection(null);
        setHighlightPaletteOpen(false);
        textSelectionSourceRef.current = "none";
        setTextSelectionSource("none");
        setTextSelectionVisualMode("selection");
        activeAnnotationTargetKeyRef.current = null;
        setActiveAnnotationTargetKey(null);
        setHoveredAnnotationTargetKey(null);
        window.getSelection()?.removeAllRanges();
        return;
      }

      const key = event.key.toLowerCase();
      if (key === "h") {
        event.preventDefault();
        highlightTextSelection(annotationColor);
        return;
      }

      if (key === "e") {
        event.preventDefault();
        openTextSelectionNote();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [annotationColor, textSelection]);

  useEffect(() => {
    if (!(contextPanelOpen && activeSentence)) {
      return;
    }

    const sentence = activeSentence;

    function handleKeyDown(event: KeyboardEvent) {
      if (isEditableKeyboardTarget(event.target) || event.metaKey || event.ctrlKey || event.altKey) {
        return;
      }

      const key = event.key.toLowerCase();
      if (key === "h") {
        event.preventDefault();
        void saveHighlight();
        return;
      }

      if (key === "e") {
        event.preventDefault();
        openSentenceNote(sentence);
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [activeSentence, contextPanelOpen]);

  const handleLookupSnapshot = useCallback((snapshot: DictionaryLookupSnapshot) => {
    setActiveLookup(snapshot);
    setActiveInspect(null);
    setDictionaryQuery(snapshot.query);
    setDictionarySaveState({ kind: "idle" });
    setDictionaryAINoteState({ kind: "idle" });

    if (snapshot.state.kind === "ready") {
      const snapshotHistoryKey = dictionaryLookupHistoryKey(snapshot);
      setLookupHistory((current) => [
        snapshot,
        ...current.filter((item) => dictionaryLookupHistoryKey(item) !== snapshotHistoryKey),
      ].slice(0, 8));
    }
  }, []);

  const lookupPlainText = useCallback(
    async (
      intent: ReaderLookupIntent,
      options?: {
        showPreview?: boolean;
        anchor?: ReaderLookupPreviewAnchor | null;
        openRail?: boolean;
      },
    ) => {
      const nextShowPreview = options?.showPreview ?? shouldShowLookupPreview();

      if (nextShowPreview) {
        setDictionarySearchExpanded(false);
      }
      if (options?.openRail) {
        setDictionaryRailOpen(true);
      }
      setLookupPreviewOpen(nextShowPreview);
      setLookupPreviewAnchor(nextShowPreview ? (options?.anchor ?? null) : null);
      const loadingState = { kind: "loading" } satisfies DictionaryLookupSnapshot["state"];
      handleLookupSnapshot(readerLookupSnapshotFromIntent(record.id, intent, loadingState));

      try {
        const params = new URLSearchParams({
          word: intent.query,
          type: intent.lookupType,
          context: intent.contextSentence,
          sentenceId: intent.sentenceId,
        });
        if (intent.occurrence !== undefined) {
          params.set("occurrence", String(intent.occurrence));
        }
        const response = await fetch(`/api/web/dict/lookup?${params.toString()}`);
        const payload = (await response.json().catch(() => null)) as WebDictResult | null;
        if (!payload) {
          return;
        }
        handleLookupSnapshot(readerLookupSnapshotFromIntent(record.id, intent, { kind: "ready", result: payload }));

        if (!response.ok && payload.kind !== "error") {
          handleLookupSnapshot(readerLookupSnapshotFromIntent(record.id, intent, {
            kind: "error",
            message: "词典查询失败。",
          }));
        }
      } catch (error) {
        handleLookupSnapshot(readerLookupSnapshotFromIntent(record.id, intent, {
          kind: "error",
          message: error instanceof Error ? error.message : "词典查询失败。",
        }));
      }
    },
    [handleLookupSnapshot, record.id],
  );

  const lookupDictionaryQuery = useCallback((query: string) => {
    const trimmed = query.trim();
    if (!trimmed) {
      return;
    }

    setDictionarySearchExpanded(false);
    setDictionaryRailOpen(true);
    void lookupPlainText(
      {
        kind: "lexical_lookup",
        query: trimmed,
        lookupType: trimmed.includes(" ") ? "phrase" : "word",
        contextSentence: "",
        sourceContext: undefined,
        sentenceId: "__manual__",
        anchorText: trimmed,
        title: "手动查词",
        label: "手动查词",
      },
      { showPreview: false, openRail: true },
    );
  }, [lookupPlainText]);

  const selectLookupFromTrail = useCallback((lookup: DictionaryLookupSnapshot) => {
    setActiveLookup(lookup);
    setActiveInspect(null);
    setDictionaryRailOpen(true);
    setDictionaryQuery(lookup.query);
    setDictionarySaveState({ kind: "idle" });
    setDictionaryAINoteState({ kind: "idle" });
    setLookupPreviewAnchor(null);
    setLookupPreviewOpen(false);
  }, []);

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
      anchorOffsets: activeLookup.anchorOffsets,
      occurrence: activeLookup.occurrence,
      textHash: activeLookup.textHash,
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
      const payload = (await response.json().catch(() => null)) as WebDictResult | null;
      if (!payload) {
        return;
      }
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

  const toggleDictionaryAIPanel = useCallback(() => {
    setDictionaryAIPanelOpen((value) => {
      const nextValue = !value;
      if (activeLookup && (dictionaryAI.kind === "ready" || dictionaryAI.kind === "error")) {
        updateDictionaryAICacheEntry(activeLookup, dictionaryAI, nextValue);
      }
      return nextValue;
    });
  }, [activeLookup, dictionaryAI, updateDictionaryAICacheEntry]);

  const requestDictionaryAI = useCallback(
    async (mode: WebDictAIRequest["mode"]) => {
      if (!activeLookup) {
        return;
      }

      const requestBody = dictionaryAIRequestForLookup(activeLookup, mode);
      if (!requestBody) {
        return;
      }

      const requestKey = dictionaryAIRequestKey(requestBody);
      if (
        dictionaryAI.kind === "ready" &&
        dictionaryAI.mode === mode &&
        dictionaryAI.requestKey === requestKey
      ) {
        setDictionaryAIPanelOpen((value) => !value);
        return;
      }

      if (
        dictionaryAI.kind === "loading" &&
        dictionaryAI.mode === mode &&
        dictionaryAI.requestKey === requestKey
      ) {
        return;
      }

      const lookupAtRequest = activeLookup;
      dictionaryAIRequestKeyRef.current = requestKey;
      setDictionaryAI({ kind: "loading", mode, requestKey });
      setDictionaryAIPanelOpen(true);
      setDictionaryAINoteState({ kind: "idle" });

      try {
        const response = await fetch("/api/web/dict/ai", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(requestBody),
        });
        const payload = (await response.json().catch(() => null)) as WebDictAIResult | { kind: "error"; [key: string]: unknown } | null;

        if (dictionaryAIRequestKeyRef.current !== requestKey) {
          return;
        }

        if (!response.ok || !payload || payload.kind === "error") {
          const fallbackError: WebDictAIErrorResult = {
            kind: "error",
            query: requestBody.query,
            mode,
            status: response.status || 503,
            code:
              response.status >= 500 || response.status === 0
                ? "upstream_unavailable"
                : "upstream_error",
            message:
              response.status >= 500 || response.status === 0
                ? "AI 查词暂时不可用，请稍后再试。"
                : "AI 查词失败。",
          };
          const errorResult =
            payload && isDictionaryAIErrorResult(payload) ? payload : fallbackError;

          const nextState = { kind: "error", mode, requestKey, error: errorResult } satisfies DictionaryAIViewState;
          setDictionaryAI(nextState);
          setDictionaryAIPanelOpen(true);
          updateDictionaryAICacheEntry(lookupAtRequest, nextState, true);

          if (errorResult.code === "canonical_dictionary_available") {
            void lookupPlainText(lookupIntentFromSnapshotBase(dictionaryLookupBase(lookupAtRequest)), {
              showPreview: false,
              openRail: true,
            });
          }

          return;
        }

        const nextState = {
          kind: "ready",
          mode,
          requestKey,
          result: payload,
        } satisfies DictionaryAIViewState;
        setDictionaryAI(nextState);
        setDictionaryAIPanelOpen(true);
        updateDictionaryAICacheEntry(lookupAtRequest, nextState, true);
      } catch {
        if (dictionaryAIRequestKeyRef.current !== requestKey) {
          return;
        }

        const nextState = {
          kind: "error",
          mode,
          requestKey,
          error: {
            kind: "error",
            query: requestBody.query,
            mode,
            status: 503,
            code: "upstream_unavailable",
            message: "AI 查词暂时不可用，请稍后再试。",
          },
        } satisfies DictionaryAIViewState;
        setDictionaryAI(nextState);
        setDictionaryAIPanelOpen(true);
        updateDictionaryAICacheEntry(lookupAtRequest, nextState, true);
      }
    },
    [activeLookup, dictionaryAI, lookupPlainText, updateDictionaryAICacheEntry],
  );

  const selectAISuggestedQuery = useCallback(
    (query: string) => {
      if (!activeLookup) {
        return;
      }

      const trimmed = query.trim();
      if (!trimmed) {
        return;
      }

      dictionaryAIRequestKeyRef.current = null;
      setDictionaryAI({ kind: "idle" });
      setDictionaryAIPanelOpen(false);
      setDictionaryAINoteState({ kind: "idle" });

      void lookupPlainText(
        {
          ...lookupIntentFromSnapshotBase(dictionaryLookupBase(activeLookup)),
          query: trimmed,
          anchorText: trimmed,
          lookupType: trimmed.includes(" ") ? "phrase" : "word",
          title: "AI 建议查词",
          label: "AI 建议",
        },
        { showPreview: false, openRail: true },
      );
    },
    [activeLookup, lookupPlainText],
  );

  const handleLookupIntent = useCallback(
    (
      intent: ReaderLookupIntent,
      anchor: ReaderLookupPreviewAnchor | null,
      options?: { showPreview?: boolean; openRail?: boolean },
      triggerEl?: HTMLElement | null,
    ) => {
      lastLookupTriggerRef.current = triggerEl ?? null;
      setActiveSentence(sentenceById.get(intent.sentenceId) ?? null);
      setContextPanelOpen(false);
      setSentencePopoverAnchorEl(null);
      void lookupPlainText(intent, {
        showPreview: options?.showPreview,
        anchor,
        openRail: options?.openRail,
      });
    },
    [lookupPlainText, sentenceById],
  );

  const handleInspectIntent = useCallback(
    (
      intent: ReaderStructuredInspectIntent,
      anchor: ReaderLookupPreviewAnchor | null,
      options?: { showPreview?: boolean; openRail?: boolean },
      triggerEl?: HTMLElement | null,
    ) => {
      lastLookupTriggerRef.current = triggerEl ?? null;
      const nextShowPreview = options?.showPreview ?? shouldShowLookupPreview();
      if (nextShowPreview) {
        setDictionarySearchExpanded(false);
      }
      if (options?.openRail) {
        setDictionaryRailOpen(true);
      }
      setLookupPreviewOpen(nextShowPreview);
      setLookupPreviewAnchor(nextShowPreview ? anchor : null);
      setActiveLookup(null);
      setActiveInspect(intent);
      setDictionaryQuery(intent.lookupText ?? intent.anchorText);
      setDictionarySaveState({ kind: "idle" });
      setDictionaryAINoteState({ kind: "idle" });
      dictionaryAIRequestKeyRef.current = null;
      setDictionaryAI({ kind: "idle" });
      setDictionaryAIPanelOpen(false);
      setActiveSentence(sentenceById.get(intent.sentenceId) ?? null);
      setContextPanelOpen(false);
      setSentencePopoverAnchorEl(null);
    },
    [sentenceById],
  );

  const openDictionaryRail = useCallback(() => {
    setDictionaryRailOpen(true);
    setLookupPreviewOpen(false);
  }, []);

  const readerSentenceTextElement = useCallback((sentenceId: string) => {
    if (!articleRef.current) {
      return null;
    }

    return articleRef.current.querySelector<HTMLElement>(
      `[data-reader-anchor="sentence"][data-sentence-id="${CSS.escape(sentenceId)}"] [data-reader-sentence-text="true"]`,
    );
  }, []);

  const focusReaderSelection = useCallback(
    (
      selection: ReaderTextSelection | null,
      options?: {
        openHighlightPalette?: boolean;
        source?: ReaderSelectionSource;
        visualMode?: ReaderSelectionVisualMode;
        activeAnnotationTargetKey?: string | null;
        hoveredAnnotationTargetKey?: string | null;
        toolbarVisible?: boolean;
      },
    ) => {
      setTextSelection(selection);
      setHighlightPaletteOpen(Boolean(options?.openHighlightPalette));
      setSelectionToolbarVisible(Boolean(selection && options?.toolbarVisible));
      const nextSource = selection ? (options?.source ?? "programmatic") : "none";
      textSelectionSourceRef.current = nextSource;
      setTextSelectionSource(nextSource);
      setTextSelectionVisualMode(selection ? (options?.visualMode ?? "selection") : "selection");
      const nextActiveAnnotationTargetKey = selection ? (options?.activeAnnotationTargetKey ?? null) : null;
      activeAnnotationTargetKeyRef.current = nextActiveAnnotationTargetKey;
      setActiveAnnotationTargetKey(nextActiveAnnotationTargetKey);
      setHoveredAnnotationTargetKey(
        selection
          ? (options?.hoveredAnnotationTargetKey ?? options?.activeAnnotationTargetKey ?? null)
          : null,
      );

      if (selection) {
        setActiveSentence(selection.sentence);
        setSettingsPanelOpen(false);
        setContextPanelOpen(false);
        setSentencePopoverAnchorEl(null);
        setLookupPreviewOpen(false);
        setLookupPreviewAnchor(null);
        setAnnotationSaveState({ kind: "idle" });
        setReaderNoteSaveState({ kind: "idle" });
        return;
      }

      setReaderNoteSaveState({ kind: "idle" });
    },
    [],
  );

  const clearReaderSelection = useCallback(
    (options?: { preserveDomSelection?: boolean }) => {
      setTextSelection(null);
      setHighlightPaletteOpen(false);
      setSelectionToolbarVisible(false);
      textSelectionSourceRef.current = "none";
      setTextSelectionSource("none");
      setTextSelectionVisualMode("selection");
      activeAnnotationTargetKeyRef.current = null;
      setActiveAnnotationTargetKey(null);
      setHoveredAnnotationTargetKey(null);
      if (!options?.preserveDomSelection) {
        window.getSelection()?.removeAllRanges();
      }
    },
    [],
  );
  const openAiWorkspace = useCallback(() => {
    setAiOpen(true);
    setComposerTextareaFocused(false);
    setContextPanelOpen(false);
    setSentencePopoverAnchorEl(null);
  }, []);
  const closeAiWorkspace = useCallback(
    (options?: { clearSelectionIfLinked?: boolean }) => {
      setAiOpen(false);
      setComposerTextareaFocused(false);
      const shouldClearSelection =
        Boolean(options?.clearSelectionIfLinked) &&
        textSelection &&
        (activeSelectionMatchesLiveContext || textSelectionVisualMode === "context");
      setLiveContextSelection(null);
      if (shouldClearSelection) {
        clearReaderSelection({ preserveDomSelection: true });
      }
    },
    [activeSelectionMatchesLiveContext, clearReaderSelection, textSelection, textSelectionVisualMode],
  );

  const selectionFromSentence = useCallback(
    (sentence: SentenceModel, anchorEl?: HTMLElement | null): Extract<ReaderTextSelection, { anchorType: "sentence" }> => {
      const sentenceText = sentence.text;
      const sentenceTextHash = hashAnchorText(sentenceText);
      const sentenceTextElement = readerSentenceTextElement(sentence.sentenceId);
      const fallbackRect = anchorEl ? copyDomRect(anchorEl.getBoundingClientRect()) : new DOMRect();
      return {
        anchorType: "sentence",
        sentence,
        selectedText: sentenceText,
        rect: sentenceTextElement ? copyDomRect(sentenceTextElement.getBoundingClientRect()) : fallbackRect,
        segments: [
          {
            paragraphId: sentence.paragraphId,
            sentenceId: sentence.sentenceId,
            sentence,
            selectedText: sentenceText,
            startOffset: 0,
            endOffset: sentenceText.length,
            textHash: sentenceTextHash,
          },
        ],
        startOffset: 0,
        endOffset: sentenceText.length,
        textHash: sentenceTextHash,
      };
    },
    [readerSentenceTextElement],
  );

  const selectionFromAnnotation = useCallback(
    (
      annotation: WebAnnotationVm,
      options?: { preferredSentenceId?: string; anchorEl?: HTMLElement | null },
    ): ReaderTextSelection | null => {
      const fallbackRect = options?.anchorEl ? copyDomRect(options.anchorEl.getBoundingClientRect()) : new DOMRect();

      if (annotation.anchorType === "sentence" && annotation.sentenceId) {
        const sentence = sentenceById.get(annotation.sentenceId);
        if (!sentence) {
          return null;
        }

        return selectionFromSentence(sentence, options?.anchorEl);
      }

      if (
        annotation.anchorType === "text_range" &&
        annotation.sentenceId &&
        typeof annotation.startOffset === "number" &&
        typeof annotation.endOffset === "number"
      ) {
        const sentence = sentenceById.get(annotation.sentenceId);
        if (!sentence) {
          return null;
        }

        const sentenceTextElement = readerSentenceTextElement(annotation.sentenceId);
        return {
          anchorType: "text_range",
          sentence,
          selectedText: annotation.selectedText,
          rect:
            (sentenceTextElement
              ? rectForTextOffsets(sentenceTextElement, annotation.startOffset, annotation.endOffset)
              : null) ?? fallbackRect,
          segments: [
            {
              paragraphId: sentence.paragraphId,
              sentenceId: sentence.sentenceId,
              sentence,
              selectedText: annotation.selectedText,
              startOffset: annotation.startOffset,
              endOffset: annotation.endOffset,
              textHash: annotation.textHash ?? hashAnchorText(annotation.selectedText),
            },
          ],
          startOffset: annotation.startOffset,
          endOffset: annotation.endOffset,
          textHash: annotation.textHash ?? hashAnchorText(annotation.selectedText),
        };
      }

      if (annotation.anchorType !== "multi_text") {
        return null;
      }

      const segments = annotation.segments
        .map((segment) => {
          const sentence = sentenceById.get(segment.sentenceId);
          if (!sentence) {
            return null;
          }

          return {
            paragraphId: segment.paragraphId ?? sentence.paragraphId,
            sentenceId: segment.sentenceId,
            sentence,
            selectedText: segment.selectedText,
            startOffset: segment.startOffset,
            endOffset: segment.endOffset,
            textHash: segment.textHash,
          };
        })
        .filter((segment): segment is NonNullable<typeof segment> => Boolean(segment));

      if (segments.length === 0) {
        return null;
      }

      const preferredSegment =
        segments.find((segment) => segment.sentenceId === options?.preferredSentenceId) ?? segments[0];
      if (!preferredSegment) {
        return null;
      }

      const sentenceTextElement = readerSentenceTextElement(preferredSegment.sentenceId);
      return {
        anchorType: "multi_text",
        sentence: preferredSegment.sentence,
        selectedText: annotation.selectedText,
        rect:
          (sentenceTextElement
            ? rectForTextOffsets(sentenceTextElement, preferredSegment.startOffset, preferredSegment.endOffset)
            : null) ?? fallbackRect,
        segments,
        startOffset: segments[0]?.startOffset ?? preferredSegment.startOffset,
        endOffset: segments[segments.length - 1]?.endOffset ?? preferredSegment.endOffset,
        textHash: hashAnchorText(annotation.selectedText),
      };
    },
    [readerSentenceTextElement, selectionFromSentence, sentenceById],
  );

  const combinedMultiTextSelection = useCallback(
    (selection: ReaderTextSelection, annotation: WebAnnotationVm): ReaderTextSelection | null => {
      if (annotation.anchorType !== "multi_text") {
        return null;
      }

      const annotationSelection = selectionFromAnnotation(annotation);
      if (!annotationSelection) {
        return null;
      }

      const mergedSegments = mergeSelectionSegments(sentenceById, sentenceOrderById, [
        ...annotationSelection.segments,
        ...selection.segments,
      ]);
      if (mergedSegments.length < 2) {
        return null;
      }

      return {
        anchorType: "multi_text",
        sentence: selection.sentence,
        selectedText: mergedSelectionText(mergedSegments),
        rect: selection.rect,
        range: selection.range,
        segments: mergedSegments,
        startOffset: mergedSegments[0]?.startOffset ?? selection.startOffset,
        endOffset: mergedSegments[mergedSegments.length - 1]?.endOffset ?? selection.endOffset,
        textHash: hashAnchorText(mergedSelectionText(mergedSegments)),
      };
    },
    [selectionFromAnnotation, sentenceById, sentenceOrderById],
  );

  const updateTextSelectionFromDom = useCallback((options?: { toolbarVisible?: boolean }) => {
    const nextSelection = readPlateReaderSelection(articleRef.current, sentenceById);
    if (!nextSelection && textSelectionSourceRef.current === "programmatic") {
      return;
    }

    const matchingHighlight =
      nextSelection
        ? annotations.find(
            (item) =>
              item.type === "highlight" &&
              belongsToCurrentRecord(item.recordId, item.targetKey, record.id) &&
              annotationMatchesSelection(item, nextSelection),
          ) ?? null
        : null;

    const currentActiveAnnotation =
      activeAnnotationTargetKeyRef.current
        ? annotations.find(
            (item) =>
              belongsToCurrentRecord(item.recordId, item.targetKey, record.id) &&
              item.targetKey === activeAnnotationTargetKeyRef.current,
          ) ?? null
        : null;
    const preservedActiveAnnotationTargetKey =
      currentActiveAnnotation?.type === "highlight" &&
      currentActiveAnnotation.anchorType === "multi_text" &&
      nextSelection &&
      annotationOverlapsSelection(currentActiveAnnotation, nextSelection)
        ? currentActiveAnnotation.targetKey
        : matchingHighlight?.anchorType === "multi_text"
          ? matchingHighlight.targetKey
          : null;

    focusReaderSelection(nextSelection, {
      openHighlightPalette: Boolean(options?.toolbarVisible && matchingHighlight),
      source: nextSelection ? "dom" : "none",
      visualMode: "selection",
      activeAnnotationTargetKey: preservedActiveAnnotationTargetKey,
      hoveredAnnotationTargetKey: matchingHighlight?.targetKey ?? null,
      toolbarVisible: Boolean(nextSelection && options?.toolbarVisible),
    });
    if (aiOpen && nextSelection) {
      setLiveContextSelection(nextSelection);
      setComposerTextareaFocused(false);
    }
  }, [aiOpen, annotations, focusReaderSelection, record.id, sentenceById]);

  const commitDomTextSelection = useCallback(() => {
    window.requestAnimationFrame(() => {
      updateTextSelectionFromDom({ toolbarVisible: true });
    });
  }, [updateTextSelectionFromDom]);

  useEffect(() => {
    function handlePointerSelectionEnd() {
      if (!pointerSelectionActiveRef.current) {
        return;
      }
      pointerSelectionActiveRef.current = false;
      commitDomTextSelection();
    }

    window.addEventListener("pointerup", handlePointerSelectionEnd);
    window.addEventListener("pointercancel", handlePointerSelectionEnd);
    return () => {
      window.removeEventListener("pointerup", handlePointerSelectionEnd);
      window.removeEventListener("pointercancel", handlePointerSelectionEnd);
    };
  }, [commitDomTextSelection]);

  useEffect(() => {
    function handleSelectionChange() {
      const nativeSelection = window.getSelection();
      if (!nativeSelection || nativeSelection.isCollapsed || !nativeSelection.toString().trim()) {
        if (pointerSelectionActiveRef.current) {
          return;
        }
        if (textSelectionSourceRef.current === "dom") {
          clearReaderSelection({ preserveDomSelection: true });
        }
        return;
      }

      const articleElement = articleRef.current;
      const anchorElement =
        nativeSelection.anchorNode instanceof Element
          ? nativeSelection.anchorNode
          : nativeSelection.anchorNode?.parentElement ?? null;
      const focusElement =
        nativeSelection.focusNode instanceof Element
          ? nativeSelection.focusNode
          : nativeSelection.focusNode?.parentElement ?? null;

      if (
        !articleElement ||
        !anchorElement ||
        !focusElement ||
        !articleElement.contains(anchorElement) ||
        !articleElement.contains(focusElement)
      ) {
        if (pointerSelectionActiveRef.current) {
          return;
        }
        if (textSelectionSourceRef.current === "dom") {
          clearReaderSelection({ preserveDomSelection: true });
        }
        return;
      }

      if (pointerSelectionActiveRef.current) {
        return;
      }

      updateTextSelectionFromDom({ toolbarVisible: false });
    }

    document.addEventListener("selectionchange", handleSelectionChange);
    return () => {
      document.removeEventListener("selectionchange", handleSelectionChange);
    };
  }, [clearReaderSelection, sentenceById, updateTextSelectionFromDom]);

  const selectionFocusRangesBySentence = useMemo(() => {
    const map = new Map<string, ReaderJumpRangeSegment[]>();
    if (!textSelection || textSelectionVisualMode !== "selection") {
      return map;
    }

    textSelection.segments.forEach((segment) => {
      const current = map.get(segment.sentenceId) ?? [];
      map.set(segment.sentenceId, [
        ...current,
        {
          paragraphId: segment.paragraphId ?? null,
          sentenceId: segment.sentenceId,
          selectedText: segment.selectedText,
          startOffset: segment.startOffset,
          endOffset: segment.endOffset,
          textHash: segment.textHash,
        },
      ]);
    });

    return map;
  }, [textSelection, textSelectionVisualMode]);
  const contextFocusRangesBySentence = useMemo(() => {
    const map = new Map<string, ReaderJumpRangeSegment[]>();
    if (!textSelection || textSelectionVisualMode !== "context") {
      return map;
    }

    textSelection.segments.forEach((segment) => {
      const current = map.get(segment.sentenceId) ?? [];
      map.set(segment.sentenceId, [
        ...current,
        {
          paragraphId: segment.paragraphId ?? null,
          sentenceId: segment.sentenceId,
          selectedText: segment.selectedText,
          startOffset: segment.startOffset,
          endOffset: segment.endOffset,
          textHash: segment.textHash,
        },
      ]);
    });

    return map;
  }, [textSelection, textSelectionVisualMode]);

  function mergeAnnotation(item: WebAnnotationVm) {
    setAnnotations((current) => [item, ...current.filter((existing) => existing.id !== item.id)]);
  }

  function removeAnnotation(annotationId: string) {
    setAnnotations((current) => current.filter((existing) => existing.id !== annotationId));
  }

  function mergeReaderNote(item: WebReaderNoteVm) {
    setReaderNotes((current) => [item, ...current.filter((existing) => existing.id !== item.id)]);
  }

  function removeReaderNote(noteId: string) {
    setReaderNotes((current) => current.filter((existing) => existing.id !== noteId));
  }

  async function patchAnnotation(
    annotationId: string,
    body: { color: UserAnnotationColorDto },
    errorMessage: string,
  ) {
    const response = await fetch(`/api/web/annotations/${encodeURIComponent(annotationId)}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = (await response.json().catch(() => ({ ok: false, message: "请求失败" }))) as
      | { ok: true; item: WebAnnotationVm }
      | { ok: false; message?: string };

    if (!response.ok || !payload.ok) {
      throw new Error(payload.ok === false && payload.message ? payload.message : errorMessage);
    }

    mergeAnnotation(payload.item);
    return payload.item;
  }

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

    if (!activeLookup.contextSentence.trim()) {
      setDictionarySaveState({ kind: "error", message: "手动查词需要先选中正文句子后再加入生词本。" });
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
      const payload = (await response.json().catch(() => ({}))) as { id?: string; message?: string };

      if (!response.ok) {
        setDictionarySaveState({ kind: "error", message: payload.message ?? "加入生词本失败。" });
        return;
      }

      const nextLookupSaveRequest = lookupSaveRequestFromSnapshot(activeLookup);
      const nextLookupSaveCacheKey = lookupSaveCacheKey(nextLookupSaveRequest);
      if (nextLookupSaveCacheKey) {
        setSavedVocabularyMatches((current) => {
          const existing = current[nextLookupSaveCacheKey] ?? null;
          const optimisticMatch = buildOptimisticLookupMatch(
            activeLookup,
            existing,
            payload.id ?? existing?.id ?? `${result.entry.id}`,
          );

          return {
            ...current,
            [nextLookupSaveCacheKey]: optimisticMatch,
          };
        });
      }
      setDictionarySaveState({ kind: "saved", message: payload.message ?? "已加入生词本。" });
    } catch (error) {
      setDictionarySaveState({
        kind: "error",
        message: error instanceof Error ? error.message : "加入生词本失败。",
      });
    }
  }

  async function saveHighlight(options?: {
    color?: UserAnnotationColorDto;
    selection?: ReaderTextSelection | null;
    mode?: "create" | "recolor";
  }) {
    const requestedSelection = options?.selection ?? textSelection;
    const targetSelection =
      options?.mode !== "recolor" &&
      requestedSelection &&
      multiTextHighlightExtensionBase
        ? combinedMultiTextSelection(requestedSelection, multiTextHighlightExtensionBase) ?? requestedSelection
        : requestedSelection;
    const targetSentence = targetSelection?.sentence ?? activeSentence;

    if (!targetSentence) {
      setAnnotationSaveState({ kind: "error", message: "请先选中一句或一个片段。" });
      return;
    }

    setAnnotationSaveState({
      kind: "saving",
      message: options?.mode === "recolor" ? "正在更新高亮…" : "正在保存高亮…",
    });

    const color = options?.color ?? annotationColor;
    const existingTargetAnnotation = targetSelection
      ? annotations.find(
          (item) =>
            belongsToCurrentRecord(item.recordId, item.targetKey, record.id) &&
            annotationMatchesSelection(item, targetSelection),
        ) ?? null
      : (annotationsBySentence.get(targetSentence.sentenceId)?.annotations ?? []).find(
          (item) => item.anchorType === "sentence",
        ) ?? null;

    if (options?.mode === "recolor" && existingTargetAnnotation?.type === "highlight") {
      try {
        const updatedAnnotation = await patchAnnotation(
          existingTargetAnnotation.id,
          { color },
          "高亮更新失败。",
        );
        const updatedSelection =
          selectionFromAnnotation(updatedAnnotation, {
            preferredSentenceId: targetSelection?.sentence.sentenceId,
          }) ?? targetSelection;
        if (updatedSelection) {
          focusReaderSelection(updatedSelection, {
            openHighlightPalette: true,
            source: "programmatic",
            visualMode: "annotation_hover",
            activeAnnotationTargetKey: updatedAnnotation.targetKey,
            hoveredAnnotationTargetKey: updatedAnnotation.targetKey,
            toolbarVisible: true,
          });
          window.getSelection()?.removeAllRanges();
        }
        setAnnotationSaveState({ kind: "saved", message: "高亮颜色已更新。" });
        setHighlightPaletteOpen(true);
      } catch (error) {
        setAnnotationSaveState({
          kind: "error",
          message: error instanceof Error ? error.message : "高亮更新失败。",
        });
      }
      return;
    }

    const anchorPayload = targetSelection
      ? anchorPayloadFromSelection(record.id, targetSelection)
      : anchorPayloadFromSentence(record.id, targetSentence);
    const body: WebAnnotationCreateRequest = annotationRequestFromAnchorPayload(anchorPayload, {
      color,
      sentenceTextById,
      translationBySentence: translationModelBySentence,
    });

    try {
      const response = await fetch("/api/web/annotations", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = (await response.json().catch(() => ({ ok: false, message: "请求失败" }))) as
        | { ok: true; item: WebAnnotationVm }
        | { ok: false; message?: string };

      if (!response.ok || !payload.ok) {
        setAnnotationSaveState({
          kind: "error",
          message: payload.ok === false && payload.message ? payload.message : "高亮保存失败。",
        });
        return;
      }

      mergeAnnotation(payload.item);
      if (payload.item.supersededIds?.length) {
        for (const supersededId of payload.item.supersededIds) {
          removeAnnotation(supersededId);
        }
      }
      const nextSelection =
        payload.item.type === "highlight"
          ? selectionFromAnnotation(payload.item)
          : null;
      const requestedTargetKey = requestedSelection ? targetKeyForSelection(record.id, requestedSelection) : null;
      const targetSelectionKey = targetSelection ? targetKeyForSelection(record.id, targetSelection) : null;
      const exactRecalled =
        Boolean(nextSelection) &&
        payload.item.targetKey === targetKeyForSelection(record.id, nextSelection!);
      const shouldReplaceSelection =
        Boolean(nextSelection) &&
        (targetSelectionKey !== requestedTargetKey || payload.item.targetKey !== requestedTargetKey);
      if (nextSelection && (shouldReplaceSelection || exactRecalled)) {
        focusReaderSelection(nextSelection, {
          openHighlightPalette: true,
          source: "programmatic",
          visualMode: exactRecalled ? "annotation_hover" : "selection",
          activeAnnotationTargetKey: payload.item.targetKey,
          hoveredAnnotationTargetKey: payload.item.targetKey,
          toolbarVisible: true,
        });
        if (exactRecalled) {
          window.getSelection()?.removeAllRanges();
        }
      }
      window.dispatchEvent(
        new CustomEvent<WebAnnotationVm>(ANNOTATION_CREATED_EVENT, { detail: payload.item }),
      );
      setAnnotationSaveState({ kind: "saved", message: "已高亮当前选区。" });
      setHighlightPaletteOpen(true);
    } catch (error) {
      setAnnotationSaveState({
        kind: "error",
        message: error instanceof Error ? error.message : "高亮保存失败。",
      });
    }
  }

  function closeReaderNoteUi() {
    setPendingReaderNote(null);
    setPendingReaderNoteSource(null);
    setActiveReaderNoteId(null);
    setFocusedReaderNoteTarget(null);
    setReaderNoteDraft("");
    setReaderNoteSaveState({ kind: "idle" });
    setNotePanelOpen(false);
  }

  function focusReaderNote(note: WebReaderNoteVm) {
    closeAiWorkspace({ clearSelectionIfLinked: true });
    clearReaderSelection({ preserveDomSelection: true });
    setActiveReaderNoteId(note.id);
    setPendingReaderNote(null);
    setPendingReaderNoteSource(null);
    setReaderNoteDraft(note.noteText);
    const nextJumpTarget = readerNoteJumpTarget(note);
    setFocusedReaderNoteTarget(nextJumpTarget);
    setReaderNoteSaveState({ kind: "idle" });
    setNotePanelOpen(true);
  }

  async function createDictionaryAINote() {
    if (!activeLookup || dictionaryAI.kind !== "ready") {
      setDictionaryAINoteState({ kind: "error", message: "请先生成可用的 AI 结果。" });
      return;
    }

    const sentence = sentenceById.get(activeLookup.sentenceId);
    if (!sentence) {
      setDictionaryAINoteState({ kind: "error", message: "当前定位不到原文句子，暂时无法生成笔记。" });
      return;
    }

    const request = dictionaryAINoteRequestFromLookup(activeLookup, sentence, dictionaryAI.result);
    if (!request) {
      setDictionaryAINoteState({ kind: "error", message: "当前结果缺少精确原文锚点，暂时无法生成笔记。" });
      return;
    }

    const existing = readerNotesByTargetKey.get(noteTargetKeyFromRequest(request)) ?? null;
    if (existing) {
      focusReaderNote(existing);
      setDictionaryAINoteState({ kind: "saved", message: "该位置已有笔记，已为你打开。" });
      return;
    }

    setDictionaryAINoteState({ kind: "saving" });
    try {
      const response = await fetch("/api/web/reader-notes", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(request),
      });
      const payload = (await response.json().catch(() => ({ ok: false, message: "请求失败" }))) as
        | { ok: true; item: WebReaderNoteVm }
        | { ok: false; message?: string };
      if (!response.ok || !payload.ok) {
        setDictionaryAINoteState({
          kind: "error",
          message: payload.ok === false && payload.message ? payload.message : "AI 笔记生成失败。",
        });
        return;
      }

      mergeReaderNote(payload.item);
      focusReaderNote(payload.item);
      setDictionaryAINoteState({ kind: "saved", message: "AI 笔记已生成。" });
    } catch (error) {
      setDictionaryAINoteState({
        kind: "error",
        message: error instanceof Error ? error.message : "AI 笔记生成失败。",
      });
    }
  }

  function openReaderNoteComposer(
    request: WebReaderNoteCreateRequest,
    existing?: WebReaderNoteVm | null,
    source: PendingReaderNoteSource = "selection",
  ) {
    setSettingsPanelOpen(false);
    setContextPanelOpen(false);
    closeAiWorkspace({ clearSelectionIfLinked: true });
    setSelectionToolbarVisible(false);

    if (existing) {
      focusReaderNote(existing);
      return;
    }

    setActiveReaderNoteId(null);
    setPendingReaderNote(request);
    setPendingReaderNoteSource(source);
    setReaderNoteDraft("");
    const nextJumpTarget = readerNoteJumpTargetFromRequest(request);
    setFocusedReaderNoteTarget(nextJumpTarget);
    setReaderNoteSaveState({ kind: "idle" });
    setNotePanelOpen(true);
  }

  function highlightTextSelection(colorValue: string) {
    if (!textSelection) {
      return;
    }
    const color = isUserAnnotationColor(colorValue) ? colorValue : annotationColor;
    setAnnotationColor(color);
    void saveHighlight({
      color,
      selection: textSelection,
      mode: hasExactSelectedHighlight ? "recolor" : "create",
    });
  }

  function openTextSelectionNote() {
    if (!textSelection) {
      return;
    }
    setActiveSentence(textSelection.sentence);
    openReaderNoteComposer(
      noteRequestFromSelection(record.id, textSelection),
      selectedReaderNote,
      "selection",
    );
  }

  function openSentenceNote(sentence: SentenceModel) {
    const request = noteRequestFromSentence(record.id, sentence);
    const existing = readerNotesByTargetKey.get(noteTargetKeyFromRequest(request)) ?? null;
    openReaderNoteComposer(request, existing, "sentence");
  }

  function openSentenceNotes(sentenceId: string, _anchorEl?: HTMLElement) {
    const sentenceNotes = readerNotesBySentence.get(sentenceId) ?? [];
    if (sentenceNotes.length === 0) {
      return;
    }
    setContextPanelOpen(false);
    setSentencePopoverAnchorEl(null);
    closeAiWorkspace({ clearSelectionIfLinked: true });
    setSelectionToolbarVisible(false);
    setNotePanelOpen(true);

    const activeInSentence = sentenceNotes.find((note) => note.id === activeReaderNoteId) ?? null;
    if (activeInSentence) {
      return;
    }

    const firstNote = sentenceNotes[0];
    if (firstNote) {
      focusReaderNote(firstNote);
    }
  }

  function closeSentenceNotes() {
    closeReaderNoteUi();
  }

  async function saveActiveReaderNote() {
    const trimmed = readerNoteDraft.trim();
    if (!trimmed) {
      setReaderNoteSaveState({ kind: "error", message: "请先输入笔记内容。" });
      return;
    }

    if (activeReaderNote) {
      setReaderNoteSaveState({ kind: "saving" });
      try {
        const response = await fetch("/api/web/reader-notes/" + encodeURIComponent(activeReaderNote.id), {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ noteText: trimmed }),
        });
        const payload = (await response.json().catch(() => ({ ok: false, message: "请求失败" }))) as
          | { ok: true; item: WebReaderNoteVm }
          | { ok: false; message?: string };
        if (!response.ok || !payload.ok) {
          setReaderNoteSaveState({
            kind: "error",
            message: payload.ok === false && payload.message ? payload.message : "笔记保存失败。",
          });
          return;
        }
        mergeReaderNote(payload.item);
        setReaderNoteDraft(payload.item.noteText);
        setReaderNoteSaveState({ kind: "saved", message: "笔记已保存。" });
        return;
      } catch (error) {
        setReaderNoteSaveState({
          kind: "error",
          message: error instanceof Error ? error.message : "笔记保存失败。",
        });
        return;
      }
    }

    if (!pendingReaderNote) {
      setReaderNoteSaveState({ kind: "error", message: "当前没有可保存的笔记选区。" });
      return;
    }

    setReaderNoteSaveState({ kind: "saving" });
    try {
      const response = await fetch("/api/web/reader-notes", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          ...pendingReaderNote,
          noteText: trimmed,
        }),
      });
      const payload = (await response.json().catch(() => ({ ok: false, message: "请求失败" }))) as
        | { ok: true; item: WebReaderNoteVm }
        | { ok: false; message?: string };
      if (!response.ok || !payload.ok) {
        setReaderNoteSaveState({
          kind: "error",
          message: payload.ok === false && payload.message ? payload.message : "笔记保存失败。",
        });
        return;
      }
      mergeReaderNote(payload.item);
      focusReaderNote(payload.item);
      setReaderNoteSaveState({ kind: "saved", message: "笔记已保存。" });
      setNotePanelOpen(true);
    } catch (error) {
      setReaderNoteSaveState({
        kind: "error",
        message: error instanceof Error ? error.message : "笔记保存失败。",
      });
    }
  }

  async function deleteReaderNote(note: WebReaderNoteVm) {
    if (!note) {
      setPendingReaderNote(null);
      setPendingReaderNoteSource(null);
      setReaderNoteDraft("");
      setReaderNoteSaveState({ kind: "idle" });
      return;
    }

    setReaderNoteSaveState({ kind: "saving" });
    try {
      const response = await fetch("/api/web/reader-notes/" + encodeURIComponent(note.id), {
        method: "DELETE",
      });
      const payload = (await response.json().catch(() => response.status === 204 ? { ok: true } : { ok: false, message: "删除失败" })) as { ok: true } | { ok: false; message?: string };
      if (!response.ok || !payload.ok) {
        setReaderNoteSaveState({
          kind: "error",
          message: payload.ok === false && payload.message ? payload.message : "笔记删除失败。",
        });
        return;
      }
      const siblingNotes = (readerNotesBySentence.get(note.anchorSentenceId) ?? []).filter(
        (candidate) => candidate.id !== note.id,
      );
      removeReaderNote(note.id);
      if (activeReaderNoteId === note.id) {
        const nextNote = siblingNotes[0] ?? null;
        if (nextNote) {
          focusReaderNote(nextNote);
        } else {
          setActiveReaderNoteId(null);
          setFocusedReaderNoteTarget(null);
          setReaderNoteDraft("");
          setNotePanelOpen(false);
        }
      }
      setReaderNoteSaveState({ kind: "saved", message: "笔记已删除。" });
    } catch (error) {
      setReaderNoteSaveState({
        kind: "error",
        message: error instanceof Error ? error.message : "笔记删除失败。",
      });
    }
  }

  async function deleteTextSelectionAnnotation() {
    if (!selectedAnnotation) {
      return;
    }

    setAnnotationSaveState({ kind: "saving", message: "正在取消高亮…" });
    try {
      const response = await fetch("/api/web/annotations/" + encodeURIComponent(selectedAnnotation.id), {
        method: "DELETE",
      });
      const payload = (await response.json().catch(() => response.status === 204 ? { ok: true } : { ok: false, message: "删除失败" })) as { ok: true } | { ok: false; message?: string };

      if (!response.ok || !payload.ok) {
        setAnnotationSaveState({
          kind: "error",
          message: payload.ok === false && payload.message ? payload.message : "取消高亮失败。",
        });
        return;
      }

      setAnnotations((current) => current.filter((existing) => existing.id !== selectedAnnotation.id));
      setHighlightPaletteOpen(false);
      setAnnotationSaveState({ kind: "saved", message: "已取消高亮。" });
    } catch (error) {
      setAnnotationSaveState({
        kind: "error",
        message: error instanceof Error ? error.message : "取消高亮失败。",
      });
    }
  }

  function selectCurrentSentenceFromToolbar() {
    if (!textSelection || textSelection.anchorType !== "text_range") {
      return;
    }

    const sentence = textSelection.sentence;
    const sentenceHighlight =
      (annotationsBySentence.get(sentence.sentenceId)?.annotations ?? []).find(
        (item) => item.type === "highlight" && item.anchorType === "sentence",
      ) ?? null;
    focusReaderSelection(selectionFromSentence(sentence), {
      openHighlightPalette: Boolean(sentenceHighlight),
      visualMode: "selection",
      activeAnnotationTargetKey: sentenceHighlight?.targetKey ?? null,
      hoveredAnnotationTargetKey: null,
      toolbarVisible: true,
    });
    window.getSelection()?.removeAllRanges();
  }

  function lookupTextSelection() {
    if (!textSelection) {
      return;
    }

    const intent = lookupIntentFromSelection(
      textSelection,
      translationBySentence.get(textSelection.sentence.sentenceId),
    );
    handleLookupIntent(intent, null, { showPreview: false, openRail: true });
  }

  function appendAskAttachments(nextAttachments: ReaderAskAttachment[]) {
    setAskAttachments((current) => {
      const merged = [...current];
      const seen = new Set(current.map((attachment) => askAttachmentKey(attachment)));
      nextAttachments.forEach((attachment) => {
        const key = askAttachmentKey(attachment);
        if (!seen.has(key)) {
          merged.push(attachment);
          seen.add(key);
        }
      });
      return merged;
    });
  }

  function removeAskAttachment(attachmentKey: string) {
    if (liveContextAttachment && attachmentKey === askAttachmentKey(liveContextAttachment)) {
      setLiveContextSelection(null);
      setComposerTextareaFocused(false);
      if (activeSelectionMatchesLiveContext) {
        clearReaderSelection({ preserveDomSelection: true });
      }
      return;
    }
    setAskAttachments((current) => current.filter((attachment) => askAttachmentKey(attachment) !== attachmentKey));
  }

  function clearAskAttachments() {
    setAskAttachments([]);
  }

  function openAskWithAttachments(nextAttachments: ReaderAskAttachment[]) {
    if (nextAttachments.length === 0) {
      return;
    }
    appendAskAttachments(nextAttachments);
    setLiveContextSelection(null);
    openAiWorkspace();
  }

  function openAskWithSelection() {
    if (!textSelection) {
      return;
    }
    setActiveSentence(textSelection.sentence);
    setLiveContextSelection(textSelection);
    openAiWorkspace();
  }

  function triggerAskQuickAction(config: {
    content: string;
    entryAction: "explain_this" | "why_here";
    attachment: ReaderAskAttachment;
  }) {
    if (askAttachments.length > 0) {
      const shouldReplace = window.confirm("当前输入框里还有待发送的上下文。快捷分析将只保留当前选中的分析对象，是否继续替换？");
      if (!shouldReplace) {
        return;
      }
      clearAskAttachments();
    }
    setPendingAskQuickAction({
      content: config.content,
      entryAction: config.entryAction,
      attachments: [config.attachment],
    });
    setLiveContextSelection(null);
    openAiWorkspace();
  }

  function activateLiveContextSelection() {
    if (!liveContextSelection) {
      return;
    }
    setComposerTextareaFocused(false);
    focusReaderSelection(liveContextSelection, {
      source: "programmatic",
      visualMode: "selection",
      toolbarVisible: true,
    });
  }

  function handleComposerTextareaFocus() {
    setComposerTextareaFocused(true);
    if (!liveContextSelection) {
      return;
    }
    focusReaderSelection(liveContextSelection, {
      source: "programmatic",
      visualMode: "context",
      toolbarVisible: false,
    });
    window.getSelection()?.removeAllRanges();
  }

  function handleComposerTextareaBlur() {
    setComposerTextareaFocused(false);
  }

  function handleAiPanelPointerDownOutsideComposer() {
    if (!composerTextareaFocused && !textSelection) {
      return;
    }
    setComposerTextareaFocused(false);
    if (textSelection) {
      clearReaderSelection({ preserveDomSelection: true });
    }
  }

  function openAskWithSentenceContext() {
    if (!activeSentence) {
      return;
    }
    openAskWithAttachments([
      askAttachmentFromSentence(pageIdentity, activeSentence, {
        sourceSurface: "context_panel",
        entryAction: "explain_this",
      }),
    ]);
  }

  function openAskWithTranslation(sentenceId: string, translationZh: string) {
    const sentence = sentenceById.get(sentenceId);
    if (!sentence) {
      return;
    }
    setActiveSentence(sentence);
    openAskWithAttachments([
      askAttachmentFromTranslation(pageIdentity, sentence, translationZh, {
        sourceSurface: "translation",
        entryAction: "ask_about_this",
      }),
    ]);
  }

  function openAskWithAnalysis(sentenceId: string, entryId: string) {
    const sentence = sentenceById.get(sentenceId);
    const entry = reader.sentenceEntries.find((item) => item.id === entryId);
    if (!sentence || !entry) {
      return;
    }
    if (
      entry.entryType !== "grammar_note" &&
      entry.entryType !== "sentence_analysis" &&
      entry.entryType !== "term_note" &&
      entry.entryType !== "logic_note" &&
      entry.entryType !== "interpretation_note"
    ) {
      return;
    }
    setActiveSentence(sentence);
    openAskWithAttachments([
      askAttachmentFromAnalysisBlock(
        pageIdentity,
        sentence,
        {
          entryId: entry.id,
          entryType: entry.entryType,
          label: entryLabel(entry),
          title: entryLabel(entry),
          content: entry.content,
          sourceKind: entry.sourceKind,
          supplementId: entry.supplementId,
        },
        {
          sourceSurface: "analysis_block",
          entryAction: "explain_this",
        },
      ),
    ]);
  }

  function openAskWithContentSummary(summary: ReaderContentSummaryNode) {
    openAskWithAttachments([
      askAttachmentFromContentSummary(pageIdentity, summary, {
        sourceSurface: "content_summary",
        entryAction: "explain_this",
      }),
    ]);
  }

  function openAskWithAnnotation(annotation: WebAnnotationVm) {
    openAskWithAttachments([
      askAttachmentFromAnnotation(pageIdentity, annotation, {
        sourceSurface: "annotation",
        entryAction: "ask_about_this",
      }),
    ]);
  }

  function openAskWithReaderNote(note: WebReaderNoteVm) {
    openAskWithAttachments([
      askAttachmentFromReaderNote(pageIdentity, note, {
        sourceSurface: "note_card",
        entryAction: "ask_about_this",
      }),
    ]);
  }

  function openAskWithStructuredInspect(intent: ReaderStructuredInspectIntent) {
    const sentence = sentenceById.get(intent.sentenceId);
    if (!sentence) {
      return;
    }
    setActiveSentence(sentence);
    openAskWithAttachments([
      askAttachmentFromStructuredInspect(pageIdentity, intent, sentence, {
        sourceSurface: "dictionary_inspect",
        entryAction: "lookup_in_context",
      }),
    ]);
  }

  function applySupplementProjection(projection: Record<string, unknown>) {
    setReaderScene((current) => {
      const exists = current.sentenceEntries.some((entry) => entry.id === String(projection.id ?? ""));
      if (exists) {
        return current;
      }
      return {
        ...current,
        sentenceEntries: [
          ...current.sentenceEntries,
          {
            id: String(projection.id ?? ""),
            sentenceId: String(projection.sentence_id ?? ""),
            entryType: String(projection.entry_type ?? "grammar_note") as SentenceEntryModel["entryType"],
            label: String(projection.label ?? "AI 补充语法旁注"),
            title: typeof projection.title === "string" ? projection.title : undefined,
            content: String(projection.content ?? ""),
            sourceKind: projection.source_kind === "ask_supplement" ? "ask_supplement" : "workflow",
            supplementId: typeof projection.supplement_id === "string" ? projection.supplement_id : undefined,
            deletable: Boolean(projection.deletable),
            createdFromTurnRunId:
              typeof projection.created_from_turn_run_id === "string"
                ? projection.created_from_turn_run_id
                : undefined,
          },
        ],
      };
    });
  }

  async function deleteAnalysisSupplement(supplementId: string) {
    const response = await fetch(`/api/web/reader-ask/supplements/${supplementId}`, {
      method: "DELETE",
      headers: { "content-type": "application/json" },
    });
    if (!response.ok) {
      return;
    }
    setReaderScene((current) => ({
      ...current,
      sentenceEntries: current.sentenceEntries.filter((entry) => entry.supplementId !== supplementId),
    }));
  }

  function handleAskActionExecuted(result: Record<string, unknown>) {
    const projection = result.supplement_projection;
    if (projection && typeof projection === "object" && !Array.isArray(projection)) {
      applySupplementProjection(projection as Record<string, unknown>);
    }
  }

  function jumpToAnnotation(annotation: WebAnnotationVm, anchorEl?: HTMLElement, sentenceId?: string) {
    const nextSelection = selectionFromAnnotation(annotation, {
      preferredSentenceId: sentenceId,
      anchorEl,
    });
    if (nextSelection) {
      setContextPanelOpen(false);
      setSentencePopoverAnchorEl(null);
        focusReaderSelection(nextSelection, {
          openHighlightPalette: annotation.type === "highlight",
          visualMode: annotation.type === "highlight" ? "annotation_hover" : "selection",
          activeAnnotationTargetKey: annotation.targetKey,
          hoveredAnnotationTargetKey: annotation.targetKey,
          toolbarVisible: annotation.type === "highlight",
        });
      window.requestAnimationFrame(() => {
        articleRef.current
          ?.querySelector<HTMLElement>(`#reader-sentence-${CSS.escape(nextSelection.sentence.sentenceId)}`)
          ?.scrollIntoView({ block: "center", behavior: "smooth" });
      });
    }
  }

  function lookupPhraseFromInspect(intent: ReaderStructuredInspectIntent) {
    const nextIntent = lookupIntentFromStructuredInspect(intent);
    handleLookupIntent(nextIntent, lookupPreviewAnchor, { showPreview: false, openRail: true });
  }

  function jumpToAskAttachment(attachment: ReaderAskAttachment) {
    const nextJumpTarget = jumpTargetFromAskAttachment(attachment, {
      annotations,
    });
    if (nextJumpTarget) {
      setJumpTarget(nextJumpTarget);
    }
  }

  function jumpToAskCitation(citation: ReaderAskCitationDto) {
    const nextJumpTarget = jumpTargetFromAskCitation(citation, record.id, {
      annotations,
    });
    if (nextJumpTarget) {
      setJumpTarget(nextJumpTarget);
      return;
    }

    if (citation.record_id === record.id && citation.sentence_id) {
      const sentence = sentenceById.get(citation.sentence_id);
      if (!sentence) {
        return;
      }
      const fallbackJumpTarget = jumpToTargetRef(sentenceToTargetRef(record.id, sentence));
      if (fallbackJumpTarget) {
        setJumpTarget(fallbackJumpTarget);
      }
    }
  }

  function selectSentence(sentence: SentenceModel, anchorEl?: HTMLElement | null) {
    setActiveSentence(sentence);
    lastSentencePopoverTriggerRef.current = anchorEl ?? null;
    setExpandedAnalysisEntryIds([]);
    setActiveEntryId(null);
    const sentenceAnnotation =
      (annotationsBySentence.get(sentence.sentenceId)?.annotations ?? []).find(
        (item) => item.type === "highlight" && item.anchorType === "sentence",
      ) ?? null;
    setAnnotationColor(sentenceAnnotation?.color ?? "warm_yellow");
    focusReaderSelection(selectionFromSentence(sentence, anchorEl), {
      openHighlightPalette: Boolean(sentenceAnnotation),
      visualMode: "selection",
      activeAnnotationTargetKey: sentenceAnnotation?.targetKey ?? null,
      hoveredAnnotationTargetKey: null,
      toolbarVisible: true,
    });
    window.getSelection()?.removeAllRanges();
  }

  function toggleSentenceActions(sentenceId: string, anchorEl?: HTMLElement | null) {
    const sentence = sentenceById.get(sentenceId);
    if (!sentence) {
      return;
    }

    lastSentencePopoverTriggerRef.current = anchorEl ?? null;

    if (contextPanelOpen && activeSentence?.sentenceId === sentenceId) {
      closeContextPanel();
      return;
    }

    const sentenceAnnotation =
      (annotationsBySentence.get(sentence.sentenceId)?.annotations ?? []).find(
        (item) => item.type === "highlight" && item.anchorType === "sentence",
      ) ?? null;

    setAnnotationColor(sentenceAnnotation?.color ?? "warm_yellow");
    setActiveSentence(sentence);
    setExpandedAnalysisEntryIds([]);
    setActiveEntryId(null);
    setHoveredAnnotationTargetKey(null);
    clearReaderSelection();
    setLookupPreviewOpen(false);
    setLookupPreviewAnchor(null);
    setSettingsPanelOpen(false);
    setSentencePopoverAnchorEl(anchorEl ?? null);
    setContextPanelOpen(true);
  }

  function openSettingsPanel() {
    setSettingsPanelOpen(true);
    setContextPanelOpen(false);
    setSentencePopoverAnchorEl(null);
  }

  function toggleAiWorkspace() {
    if (aiOpen) {
      closeAiWorkspace({ clearSelectionIfLinked: true });
      return;
    }
    openAiWorkspace();
  }

  function closeContextPanel() {
    const trigger = sentencePopoverAnchorEl ?? lastSentencePopoverTriggerRef.current;
    setContextPanelOpen(false);
    setSentencePopoverAnchorEl(null);
    setActiveSentence(null);
    setExpandedAnalysisEntryIds([]);
    setActiveEntryId(null);
    clearReaderSelection();
    window.requestAnimationFrame(() => {
      trigger?.focus();
    });
  }

  function toggleAnalysisEntry(entryId: string) {
    setExpandedAnalysisEntryIds((current) => {
      const isExpanding = !current.includes(entryId);
      if (isExpanding) {
        setActiveEntryId(entryId);
        const entry = reader.sentenceEntries.find((e) => e.id === entryId);
        if (entry && entry.entryType === "grammar_note") {
          // Find all other grammar notes for the same sentence
          const otherGrammarEntryIds = reader.sentenceEntries
            .filter((e) => e.sentenceId === entry.sentenceId && e.entryType === "grammar_note" && e.id !== entryId)
            .map((e) => e.id);
          // Filter out the other grammar notes of the same sentence
          return [...current.filter((id) => !otherGrammarEntryIds.includes(id)), entryId];
        }
        return [...current, entryId];
      } else {
        // If we are collapsing, we should clear the active state if it was this entry
        setActiveEntryId((currActive) => currActive === entryId ? null : currActive);
        return current.filter((id) => id !== entryId);
      }
    });
    setContextPanelOpen(false);
    setSentencePopoverAnchorEl(null);
  }

  function setAnalysisEntryFocus(entryId: string, focused: boolean) {
    setActiveEntryId((current) => {
      if (focused) {
        return entryId;
      }
      // If we just left the currently active entry
      if (current === entryId) {
        // If the entry we just left is expanded, KEEP it active (persistent study mode)
        if (expandedAnalysisEntryIds.includes(entryId)) {
          return entryId;
        }
        // If the entry we left is NOT expanded, but there are OTHER expanded entries,
        // fall back to the last expanded one instead of clearing focus mode.
        if (expandedAnalysisEntryIds.length > 0) {
          return expandedAnalysisEntryIds[expandedAnalysisEntryIds.length - 1] ?? null;
        }
        return null;
      }
      return current;
    });
  }

  const isImmersiveMode = readerSettings.mode === "immersive";
  const managedSelectionVisible = Boolean(textSelection && textSelectionVisualMode === "selection");
  const canvasThemeClass = readerThemeClassName(themeName);
  const readingTypography = readerModeTypography(readerSettings);
  const readingClass = readingTypography.bodyClassName;
  const translationClass = readingTypography.translationClassName;
  const readingColumnClass = readingTypography.columnClassName;
  const paragraphDensityClass = readingTypography.paragraphDensityClassName;
  const contentVisibility = modeVisibility(readerSettings.mode);
  const showTranslation = modeShowsTranslation(readerSettings.mode);
  const contextPanelVisible = Boolean(contextPanelOpen && activeSentence);
  const compactDictionaryPanelVisible = Boolean(
    dictionaryPanelVisible && !dictionaryDockLayout && !contextPanelVisible && !settingsPanelOpen,
  );
  const floatingLookupPreviewVisible = Boolean(
    lookupPreviewOpen && lookupPreviewAnchor && (activeLookup || activeInspect) && !settingsPanelOpen && !contextPanelVisible,
  );
  const compactSurfaceBottom = "max(5.25rem, calc(env(safe-area-inset-bottom) + 4.25rem))";
  const mobileSettingsPanelStyle = compactDictionaryPanelVisible
    ? ({ bottom: "min(calc(72vh + 6.5rem), calc(100vh - 18rem))" } satisfies CSSProperties)
    : undefined;
  const shellModeClass = isImmersiveMode ? "reader-shell--immersive" : "reader-shell--intensive";

  const headerShellClass = `reader-header-band reader-header-band--immersive sticky top-3 z-20 bg-background/88 backdrop-blur transition-[padding,background-color,border-color,box-shadow,transform] border-b-0 px-5 py-6 sm:px-8 lg:px-10 lg:py-8 shadow-none reader-header-band--clean ${isImmersiveMode && immersiveHeaderHidden ? "reader-header-band--hidden" : ""}`;
  const headerTitleClass = isImmersiveMode
    ? `font-headline font-semibold tracking-[-0.02em] text-ink transition-[font-size,max-width,margin] mt-3 max-w-[20ch] text-[3rem] leading-[0.94] md:text-[4.35rem]`
    : "mt-2 max-w-[24ch] font-headline text-3xl font-semibold leading-tight tracking-normal text-ink md:text-[2.35rem]";
  const headerMetaClass = `flex flex-wrap items-center gap-2 text-muted transition-[opacity,max-height,margin] ${
    isImmersiveMode
      ? "mt-4 max-h-16 text-[0.74rem] opacity-100"
      : "mt-3 text-xs opacity-100"
  }`;
  const headerEyebrowClass = isImmersiveMode
    ? "text-[0.72rem] font-semibold tracking-[0.22em] text-lens-blue"
    : "text-xs font-semibold text-muted";

  const formattedDate = useMemo(() => {
    try {
      if (!record.createdAt) return "";
      const d = new Date(record.createdAt);
      if (isNaN(d.getTime())) return "";
      return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
    } catch (e) {
      return "";
    }
  }, [record.createdAt]);

  const readingGoalLabel = useMemo(() => {
    const goals: Record<string, string> = {
      daily_reading: "日常阅读",
      academic: "学术及行业阅读",
      exam: "备考精读",
    };
    return goals[record.readingGoal] || "透读文章";
  }, [record.readingGoal]);

  const articleSourceInfo = useMemo(() => {
    try {
      const payload = record.requestPayloadJson ?? {};
      const urlStr = (payload.url || payload.source_url || "") as string;
      if (urlStr) {
        const url = new URL(urlStr);
        let name = url.hostname.split(".")[0];
        if (name) {
          name = name.charAt(0).toUpperCase() + name.slice(1);
        }
        return {
          domain: url.hostname.replace("www.", ""),
          name: name || "外部来源",
          url: urlStr,
        };
      }
    } catch (e) {
      // ignore
    }
    return null;
  }, [record.requestPayloadJson]);

  function handleReaderSettingsChange(next: ReaderSettingsState) {
    setReaderSettings(next);
  }

  return (
    <main className="paper-grain reader-shell-page min-h-screen px-3 pb-24 pt-3 text-ink sm:px-4 md:pb-6 lg:px-5">
      <div className="relative">
        <div className="relative min-w-0">
        <article
            ref={articleRef}
            className={`reader-shell min-w-0 overflow-visible rounded-panel border border-hairline shadow-surface-quiet ${shellModeClass} ${canvasThemeClass} ${
              managedSelectionVisible ? "reader-shell--managed-selection" : ""
            }`}
            onPointerDownCapture={(event) => {
            if (event.button !== 0) {
              return;
            }

            const target = event.target instanceof HTMLElement ? event.target : null;
            if (!target) {
              return;
            }

            if (
              target.closest(
                "button,a,[role='dialog'],[data-reader-sentence-popover='true'],[data-reader-sentence-handle='true']",
              )
            ) {
              pointerSelectionActiveRef.current = false;
              return;
            }

            if (!target.closest("[data-reader-sentence-text='true']")) {
              pointerSelectionActiveRef.current = false;
              return;
            }

            pointerSelectionActiveRef.current = true;
            if (textSelectionSourceRef.current === "dom" || textSelection) {
              clearReaderSelection({ preserveDomSelection: true });
            }
          }}
            onClick={(event) => {
            const target = event.target instanceof HTMLElement ? event.target : null;
            const nativeSelection = window.getSelection();
            if (nativeSelection && !nativeSelection.isCollapsed && nativeSelection.toString().trim()) {
              return;
            }
            if (
              target?.closest(
                "button,a,[role='dialog'],[data-reader-mark-id],[data-reader-sentence-popover='true'],[data-reader-sentence-handle='true']",
              )
            ) {
              if (lookupPreviewOpen) {
                dismissLookupPreview();
              }
              return;
            }

            if (lookupPreviewOpen) {
              dismissLookupPreview();
            }

            if (textSelection) {
              clearReaderSelection();
            }
          }}
          onKeyUp={(event) => {
            if (event.key === "Escape") {
              clearReaderSelection();
              return;
            }
            commitDomTextSelection();
          }}
        >
          <header className={headerShellClass}>
            <div
              ref={readingColumnRef}
              className="reader-header-band-inner mx-auto flex max-w-[82ch] w-full flex-col gap-6 lg:gap-8"
            >
                {/* 1. Eyebrow */}
                <div className="flex items-center gap-1.5 text-[0.8rem] font-semibold tracking-wide leading-none">
                  <span className="text-lens-blue">
                    {isImmersiveMode ? "沉浸阅读" : "精读模式"}
                  </span>
                  <span className="text-muted/60">·</span>
                  <span className="text-muted font-medium">
                    {formattedDate || "今日"}
                  </span>
                </div>

                {/* 2. Main Title & Overview */}
                <div className="min-w-0">
                  <h1 className="font-headline text-[clamp(2rem,4vw,3.25rem)] font-bold leading-[1.08] text-ink tracking-tight">
                    {record.title}
                  </h1>

                  {/* 3. Subtitle / Overview */}
                  {reader.contentSummary?.overview ? (
                    <p className="mt-4 max-w-[72ch] text-[1.025rem] font-medium leading-[1.68] text-muted font-sans tracking-wide">
                      {reader.contentSummary.overview}
                    </p>
                  ) : null}
                </div>

                {/* 4. Action & Control Bar */}
                <div className="w-full border-t border-b border-hairline py-0 flex flex-col sm:flex-row items-stretch justify-between min-h-[56px] bg-transparent">
                  {/* Left Metadata Status Block */}
                  <div className="flex items-center gap-3.5 py-3 sm:py-0">
                    <span className="px-3 py-1 text-[0.75rem] font-semibold text-ink-soft bg-surface-warm border border-hairline/80 rounded-[0.5rem] flex items-center gap-1.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.8),0_1px_2px_rgba(0,0,0,0.03)] select-none">
                      <Sparkles className="h-3.5 w-3.5 text-vocab-amber fill-vocab-amber/10" />
                      <span>{dataSourceLabel[dataSource]}</span>
                    </span>
                    <div className="w-[1px] h-3.5 bg-hairline" />
                    <span className="text-[0.8rem] font-semibold text-muted">
                      {reader.article.sentences.length} 句
                    </span>
                    <div className="w-[1px] h-3.5 bg-hairline" />
                    <span className="text-[0.8rem] font-semibold text-muted">
                      {readingGoalLabel}
                    </span>
                  </div>

                  {/* Right Action Switchers Block */}
                  <div className="flex items-stretch divide-x divide-hairline border-t border-hairline sm:border-t-0 select-none">
                    {/* Button 1: Favorite */}
                    <FavoriteButton recordId={record.id} variant="action-bar" />

                    {/* Button 2: Intensive ("精读") */}
                    <button
                      type="button"
                      onClick={() => handleReaderSettingsChange({ ...readerSettings, mode: "intensive" })}
                      className={cn(
                        readerCommandControl,
                        "relative flex flex-1 justify-center rounded-none px-3.5 py-2.5 text-left sm:py-3.5 md:px-5",
                        readerSettings.mode === "intensive"
                          ? "text-vocab-amber after:absolute after:bottom-0 after:left-0 after:right-0 after:h-[2px] after:bg-vocab-amber"
                          : "text-ink hover:text-ink-soft",
                      )}
                    >
                      <BookOpen
                        aria-hidden="true"
                        className={`h-[18px] w-[18px] shrink-0 transition-transform ${
                          readerSettings.mode === "intensive" ? "text-vocab-amber" : "text-muted"
                        }`}
                        strokeWidth={1.5}
                      />
                      <span className="flex min-w-0 flex-col items-start leading-none whitespace-nowrap">
                        <span className="text-[0.85rem] font-semibold whitespace-nowrap">精读</span>
                        <span className="hidden sm:block mt-1 text-[0.65rem] font-medium text-subtle whitespace-nowrap">逐句研读</span>
                      </span>
                    </button>

                    {/* Button 3: Immersive ("沉浸") */}
                    <button
                      type="button"
                      onClick={() => handleReaderSettingsChange({ ...readerSettings, mode: "immersive" })}
                      className={cn(
                        readerCommandControl,
                        "relative flex flex-1 justify-center rounded-none px-3.5 py-2.5 text-left sm:py-3.5 md:px-5",
                        readerSettings.mode === "immersive"
                          ? "text-vocab-amber after:absolute after:bottom-0 after:left-0 after:right-0 after:h-[2px] after:bg-vocab-amber"
                          : "text-ink hover:text-ink-soft",
                      )}
                    >
                      <Eye
                        aria-hidden="true"
                        className={`h-[18px] w-[18px] shrink-0 transition-transform ${
                          readerSettings.mode === "immersive" ? "text-vocab-amber" : "text-muted"
                        }`}
                        strokeWidth={1.5}
                      />
                      <span className="flex min-w-0 flex-col items-start leading-none whitespace-nowrap">
                        <span className="text-[0.85rem] font-semibold whitespace-nowrap">沉浸</span>
                        <span className="hidden sm:block mt-1 text-[0.65rem] font-medium text-subtle whitespace-nowrap">专注阅读</span>
                      </span>
                    </button>

                    {/* Button 4: Settings */}
                    <button
                      ref={settingsButtonRef}
                      type="button"
                      aria-expanded={settingsPanelOpen}
                      aria-haspopup="dialog"
                      onClick={openSettingsPanel}
                      className={cn(
                        readerCommandControl,
                        "relative flex flex-1 justify-center rounded-none px-3.5 py-2.5 text-left sm:py-3.5 md:px-5",
                        settingsPanelOpen
                          ? "text-vocab-amber after:absolute after:bottom-0 after:left-0 after:right-0 after:h-[2px] after:bg-vocab-amber"
                          : "text-ink hover:text-ink-soft",
                      )}
                    >
                      <SlidersHorizontal
                        aria-hidden="true"
                        className={`h-[18px] w-[18px] shrink-0 transition-transform ${
                          settingsPanelOpen ? "text-vocab-amber" : "text-muted"
                        }`}
                        strokeWidth={1.5}
                      />
                      <span className="flex min-w-0 flex-col items-start leading-none whitespace-nowrap">
                        <span className="text-[0.85rem] font-semibold whitespace-nowrap">阅读设置</span>
                        <span className="hidden sm:block mt-1 text-[0.65rem] font-medium text-subtle whitespace-nowrap">版式与偏好</span>
                      </span>
                    </button>
                  </div>
                </div>

                {/* 5. Footer Metadata (sitting below the action bar) */}
                <div className="w-full flex flex-col sm:flex-row sm:items-center justify-between gap-3 sm:gap-0 text-[0.78rem] text-muted tracking-wide leading-normal sm:leading-none select-none">
                  <div className="flex flex-wrap items-center gap-1.5 font-medium">
                    <span>
                      {articleSourceInfo
                        ? `来源 ${articleSourceInfo.name} · ${articleSourceInfo.domain}`
                        : "来源 粘贴导入"}
                    </span>
                    {formattedDate && (
                      <>
                        <span className="text-muted/60">·</span>
                        <span>{formattedDate}</span>
                      </>
                    )}
                    {reader.article.sentences.length > 0 && (
                      <>
                        <span className="text-muted/60">·</span>
                        <span>约 {Math.max(1, Math.ceil(reader.article.sentences.length / 5))} 分钟阅读</span>
                      </>
                    )}
                  </div>

                  <div>
                    {articleSourceInfo?.url ? (
                      <a
                        href={articleSourceInfo.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="focus-ring inline-flex items-center gap-1.5 text-muted hover:text-ink transition-colors font-semibold cursor-pointer"
                      >
                        <Globe className="h-4 w-4 shrink-0" strokeWidth={1.75} />
                        <span>英文原文</span>
                      </a>
                    ) : (
                      <span className="inline-flex items-center gap-1.5 text-muted/60">
                        <Globe className="h-4 w-4 shrink-0" strokeWidth={1.75} />
                        <span>粘贴导入</span>
                      </span>
                    )}
                  </div>
                </div>
              </div>

            {message ? (
              <div className={`reader-shell-message mx-auto mt-5 ${readingColumnClass} rounded-[10px] border border-lens-blue/20 bg-lens-blue-soft px-4 py-3 text-sm leading-6 text-ink-soft`}>
                {message}
              </div>
            ) : null}
          </header>

            <div className={`reader-reading-stage ${isImmersiveMode ? "reader-reading-stage--immersive" : "reader-reading-stage--intensive"}`}>
              {isImmersiveMode ? (
                <ImmersiveReaderSurface
                  document={plateDocument}
                  readingClassName={readingClass}
                  columnClassName={readingColumnClass}
                  paragraphDensityClassName={paragraphDensityClass}
                  themeClassName={canvasThemeClass}
                  jumpTarget={jumpTarget}
                  focusTarget={focusedReaderNoteTarget}
                  selectionFocusRangesBySentence={selectionFocusRangesBySentence}
                  contextFocusRangesBySentence={contextFocusRangesBySentence}
                  hoveredAnnotationTargetKey={hoveredAnnotationTargetKey}
                  activeInlineMarkKey={activeInspect?.markId ?? null}
                  assetProjection={assetProjection}
                  readerNotesBySentence={readerNotesBySentence}
                  onAnnotationJump={jumpToAnnotation}
                  onHoverAnnotationTargetKeyChange={setHoveredAnnotationTargetKey}
                  onOpenSentenceNotes={openSentenceNotes}
                  onLookupIntent={(intent, anchor, triggerEl) =>
                    handleLookupIntent(intent, anchor, { showPreview: true }, triggerEl)
                  }
                  onInspectIntent={(intent, anchor, triggerEl) =>
                    handleInspectIntent(intent, anchor, { showPreview: true }, triggerEl)
                  }
                />
              ) : (
                <IntensiveReaderSurface
                  document={plateDocument}
                  showTranslation={showTranslation}
                  readingClassName={readingClass}
                  translationClassName={translationClass}
                  columnClassName={readingColumnClass}
                  paragraphDensityClassName={paragraphDensityClass}
                  themeClassName={canvasThemeClass}
                  annotationVisibilityGroups={contentVisibility}
                  activeSentenceId={activeSentence?.sentenceId ?? null}
                  sentenceActionsOpenSentenceId={contextPanelVisible ? activeSentence?.sentenceId ?? null : null}
                  selectedSentenceId={textSelection?.anchorType === "sentence" ? textSelection.sentence.sentenceId : null}
                  activeAnalysisEntryId={activeEntryId}
                  expandedAnalysisEntryIds={expandedAnalysisEntryIds}
                  jumpTarget={jumpTarget}
                  focusTarget={focusedReaderNoteTarget}
                  selectionFocusRangesBySentence={selectionFocusRangesBySentence}
                  contextFocusRangesBySentence={contextFocusRangesBySentence}
                  hoveredAnnotationTargetKey={hoveredAnnotationTargetKey}
                  activeInlineMarkKey={activeInspect?.markId ?? null}
                  assetProjection={assetProjection}
                  readerNotesBySentence={readerNotesBySentence}
                  activeReaderNoteId={activeReaderNoteId}
                  onAnalysisFocusChange={setAnalysisEntryFocus}
                  onAnalysisToggle={toggleAnalysisEntry}
                  onAnnotationJump={jumpToAnnotation}
                  onHoverAnnotationTargetKeyChange={setHoveredAnnotationTargetKey}
                  onAnalysisFeedback={openAnalysisFeedback}
                  onOpenSentenceNotes={openSentenceNotes}
                  onDeleteAnalysisSupplement={deleteAnalysisSupplement}
                  onAskAnalysis={openAskWithAnalysis}
                  onAskContentSummary={openAskWithContentSummary}
                  onLookupIntent={(intent, anchor, triggerEl) =>
                    handleLookupIntent(intent, anchor, { showPreview: true }, triggerEl)
                  }
                  onInspectIntent={(intent, anchor, triggerEl) =>
                    handleInspectIntent(intent, anchor, { showPreview: true }, triggerEl)
                  }
                  onOpenSentenceActions={toggleSentenceActions}
                />
              )}
            </div>

            <ReaderGlobalFeedbackPrompt
              onHelpful={() =>
                openFeedbackSheet({
                  scope: "analysis_result",
                  sentiment: "positive",
                  feedbackType: "thumbs_up",
                  targetId: record.id,
                  analysisRecordId: record.id,
                  clientSurface: "reader",
                  entryPoint: "reader_global_helpful",
                  contextJson: {
                    record_id: record.id,
                    title: record.title,
                    sentence_count: reader.article.sentences.length,
                    reading_goal: record.readingGoal,
                    feedback_intent: "helpful",
                  },
                  contextSummary: record.title ?? "本次阅读结果反馈",
                })
              }
              onIssue={() =>
                openFeedbackSheet({
                  scope: "analysis_result",
                  sentiment: "negative",
                  targetId: record.id,
                  analysisRecordId: record.id,
                  clientSurface: "reader",
                  entryPoint: "reader_global_issue",
                  contextJson: {
                    record_id: record.id,
                    title: record.title,
                    sentence_count: reader.article.sentences.length,
                    reading_goal: record.readingGoal,
                    feedback_intent: "issue",
                  },
                  contextSummary: record.title ?? "本次阅读结果反馈",
                })
              }
              onSuggestion={() =>
                openFeedbackSheet({
                  scope: "app",
                  sentiment: "neutral",
                  feedbackType: "feature_request",
                  targetId: `reader:${record.id}`,
                  analysisRecordId: record.id,
                  clientSurface: "reader",
                  entryPoint: "reader_global_suggestion",
                  contextJson: {
                    record_id: record.id,
                    title: record.title,
                    sentence_count: reader.article.sentences.length,
                    reading_goal: record.readingGoal,
                    feedback_intent: "suggestion",
                  },
                  contextSummary: record.title ?? "阅读体验建议",
                })
              }
            />
        </article>

        {notePanelSentence ? (
          <ReaderNotePanel
            open={Boolean(notePanelOpen && (activeReaderNote || noteDraftReaderNote))}
            sentence={notePanelSentence}
            sentenceIndex={notePanelSentenceIndex}
            notes={notePanelNotes}
            activeNote={activeReaderNote && activeReaderNote.anchorSentenceId === notePanelSentence.sentenceId ? activeReaderNote : null}
            draft={noteDraftReaderNote && noteDraftReaderNote.anchorSentenceId === notePanelSentence.sentenceId ? noteDraftReaderNote : null}
            draftText={readerNoteDraft}
            saveState={readerNoteSaveState}
            floatingRef={setNotePanelFloating}
            style={notePanelStyles}
            onClose={closeSentenceNotes}
            onSelectNote={focusReaderNote}
            onDraftTextChange={setReaderNoteDraft}
            onSave={saveActiveReaderNote}
            onDeleteNote={deleteReaderNote}
            onAsk={openAskWithReaderNote}
          />
        ) : null}

        {textSelection && selectionToolbarVisible && !contextPanelVisible ? (
          <div
            ref={setSelectionToolbarFloating}
            style={selectionToolbarStyles}
            className="z-50"
            onPointerDown={(event) => {
              event.preventDefault();
            }}
          >
            <SelectionToolbar
              className={isImmersiveMode ? "reader-selection-toolbar reader-selection-toolbar--immersive" : "reader-selection-toolbar"}
              selectedText={textSelection.selectedText}
              selectionMode={textSelection.anchorType}
              activeColor={selectedHighlight?.color ?? annotationColor}
              hasAnnotation={Boolean(selectedHighlight)}
              hasHighlight={Boolean(selectedHighlight)}
              canToggleHighlightPalette={hasExactSelectedHighlight}
              hasNote={Boolean(selectedReaderNote)}
              highlightPaletteOpen={highlightPaletteOpen}
              statusMessage={selectionToolbarStatus?.message}
              statusKind={selectionToolbarStatus?.kind}
              onSelectSentence={selectCurrentSentenceFromToolbar}
              onHighlight={(color) => highlightTextSelection(color)}
              onToggleHighlightPalette={() => setHighlightPaletteOpen((current) => !current)}
              onNote={openTextSelectionNote}
              onClearAnnotation={deleteTextSelectionAnnotation}
              onLookup={lookupTextSelection}
              onAsk={openAskWithSelection}
              onFeedback={() => {
                const sentenceId = textSelection?.sentence?.sentenceId;
                if (!sentenceId) return;
                openFeedbackSheet({
                  scope: "sentence",
                  sentiment: "negative",
                  targetId: buildSentenceTargetKey(record.id, sentenceId),
                  analysisRecordId: record.id,
                  clientSurface: "reader",
                  entryPoint: "selection_toolbar",
                  contextSummary: textSelection.selectedText,
                  contextJson: {
                    selected_text: textSelection.selectedText,
                    sentence_id: sentenceId,
                    anchor_type: textSelection.anchorType,
                  },
                });
              }}
            />
          </div>
        ) : null}
        {floatingLookupPreviewVisible ? (
          <ReaderQuickPeek
            lookup={activeLookup}
            inspect={activeInspect}
            className={isImmersiveMode ? "reader-tool-float reader-tool-float--immersive" : "reader-tool-float"}
            floatingRef={(node) => {
              setLookupPreviewFloating(node);
              lookupPreviewPanelRef.current = node;
            }}
            style={lookupPreviewStyles}
            onDismiss={dismissLookupPreview}
            onOpenDetail={dictionaryPanelVisible ? undefined : openDictionaryRail}
            onLookupPhrase={activeInspect ? () => lookupPhraseFromInspect(activeInspect) : undefined}
            onAttachToAsk={activeInspect ? () => openAskWithStructuredInspect(activeInspect) : undefined}
            onRequestAI={requestDictionaryAI}
            dictionaryAI={dictionaryAI}
          />
        ) : null}
        </div>
      </div>

      {dictionaryPanelVisible && dictionaryDockLayout ? (
        <div
          className={`reader-tool-surface reader-tool-surface--rail fixed top-3 bottom-3 z-40 hidden xl:block ${isImmersiveMode ? "reader-tool-surface--immersive" : "reader-tool-surface--intensive"}`}
          style={{ left: `${dictionaryDockLayout.left}px`, width: `${dictionaryDockLayout.width}px` }}
        >
          <ReaderDictionaryRail
            className="h-full"
            lookup={activeLookup}
            inspect={activeInspect}
            history={lookupHistory}
            readingGoal={record.readingGoal}
            saveState={dictionarySaveState}
            lookupSaveState={activeLookupSaveState}
            savedVocabularyMatch={activeLookupSavedVocabularyMatch}
            dictionaryAI={dictionaryAI}
            dictionaryAIPanelOpen={dictionaryAIPanelOpen}
            dictionaryAINoteState={dictionaryAINoteState}
            searchQuery={dictionaryQuery}
            searchExpanded={dictionarySearchExpanded}
            onSave={saveVocabularyFromDictionary}
            onRequestAI={requestDictionaryAI}
            onCreateAINote={createDictionaryAINote}
            onSelectAISuggestedQuery={selectAISuggestedQuery}
            onSearchQueryChange={setDictionaryQuery}
            onSearchSubmit={lookupDictionaryQuery}
            onSelectCandidate={selectDictionaryCandidate}
            onToggleAIPanel={toggleDictionaryAIPanel}
            onToggleSearchExpanded={() => setDictionarySearchExpanded((value) => !value)}
            onDismiss={closeDictionaryPanel}
            pinned={dictionaryPinned}
            onTogglePinned={() => setDictionaryPinned((value) => !value)}
            variant="card"
            canSaveVocabulary={Boolean(activeLookup?.contextSentence.trim())}
            canCreateAINote={Boolean(canCreateDictionaryAINote)}
            onLookupPhraseFromInspect={lookupPhraseFromInspect}
            onAttachToAsk={openAskWithStructuredInspect}
            onFeedback={() => {
              if (!activeLookup?.query) return;
              openFeedbackSheet({
                scope: "dictionary",
                sentiment: "negative",
                targetId: activeLookup.query,
                analysisRecordId: record.id,
                clientSurface: "dictionary",
                entryPoint: "dictionary_panel",
                contextSummary: activeLookup.query,
                contextJson: {
                  query: activeLookup.query,
                  context_sentence: activeLookup.contextSentence ?? "",
                  sentence_id: activeLookup.sentenceId ?? "",
                  lookup_type: activeLookup.lookupType ?? "",
                },
              });
            }}
            onNotFoundFeedback={() => {
              if (!activeLookup?.query) return;
              openFeedbackSheet({
                scope: "dictionary",
                sentiment: "negative",
                feedbackType: "missing_definition",
                targetId: activeLookup.query,
                analysisRecordId: record.id,
                clientSurface: "dictionary",
                entryPoint: "dictionary_not_found",
                contextSummary: activeLookup.query,
                contextJson: {
                  query: activeLookup.query,
                  context_sentence: activeLookup.contextSentence ?? "",
                  sentence_id: activeLookup.sentenceId ?? "",
                },
              });
            }}
            onInspectFeedback={(inspect) =>
              openFeedbackSheet({
                scope: "annotation",
                sentiment: "negative",
                targetId: inspect.markId,
                analysisRecordId: record.id,
                annotationType: inspect.annotationType,
                clientSurface: "reader",
                entryPoint: "dictionary_inspect_feedback",
                contextSummary: inspect.label ?? inspect.anchorText ?? inspect.lookupText ?? "标注反馈",
                contextJson: {
                  mark_id: inspect.markId,
                  annotation_type: inspect.annotationType,
                  lookup_text: inspect.lookupText ?? "",
                  anchor_text: inspect.anchorText ?? "",
                  label: inspect.label ?? "",
                  sentence_id: inspect.sentenceId,
                },
              })
            }
            onSelectHistory={selectLookupFromTrail}
          />
        </div>
      ) : null}

      {dictionaryPanelVisible && !dictionaryDockLayout && !contextPanelVisible ? (
        <div className={`reader-tool-surface reader-tool-surface--compact fixed inset-x-3 z-50 flex max-h-[72vh] flex-col md:bottom-6 ${isImmersiveMode ? "reader-tool-surface--immersive" : "reader-tool-surface--intensive"}`} style={{ bottom: compactSurfaceBottom }}>
          <ReaderDictionaryRail
            lookup={activeLookup}
            inspect={activeInspect}
            history={lookupHistory}
            readingGoal={record.readingGoal}
            saveState={dictionarySaveState}
            lookupSaveState={activeLookupSaveState}
            savedVocabularyMatch={activeLookupSavedVocabularyMatch}
            dictionaryAI={dictionaryAI}
            dictionaryAIPanelOpen={dictionaryAIPanelOpen}
            dictionaryAINoteState={dictionaryAINoteState}
            searchQuery={dictionaryQuery}
            searchExpanded={dictionarySearchExpanded}
            onSave={saveVocabularyFromDictionary}
            onRequestAI={requestDictionaryAI}
            onCreateAINote={createDictionaryAINote}
            onSelectAISuggestedQuery={selectAISuggestedQuery}
            onSearchQueryChange={setDictionaryQuery}
            onSearchSubmit={lookupDictionaryQuery}
            onSelectCandidate={selectDictionaryCandidate}
            onToggleAIPanel={toggleDictionaryAIPanel}
            onToggleSearchExpanded={() => setDictionarySearchExpanded((value) => !value)}
            onDismiss={clearLookup}
            canSaveVocabulary={Boolean(activeLookup?.contextSentence.trim())}
            canCreateAINote={Boolean(canCreateDictionaryAINote)}
            onLookupPhraseFromInspect={lookupPhraseFromInspect}
            onAttachToAsk={openAskWithStructuredInspect}
            onFeedback={() => {
              if (!activeLookup?.query) return;
              openFeedbackSheet({
                scope: "dictionary",
                sentiment: "negative",
                targetId: activeLookup.query,
                analysisRecordId: record.id,
                clientSurface: "dictionary",
                entryPoint: "dictionary_panel",
                contextSummary: activeLookup.query,
                contextJson: {
                  query: activeLookup.query,
                  context_sentence: activeLookup.contextSentence ?? "",
                  sentence_id: activeLookup.sentenceId ?? "",
                  lookup_type: activeLookup.lookupType ?? "",
                },
              });
            }}
            onNotFoundFeedback={() => {
              if (!activeLookup?.query) return;
              openFeedbackSheet({
                scope: "dictionary",
                sentiment: "negative",
                feedbackType: "missing_definition",
                targetId: activeLookup.query,
                analysisRecordId: record.id,
                clientSurface: "dictionary",
                entryPoint: "dictionary_not_found",
                contextSummary: activeLookup.query,
                contextJson: {
                  query: activeLookup.query,
                  context_sentence: activeLookup.contextSentence ?? "",
                  sentence_id: activeLookup.sentenceId ?? "",
                },
              });
            }}
            onInspectFeedback={(inspect) =>
              openFeedbackSheet({
                scope: "annotation",
                sentiment: "negative",
                targetId: inspect.markId,
                analysisRecordId: record.id,
                annotationType: inspect.annotationType,
                clientSurface: "reader",
                entryPoint: "dictionary_inspect_feedback",
                contextSummary: inspect.label ?? inspect.anchorText ?? inspect.lookupText ?? "标注反馈",
                contextJson: {
                  mark_id: inspect.markId,
                  annotation_type: inspect.annotationType,
                  lookup_text: inspect.lookupText ?? "",
                  anchor_text: inspect.anchorText ?? "",
                  label: inspect.label ?? "",
                  sentence_id: inspect.sentenceId,
                },
              })
            }
            onSelectHistory={selectLookupFromTrail}
          />
        </div>
      ) : null}

        {contextPanelVisible && sentencePopoverAnchorEl ? (
          <div
            ref={(node) => {
              setSentencePopoverFloating(node);
              sentencePopoverPanelRef.current = node;
            }}
            style={sentencePopoverStyles}
            className={`reader-tool-surface reader-tool-surface--context z-50 ${isImmersiveMode ? "reader-tool-surface--immersive" : "reader-tool-surface--intensive"}`}
            data-reader-sentence-popover="true"
            onPointerDown={(event) => {
              event.stopPropagation();
            }}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.stopPropagation();
                closeContextPanel();
              }
            }}
          >
          <ReaderContextPanel
            className={isImmersiveMode ? "reader-context-panel reader-context-panel--immersive" : "reader-context-panel"}
            sentence={activeSentence}
            translationText={activeSentence ? translationBySentence.get(activeSentence.sentenceId) ?? null : null}
            color={annotationColor}
            saveState={annotationSaveState}
            hasHighlight={Boolean(
              activeSentence &&
                annotationsBySentence.get(activeSentence.sentenceId)?.annotations.find(
                  (item) => item.anchorType === "sentence" && item.type === "highlight"
                )
            )}
            onColorChange={setAnnotationColor}
            onSelectSentence={() => {
              if (!activeSentence) {
                return;
              }
              selectSentence(activeSentence, sentencePopoverAnchorEl ?? lastSentencePopoverTriggerRef.current);
            }}
            onHighlight={() => void saveHighlight()}
            onNote={() => activeSentence && openSentenceNote(activeSentence)}
            onAsk={openAskWithSentenceContext}
            onAskTranslation={() => {
              if (!activeSentence) {
                return;
              }
              const translationZh = translationBySentence.get(activeSentence.sentenceId);
              if (!translationZh) {
                return;
              }
              openAskWithTranslation(activeSentence.sentenceId, translationZh);
            }}
            onFeedback={() => {
              if (!activeSentence) return;
              openFeedbackSheet({
                scope: "sentence",
                sentiment: "negative",
                targetId: buildSentenceTargetKey(record.id, activeSentence.sentenceId),
                analysisRecordId: record.id,
                clientSurface: "reader",
                entryPoint: "sentence_context_panel",
                contextSummary: activeSentence.text,
                contextJson: {
                  sentence_id: activeSentence.sentenceId,
                  sentence_text: activeSentence.text,
                },
              });
            }}
            onClose={closeContextPanel}
          />
        </div>
      ) : null}

        {settingsPanelOpen ? (
          <div
            className={`reader-tool-surface reader-tool-surface--settings fixed inset-x-0 top-3 bottom-3 z-50 overflow-y-auto px-3 md:inset-x-auto md:top-auto md:bottom-auto md:overflow-visible md:px-0 ${isImmersiveMode ? "reader-tool-surface--immersive" : "reader-tool-surface--intensive"}`}
            style={{
              bottom: compactSurfaceBottom,
              ...(mobileSettingsPanelStyle ?? {}),
              ...(settingsFloatingStyle ?? {}),
            }}
          >
              <div className="mx-auto rounded-[1.5rem] border border-hairline bg-background/72 shadow-[0_-20px_40px_rgba(17,17,17,0.12)] md:mx-0 md:rounded-none md:border-0 md:bg-transparent md:shadow-none">
                <ReaderSettingsPanel
                  themeName={themeName}
                  value={readerSettings}
                  onChange={handleReaderSettingsChange}
                  onThemeChange={setThemeName}
                  onClose={() => setSettingsPanelOpen(false)}
                />
              </div>
          </div>
        ) : null}

      {!contextPanelVisible || aiOpen ? (
        <AiWorkspacePanel
          key={record.id}
          open={aiOpen}
          presentation={isImmersiveMode ? "immersive" : "intensive"}
          recordId={record.id}
          recordTitle={record.title}
          pageIdentity={pageIdentity}
          attachments={askAttachments}
          liveContextAttachment={liveContextAttachment}
          pendingQuickActionRequest={pendingAskQuickAction}
          hideLauncherOnMobile={Boolean(dictionaryPanelVisible)}
          hideLauncherInCompactLayout={Boolean(dictionaryPanelVisible)}
          onRemoveAttachment={removeAskAttachment}
          onClearAttachments={clearAskAttachments}
          onAppendAttachments={appendAskAttachments}
          onJumpToAttachment={jumpToAskAttachment}
          onJumpToCitation={jumpToAskCitation}
          onActionExecuted={handleAskActionExecuted}
          onSupplementDeleted={deleteAnalysisSupplement}
          onPendingQuickActionConsumed={() => setPendingAskQuickAction(null)}
          onActivateLiveContextSelection={activateLiveContextSelection}
          onComposerTextareaFocus={handleComposerTextareaFocus}
          onComposerTextareaBlur={handleComposerTextareaBlur}
          onPanelPointerDownOutsideComposer={handleAiPanelPointerDownOutsideComposer}
          onToggle={toggleAiWorkspace}
          onAnnotationFeedback={(params) =>
            openFeedbackSheet({
              scope: "annotation",
              sentiment: "negative",
              targetId: params.entryId,
              analysisRecordId: record.id,
              annotationType: params.entryType,
              clientSurface: "reader",
              entryPoint: "ai_workspace_annotation_feedback",
              contextSummary: params.entryType,
              contextJson: {
                entry_id: params.entryId,
                entry_type: params.entryType,
              },
            })
          }
          analysisRecordId={record.id}
        />
      ) : null}
      {feedbackSheet?.open ? (
        <FeedbackSheet
          scope={feedbackSheet.scope}
          prefillSentiment={feedbackSheet.sentiment}
          prefillType={feedbackSheet.feedbackType}
          analysisRecordId={feedbackSheet.analysisRecordId}
          targetId={feedbackSheet.targetId}
          annotationType={feedbackSheet.annotationType}
          contextSummary={feedbackSheet.contextSummary}
          contextJson={feedbackSheet.contextJson}
          clientSurface={feedbackSheet.clientSurface}
          entryPoint={feedbackSheet.entryPoint}
          onClose={closeFeedbackSheet}
        />
      ) : null}
    </main>
  );
}
