"use client";

import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { TEXT_RANGE_HASH_ALGORITHM, TEXT_RANGE_OFFSET_UNIT, USER_ANNOTATION_COLORS } from "@claread/contracts";
import {
  BookOpen,
  Type,
  X,
} from "lucide-react";
import { useSearchParams } from "next/navigation";

import type { ReaderRecordVm } from "@/adapters/records.adapter";
import {
  AiWorkspacePanel,
  ReaderContextPanel,
  ReaderDictionaryRail,
  ReaderQuickPeek,
  ReaderSettingsPanel,
  PlateReaderSurface,
  SelectionToolbar,
  defaultReaderSettings,
  persistReaderSettings,
  readerColumnWidthClassName,
  readStoredReaderSettings,
  readerTextClassName,
  readerThemeClassName,
  type ReaderSettingsState,
  textRangeAnchorAttributes,
  useReaderFloatingLayer,
} from "@/components/reader";
import {
  askAttachmentFromAnnotation,
  askAttachmentFromAnalysisBlock,
  askAttachmentFromContentSummary,
  askAttachmentFromFavorite,
  askAttachmentFromRecord,
  askAttachmentFromSelection,
  askAttachmentFromSentence,
  askAttachmentFromStructuredInspect,
  askAttachmentFromTranslation,
  askAttachmentKey,
  annotationMatchesSelection,
  annotationRequestFromAnchorPayload,
  annotationToTargetRef,
  anchorPayloadFromSelection,
  anchorPayloadFromSentence,
  copyDomRect,
  favoriteMutationFromAnchorPayload,
  favoriteTargetVmFromAnchorPayload,
  favoriteToTargetRef,
  hashAnchorText,
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
  type ReaderJumpTarget,
  type ReaderTextSelection,
} from "@/lib/reader-plate";
import type {
  UserAnnotationColorDto,
  WebAnchorSegmentVm,
  WebAnnotationCreateRequest,
  WebAnnotationVm,
} from "@/types/api/annotations";
import type { ReaderAskCitationDto } from "@/types/api/reader-ask";
import type { WebFavoriteTargetVm } from "@/types/api/favorites";
import type { WebDictResult } from "@/types/api/dict";
import type {
  DictionaryAIViewState,
  DictAISourceDto,
  WebDictAIErrorResult,
  WebDictAIRequest,
  WebDictAIResult,
} from "@/types/api/dict-ai";
import type { VocabularyCreateRequestDto } from "@/types/api/vocabulary";
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
import { FavoriteButton } from "./FavoriteButton";

type ReaderDataSource = "upstream-render-scene" | "upstream-source-text";

interface ReaderWorkbenchProps {
  record: ReaderRecordVm;
  dataSource: ReaderDataSource;
  message?: string;
  initialAnnotations: WebAnnotationVm[];
  initialFavoriteTargets?: WebFavoriteTargetVm[];
}

type AnnotationSaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; message: string }
  | { kind: "error"; message: string };

const dataSourceLabel: Record<ReaderDataSource, string> = {
  "upstream-render-scene": "解析结果",
  "upstream-source-text": "原文回退",
};

const annotationColorValues = [...USER_ANNOTATION_COLORS];

