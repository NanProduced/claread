"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  BookOpen,
  Eye,
  Highlighter,
  MessageSquare,
  Search,
  SlidersHorizontal,
  Sparkles,
} from "lucide-react";

import {
  AiWorkspacePanel,
  type AiWorkspaceSurface,
} from "@/components/reader/AiWorkspacePanel";
import {
  ReaderDictionaryRail,
  ReaderQuickPeek,
  dictionaryLookupHistoryKey,
  type DictionaryLookupSnapshot,
  type SaveState,
} from "@/components/reader/dictionary";
import { readerCommandControl } from "@/components/reader/interaction";
import { useReaderFloatingLayer } from "@/components/reader/ReaderFloatingLayer";
import { SelectionToolbar } from "@/components/reader/SelectionToolbar";
import { ImmersiveReaderSurface } from "@/components/reader/plate/ImmersiveReaderSurface";
import { IntensiveReaderSurface } from "@/components/reader/plate/IntensiveReaderSurface";
import {
  defaultReaderSettings,
  modeShowsTranslation,
  modeVisibility,
  readerModeTypography,
  ReaderSettingsPanel,
  type ReaderSettingsState,
} from "@/components/reader/settings";
import { useAppearance } from "@/components/providers/appearance-provider";
import { cn } from "@/lib/cn";
import {
  anchorDraftsForSelection,
  askAttachmentFromSelection,
  askAttachmentKey,
  lookupIntentFromSelection,
  lookupIntentFromStructuredInspect,
  readPlateReaderSelection,
  readerLookupSnapshotFromIntent,
  rectForTextOffsets,
  selectionToolbarRectForReaderSelection,
  type ReaderAskAttachment,
  type ReaderAskPageIdentity,
  type ReaderJumpRangeSegment,
  type ReaderLookupIntent,
  type ReaderLookupPreviewAnchor,
  type ReaderStructuredInspectIntent,
  type ReaderTextSelection,
} from "@/lib/reader-plate";
import {
  adaptReaderPlateSnapshotToPlateDocument,
  adaptReaderPlateSnapshotToReaderVm,
} from "@/lib/reader-plate/projection";
import type { WebDictResult } from "@/types/api/dict";
import type { DictionaryAIViewState } from "@/types/api/dict-ai";
import type {
  ReaderEnhancementCapability,
  ReaderEnhancementProgressDto,
  ReaderEnhancementProgressLayerStatus,
  ReaderEnhancementProgressOverallStatus,
  ReaderPlateSnapshotDto,
  ReadingRecordProductState,
  ReadingRecordReadinessState,
} from "@/types/api/reader-plate";

interface ReaderRecordWorkbenchSurfaceProps {
  snapshot: ReaderPlateSnapshotDto;
}

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "今日";
  }
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
}

function sourceTypeLabel(sourceType: string) {
  if (sourceType === "plain_text" || sourceType === "text") {
    return "粘贴导入";
  }
  if (sourceType === "url") {
    return "网页导入";
  }
  return sourceType || "Reading Record";
}

function productStateLabel(productState: ReadingRecordProductState) {
  switch (productState) {
    case "processing":
      return "处理中";
    case "needs_confirmation":
      return "待确认";
    case "readable_enhancing":
      return "可读增强中";
    case "action_required":
      return "需要处理";
    case "failed":
      return "处理失败";
    case "deleted":
      return "已删除";
    default:
      return "只读快照";
  }
}

function readinessStateLabel(readinessState: ReadingRecordReadinessState) {
  switch (readinessState) {
    case "submitted":
      return "已提交";
    case "candidate_base_ready":
      return "候选底稿已就绪";
    case "article_ready":
      return "正文可读";
    case "initial_enhancement_ready":
      return "初始增强已就绪";
    case "coverage_complete":
      return "增强覆盖完成";
    default:
      return readinessState;
  }
}

function productStateBanner(productState: ReadingRecordProductState) {
  switch (productState) {
    case "processing":
      return {
        title: "处理中",
        body: "阅读记录已创建，系统正在准备增强内容；正文仍可继续阅读。",
        className: "border-lens-blue/20 bg-lens-blue-soft text-ink-soft",
      };
    case "readable_enhancing":
      return {
        title: "可读增强中",
        body: "正文已经可读，系统仍在补充译文、标注或其他增强内容。",
        className: "border-lens-blue/20 bg-lens-blue-soft text-ink-soft",
      };
    case "failed":
      return {
        title: "增强失败",
        body: "本次增强未成功完成，但正文和已发布内容仍可继续阅读。",
        className: "border-amber-300/70 bg-amber-50/95 text-amber-950",
      };
    case "action_required":
      return {
        title: "需要处理",
        body: "此阅读记录需要额外处理后才能继续增强；本轮页面暂不提供处理动作。",
        className: "border-orange-300/80 bg-orange-50/95 text-orange-950",
      };
    default:
      return null;
  }
}

function enhancementOverallLabel(status: ReaderEnhancementProgressOverallStatus) {
  switch (status) {
    case "processing":
      return "增强准备中";
    case "readable_enhancing":
      return "批注/增强处理中";
    case "ready":
      return "增强已完成";
    case "failed":
      return "部分增强失败";
    case "action_required":
      return "需要处理";
    default:
      return "增强状态";
  }
}

function enhancementOverallMessage(
  status: ReaderEnhancementProgressOverallStatus,
) {
  switch (status) {
    case "processing":
      return "正在准备增强任务，正文仍可阅读。";
    case "readable_enhancing":
      return "译文、词汇或语法批注仍在排队或生成中。";
    case "ready":
      return "已发布的增强内容会随当前快照一起展示。";
    case "failed":
      return "部分增强任务未完成，正文和已发布内容仍可阅读。";
    case "action_required":
      return "该记录需要额外处理，本页暂不提供处理动作。";
    default:
      return "增强状态来自当前阅读快照。";
  }
}