function isUserAnnotationColor(value: string): value is UserAnnotationColorDto {
  return annotationColorValues.includes(value as UserAnnotationColorDto);
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

function dictionaryResolvedQuery(lookup: DictionaryLookupSnapshot) {
  if (lookup.state.kind === "ready") {
    return lookup.state.result.query.trim() || lookup.query;
  }
  return lookup.query;
}

function dictionaryContextExplainQuery(result: Extract<WebDictResult, { kind: "entry" }>, fallbackQuery: string) {
  return result.entry.baseWord?.trim() || result.query.trim() || result.entry.word.trim() || fallbackQuery;
}

function dictionaryIsManualLookup(lookup: DictionaryLookupSnapshot | null) {
  if (!lookup) {
    return false;
  }
  return lookup.sentenceId === "__manual__" || lookup.label === "手动查词";
}

function dictionaryAILookupSource(lookup: DictionaryLookupSnapshot): DictAISourceDto {
  if (dictionaryIsManualLookup(lookup)) {
    return "manual_search";
  }
  if (lookup.label === "选区查词" || lookup.title === "选区查词") {
    return "selection";
  }
  return "reader_click";
}

function dictionaryAIRequestForLookup(
  lookup: DictionaryLookupSnapshot | null,
  mode: WebDictAIRequest["mode"],
): WebDictAIRequest | null {
  if (!lookup || !lookup.contextSentence.trim() || dictionaryIsManualLookup(lookup)) {
    return null;
  }

  const resolvedQuery = dictionaryResolvedQuery(lookup);

  if (mode === "context_explain") {
    const result = lookup.state.kind === "ready" ? lookup.state.result : null;
    if (!result || result.kind !== "entry") {
      return null;
    }
    return {
      mode,
      query: dictionaryContextExplainQuery(result, resolvedQuery),
      queryType: lookup.lookupType,
      contextSentence: lookup.contextSentence,
      occurrence: lookup.occurrence,
      recordId: lookup.recordId,
      sentenceId: lookup.sentenceId,
      source: dictionaryAILookupSource(lookup),
      entryId: result.entry.id,
    };
  }

  return {
    mode,
    query: resolvedQuery,
    queryType: lookup.lookupType,
    contextSentence: lookup.contextSentence,
    occurrence: lookup.occurrence,
    recordId: lookup.recordId,
    sentenceId: lookup.sentenceId,
    source: dictionaryAILookupSource(lookup),
  };
}

function dictionaryAIRequestKey(request: WebDictAIRequest) {
  const entryIdPart = request.mode === "context_explain" ? String(request.entryId) : "missing";
  return [
    request.mode,
    request.query.toLowerCase(),
    request.queryType,
    request.contextSentence.trim().toLowerCase(),
    entryIdPart,
  ].join("::");
}

function dictionaryAIContextKey(lookup: DictionaryLookupSnapshot | null) {
  if (!lookup) {
    return null;
  }

  const base = [
    lookup.query.toLowerCase(),
    lookup.lookupType,
    lookup.contextSentence.trim().toLowerCase(),
    lookup.sentenceId,
    lookup.anchorText.toLowerCase(),
    lookup.occurrence ?? "",
  ].join("::");

  if (lookup.state.kind !== "ready") {
    return `${base}::${lookup.state.kind}`;
  }

  if (lookup.state.result.kind === "entry") {
    return `${base}::entry::${lookup.state.result.entry.id}`;
  }

  return `${base}::${lookup.state.result.kind}`;
}

function dictionaryLookupBase(lookup: DictionaryLookupSnapshot): LookupBase {
  const { state: _state, ...base } = lookup;
  return base;
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
  initialFavoriteTargets = [],
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
  const [dictionarySaveState, setDictionarySaveState] = useState<SaveState>({ kind: "idle" });
  const [annotations, setAnnotations] = useState(initialAnnotations);
  const [favoriteTargets, setFavoriteTargets] = useState(initialFavoriteTargets);
  const [jumpTarget, setJumpTarget] = useState<ReaderJumpTarget | null>(null);
  const [activeSentence, setActiveSentence] = useState<SentenceModel | null>(null);
  const [textSelection, setTextSelection] = useState<ReaderTextSelection | null>(null);
  const [contextPanelOpen, setContextPanelOpen] = useState(false);
  const [sentencePopoverAnchorEl, setSentencePopoverAnchorEl] = useState<HTMLElement | null>(null);
  const [settingsPanelOpen, setSettingsPanelOpen] = useState(false);
  const [expandedAnalysisEntryId, setExpandedAnalysisEntryId] = useState<string | null>(null);
  const sentencePopoverPanelRef = useRef<HTMLDivElement | null>(null);
  const lookupPreviewPanelRef = useRef<HTMLDivElement | null>(null);
  const lastSentencePopoverTriggerRef = useRef<HTMLElement | null>(null);
  const lastLookupTriggerRef = useRef<HTMLElement | null>(null);
  const [activeEntryId, setActiveEntryId] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [selectionNoteOpen, setSelectionNoteOpen] = useState(false);
  const [selectionNoteDraft, setSelectionNoteDraft] = useState("");
  const [selectionFavorited, setSelectionFavorited] = useState(false);
  const [selectionFavoriteLoading, setSelectionFavoriteLoading] = useState(false);
  const [annotationColor, setAnnotationColor] = useState<UserAnnotationColorDto>("warm_yellow");
  const [annotationSaveState, setAnnotationSaveState] = useState<AnnotationSaveState>({ kind: "idle" });
  const [readerSettings, setReaderSettings] = useState<ReaderSettingsState>(defaultReaderSettings);
  const [aiOpen, setAiOpen] = useState(false);
  const [askAttachments, setAskAttachments] = useState<ReaderAskAttachment[]>([]);
  const [dictionaryPinned, setDictionaryPinned] = useState(false);
  const [dictionaryRailOpen, setDictionaryRailOpen] = useState(false);
  const [dictionaryQuery, setDictionaryQuery] = useState("");
  const [dictionarySearchExpanded, setDictionarySearchExpanded] = useState(false);
  const [dictionaryAI, setDictionaryAI] = useState<DictionaryAIViewState>({ kind: "idle" });
  const [dictionaryAIPanelOpen, setDictionaryAIPanelOpen] = useState(false);

  useEffect(() => {
    setReaderScene(record.reader);
  }, [record.reader]);
  const articleRef = useRef<HTMLElement | null>(null);
  const readingColumnRef = useRef<HTMLDivElement | null>(null);
  const focusedRouteTargetKeyRef = useRef<string | null>(null);
  const dictionaryAIRequestKeyRef = useRef<string | null>(null);
  const [dictionaryDockLayout, setDictionaryDockLayout] = useState<DictionaryDockLayout | null>(null);
  const dictionaryPanelVisible = Boolean(dictionaryRailOpen || dictionaryPinned);

  const {
    refs: {
      setFloating: setSelectionToolbarFloating,
      setPositionReference: setSelectionToolbarReference,
    },
    floatingStyles: selectionToolbarStyles,
  } = useReaderFloatingLayer({
    open: Boolean(textSelection),
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
    setReaderSettings(readStoredReaderSettings());
  }, []);

  useEffect(() => {
    persistReaderSettings(readerSettings);
  }, [readerSettings]);

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
        target.closest("[data-reader-sentence-handle='true']")
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
        favoriteTargets,
        recordId: record.id,
      }),
    [annotations, favoriteTargets, record.id],
  );

  const annotationsBySentence = assetProjection.sentenceAssetSummaryBySentence;
  const favoriteTargetsBySentence = useMemo(() => {
    const map = new Map<string, WebFavoriteTargetVm[]>();
    assetProjection.sentenceAssetSummaryBySentence.forEach((summary, sentenceId) => {
      map.set(sentenceId, summary.favoriteTargets);
    });
    return map;
  }, [assetProjection]);

  const activeSentenceAnnotations = activeSentence
    ? annotationsBySentence.get(activeSentence.sentenceId)?.annotations ?? []
    : [];
  const plateDocument = useMemo(() => renderSceneToPlateDocument(reader), [reader]);
  const pageIdentity: ReaderAskPageIdentity = useMemo(
    () => ({
      recordId: record.id,
      recordTitle: record.title,
      surface: "reader",
      source: "reader_2_0",
    }),
    [record.id, record.title],
  );
  const activeLookupAIContextKey = useMemo(() => dictionaryAIContextKey(activeLookup), [activeLookup]);

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

  useEffect(() => {
    const targetKey = searchParams.get("targetKey");
    if (!targetKey || focusedRouteTargetKeyRef.current === targetKey) {
      return;
    }

    const nextJumpTarget = jumpToTargetKey(targetKey, {
      annotations,
      favoriteTargets,
    });
    if (!nextJumpTarget) {
      return;
    }

    setJumpTarget(nextJumpTarget);
    focusedRouteTargetKeyRef.current = targetKey;
  }, [annotations, favoriteTargets, searchParams]);

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
        setActiveSentence(targetSentence);
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

  useEffect(() => {
    dictionaryAIRequestKeyRef.current = null;
    setDictionaryAI({ kind: "idle" });
    setDictionaryAIPanelOpen(false);
  }, [activeLookupAIContextKey]);

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
      setDictionaryDockLayout(null);
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
    if (!textSelection) {
      return;
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setTextSelection(null);
        window.getSelection()?.removeAllRanges();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [textSelection]);

  const handleLookupSnapshot = useCallback((snapshot: DictionaryLookupSnapshot) => {
    setActiveLookup(snapshot);
    setActiveInspect(null);
    setDictionaryQuery(snapshot.query);
    setDictionarySaveState({ kind: "idle" });

    if (snapshot.state.kind === "ready") {
      setLookupHistory((current) => [
        snapshot,
        ...current.filter(
          (item) =>
            item.query.toLowerCase() !== snapshot.query.toLowerCase() ||
            item.sentenceId !== snapshot.sentenceId,
        ),
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
        const payload = (await response.json()) as WebDictResult;
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

    setDictionarySearchExpanded(true);
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
      occurrence: activeLookup.occurrence,
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
      const payload = (await response.json()) as WebDictResult;
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
    setDictionaryAIPanelOpen((value) => !value);
  }, []);

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

          setDictionaryAI({ kind: "error", mode, requestKey, error: errorResult });
          setDictionaryAIPanelOpen(true);

          if (errorResult.code === "canonical_dictionary_available") {
            void lookupPlainText(lookupIntentFromSnapshotBase(dictionaryLookupBase(lookupAtRequest)), {
              showPreview: false,
              openRail: true,
            });
          }

          return;
        }

        setDictionaryAI({
          kind: "ready",
          mode,
          requestKey,
          result: payload,
        });
        setDictionaryAIPanelOpen(true);
      } catch {
        if (dictionaryAIRequestKeyRef.current !== requestKey) {
          return;
        }

        setDictionaryAI({
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
        });
        setDictionaryAIPanelOpen(true);
      }
    },
    [activeLookup, dictionaryAI, lookupPlainText],
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
      setAiOpen(false);
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
      dictionaryAIRequestKeyRef.current = null;
      setDictionaryAI({ kind: "idle" });
      setDictionaryAIPanelOpen(false);
      setActiveSentence(sentenceById.get(intent.sentenceId) ?? null);
      setContextPanelOpen(false);
      setSentencePopoverAnchorEl(null);
      setAiOpen(false);
    },
    [sentenceById],
  );

  const openDictionaryRail = useCallback(() => {
    setDictionaryRailOpen(true);
    setLookupPreviewOpen(false);
  }, []);

  const updateTextSelectionFromDom = useCallback(() => {
    const nextSelection = readPlateReaderSelection(articleRef.current, sentenceById);
    setTextSelection(nextSelection);

    if (nextSelection) {
      setActiveSentence(nextSelection.sentence);
      setSettingsPanelOpen(false);
      setContextPanelOpen(false);
      setSentencePopoverAnchorEl(null);
      setLookupPreviewOpen(false);
      setLookupPreviewAnchor(null);
      setSelectionNoteOpen(false);
      setSelectionNoteDraft("");
      setSelectionFavorited(false);
      setSelectionFavoriteLoading(false);
      setAnnotationSaveState({ kind: "idle" });
    } else {
      setSelectionNoteOpen(false);
      setSelectionNoteDraft("");
      setSelectionFavorited(false);
      setSelectionFavoriteLoading(false);
    }
  }, [sentenceById]);

  useEffect(() => {
    if (!textSelection) {
      return;
    }

    const controller = new AbortController();
    const favoriteMutation = favoriteMutationFromAnchorPayload(
      anchorPayloadFromSelection(record.id, textSelection),
    );

    const params = new URLSearchParams({
      targetType: favoriteMutation.targetType,
      targetKey: favoriteMutation.targetKey,
    });

    void fetch(`/api/web/favorites/target?${params.toString()}`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        const payload = (await response.json()) as { ok: boolean; favorited?: boolean };
        if (response.ok && payload.ok) {
          setSelectionFavorited(Boolean(payload.favorited));
        }
      })
      .catch((error) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setSelectionFavorited(false);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setSelectionFavoriteLoading(false);
        }
      });

    return () => controller.abort();
  }, [record.id, textSelection]);

  function mergeAnnotation(item: WebAnnotationVm) {
    setAnnotations((current) => [item, ...current.filter((existing) => existing.id !== item.id)]);
  }

  function removeAnnotation(annotationId: string) {
    setAnnotations((current) => current.filter((existing) => existing.id !== annotationId));
  }

  async function patchAnnotation(
    annotationId: string,
    body: { color?: UserAnnotationColorDto | null; note?: string | null },
    errorMessage: string,
  ) {
    const response = await fetch(`/api/web/annotations/${encodeURIComponent(annotationId)}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = (await response.json()) as
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
      const payload = (await response.json().catch(() => ({}))) as { message?: string };

      if (!response.ok) {
        setDictionarySaveState({ kind: "error", message: payload.message ?? "加入生词本失败。" });
        return;
      }

      setDictionarySaveState({ kind: "saved", message: payload.message ?? "已加入生词本。" });
    } catch (error) {
      setDictionarySaveState({
        kind: "error",
        message: error instanceof Error ? error.message : "加入生词本失败。",
      });
    }
  }

  async function saveSentenceAnnotation(
    useNote: boolean,
    options?: { color?: UserAnnotationColorDto; selection?: ReaderTextSelection | null; noteText?: string },
  ) {
    const targetSelection = options?.selection ?? textSelection;
    const targetSentence = targetSelection?.sentence ?? activeSentence;

    if (!targetSentence) {
      setAnnotationSaveState({ kind: "error", message: "请先选择一个句子。" });
      return;
    }

    setAnnotationSaveState({ kind: "saving" });

    const color = options?.color ?? annotationColor;
    const noteText = options?.noteText ?? note;
    const sentenceAnnotations = annotationsBySentence.get(targetSentence.sentenceId)?.annotations ?? [];
    const existingTargetAnnotation = targetSelection
      ? annotations.find(
          (item) =>
            belongsToCurrentRecord(item.recordId, item.targetKey, record.id) &&
            annotationMatchesSelection(item, targetSelection),
        ) ?? null
      : sentenceAnnotations.find((item) => item.anchorType === "sentence") ?? null;

    if (useNote && existingTargetAnnotation) {
      try {
        const item = await patchAnnotation(
          existingTargetAnnotation.id,
          {
            note: noteText.trim(),
            color: existingTargetAnnotation.color ?? color,
          },
          "笔记保存失败。",
        );
        setNote(item.note ?? "");
        setSelectionNoteDraft(item.note ?? "");
        setSelectionNoteOpen(Boolean(item.note));
        setAnnotationSaveState({ kind: "saved", message: "笔记已保存。" });
      } catch (error) {
        setAnnotationSaveState({
          kind: "error",
          message: error instanceof Error ? error.message : "笔记保存失败。",
        });
      }
      return;
    }

    const anchorPayload = targetSelection
      ? anchorPayloadFromSelection(record.id, targetSelection)
      : anchorPayloadFromSentence(record.id, targetSentence);
    const body: WebAnnotationCreateRequest = annotationRequestFromAnchorPayload(anchorPayload, {
      color,
      note: useNote ? noteText.trim() : undefined,
      sentenceTextById,
      translationBySentence: translationModelBySentence,
    });

    try {
      const response = await fetch("/api/web/annotations", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = (await response.json()) as
        | { ok: true; item: WebAnnotationVm }
        | { ok: false; message?: string };

      if (!response.ok || !payload.ok) {
        setAnnotationSaveState({
          kind: "error",
          message: payload.ok === false && payload.message ? payload.message : "批注保存失败。",
        });
        return;
      }

      mergeAnnotation(payload.item);
      window.dispatchEvent(
        new CustomEvent<WebAnnotationVm>(ANNOTATION_CREATED_EVENT, { detail: payload.item }),
      );
      setNote(payload.item.note ?? "");
      setSelectionNoteDraft(payload.item.note ?? "");
      setSelectionNoteOpen(Boolean(payload.item.note));
      setAnnotationSaveState({ kind: "saved", message: useNote ? "笔记已保存。" : "高亮已保存。" });
    } catch (error) {
      setAnnotationSaveState({
        kind: "error",
        message: error instanceof Error ? error.message : "批注保存失败。",
      });
    }
  }

  function highlightTextSelection(colorValue: string) {
    if (!textSelection) {
      return;
    }
    const color = isUserAnnotationColor(colorValue) ? colorValue : annotationColor;
    setAnnotationColor(color);
    void saveSentenceAnnotation(false, { color, selection: textSelection });
  }

  function openTextSelectionNote() {
    if (!textSelection) {
      return;
    }
    setActiveSentence(textSelection.sentence);
    setSettingsPanelOpen(false);
    setContextPanelOpen(false);
    setAiOpen(false);
    setSelectionNoteDraft(selectedAnnotation?.note ?? "");
    setSelectionNoteOpen((value) => !value);
    setAnnotationSaveState({ kind: "idle" });
  }

  async function saveTextSelectionNote() {
    if (!textSelection) {
      return;
    }

    const trimmed = selectionNoteDraft.trim();
    if (!trimmed) {
      setAnnotationSaveState({ kind: "error", message: "请先输入笔记内容。" });
      return;
    }

    if (selectedAnnotation) {
      setAnnotationSaveState({ kind: "saving" });
      try {
        await patchAnnotation(
          selectedAnnotation.id,
          { note: trimmed, color: selectedAnnotation.color ?? annotationColor },
          "笔记保存失败。",
        );
        setAnnotationSaveState({ kind: "saved", message: "笔记已保存。" });
      } catch (error) {
        setAnnotationSaveState({
          kind: "error",
          message: error instanceof Error ? error.message : "笔记保存失败。",
        });
      }
      return;
    }

    await saveSentenceAnnotation(true, {
      color: annotationColor,
      selection: textSelection,
      noteText: trimmed,
    });
  }

  async function clearTextSelectionNote() {
    if (!selectedAnnotation) {
      setSelectionNoteDraft("");
      setSelectionNoteOpen(false);
      return;
    }

    setAnnotationSaveState({ kind: "saving" });
    try {
      if (selectedAnnotation.type === "note") {
        const response = await fetch(`/api/web/annotations/${encodeURIComponent(selectedAnnotation.id)}`, {
          method: "DELETE",
        });
        const payload = (await response.json()) as { ok: true } | { ok: false; message?: string };

        if (!response.ok || !payload.ok) {
          setAnnotationSaveState({
            kind: "error",
            message: payload.ok === false && payload.message ? payload.message : "删除笔记失败。",
          });
          return;
        }

        removeAnnotation(selectedAnnotation.id);
        setSelectionNoteDraft("");
        setSelectionNoteOpen(false);
        setAnnotationSaveState({ kind: "saved", message: "笔记已删除。" });
        return;
      }

      await patchAnnotation(selectedAnnotation.id, { note: null }, "删除笔记失败。");
      setSelectionNoteDraft("");
      setSelectionNoteOpen(false);
      setAnnotationSaveState({ kind: "saved", message: "笔记已删除。" });
    } catch (error) {
      setAnnotationSaveState({
        kind: "error",
        message: error instanceof Error ? error.message : "删除笔记失败。",
      });
    }
  }

  async function deleteTextSelectionAnnotation() {
    if (!selectedAnnotation) {
      return;
    }

    setAnnotationSaveState({ kind: "saving" });
    try {
      const response = await fetch(`/api/web/annotations/${encodeURIComponent(selectedAnnotation.id)}`, {
        method: "DELETE",
      });
      const payload = (await response.json()) as { ok: true } | { ok: false; message?: string };

      if (!response.ok || !payload.ok) {
        setAnnotationSaveState({
          kind: "error",
          message: payload.ok === false && payload.message ? payload.message : "取消标注失败。",
        });
        return;
      }

      setAnnotations((current) => current.filter((existing) => existing.id !== selectedAnnotation.id));
      setSelectionNoteDraft("");
      setSelectionNoteOpen(false);
      setAnnotationSaveState({ kind: "saved", message: "标注已取消。" });
    } catch (error) {
      setAnnotationSaveState({
        kind: "error",
        message: error instanceof Error ? error.message : "取消标注失败。",
      });
    }
  }

  async function toggleTextSelectionFavorite() {
    if (!textSelection) {
      return;
    }

    const anchorPayload = anchorPayloadFromSelection(record.id, textSelection);
    const favoriteMutation = favoriteMutationFromAnchorPayload(anchorPayload);
    setSelectionFavoriteLoading(true);

    try {
      const response = selectionFavorited
        ? await fetch(
            `/api/web/favorites/target?${new URLSearchParams({
              targetType: favoriteMutation.targetType,
              targetKey: favoriteMutation.targetKey,
            }).toString()}`,
            { method: "DELETE" },
          )
        : await fetch("/api/web/favorites/target", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(favoriteMutation),
          });
      const payload = (await response.json()) as { ok: boolean; favorited?: boolean; message?: string };

      if (!response.ok || !payload.ok) {
        setAnnotationSaveState({ kind: "error", message: payload.message ?? "收藏操作失败。" });
        return;
      }

      setSelectionFavorited(Boolean(payload.favorited));
      setFavoriteTargets((current) => {
        const withoutTarget = current.filter(
          (item) => !(item.targetType === favoriteMutation.targetType && item.targetKey === favoriteMutation.targetKey),
        );
        if (!payload.favorited) {
          return withoutTarget;
        }
        return [
          favoriteTargetVmFromAnchorPayload(anchorPayload, favoriteMutation.targetKey),
          ...withoutTarget,
        ];
      });
      setAnnotationSaveState({
        kind: "saved",
        message: payload.favorited ? "已收藏。" : "已取消收藏。",
      });
    } catch (error) {
      setAnnotationSaveState({
        kind: "error",
        message: error instanceof Error ? error.message : "收藏操作失败。",
      });
    } finally {
      setSelectionFavoriteLoading(false);
    }
  }

  function selectCurrentSentenceFromToolbar() {
    if (!textSelection || textSelection.anchorType !== "text_range") {
      return;
    }

    const sentence = textSelection.sentence;
    const sentenceElement = articleRef.current?.querySelector<HTMLElement>(
      `[data-reader-anchor="sentence"][data-sentence-id="${CSS.escape(sentence.sentenceId)}"] [data-reader-sentence-text="true"]`,
    );
    const rect = sentenceElement ? copyDomRect(sentenceElement.getBoundingClientRect()) : textSelection.rect;
    setActiveSentence(sentence);
    setSettingsPanelOpen(false);
    setContextPanelOpen(false);
    setTextSelection({
      anchorType: "sentence",
      sentence,
      selectedText: sentence.text,
      segments: [
        {
          paragraphId: sentence.paragraphId,
          sentenceId: sentence.sentenceId,
          sentence,
          selectedText: sentence.text,
          startOffset: 0,
          endOffset: sentence.text.length,
          textHash: hashAnchorText(sentence.text),
        },
      ],
      startOffset: 0,
      endOffset: sentence.text.length,
      textHash: hashAnchorText(sentence.text),
      rect,
    });
    setSelectionNoteOpen(false);
    setSelectionNoteDraft("");
    setSelectionFavorited(false);
    setSelectionFavoriteLoading(false);
    setAnnotationSaveState({ kind: "idle" });
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
    setContextPanelOpen(false);
    setSentencePopoverAnchorEl(null);
    setAiOpen(true);
  }

  function openAskWithSelection() {
    if (!textSelection) {
      return;
    }
    setActiveSentence(textSelection.sentence);
    openAskWithAttachments([
      askAttachmentFromSelection(pageIdentity, textSelection, {
        sourceSurface: "selection_toolbar",
        entryAction: "ask_about_this",
      }),
    ]);
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
        entryAction: "compare_translation",
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

  function openAskWithFavorite(favorite: WebFavoriteTargetVm) {
    openAskWithAttachments([
      askAttachmentFromFavorite(pageIdentity, favorite, {
        sourceSurface: "favorite",
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

  function attachCurrentRecordToAsk() {
    openAskWithAttachments([
      askAttachmentFromRecord(pageIdentity, {
        sourceSurface: "ask_panel",
        entryAction: "ask_about_this",
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

  function jumpToAnnotation(annotation: WebAnnotationVm) {
    const nextJumpTarget = jumpToTargetRef(annotationToTargetRef(annotation), {
      annotations,
      favoriteTargets,
    });
    if (nextJumpTarget) {
      setJumpTarget(nextJumpTarget);
    }
  }

  function jumpToFavorite(favorite: WebFavoriteTargetVm) {
    const nextJumpTarget = jumpToTargetRef(favoriteToTargetRef(favorite), {
      annotations,
      favoriteTargets,
    });
    if (nextJumpTarget) {
      setJumpTarget(nextJumpTarget);
    }
  }

  function lookupPhraseFromInspect(intent: ReaderStructuredInspectIntent) {
    const nextIntent = lookupIntentFromStructuredInspect(intent);
    handleLookupIntent(nextIntent, lookupPreviewAnchor, { showPreview: false, openRail: true });
  }

  function jumpToAskAttachment(attachment: ReaderAskAttachment) {
    const nextJumpTarget = jumpTargetFromAskAttachment(attachment, {
      annotations,
      favoriteTargets,
    });
    if (nextJumpTarget) {
      setJumpTarget(nextJumpTarget);
    }
  }

  function jumpToAskCitation(citation: ReaderAskCitationDto) {
    const nextJumpTarget = jumpTargetFromAskCitation(citation, record.id, {
      annotations,
      favoriteTargets,
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
    setTextSelection(null);
    setContextPanelOpen(true);
    setSentencePopoverAnchorEl(anchorEl ?? null);
    lastSentencePopoverTriggerRef.current = anchorEl ?? null;
    setSettingsPanelOpen(false);
    setExpandedAnalysisEntryId(null);
    setActiveEntryId(null);
    const sentenceAnnotation =
      (annotationsBySentence.get(sentence.sentenceId)?.annotations ?? []).find((item) => item.anchorType === "sentence") ?? null;
    setNote(sentenceAnnotation?.note ?? "");
    setAnnotationColor(sentenceAnnotation?.color ?? "warm_yellow");
    setAnnotationSaveState({ kind: "idle" });
  }

  function openSettingsPanel() {
    setSettingsPanelOpen(true);
    setContextPanelOpen(false);
    setSentencePopoverAnchorEl(null);
  }

  function toggleAiWorkspace() {
    setAiOpen((value) => {
      const nextValue = !value;
      if (nextValue) {
        setContextPanelOpen(false);
        setSentencePopoverAnchorEl(null);
      }
      return nextValue;
    });
  }

  function closeContextPanel() {
    const trigger = sentencePopoverAnchorEl ?? lastSentencePopoverTriggerRef.current;
    setContextPanelOpen(false);
    setSentencePopoverAnchorEl(null);
    setActiveSentence(null);
    setExpandedAnalysisEntryId(null);
    setActiveEntryId(null);
    setTextSelection(null);
    window.requestAnimationFrame(() => {
      trigger?.focus();
    });
  }

  function toggleAnalysisEntry(entryId: string) {
    setExpandedAnalysisEntryId((current) => (current === entryId ? null : entryId));
    setActiveEntryId(entryId);
    setContextPanelOpen(false);
    setSentencePopoverAnchorEl(null);
  }

  function setAnalysisEntryFocus(entryId: string, focused: boolean) {
    setActiveEntryId((current) => {
      if (focused) {
        return entryId;
      }
      if (current === entryId && expandedAnalysisEntryId !== entryId) {
        return null;
      }
      return current;
    });
  }

  const canvasThemeClass = readerThemeClassName(readerSettings.theme);
  const readingClass = readerTextClassName(readerSettings);
  const readingColumnClass = readerColumnWidthClassName(readerSettings.columnWidth);
  const contextPanelVisible = Boolean(contextPanelOpen && activeSentence);
  const compactDictionaryPanelVisible = Boolean(
    dictionaryPanelVisible && !dictionaryDockLayout && !aiOpen && !contextPanelVisible && !settingsPanelOpen,
  );
  const floatingLookupPreviewVisible = Boolean(
    lookupPreviewOpen && lookupPreviewAnchor && (activeLookup || activeInspect) && !settingsPanelOpen && !contextPanelVisible,
  );
  const compactSurfaceBottom = "max(5.25rem, calc(env(safe-area-inset-bottom) + 4.25rem))";
  const settingsPanelStyle = compactDictionaryPanelVisible
    ? ({ bottom: "min(calc(72vh + 6.5rem), calc(100vh - 18rem))" } satisfies CSSProperties)
    : undefined;

  return (
    <main className="paper-grain min-h-screen px-3 pb-24 pt-3 text-ink sm:px-4 md:pb-6 lg:px-5">
      <div className="relative">
        <div className="relative min-w-0">
          <article
          ref={articleRef}
          className={`min-w-0 overflow-visible rounded-panel border border-hairline shadow-surface-quiet ${canvasThemeClass}`}
          onClick={lookupPreviewOpen ? dismissLookupPreview : undefined}
          onMouseUp={() => {
            window.requestAnimationFrame(updateTextSelectionFromDom);
          }}
          onKeyUp={(event) => {
            if (event.key === "Escape") {
              setTextSelection(null);
              return;
            }
            window.requestAnimationFrame(updateTextSelectionFromDom);
          }}
        >
          <header className="border-b border-hairline px-5 py-4 sm:px-8 lg:px-10">
            <div
              ref={readingColumnRef}
              className={`mx-auto flex ${readingColumnClass} flex-col gap-4 lg:flex-row lg:items-start lg:justify-between`}
            >
              <div className="min-w-0">
                <p className="text-xs font-semibold text-muted">透读正文</p>
                <h1 className="mt-2 max-w-[24ch] font-headline text-3xl font-semibold leading-tight tracking-normal text-ink md:text-[2.35rem]">
                  {record.title}
                </h1>
                <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted">
                  <span>{dataSourceLabel[dataSource]}</span>
                  <span aria-hidden="true" className="h-1 w-1 rounded-full bg-hairline" />
                  <span>{reader.article.sentences.length} 句</span>
                </div>
              </div>
              <div className="flex shrink-0 flex-wrap items-center justify-end gap-2 lg:gap-2.5">
                <FavoriteButton recordId={record.id} />
                <div className="flex items-stretch gap-1 rounded-xl border border-border/60 bg-background/72 p-1 shadow-sm">
                  <button
                    type="button"
                    className={`focus-ring inline-flex min-h-[2.55rem] min-w-[6rem] items-center gap-2 rounded-[0.85rem] border px-3 text-left transition-[background-color,border-color,color,box-shadow,transform] ${
                      readerSettings.showTranslation
                        ? "border-lens-blue/15 bg-background text-ink shadow-sm"
                        : "border-transparent bg-transparent text-ink-soft hover:border-border/55 hover:bg-muted/45 hover:text-ink"
                    }`}
                    onClick={() =>
                      setReaderSettings((current) => ({
                        ...current,
                        showTranslation: !current.showTranslation,
                      }))
                    }
                  >
                    <BookOpen
                      aria-hidden="true"
                      className={`h-4 w-4 shrink-0 ${readerSettings.showTranslation ? "text-lens-blue" : "text-muted"}`}
                    />
                      <span className="flex min-w-0 flex-col">
                        <span className="text-[0.84rem] font-semibold leading-none">
                          {readerSettings.showTranslation ? "原文 + 译文" : "仅看原文"}
                        </span>
                      </span>
                    </button>
                  <button
                    type="button"
                    className={`focus-ring inline-flex min-h-[2.55rem] min-w-[5.2rem] items-center gap-2 rounded-[0.85rem] border px-3 text-left transition-[background-color,border-color,color,box-shadow,transform] ${
                      settingsPanelOpen
                        ? "border-lens-blue/15 bg-background text-ink shadow-sm"
                        : "border-transparent bg-transparent text-ink-soft hover:border-border/55 hover:bg-muted/45 hover:text-ink"
                    }`}
                    onClick={openSettingsPanel}
                  >
                    <Type
                      aria-hidden="true"
                      className={`h-4 w-4 shrink-0 ${settingsPanelOpen ? "text-lens-blue" : "text-muted"}`}
                    />
                      <span className="flex min-w-0 flex-col">
                        <span className="text-[0.84rem] font-semibold leading-none">阅读显示</span>
                      </span>
                    </button>
                </div>
              </div>
            </div>

            {message ? (
              <div className={`mx-auto mt-5 ${readingColumnClass} rounded-[10px] border border-lens-blue/20 bg-lens-blue-soft px-4 py-3 text-sm leading-6 text-ink-soft`}>
                {message}
              </div>
            ) : null}
          </header>

          <PlateReaderSurface
            document={plateDocument}
            showTranslation={readerSettings.showTranslation}
            readingClassName={readingClass}
            columnWidth={readerSettings.columnWidth}
            themeClassName={canvasThemeClass}
            activeSentenceId={activeSentence?.sentenceId ?? null}
            activeAnalysisEntryId={activeEntryId}
            expandedAnalysisEntryId={expandedAnalysisEntryId}
            jumpTarget={jumpTarget}
            assetProjection={assetProjection}
            annotationVisibilityGroups={readerSettings.annotationVisibilityGroups}
            onAnalysisFocusChange={setAnalysisEntryFocus}
            onAnalysisToggle={toggleAnalysisEntry}
            onAnnotationJump={jumpToAnnotation}
            onAnnotationAsk={openAskWithAnnotation}
            onFavoriteJump={jumpToFavorite}
            onDeleteAnalysisSupplement={deleteAnalysisSupplement}
            onAskTranslation={openAskWithTranslation}
            onAskAnalysis={openAskWithAnalysis}
            onAskContentSummary={openAskWithContentSummary}
            onLookupIntent={(intent, anchor, triggerEl) =>
              handleLookupIntent(intent, anchor, { showPreview: true }, triggerEl)
            }
            onInspectIntent={(intent, anchor, triggerEl) =>
              handleInspectIntent(intent, anchor, { showPreview: true }, triggerEl)
            }
            onSentenceActivate={(sentenceId, anchorEl) => {
              const sentence = sentenceById.get(sentenceId);
              if (sentence) {
                selectSentence(sentence, anchorEl);
              }
            }}
          />
        </article>

        {textSelection && !contextPanelVisible ? (
          <div
            ref={setSelectionToolbarFloating}
            style={selectionToolbarStyles}
            className="z-50"
            onPointerDown={(event) => {
              const target = event.target instanceof HTMLElement ? event.target : null;
              if (target?.closest("[data-selection-note-input='true']")) {
                return;
              }
              event.preventDefault();
            }}
          >
            <SelectionToolbar
              selectedText={textSelection.selectedText}
              selectionMode={textSelection.anchorType}
              activeColor={selectedAnnotation?.color ?? annotationColor}
              hasAnnotation={Boolean(selectedAnnotation)}
              hasHighlight={selectedAnnotation?.type === "highlight"}
              hasNote={Boolean(selectedAnnotation?.note)}
              favorited={selectionFavorited}
              noteOpen={selectionNoteOpen}
              noteValue={selectionNoteDraft}
              noteSaving={annotationSaveState.kind === "saving"}
              statusMessage={
                annotationSaveState.kind === "saved" || annotationSaveState.kind === "error"
                  ? annotationSaveState.message
                  : undefined
              }
              statusKind={
                annotationSaveState.kind === "saved" || annotationSaveState.kind === "error"
                  ? annotationSaveState.kind
                  : undefined
              }
              disabled={{
                favorite: selectionFavoriteLoading,
              }}
              onSelectSentence={selectCurrentSentenceFromToolbar}
              onHighlight={(color) => highlightTextSelection(color)}
              onNote={openTextSelectionNote}
              onNoteChange={setSelectionNoteDraft}
              onNoteSave={saveTextSelectionNote}
              onNoteClear={clearTextSelectionNote}
              onClearAnnotation={deleteTextSelectionAnnotation}
              onFavorite={toggleTextSelectionFavorite}
              onLookup={lookupTextSelection}
              onAsk={openAskWithSelection}
            />
          </div>
        ) : null}
        {floatingLookupPreviewVisible ? (
          <ReaderQuickPeek
            lookup={activeLookup}
            inspect={activeInspect}
            floatingRef={(node) => {
              setLookupPreviewFloating(node);
              lookupPreviewPanelRef.current = node;
            }}
            style={lookupPreviewStyles}
            onDismiss={dismissLookupPreview}
            onOpenDetail={openDictionaryRail}
            onLookupPhrase={activeInspect ? () => lookupPhraseFromInspect(activeInspect) : undefined}
            onAttachToAsk={activeInspect ? () => openAskWithStructuredInspect(activeInspect) : undefined}
          />
        ) : null}
        </div>
      </div>

      {dictionaryPanelVisible && dictionaryDockLayout ? (
        <div
          className="fixed top-3 bottom-3 z-40 hidden xl:block"
          style={{ left: `${dictionaryDockLayout.left}px`, width: `${dictionaryDockLayout.width}px` }}
        >
          <ReaderDictionaryRail
            className="h-full"
            lookup={activeLookup}
            inspect={activeInspect}
            history={lookupHistory}
            readingGoal={record.readingGoal}
            saveState={dictionarySaveState}
            dictionaryAI={dictionaryAI}
            dictionaryAIPanelOpen={dictionaryAIPanelOpen}
            searchQuery={dictionaryQuery}
            searchExpanded={dictionarySearchExpanded}
            onSave={saveVocabularyFromDictionary}
            onRequestAI={requestDictionaryAI}
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
            onLookupPhraseFromInspect={lookupPhraseFromInspect}
            onAttachToAsk={openAskWithStructuredInspect}
            onSelectHistory={selectLookupFromTrail}
          />
        </div>
      ) : null}

      {dictionaryPanelVisible && !dictionaryDockLayout && !aiOpen && !contextPanelVisible ? (
        <div className="fixed inset-x-3 z-50 flex max-h-[72vh] flex-col md:bottom-6" style={{ bottom: compactSurfaceBottom }}>
          <ReaderDictionaryRail
            lookup={activeLookup}
            inspect={activeInspect}
            history={lookupHistory}
            readingGoal={record.readingGoal}
            saveState={dictionarySaveState}
            dictionaryAI={dictionaryAI}
            dictionaryAIPanelOpen={dictionaryAIPanelOpen}
            searchQuery={dictionaryQuery}
            searchExpanded={dictionarySearchExpanded}
            onSave={saveVocabularyFromDictionary}
            onRequestAI={requestDictionaryAI}
            onSelectAISuggestedQuery={selectAISuggestedQuery}
            onSearchQueryChange={setDictionaryQuery}
            onSearchSubmit={lookupDictionaryQuery}
            onSelectCandidate={selectDictionaryCandidate}
            onToggleAIPanel={toggleDictionaryAIPanel}
            onToggleSearchExpanded={() => setDictionarySearchExpanded((value) => !value)}
            onDismiss={clearLookup}
            canSaveVocabulary={Boolean(activeLookup?.contextSentence.trim())}
            onLookupPhraseFromInspect={lookupPhraseFromInspect}
            onAttachToAsk={openAskWithStructuredInspect}
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
            className="z-50"
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
            sentence={activeSentence}
            selectedText={textSelection?.selectedText ?? null}
            annotationScope={textSelection ? "text_range" : "sentence"}
            note={note}
            color={annotationColor}
            saveState={annotationSaveState}
            sentenceAnnotations={activeSentenceAnnotations}
            sentenceFavorites={activeSentence ? favoriteTargetsBySentence.get(activeSentence.sentenceId) ?? [] : []}
            onNoteChange={setNote}
            onColorChange={setAnnotationColor}
            onSaveAnnotation={saveSentenceAnnotation}
            onAsk={openAskWithSentenceContext}
            onAnnotationJump={jumpToAnnotation}
            onAnnotationAsk={openAskWithAnnotation}
            onFavoriteJump={jumpToFavorite}
            onFavoriteAsk={openAskWithFavorite}
            onClose={closeContextPanel}
          />
        </div>
      ) : null}

        {settingsPanelOpen ? (
          <div
            className="fixed inset-x-3 z-50 md:bottom-6 md:left-1/2 md:right-auto md:w-[min(640px,calc(100vw-6rem))] md:-translate-x-1/2"
            style={{ bottom: compactSurfaceBottom, ...settingsPanelStyle }}
        >
          <ReaderSettingsPanel
            value={readerSettings}
            onChange={setReaderSettings}
            onClose={() => setSettingsPanelOpen(false)}
          />
        </div>
      ) : null}

      {!contextPanelVisible || aiOpen ? (
        <AiWorkspacePanel
          key={record.id}
          open={aiOpen}
          recordId={record.id}
          recordTitle={record.title}
          activeSentence={activeSentence}
          pageIdentity={pageIdentity}
          attachments={askAttachments}
          hideLauncherOnMobile={Boolean(dictionaryPanelVisible)}
          hideLauncherInCompactLayout={Boolean(dictionaryPanelVisible)}
          onRemoveAttachment={removeAskAttachment}
          onClearAttachments={clearAskAttachments}
          onAttachCurrentRecord={attachCurrentRecordToAsk}
          onJumpToAttachment={jumpToAskAttachment}
          onJumpToCitation={jumpToAskCitation}
          onActionExecuted={handleAskActionExecuted}
          onToggle={toggleAiWorkspace}
        />
      ) : null}
    </main>
  );
}