function enhancementStatusTone(
  status:
    | ReaderEnhancementProgressOverallStatus
    | ReaderEnhancementProgressLayerStatus,
) {
  switch (status) {
    case "ready":
    case "succeeded":
      return "border-emerald-200 bg-emerald-50/80 text-emerald-900";
    case "failed":
      return "border-amber-300/80 bg-amber-50/90 text-amber-950";
    case "action_required":
      return "border-orange-300/80 bg-orange-50/90 text-orange-950";
    case "processing":
    case "readable_enhancing":
    case "queued":
      return "border-lens-blue/25 bg-lens-blue-soft text-ink-soft";
    default:
      return "border-hairline bg-surface-warm text-muted-foreground";
  }
}

type EnhancementCapabilitySummary = {
  capability: ReaderEnhancementCapability;
  label: string;
  total: number;
  succeeded: number;
  queued: number;
  processing: number;
  failed: number;
  actionRequired: number;
  notStarted: number;
  status: ReaderEnhancementProgressLayerStatus;
};

function summarizeEnhancementProgress(progress: ReaderEnhancementProgressDto) {
  const summaries = new Map<
    ReaderEnhancementCapability,
    EnhancementCapabilitySummary
  >([
    [
      "translation",
      {
        capability: "translation",
        label: "译文",
        total: 0,
        succeeded: 0,
        queued: 0,
        processing: 0,
        failed: 0,
        actionRequired: 0,
        notStarted: 0,
        status: "not_started",
      },
    ],
    [
      "vocabulary",
      {
        capability: "vocabulary",
        label: "词汇",
        total: 0,
        succeeded: 0,
        queued: 0,
        processing: 0,
        failed: 0,
        actionRequired: 0,
        notStarted: 0,
        status: "not_started",
      },
    ],
    [
      "grammar",
      {
        capability: "grammar",
        label: "语法",
        total: 0,
        succeeded: 0,
        queued: 0,
        processing: 0,
        failed: 0,
        actionRequired: 0,
        notStarted: 0,
        status: "not_started",
      },
    ],
  ]);

  progress.layers.forEach((layer) => {
    const summary = summaries.get(layer.capability);
    if (!summary) {
      return;
    }

    summary.total += layer.status === "not_started" ? 0 : 1;
    if (layer.status === "succeeded") {
      summary.succeeded += 1;
    } else if (layer.status === "queued") {
      summary.queued += 1;
    } else if (layer.status === "processing") {
      summary.processing += 1;
    } else if (layer.status === "failed") {
      summary.failed += 1;
    } else if (layer.status === "action_required") {
      summary.actionRequired += 1;
    } else {
      summary.notStarted += 1;
    }
  });

  summaries.forEach((summary) => {
    if (summary.actionRequired > 0) {
      summary.status = "action_required";
    } else if (summary.failed > 0) {
      summary.status = "failed";
    } else if (summary.processing > 0) {
      summary.status = "processing";
    } else if (summary.queued > 0) {
      summary.status = "queued";
    } else if (summary.total > 0 && summary.succeeded === summary.total) {
      summary.status = "succeeded";
    } else {
      summary.status = "not_started";
    }
  });

  return [...summaries.values()];
}

function enhancementSummaryText(summary: EnhancementCapabilitySummary) {
  if (summary.status === "not_started") {
    return "未开始";
  }

  const parts = [`${summary.succeeded}/${summary.total} 已完成`];
  if (summary.processing > 0) {
    parts.push(`${summary.processing} 处理中`);
  }
  if (summary.queued > 0) {
    parts.push(`${summary.queued} 排队中`);
  }
  if (summary.failed > 0) {
    parts.push(`${summary.failed} 失败`);
  }
  if (summary.actionRequired > 0) {
    parts.push(`${summary.actionRequired} 需处理`);
  }
  return parts.join(" · ");
}

function ReaderRecordEnhancementProgress({
  progress,
}: {
  progress?: ReaderEnhancementProgressDto;
}) {
  if (!progress) {
    return null;
  }

  const summaries = summarizeEnhancementProgress(progress);

  return (
    <div
      className={cn(
        "mx-auto mt-1 rounded-[10px] border px-4 py-3 text-sm leading-6",
        enhancementStatusTone(progress.overall_status),
      )}
      data-testid="reader-record-enhancement-progress"
    >
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0">
          <p className="text-[0.72rem] font-semibold uppercase tracking-[0.14em] opacity-70">
            增强进度
          </p>
          <p className="mt-1 font-semibold">
            {enhancementOverallLabel(progress.overall_status)}
          </p>
          <p className="text-[0.82rem] opacity-85">
            {enhancementOverallMessage(progress.overall_status)}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {summaries.map((summary) => {
            return (
              <span
                key={summary.capability}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[0.74rem] font-semibold leading-none",
                  enhancementStatusTone(summary.status),
                )}
                data-testid="reader-record-enhancement-layer"
              >
                <span>{summary.label}</span>
                <span className="opacity-70">·</span>
                <span>{enhancementSummaryText(summary)}</span>
              </span>
            );
          })}
        </div>
      </div>
    </div>
  );
}

const READ_ONLY_DICTIONARY_PANEL_BOTTOM =
  "max(5.25rem, calc(env(safe-area-inset-bottom) + 4.25rem))";
const READ_ONLY_DICTIONARY_AI_STATE = {
  kind: "idle",
} as const satisfies DictionaryAIViewState;
const READ_ONLY_SAVE_STATE = {
  kind: "idle",
} as const satisfies SaveState;

function escapeSelectorValue(value: string) {
  if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
    return CSS.escape(value);
  }
  return value.replace(/["\\]/g, "\\$&");
}

function zeroDomRect(): DOMRect {
  if (typeof DOMRect === "function") {
    return new DOMRect(0, 0, 0, 0);
  }
  return {
    x: 0,
    y: 0,
    width: 0,
    height: 0,
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    toJSON() {
      return this;
    },
  } as DOMRect;
}

function buildReaderRecordLookupSnapshot(
  recordId: string,
  intent: ReaderLookupIntent,
  state: DictionaryLookupSnapshot["state"],
): DictionaryLookupSnapshot {
  const snapshot = readerLookupSnapshotFromIntent(recordId, intent, state);
  return {
    ...snapshot,
    // Reader Record 当前只恢复只读查词，不暴露旧 Reader 的 AI/context-save 流程。
    contextSentence: "",
  };
}

function lookupPreviewAnchorWithFallback(
  anchor: ReaderLookupPreviewAnchor | null,
  intent: Pick<
    ReaderLookupIntent | ReaderStructuredInspectIntent,
    "anchorOffsets" | "anchorText" | "sentenceId"
  >,
  triggerEl?: HTMLElement | null,
): ReaderLookupPreviewAnchor | null {
  if (anchor) {
    return anchor;
  }

  if (!triggerEl) {
    return null;
  }

  return {
    sentenceId: intent.sentenceId,
    startOffset: intent.anchorOffsets?.startOffset ?? 0,
    endOffset: intent.anchorOffsets?.endOffset ?? intent.anchorText.length,
    fallbackRect: triggerEl.getBoundingClientRect?.() ?? zeroDomRect(),
  };
}

export function ReaderRecordWorkbenchSurface({
  snapshot,
}: ReaderRecordWorkbenchSurfaceProps) {
  const readerVm = useMemo(
    () => adaptReaderPlateSnapshotToReaderVm(snapshot),
    [snapshot],
  );
  const plateDocument = useMemo(
    () => adaptReaderPlateSnapshotToPlateDocument(snapshot),
    [snapshot],
  );
  const [readerSettings, setReaderSettings] =
    useState<ReaderSettingsState>(defaultReaderSettings);
  const { themePreference, setThemePreference } = useAppearance();
  const [settingsPanelOpen, setSettingsPanelOpen] = useState(false);
  const [expandedAnalysisEntryIds, setExpandedAnalysisEntryIds] = useState<
    string[]
  >([]);
  const [activeEntryId, setActiveEntryId] = useState<string | null>(null);
  const [textSelection, setTextSelection] = useState<ReaderTextSelection | null>(
    null,
  );
  const [selectionToolbarVisible, setSelectionToolbarVisible] = useState(false);
  const [activeLookup, setActiveLookup] =
    useState<DictionaryLookupSnapshot | null>(null);
  const [activeInspect, setActiveInspect] =
    useState<ReaderStructuredInspectIntent | null>(null);
  const [lookupPreviewOpen, setLookupPreviewOpen] = useState(false);
  const [lookupPreviewAnchor, setLookupPreviewAnchor] =
    useState<ReaderLookupPreviewAnchor | null>(null);
  const [dictionaryPanelOpen, setDictionaryPanelOpen] = useState(false);
  const [lookupHistory, setLookupHistory] = useState<DictionaryLookupSnapshot[]>(
    [],
  );
  const [dictionaryQuery, setDictionaryQuery] = useState("");
  const [dictionarySearchExpanded, setDictionarySearchExpanded] =
    useState(false);
  const [askOpen, setAskOpen] = useState(false);
  const [askSurface, setAskSurface] =
    useState<AiWorkspaceSurface>("sidecar");
  const [askAttachments, setAskAttachments] = useState<ReaderAskAttachment[]>(
    [],
  );
  const readingStageRef = useRef<HTMLDivElement | null>(null);

  const isImmersiveMode = readerSettings.mode === "immersive";
  const typography = readerModeTypography(readerSettings);
  const showTranslation = modeShowsTranslation(readerSettings.mode);
  const contentVisibility = modeVisibility(readerSettings.mode);
  const sentenceCount = readerVm.article.sentences.length;
  const formattedDate = formatDate(snapshot.record.created_at);
  const readinessLabel = readinessStateLabel(snapshot.record.readiness_state);
  const statusBanner = productStateBanner(snapshot.record.product_state);
  const askPageIdentity = useMemo<ReaderAskPageIdentity>(
    () => ({
      recordId: snapshot.record_id,
      recordTitle: snapshot.record.title,
      surface: "reader",
      source: "reader_2_0",
      availableContextCapabilities: ["record_context"],
      hasArticleOverview: false,
      hasSentenceEntries: sentenceCount > 0,
      hasAnnotations: snapshot.enhancement_layers.some(
        (layer) => layer.layer_type !== "translation",
      ),
      hasReaderNotes: snapshot.user_assets.some((asset) =>
        asset.asset_type === "note" ||
        asset.asset_type === "reader_note" ||
        asset.asset_type === "comment"
      ),
    }),
    [
      sentenceCount,
      snapshot.enhancement_layers,
      snapshot.record.title,
      snapshot.record_id,
      snapshot.user_assets,
    ],
  );
  const sentenceById = useMemo(
    () =>
      new Map(
        readerVm.article.sentences.map((sentence) => [sentence.sentenceId, sentence]),
      ),
    [readerVm.article.sentences],
  );
  const sourceContextBySentence = useMemo(
    () =>
      new Map(
        readerVm.translations.map((translation) => [
          translation.sentenceId,
          translation.translationZh,
        ]),
      ),
    [readerVm.translations],
  );
  const selectionFocusRangesBySentence = useMemo(() => {
    const map = new Map<string, ReaderJumpRangeSegment[]>();
    if (!textSelection) {
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
  }, [textSelection]);
  const lookupPreviewVisible = Boolean(
    lookupPreviewOpen &&
      lookupPreviewAnchor &&
      (activeLookup || activeInspect) &&
      !dictionaryPanelOpen,
  );
  const currentAskSelectionAttachment = useMemo<ReaderAskAttachment | null>(() => {
    if (!textSelection || textSelection.anchorType === "multi_text") {
      return null;
    }

    const [anchorDraft] = anchorDraftsForSelection(snapshot, textSelection);
    if (!anchorDraft) {
      return null;
    }

    const attachment = askAttachmentFromSelection(askPageIdentity, textSelection, {
      sourceSurface: "selection_toolbar",
    });

    return {
      ...attachment,
      metadata: {
        ...attachment.metadata,
        readingRecordAnchor: anchorDraft as unknown as Record<string, unknown>,
      },
    };
  }, [askPageIdentity, snapshot, textSelection]);
  const shellModeClass = isImmersiveMode
    ? "reader-shell--immersive"
    : "reader-shell--intensive";
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
    open: lookupPreviewVisible,
    placement: "top",
    offsetPx: 12,
    strategy: "fixed",
  });

  function updateReaderSettings(next: ReaderSettingsState) {
    setReaderSettings(next);
  }

  function toggleAnalysisEntry(entryId: string) {
    setExpandedAnalysisEntryIds((current) =>
      current.includes(entryId)
        ? current.filter((id) => id !== entryId)
        : [...current, entryId],
    );
    setActiveEntryId(entryId);
  }

  function setAnalysisEntryFocus(entryId: string, focused: boolean) {
    setActiveEntryId((current) => {
      if (focused) {
        return entryId;
      }
      return current === entryId ? null : current;
    });
  }

  const clearDomSelection = useCallback(() => {
    window.getSelection()?.removeAllRanges();
  }, []);

  const clearReaderSelection = useCallback(
    (options?: { preserveDomSelection?: boolean }) => {
      setTextSelection(null);
      setSelectionToolbarVisible(false);
      if (!options?.preserveDomSelection) {
        clearDomSelection();
      }
    },
    [clearDomSelection],
  );

  const dismissLookupPreview = useCallback(() => {
    setLookupPreviewOpen(false);
    setLookupPreviewAnchor(null);
  }, []);

  const handleRemoveAskAttachment = useCallback((attachmentKey: string) => {
    setAskAttachments((current) =>
      current.filter((item) => askAttachmentKey(item) !== attachmentKey),
    );
  }, []);

  const openAskPanel = useCallback(
    (
      attachment?: ReaderAskAttachment | null,
      surface: AiWorkspaceSurface = "sidecar",
    ) => {
      if (attachment) {
        setAskAttachments([attachment]);
      }
      setAskSurface(surface);
      setAskOpen(true);
      setDictionaryPanelOpen(false);
      setLookupPreviewOpen(false);
      setLookupPreviewAnchor(null);
      if (attachment) {
        clearReaderSelection();
      } else {
        setSelectionToolbarVisible(false);
      }
    },
    [clearReaderSelection],
  );

  const handleOpenAskPanel = useCallback(() => {
    openAskPanel(currentAskSelectionAttachment, "sidecar");
  }, [currentAskSelectionAttachment, openAskPanel]);

  const handleAskFromSelection = useCallback(() => {
    if (!currentAskSelectionAttachment) {
      return;
    }
    openAskPanel(currentAskSelectionAttachment, "floating");
  }, [currentAskSelectionAttachment, openAskPanel]);

  const handleLookupSnapshot = useCallback(
    (nextSnapshot: DictionaryLookupSnapshot) => {
      setActiveLookup(nextSnapshot);
      setActiveInspect(null);
      setDictionaryQuery(nextSnapshot.query);

      if (nextSnapshot.state.kind === "ready") {
        const nextHistoryKey = dictionaryLookupHistoryKey(nextSnapshot);
        setLookupHistory((current) =>
          [
            nextSnapshot,
            ...current.filter(
              (item) => dictionaryLookupHistoryKey(item) !== nextHistoryKey,
            ),
          ].slice(0, 8),
        );
      }
    },
    [],
  );

  const lookupPlainText = useCallback(
    async (
      intent: ReaderLookupIntent,
      options?: {
        showPreview?: boolean;
        anchor?: ReaderLookupPreviewAnchor | null;
        openPanel?: boolean;
      },
    ) => {
      const shouldOpenPanel = Boolean(options?.openPanel || dictionaryPanelOpen);
      const shouldShowPreview =
        !shouldOpenPanel && Boolean(options?.showPreview ?? true);

      setSelectionToolbarVisible(false);
      setDictionarySearchExpanded(false);
      setDictionaryPanelOpen(shouldOpenPanel);
      setLookupPreviewOpen(shouldShowPreview);
      setLookupPreviewAnchor(shouldShowPreview ? (options?.anchor ?? null) : null);

      handleLookupSnapshot(
        buildReaderRecordLookupSnapshot(snapshot.record_id, intent, {
          kind: "loading",
        }),
      );

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
        const payload = (await response.json().catch(() => null)) as
          | WebDictResult
          | null;
        if (!payload) {
          handleLookupSnapshot(
            buildReaderRecordLookupSnapshot(snapshot.record_id, intent, {
              kind: "error",
              message: "词典查询失败。",
            }),
          );
          return;
        }

        handleLookupSnapshot(
          buildReaderRecordLookupSnapshot(snapshot.record_id, intent, {
            kind: "ready",
            result: payload,
          }),
        );

        if (!response.ok && payload.kind !== "error") {
          handleLookupSnapshot(
            buildReaderRecordLookupSnapshot(snapshot.record_id, intent, {
              kind: "error",
              message: "词典查询失败。",
            }),
          );
        }
      } catch (error) {
        handleLookupSnapshot(
          buildReaderRecordLookupSnapshot(snapshot.record_id, intent, {
            kind: "error",
            message: error instanceof Error ? error.message : "词典查询失败。",
          }),
        );
      }
    },
    [dictionaryPanelOpen, handleLookupSnapshot, snapshot.record_id],
  );

  const lookupDictionaryQuery = useCallback(
    (query: string) => {
      const trimmed = query.trim();
      if (!trimmed) {
        return;
      }

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
        { showPreview: false, openPanel: true },
      );
    },
    [lookupPlainText],
  );

  const selectDictionaryCandidate = useCallback(
    async (entryId: number) => {
      if (!activeLookup) {
        return;
      }

      const loadingSnapshot: DictionaryLookupSnapshot = {
        ...activeLookup,
        state: { kind: "loading" },
      };
      handleLookupSnapshot(loadingSnapshot);

      try {
        const response = await fetch(`/api/web/dict/entry?id=${entryId}`);
        const payload = (await response.json().catch(() => null)) as
          | WebDictResult
          | null;
        if (!payload) {
          handleLookupSnapshot({
            ...activeLookup,
            state: { kind: "error", message: "词条加载失败。" },
          });
          return;
        }

        handleLookupSnapshot({
          ...activeLookup,
          state: { kind: "ready", result: payload },
        });

        if (!response.ok && payload.kind !== "error") {
          handleLookupSnapshot({
            ...activeLookup,
            state: { kind: "error", message: "词条加载失败。" },
          });
        }
      } catch (error) {
        handleLookupSnapshot({
          ...activeLookup,
          state: {
            kind: "error",
            message: error instanceof Error ? error.message : "词条加载失败。",
          },
        });
      }
    },
    [activeLookup, handleLookupSnapshot],
  );

  const selectLookupFromTrail = useCallback((lookup: DictionaryLookupSnapshot) => {
    setActiveLookup(lookup);
    setActiveInspect(null);
    setDictionaryPanelOpen(true);
    setLookupPreviewOpen(false);
    setLookupPreviewAnchor(null);
    setDictionaryQuery(lookup.query);
    setDictionarySearchExpanded(false);
  }, []);

  const openDictionaryPanel = useCallback(() => {
    if (!activeLookup && !activeInspect) {
      return;
    }
    setDictionaryPanelOpen(true);
    setLookupPreviewOpen(false);
    setLookupPreviewAnchor(null);
  }, [activeInspect, activeLookup]);

  const handleLookupIntent = useCallback(
    (
      intent: ReaderLookupIntent,
      anchor: ReaderLookupPreviewAnchor | null,
      triggerEl?: HTMLElement | null,
    ) => {
      const previewAnchor = lookupPreviewAnchorWithFallback(
        anchor,
        intent,
        triggerEl,
      );
      triggerEl?.focus({ preventScroll: true });
      void lookupPlainText(intent, { showPreview: true, anchor: previewAnchor });
    },
    [lookupPlainText],
  );

  const handleInspectIntent = useCallback(
    (
      intent: ReaderStructuredInspectIntent,
      anchor: ReaderLookupPreviewAnchor | null,
      triggerEl?: HTMLElement | null,
    ) => {
      const previewAnchor = lookupPreviewAnchorWithFallback(
        anchor,
        intent,
        triggerEl,
      );
      triggerEl?.focus({ preventScroll: true });
      setActiveLookup(null);
      setActiveInspect(intent);
      setDictionaryQuery(intent.lookupText ?? intent.anchorText);
      setDictionarySearchExpanded(false);

      if (dictionaryPanelOpen) {
        setLookupPreviewOpen(false);
        setLookupPreviewAnchor(null);
        return;
      }

      setLookupPreviewOpen(true);
      setLookupPreviewAnchor(previewAnchor);
    },
    [dictionaryPanelOpen],
  );

  const lookupPhraseFromInspect = useCallback(
    (intent: ReaderStructuredInspectIntent) => {
      void lookupPlainText(lookupIntentFromStructuredInspect(intent), {
        showPreview: false,
        openPanel: true,
      });
    },
    [lookupPlainText],
  );

  const lookupTextSelection = useCallback(() => {
    if (!textSelection) {
      return;
    }

    void lookupPlainText(
      lookupIntentFromSelection(
        textSelection,
        sourceContextBySentence.get(textSelection.sentence.sentenceId),
      ),
      { showPreview: false, openPanel: true },
    );
  }, [lookupPlainText, sourceContextBySentence, textSelection]);

  useEffect(() => {
    function handleSelectionChange() {
      const nativeSelection = window.getSelection();
      if (
        !nativeSelection ||
        nativeSelection.isCollapsed ||
        !nativeSelection.toString().trim()
      ) {
        clearReaderSelection({ preserveDomSelection: true });
        return;
      }

      const readingStageElement = readingStageRef.current;
      const anchorElement =
        nativeSelection.anchorNode instanceof Element
          ? nativeSelection.anchorNode
          : nativeSelection.anchorNode?.parentElement ?? null;
      const focusElement =
        nativeSelection.focusNode instanceof Element
          ? nativeSelection.focusNode
          : nativeSelection.focusNode?.parentElement ?? null;

      if (
        !readingStageElement ||
        !anchorElement ||
        !focusElement ||
        !readingStageElement.contains(anchorElement) ||
        !readingStageElement.contains(focusElement)
      ) {
        clearReaderSelection({ preserveDomSelection: true });
        return;
      }

      const nextSelection = readPlateReaderSelection(readingStageElement, sentenceById);
      setTextSelection(nextSelection);
      setSelectionToolbarVisible(Boolean(nextSelection));
    }

    document.addEventListener("selectionchange", handleSelectionChange);
    return () => {
      document.removeEventListener("selectionchange", handleSelectionChange);
    };
  }, [clearReaderSelection, sentenceById]);

  useEffect(() => {
    if (!textSelection) {
      setSelectionToolbarReference(null);
      return;
    }

    setSelectionToolbarReference({
      getBoundingClientRect: () =>
        selectionToolbarRectForReaderSelection(readingStageRef.current, textSelection),
      contextElement: readingStageRef.current ?? undefined,
    });
  }, [setSelectionToolbarReference, textSelection]);

  useEffect(() => {
    if (!lookupPreviewVisible || !lookupPreviewAnchor || !readingStageRef.current) {
      setLookupPreviewReference(null);
      return;
    }

    setLookupPreviewReference({
      getBoundingClientRect: () => {
        const sentenceTextElement =
          readingStageRef.current?.querySelector<HTMLElement>(
            `[data-reader-anchor="sentence"][data-sentence-id="${escapeSelectorValue(
              lookupPreviewAnchor.sentenceId,
            )}"] [data-reader-sentence-text="true"]`,
          ) ?? null;
        const liveRect = sentenceTextElement
          ? rectForTextOffsets(
              sentenceTextElement,
              lookupPreviewAnchor.startOffset,
              lookupPreviewAnchor.endOffset,
            )
          : null;
        return (
          liveRect ??
          lookupPreviewAnchor.fallbackRect ??
          zeroDomRect()
        );
      },
      contextElement: readingStageRef.current ?? undefined,
    });
  }, [lookupPreviewAnchor, lookupPreviewVisible, setLookupPreviewReference]);

  return (
    <main
      className="reader-shell-page min-h-screen px-3 pb-24 pt-3 text-ink sm:px-4 md:pb-6 lg:px-5"
      data-reader-record-workbench="true"
      data-testid="reader-record-workbench-surface"
    >
      <article
        className={cn(
          "reader-shell min-w-0 overflow-visible rounded-panel border border-hairline shadow-surface-quiet",
          shellModeClass,
        )}
      >
        <header className="reader-header-band reader-header-band--immersive reader-header-band--clean sticky top-3 z-20 border-b-0 bg-background/88 px-5 py-6 shadow-none backdrop-blur transition-[padding,background-color,border-color,box-shadow,transform] sm:px-8 lg:px-10 lg:py-8">
          <div className="reader-header-band-inner mx-auto flex w-full max-w-[82ch] flex-col gap-6 lg:gap-8">
            <div className="flex items-center gap-1.5 text-[0.8rem] font-semibold leading-none tracking-wide">
              <span className="text-lens-blue">
                {isImmersiveMode ? "沉浸阅读" : "精读模式"}
              </span>
              <span className="text-muted-foreground/60">·</span>
              <span className="font-medium text-muted-foreground">{formattedDate}</span>
              <span className="text-muted-foreground/60">·</span>
              <span className="font-medium text-muted-foreground">Reading Record</span>
            </div>

            <div className="min-w-0">
              <h1 className="font-headline text-[clamp(2rem,4vw,3.25rem)] font-bold leading-[1.08] tracking-tight text-ink">
                {snapshot.record.title}
              </h1>
              <p className="mt-4 max-w-[72ch] font-sans text-[0.95rem] font-medium leading-[1.68] tracking-wide text-muted-foreground">
                正文、译文和标注来自当前阅读快照；当前为只读预览。
              </p>
            </div>

            <div className="flex min-h-[56px] w-full flex-col items-stretch justify-between border-y border-hairline bg-transparent py-0 sm:flex-row">
              <div className="flex flex-wrap items-center gap-3.5 py-3 sm:py-0">
                <span className="flex select-none items-center gap-1.5 rounded-[0.5rem] border border-hairline/80 bg-surface-warm px-3 py-1 text-[0.75rem] font-semibold text-ink-soft shadow-[inset_0_1px_0_rgba(255,255,255,0.8),0_1px_2px_rgba(0,0,0,0.03)]">
                  <Sparkles className="h-3.5 w-3.5 fill-vocab-amber/10 text-vocab-amber" />
                  <span>{sourceTypeLabel(snapshot.record.source_type)}</span>
                </span>
                <div className="h-3.5 w-px bg-hairline" />
                <span className="text-[0.8rem] font-semibold text-muted-foreground">
                  {sentenceCount} 句
                </span>
                <div className="h-3.5 w-px bg-hairline" />
                <span className="text-[0.8rem] font-semibold text-muted-foreground">
                  {productStateLabel(snapshot.record.product_state)}
                </span>
                <div className="h-3.5 w-px bg-hairline" />
                <span
                  className="text-[0.8rem] font-semibold text-muted-foreground"
                  data-testid="reader-record-readiness-chip"
                >
                  {readinessLabel}
                </span>
              </div>

              <div className="flex select-none items-stretch divide-x divide-hairline border-t border-hairline sm:border-t-0">
                <button
                  type="button"
                  onClick={() =>
                    updateReaderSettings({ ...readerSettings, mode: "intensive" })
                  }
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
                    className={cn(
                      "h-[18px] w-[18px] shrink-0",
                      readerSettings.mode === "intensive"
                        ? "text-vocab-amber"
                        : "text-muted-foreground",
                    )}
                    strokeWidth={1.5}
                  />
                  <span className="flex min-w-0 flex-col items-start whitespace-nowrap leading-none">
                    <span className="text-[0.85rem] font-semibold">精读</span>
                    <span className="mt-1 hidden text-[0.65rem] font-medium text-subtle sm:block">
                      逐句研读
                    </span>
                  </span>
                </button>

                <button
                  type="button"
                  onClick={() =>
                    updateReaderSettings({ ...readerSettings, mode: "immersive" })
                  }
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
                    className={cn(
                      "h-[18px] w-[18px] shrink-0",
                      readerSettings.mode === "immersive"
                        ? "text-vocab-amber"
                        : "text-muted-foreground",
                    )}
                    strokeWidth={1.5}
                  />
                  <span className="flex min-w-0 flex-col items-start whitespace-nowrap leading-none">
                    <span className="text-[0.85rem] font-semibold">沉浸</span>
                    <span className="mt-1 hidden text-[0.65rem] font-medium text-subtle sm:block">
                      专注阅读
                    </span>
                  </span>
                </button>

                <button
                  type="button"
                  aria-expanded={settingsPanelOpen}
                  onClick={() => setSettingsPanelOpen((current) => !current)}
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
                    className={cn(
                      "h-[18px] w-[18px] shrink-0",
                      settingsPanelOpen ? "text-vocab-amber" : "text-muted-foreground",
                    )}
                    strokeWidth={1.5}
                  />
                  <span className="flex min-w-0 flex-col items-start whitespace-nowrap leading-none">
                    <span className="text-[0.85rem] font-semibold">阅读设置</span>
                    <span className="mt-1 hidden text-[0.65rem] font-medium text-subtle sm:block">
                      版式与偏好
                    </span>
                  </span>
                </button>
              </div>
            </div>

            <div className="flex flex-col gap-3 text-[0.78rem] leading-normal tracking-wide text-muted-foreground sm:flex-row sm:items-center sm:justify-between sm:gap-0 sm:leading-none">
              <div className="flex flex-wrap items-center gap-1.5 font-medium">
                <span>快照 {snapshot.snapshot_id}</span>
                <span className="text-muted-foreground/60">·</span>
                <span>事件序列 {snapshot.last_event_sequence}</span>
                <span className="text-muted-foreground/60">·</span>
                <span>只读预览</span>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={handleOpenAskPanel}
                  title="打开 Ask Claread"
                  className={cn(
                    readerCommandControl,
                    "h-8 rounded-md px-2.5",
                    askOpen ? "border-lens-blue/30 text-lens-blue" : null,
                  )}
                >
                  <MessageSquare aria-hidden="true" className="h-3.5 w-3.5" />
                  Ask Claread
                </button>
                <button
                  type="button"
                  disabled
                  title="新 Reading Record 的笔记和高亮持久化尚未接通"
                  className={cn(readerCommandControl, "h-8 rounded-md px-2.5")}
                  data-reader-record-disabled="notes-highlights"
                >
                  <Highlighter aria-hidden="true" className="h-3.5 w-3.5" />
                  笔记/高亮
                </button>
                <button
                  type="button"
                  disabled
                  title="新 Reading Record 的词典写入和用户资产保存尚未接通"
                  className={cn(readerCommandControl, "h-8 rounded-md px-2.5")}
                  data-reader-record-disabled="dictionary-assets"
                >
                  <Search aria-hidden="true" className="h-3.5 w-3.5" />
                  词典保存
                </button>
              </div>
            </div>

            {settingsPanelOpen ? (
              <div className="mx-auto w-full max-w-[82ch]">
                <ReaderSettingsPanel
                  themePreference={themePreference}
                  value={readerSettings}
                  onChange={updateReaderSettings}
                  onThemeChange={setThemePreference}
                  onClose={() => setSettingsPanelOpen(false)}
                />
              </div>
            ) : null}

            {statusBanner ? (
              <div
                className={cn(
                  "mx-auto mt-1 rounded-[10px] border px-4 py-3 text-sm leading-6",
                  statusBanner.className,
                )}
                data-testid="reader-record-status-banner"
              >
                <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
                  <div>
                    <p className="text-[0.72rem] font-semibold uppercase tracking-[0.14em] opacity-70">
                      记录状态
                    </p>
                    <p className="mt-1 font-semibold">{statusBanner.title}</p>
                    <p>{statusBanner.body}</p>
                  </div>
                  <p
                    className="text-[0.78rem] font-medium opacity-80"
                    data-testid="reader-record-readiness-state"
                  >
                    当前阶段：{readinessLabel}
                  </p>
                </div>
              </div>
            ) : (
              <p
                className="mx-auto mt-1 text-[0.78rem] font-medium tracking-wide text-muted-foreground"
                data-testid="reader-record-readiness-state"
              >
                当前阶段：{readinessLabel}
              </p>
            )}

            <ReaderRecordEnhancementProgress
              progress={snapshot.enhancement_progress}
            />

            <div className="reader-shell-message mx-auto mt-1 rounded-[10px] border border-lens-blue/20 bg-lens-blue-soft px-4 py-3 text-sm leading-6 text-ink-soft">
              当前可使用 Ask Claread、点击单词、点击标注或选中正文进行只读查词；笔记、高亮和词典写入暂不可用。
            </div>
          </div>
        </header>

        <div
          ref={readingStageRef}
          className={cn(
            "reader-reading-stage",
            isImmersiveMode
              ? "reader-reading-stage--immersive"
              : "reader-reading-stage--intensive",
          )}
        >
          {isImmersiveMode ? (
            <ImmersiveReaderSurface
              document={plateDocument}
              readingClassName={typography.bodyClassName}
              columnClassName={typography.columnClassName}
              paragraphDensityClassName={typography.paragraphDensityClassName}
              selectionFocusRangesBySentence={selectionFocusRangesBySentence}
              activeInlineMarkKey={activeInspect?.markId ?? null}
              onLookupIntent={handleLookupIntent}
              onInspectIntent={handleInspectIntent}
            />
          ) : (
            <IntensiveReaderSurface
              document={plateDocument}
              showTranslation={showTranslation}
              readingClassName={typography.bodyClassName}
              translationClassName={typography.translationClassName}
              columnClassName={typography.columnClassName}
              paragraphDensityClassName={typography.paragraphDensityClassName}
              annotationVisibilityGroups={contentVisibility}
              selectionFocusRangesBySentence={selectionFocusRangesBySentence}
              activeInlineMarkKey={activeInspect?.markId ?? null}
              activeAnalysisEntryId={activeEntryId}
              expandedAnalysisEntryIds={expandedAnalysisEntryIds}
              onAnalysisFocusChange={setAnalysisEntryFocus}
              onAnalysisToggle={toggleAnalysisEntry}
              onLookupIntent={handleLookupIntent}
              onInspectIntent={handleInspectIntent}
            />
          )}
        </div>
      </article>

      {textSelection && selectionToolbarVisible ? (
        <div
          ref={setSelectionToolbarFloating}
          style={selectionToolbarStyles}
          className="z-50"
          data-testid="reader-record-selection-toolbar"
          onPointerDown={(event) => {
            event.preventDefault();
          }}
        >
          <SelectionToolbar
            className={
              isImmersiveMode
                ? "reader-selection-toolbar reader-selection-toolbar--immersive"
                : "reader-selection-toolbar"
            }
            selectedText={textSelection.selectedText}
            selectionMode={textSelection.anchorType}
            disabled={{
              ask: !currentAskSelectionAttachment,
              selectSentence: true,
              highlight: true,
              note: true,
              clear: true,
              feedback: true,
            }}
            onAsk={handleAskFromSelection}
            onLookup={lookupTextSelection}
          />
        </div>
      ) : null}

      <AiWorkspacePanel
        open={askOpen}
        presentation={isImmersiveMode ? "immersive" : "intensive"}
        surface={askSurface}
        pageIdentity={askPageIdentity}
        recordId={snapshot.record_id}
        recordScope="reading_record"
        hideClosedLauncher
        recordTitle={snapshot.record.title}
        attachments={askAttachments}
        onRemoveAttachment={handleRemoveAskAttachment}
        onClearAttachments={() => setAskAttachments([])}
        onOpenSidecar={() => setAskSurface("sidecar")}
        onToggle={() => setAskOpen(false)}
      />

      {lookupPreviewVisible ? (
        <ReaderQuickPeek
          lookup={activeLookup}
          inspect={activeInspect}
          className={
            isImmersiveMode
              ? "reader-tool-float reader-tool-float--immersive"
              : "reader-tool-float"
          }
          floatingRef={setLookupPreviewFloating}
          style={lookupPreviewStyles}
          onDismiss={dismissLookupPreview}
          onOpenDetail={openDictionaryPanel}
          onLookupPhrase={
            activeInspect ? () => lookupPhraseFromInspect(activeInspect) : undefined
          }
        />
      ) : null}

      {dictionaryPanelOpen ? (
        <div
          className={cn(
            "reader-tool-surface reader-tool-surface--compact fixed inset-x-3 z-50 flex max-h-[72vh] flex-col md:bottom-6",
            isImmersiveMode
              ? "reader-tool-surface--immersive"
              : "reader-tool-surface--intensive",
          )}
          style={{ bottom: READ_ONLY_DICTIONARY_PANEL_BOTTOM }}
          data-testid="reader-record-dictionary-panel"
        >
          <ReaderDictionaryRail
            lookup={activeLookup}
            inspect={activeInspect}
            history={lookupHistory}
            readingGoal="daily_reading"
            saveState={READ_ONLY_SAVE_STATE}
            lookupSaveState="not_saved"
            savedVocabularyMatch={null}
            dictionaryAI={READ_ONLY_DICTIONARY_AI_STATE}
            dictionaryAIPanelOpen={false}
            dictionaryAINoteState={READ_ONLY_SAVE_STATE}
            searchQuery={dictionaryQuery}
            searchExpanded={dictionarySearchExpanded}
            onSave={() => {}}
            onRequestAI={() => {}}
            onCreateAINote={() => {}}
            onSelectAISuggestedQuery={() => {}}
            onSearchQueryChange={setDictionaryQuery}
            onSearchSubmit={lookupDictionaryQuery}
            onSelectCandidate={selectDictionaryCandidate}
            onToggleAIPanel={() => {}}
            onToggleSearchExpanded={() =>
              setDictionarySearchExpanded((value) => !value)
            }
            onDismiss={() => setDictionaryPanelOpen(false)}
            canSaveVocabulary={false}
            canCreateAINote={false}
            onLookupPhraseFromInspect={lookupPhraseFromInspect}
            onSelectHistory={selectLookupFromTrail}
          />
        </div>
      ) : null}
    </main>
  );
}
