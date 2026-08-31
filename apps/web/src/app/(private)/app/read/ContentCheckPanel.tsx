"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  ChevronDown,
  FileSearch,
  FileText,
  MapPin,
  PanelRightOpen,
  RefreshCw,
  Wrench,
  X,
} from "lucide-react";

import { Button } from "@/components/primitives/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/primitives/sheet";
import { TextAction } from "@/components/primitives/text-action";
import { cn } from "@/lib/cn";
import type {
  ReaderAdaptationRecordDto,
  ReaderContentCheckItemDto,
} from "@/types/api/reader-plate";
import {
  applyContentCheckProposedPatch,
  type ContentCheckAnchorInspection,
  guidanceForContentCheckCode,
  inspectContentCheckAnchor,
} from "./content-check-guidance";
import {
  MarkdownTextInput,
  type MarkdownTextInputHandle,
} from "./MarkdownTextInput";
import { readRejectedReasons, useContentCheck } from "./use-content-check";

export interface ContentCheckPanelDeferInfo {
  recordId: string;
  candidateDocumentId: string | null;
  canonicalTextPreview: string | null;
}

export interface ContentCheckPanelProps {
  recordId: string;
  filename?: string | null;
  origin: "submit" | "resume";
  onOpenReader: (recordId: string) => void;
  onConfirmed: (recordId: string) => void;
  onSourceMissing: () => void;
  onBackToInput: (markdown: string) => void;
  onDefer: (info: ContentCheckPanelDeferInfo) => void;
}

const EMPTY_CONTENT_CHECK: ReaderContentCheckItemDto[] = [];

type SourcePreviewMime =
  | "application/pdf"
  | "image/png"
  | "image/jpeg"
  | "image/webp";

type SourcePreviewState = {
  identity: string | null;
  status: "idle" | "loading" | "ready" | "error";
  objectUrl: string | null;
  mime: SourcePreviewMime | null;
  pageNumber: number;
  hasPageNumber: boolean;
  message: string | null;
  retryable: boolean;
};

type MobileSheet = "review" | "source" | null;

const SOURCE_PREVIEW_MIMES = new Set<SourcePreviewMime>([
  "application/pdf",
  "image/png",
  "image/jpeg",
  "image/webp",
]);

const EMPTY_SOURCE_PREVIEW: SourcePreviewState = {
  identity: null,
  status: "idle",
  objectUrl: null,
  mime: null,
  pageNumber: 1,
  hasPageNumber: false,
  message: null,
  retryable: false,
};

function AdaptationNoticeRail({ items }: { items: ReaderAdaptationRecordDto[] }) {
  const [expanded, setExpanded] = useState(false);
  if (items.length === 0) return null;
  return (
    <div data-testid="content-check-adaptation-notice" className="border-b border-hairline pb-4">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((current) => !current)}
        className="focus-ring flex min-h-11 w-full items-center justify-between gap-3 text-left"
      >
        <span className="inline-flex items-center gap-2 text-xs font-medium text-muted-foreground">
          <Check aria-hidden className="size-4 text-lens-blue" />
          已自动处理 {items.length} 项格式问题
        </span>
        <ChevronDown
          aria-hidden
          className={cn(
            "size-4 text-subtle transition-transform duration-[var(--cl-duration-fast)] motion-reduce:transition-none",
            expanded && "rotate-180",
          )}
        />
      </button>
      {expanded ? (
        <ul className="space-y-2 pt-3 text-xs leading-5 text-muted-foreground">
          {items.map((item, index) => (
            <li key={`${item.code}-${index}`}>
              {guidanceForContentCheckCode(item.code).title}
              <details className="mt-1 text-subtle">
                <summary className="cursor-pointer">技术详情</summary>
                <code className="mt-1 block break-all">{item.code}</code>
              </details>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function ContentCheckPanel({
  recordId,
  filename,
  origin,
  onOpenReader,
  onConfirmed,
  onSourceMissing,
  onBackToInput,
  onDefer,
}: ContentCheckPanelProps) {
  const editorRef = useRef<MarkdownTextInputHandle | null>(null);
  const [anchorInspections, setAnchorInspections] = useState<
    ReadonlyMap<string, ContentCheckAnchorInspection>
  >(new Map());
  const [markerPositions, setMarkerPositions] = useState<
    ReadonlyMap<string, { top: number; documentHeight: number }>
  >(new Map());
  const [activeIssueId, setActiveIssueId] = useState<string | null>(null);
  const [interactionMessage, setInteractionMessage] = useState<string | null>(null);
  const [mobileSheet, setMobileSheet] = useState<MobileSheet>(null);
  const [isDesktop, setIsDesktop] = useState(true);
  const [desktopSourceOpen, setDesktopSourceOpen] = useState(false);
  const [sourcePreview, setSourcePreview] =
    useState<SourcePreviewState>(EMPTY_SOURCE_PREVIEW);
  const sourceTriggerRef = useRef<HTMLButtonElement | null>(null);
  const sourceCloseButtonRef = useRef<HTMLButtonElement | null>(null);
  const mobileSourceBackRef = useRef<HTMLButtonElement | null>(null);
  const previewAbortRef = useRef<AbortController | null>(null);
  const previewObjectUrlRef = useRef<string | null>(null);
  const previewRequestTokenRef = useRef(0);
  const {
    state,
    workingMarkdown,
    issueStates,
    handleEdit,
    saveNow,
    confirmAndStart,
    reloadLatest,
    retryWithLatestRevision,
    retryLoad,
    confirmIssue,
    confirmIssues,
    markIssueModified,
    unconfirmIssue,
  } = useContentCheck({ recordId, onOpenReader, onSourceMissing, onConfirmed });

  const draft = state.draft;
  const contentCheck = draft?.contentCheck ?? EMPTY_CONTENT_CHECK;
  const currentPreviewIdentity = draft
    ? `${recordId}:${draft.recordGeneration}`
    : null;
  const sourcePreviewIsCurrent =
    currentPreviewIdentity !== null &&
    sourcePreview.identity === currentPreviewIdentity;
  const visibleMobileSheet =
    mobileSheet === "source" && !sourcePreviewIsCurrent ? null : mobileSheet;
  const sheetOpen = visibleMobileSheet !== null;

  const releaseSourcePreview = useCallback(() => {
    const controller = previewAbortRef.current;
    previewAbortRef.current = null;
    controller?.abort();
    const objectUrl = previewObjectUrlRef.current;
    previewObjectUrlRef.current = null;
    if (objectUrl && typeof URL.revokeObjectURL === "function") {
      URL.revokeObjectURL(objectUrl);
    }
    previewRequestTokenRef.current += 1;
  }, []);

  const loadSourcePreview = useCallback(
    async (requestedPage: number | null | undefined) => {
      const generation = draft?.recordGeneration;
      if (!Number.isSafeInteger(generation) || !generation || generation < 1) return;
      const identity = `${recordId}:${generation}`;

      releaseSourcePreview();
      const requestToken = previewRequestTokenRef.current;
      const controller = new AbortController();
      previewAbortRef.current = controller;
      const hasPageNumber =
        Number.isSafeInteger(requestedPage) && Boolean(requestedPage && requestedPage > 0);
      const pageNumber = hasPageNumber ? Number(requestedPage) : 1;
      setSourcePreview({
        ...EMPTY_SOURCE_PREVIEW,
        identity,
        status: "loading",
        pageNumber,
        hasPageNumber,
      });

      let failureStatus: number | null = null;
      try {
        const response = await fetch(
          `/api/web/reader/records/${encodeURIComponent(recordId)}/source-preview?expected_generation=${generation}`,
          { cache: "no-store", signal: controller.signal },
        );
        if (!response.ok) {
          failureStatus = response.status;
          throw new Error("source preview request rejected");
        }
        const normalizedMime = response.headers
          .get("content-type")
          ?.split(";", 1)[0]
          ?.trim()
          .toLowerCase();
        if (!normalizedMime || !SOURCE_PREVIEW_MIMES.has(normalizedMime as SourcePreviewMime)) {
          failureStatus = 415;
          throw new Error("source preview MIME rejected");
        }
        const blob = await response.blob();
        if (controller.signal.aborted || requestToken !== previewRequestTokenRef.current) return;
        if (
          typeof URL.createObjectURL !== "function" ||
          typeof URL.revokeObjectURL !== "function"
        ) {
          throw new Error("source preview object URLs unavailable");
        }
        const objectUrl = URL.createObjectURL(blob);
        if (controller.signal.aborted || requestToken !== previewRequestTokenRef.current) {
          URL.revokeObjectURL(objectUrl);
          return;
        }
        previewObjectUrlRef.current = objectUrl;
        setSourcePreview({
          identity,
          status: "ready",
          objectUrl,
          mime: normalizedMime as SourcePreviewMime,
          pageNumber,
          hasPageNumber,
          message: null,
          retryable: false,
        });
      } catch {
        if (controller.signal.aborted || requestToken !== previewRequestTokenRef.current) return;
        releaseSourcePreview();
        const unsupported = failureStatus === 413 || failureStatus === 415;
        setSourcePreview({
          ...EMPTY_SOURCE_PREVIEW,
          identity,
          status: "error",
          pageNumber,
          hasPageNumber,
          message: unsupported
            ? "该原件暂不支持安全预览，正文可继续编辑与确认。"
            : "原件暂时无法预览，正文可继续编辑与确认。",
          retryable:
            failureStatus === null ||
            failureStatus === 502 ||
            failureStatus === 503 ||
            failureStatus === 504,
        });
      }
    },
    [draft?.recordGeneration, recordId, releaseSourcePreview],
  );

  const closeSourcePreview = useCallback(() => {
    releaseSourcePreview();
    setSourcePreview(EMPTY_SOURCE_PREVIEW);
    if (isDesktop) {
      setDesktopSourceOpen(false);
      queueMicrotask(() => sourceTriggerRef.current?.focus());
    } else {
      setMobileSheet(null);
    }
  }, [isDesktop, releaseSourcePreview]);

  const openSourcePreview = useCallback(
    (item: ReaderContentCheckItemDto, trigger: HTMLButtonElement) => {
      sourceTriggerRef.current = trigger;
      if (isDesktop) setDesktopSourceOpen(true);
      else setMobileSheet("source");
      void loadSourcePreview(item.source_media_coordinate?.page_number);
    },
    [isDesktop, loadSourcePreview],
  );

  useEffect(() => {
    releaseSourcePreview();
  }, [currentPreviewIdentity, releaseSourcePreview]);

  useEffect(() => () => releaseSourcePreview(), [releaseSourcePreview]);

  useEffect(() => {
    if (!desktopSourceOpen) return;
    sourceCloseButtonRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      closeSourcePreview();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [closeSourcePreview, desktopSourceOpen]);

  useEffect(() => {
    if (visibleMobileSheet === "source") mobileSourceBackRef.current?.focus();
  }, [visibleMobileSheet]);

  useEffect(() => {
    if (!window.matchMedia) return;
    const media = window.matchMedia("(min-width: 1024px)");
    const sync = () => setIsDesktop(media.matches);
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);

  useEffect(() => {
    let current = true;
    void Promise.all(
      contentCheck.map(async (item) => [
        item.issue_id,
        await inspectContentCheckAnchor(item, workingMarkdown),
      ] as const),
    ).then((nextEntries) => {
      if (!current) return;
      setAnchorInspections(new Map(nextEntries));
      const positions = new Map<
        string,
        { top: number; documentHeight: number }
      >();
      for (const [issueId, inspection] of nextEntries) {
        if (inspection.status !== "valid" || !inspection.excerpt) continue;
        const position = editorRef.current?.measureExact(inspection.excerpt);
        if (position) positions.set(issueId, position);
      }
      setMarkerPositions(positions);
    });
    return () => {
      current = false;
    };
  }, [contentCheck, workingMarkdown]);

  const entries = contentCheck.map((item) => ({
    item,
    issueState: issueStates.get(item.issue_id),
  }));
  const unresolvedChecks = entries.filter(
    ({ issueState }) => issueState !== "confirmed",
  );
  const resolvedChecks = entries.filter(
    ({ issueState }) => issueState === "confirmed",
  );
  const attentionChecks = unresolvedChecks.filter(
    ({ item }) => item.tier === "attention",
  );
  const routineChecks = unresolvedChecks.filter(
    ({ item }) => item.tier === "routine",
  );
  const markerEntries = entries.filter(({ item }) => {
    const inspection = anchorInspections.get(item.issue_id);
    return (
      inspection?.status === "valid" &&
      Boolean(inspection.excerpt) &&
      markerPositions.has(item.issue_id)
    );
  });
  const markerDocumentHeight = Math.max(
    0,
    ...Array.from(markerPositions.values(), ({ documentHeight }) =>
      documentHeight,
    ),
  );

  const flushEditor = useCallback(
    () => editorRef.current?.flush() ?? workingMarkdown,
    [workingMarkdown],
  );

  const handleDocumentEdit = useCallback(
    (markdown: string) => {
      for (const item of contentCheck) markIssueModified(item.issue_id);
      handleEdit(markdown);
    },
    [contentCheck, handleEdit, markIssueModified],
  );

  const activateIssue = useCallback((issueId: string) => {
    setActiveIssueId(issueId);
    document
      .getElementById(`content-check-issue-${issueId}`)
      ?.scrollIntoView?.({ block: "nearest" });
  }, []);

  const revealIssue = useCallback(
    (item: ReaderContentCheckItemDto) => {
      const inspection = anchorInspections.get(item.issue_id);
      activateIssue(item.issue_id);
      if (
        inspection?.status !== "valid" ||
        !inspection.excerpt ||
        !editorRef.current?.revealExact(inspection.excerpt)
      ) {
        setInteractionMessage("当前排版视图无法精确定位这项批注。");
        return;
      }
      setInteractionMessage("已定位到正文中的对应位置。");
      if (!isDesktop) setMobileSheet(null);
    },
    [activateIssue, anchorInspections, isDesktop],
  );

  const adoptSuggestion = useCallback(
    async (item: ReaderContentCheckItemDto) => {
      const fixed = await applyContentCheckProposedPatch(item, flushEditor());
      if (fixed === null) {
        setInteractionMessage("建议无法精确应用，正文没有改变。");
        return;
      }
      editorRef.current?.setValue(fixed);
      handleEdit(fixed);
      markIssueModified(item.issue_id);
      activateIssue(item.issue_id);
      setInteractionMessage("已采用建议。内容已修改，请确认当前内容。");
    },
    [activateIssue, flushEditor, handleEdit, markIssueModified],
  );

  async function handleReloadLatest() {
    const latest = await reloadLatest();
    if (latest !== null) editorRef.current?.setValue(latest);
  }

  const isBusy = state.phase === "saving" || state.phase === "confirming";
  const isStableReady = draft?.outcome === "stable_document_ready";
  const isRejected = draft?.outcome === "input_rejected_or_action_required";
  const canAttemptConfirm = isStableReady || Boolean(draft?.candidate);
  const primaryLabel =
    state.phase === "confirming" ? "确认中…" : "确认正文并开始阅读";
  const handleConfirm = () => {
    const text = flushEditor();
    const needsSave = text !== draft?.savedMarkdown || state.dirty;
    if (isBusy) return;
    if (needsSave) {
      void confirmAndStart(text);
      return;
    }
    if (
      (isRejected && !state.dirty) ||
      attentionChecks.length > 0 ||
      !canAttemptConfirm
    ) {
      return;
    }
    void confirmAndStart(text);
  };

  const handleDefer = async () => {
    const text = flushEditor();
    const needsSave = text !== draft?.savedMarkdown || state.dirty;
    if (needsSave && !(await saveNow(text))) return;
    onDefer({
      recordId,
      candidateDocumentId: needsSave
        ? null
        : (draft?.candidate?.candidate_document_id ?? null),
      canonicalTextPreview: needsSave
        ? null
        : (draft?.candidate?.canonical_text_preview ?? null),
    });
  };

  if (state.phase === "loading" && !draft) {
    return (
      <section
        data-testid="content-check-panel"
        role="status"
        className="flex min-h-96 flex-1 items-center justify-center bg-surface"
      >
        <p className="text-sm font-medium text-muted-foreground">
          正在载入待确认的内容…
        </p>
      </section>
    );
  }

  if (state.phase === "error" && !draft) {
    return (
      <section
        data-testid="content-check-panel"
        role="alert"
        className="flex min-h-96 flex-1 flex-col items-center justify-center gap-4 bg-surface px-8"
      >
        <p className="text-sm font-medium text-feedback-error">
          {state.errorMessage ?? "加载失败，请稍后重试。"}
        </p>
        <Button type="button" variant="secondary" size="sm" onClick={retryLoad}>
          重试加载
          <RefreshCw aria-hidden className="ml-1 size-4" />
        </Button>
      </section>
    );
  }

  const hasRail =
    entries.length > 0 ||
    Boolean(draft?.adaptationNotice.length) ||
    Boolean(isRejected);
  const statusSummary =
    attentionChecks.length > 0
      ? `${attentionChecks.length} 项需要确认${
          routineChecks.length > 0 ? `，${routineChecks.length} 项普通建议` : ""
        }`
      : routineChecks.length > 0
        ? `${routineChecks.length} 项普通建议，不阻塞确认`
        : "所有需要确认的批注均已处理";

  function renderCheckCard({
    item,
    issueState,
  }: {
    item: ReaderContentCheckItemDto;
    issueState: "confirmed" | "modified" | undefined;
  }) {
    const guidance = guidanceForContentCheckCode(item.code);
    const inspection = anchorInspections.get(item.issue_id);
    const isAttention = item.tier === "attention";
    const canReveal =
      inspection?.status === "valid" &&
      Boolean(inspection.excerpt) &&
      markerPositions.has(item.issue_id);
    const hasPatch = canReveal && Boolean(item.evidence.proposed_patch?.trim());
    return (
      <article
        id={`content-check-issue-${item.issue_id}`}
        key={item.issue_id}
        data-testid="content-check-risk-item"
        data-code={item.code}
        data-issue-id={item.issue_id}
        aria-current={activeIssueId === item.issue_id ? "true" : undefined}
        className={cn(
          "border-b border-hairline py-4 last:border-b-0",
          activeIssueId === item.issue_id && "bg-surface-raised/70",
        )}
      >
        <button
          type="button"
          onClick={() => (canReveal ? revealIssue(item) : activateIssue(item.issue_id))}
          className="focus-ring flex min-h-11 w-full items-start justify-between gap-3 text-left"
        >
          <span>
            <span className="block text-sm font-semibold text-ink">
              {guidance.title}
            </span>
            <span className="mt-1 block text-xs leading-5 text-muted-foreground">
              {guidance.suggestion}
            </span>
          </span>
          <span className="shrink-0 text-xs font-medium text-subtle">
            {isAttention ? "需要确认" : "普通建议"}
          </span>
        </button>

        <div className="mt-2 flex flex-wrap gap-2 text-xs font-medium">
          {item.target_scope === "document" ? (
            <span className="text-muted-foreground">全文检查</span>
          ) : null}
          {inspection?.status === "changed" ? (
            <span className="text-feedback-warning">位置已变化</span>
          ) : null}
          {issueState === "modified" ? (
            <span className="text-ink">内容已修改，待确认</span>
          ) : null}
        </div>

        <details className="mt-3 text-xs text-muted-foreground">
          <summary className="focus-ring min-h-11 cursor-pointer py-3 font-medium text-ink">
            查看审查详情
          </summary>
          {item.evidence.excerpt_text ? (
            <pre className="overflow-x-auto whitespace-pre-wrap border-y border-hairline bg-surface-raised px-3 py-2 font-mono leading-5 text-ink">
              {item.evidence.excerpt_text}
            </pre>
          ) : (
            <p className="py-2">后端没有提供可精确展示的局部片段。</p>
          )}
          {item.evidence.excerpt_text && item.evidence.proposed_patch?.trim() ? (
            <div className="mt-3 space-y-1 border-y border-hairline bg-surface-raised px-3 py-2 font-mono leading-5 text-ink">
              <p>
                <span aria-hidden className="mr-2 text-subtle">−</span>
                {item.evidence.excerpt_text}
              </p>
              <p>
                <span aria-hidden className="mr-2 text-subtle">+</span>
                {item.evidence.proposed_patch}
              </p>
            </div>
          ) : null}
          <details className="mt-3 text-subtle">
            <summary className="cursor-pointer py-2">技术详情</summary>
            <code className="block break-all">{item.code}</code>
          </details>
        </details>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          {canReveal ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="max-lg:min-h-11 max-lg:min-w-11"
              onClick={() => revealIssue(item)}
            >
              <MapPin aria-hidden className="mr-1 size-4" />
              查看位置
            </Button>
          ) : null}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            data-testid="source-preview-trigger"
            className="min-h-11 min-w-11"
            onClick={(event) => openSourcePreview(item, event.currentTarget)}
          >
            <FileSearch aria-hidden className="mr-1 size-4" />
            查看原件
          </Button>
          {hasPatch ? (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="max-lg:min-h-11 max-lg:min-w-11"
              onClick={() => void adoptSuggestion(item)}
            >
              <Wrench aria-hidden className="mr-1 size-4" />
              采用建议
            </Button>
          ) : null}
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="max-lg:min-h-11 max-lg:min-w-11"
            onClick={() => {
              confirmIssue(item.issue_id);
              setInteractionMessage("已确认当前内容。");
            }}
          >
            确认当前内容
          </Button>
        </div>
      </article>
    );
  }

  const reviewRail = (
    <div className="flex min-h-0 flex-col gap-4 font-sans">
      {draft?.adaptationNotice.length ? (
        <AdaptationNoticeRail items={draft.adaptationNotice} />
      ) : null}
      {unresolvedChecks.length > 0 ? (
        <div data-testid="content-check-risk-list">
          {attentionChecks.map(renderCheckCard)}
          {routineChecks.map(renderCheckCard)}
        </div>
      ) : null}
      {resolvedChecks.length > 0 ? (
        <div data-testid="content-check-resolved-summary" className="border-t border-hairline pt-4">
          <p className="text-xs font-medium text-subtle">
            已处理 {resolvedChecks.length} 项
          </p>
          <ul className="mt-2 space-y-2">
            {resolvedChecks.map(({ item }) => (
              <li key={item.issue_id} className="flex items-center justify-between gap-2 text-xs">
                <span className="min-w-0 truncate text-muted-foreground">
                  {guidanceForContentCheckCode(item.code).title}
                </span>
                <TextAction
                  onClick={() => unconfirmIssue(item.issue_id)}
                  className="min-h-11 px-2 text-xs"
                >
                  撤销
                </TextAction>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {isRejected ? (
        <div data-testid="content-check-rejected" role="alert" className="border-t border-hairline pt-4">
          <p className="inline-flex items-center gap-2 text-sm font-semibold text-ink">
            <AlertTriangle aria-hidden className="size-4 text-feedback-warning" />
            当前内容无法生成阅读版本
          </p>
          <ul className="mt-2 list-disc space-y-1 pl-4 text-xs leading-5 text-muted-foreground">
            {readRejectedReasons(draft?.quality ?? null, contentCheck)
              .slice(0, 3)
              .map((reason) => <li key={reason}>{reason}</li>)}
          </ul>
        </div>
      ) : null}
    </div>
  );

  const sourcePreviewContent = (
    <div className="flex min-h-0 flex-1 flex-col">
      {sourcePreview.status === "loading" || sourcePreview.status === "error" ? (
        <div
          data-testid="source-preview-live-region"
          role="status"
          aria-live="polite"
          className="flex min-h-0 flex-1 flex-col items-center justify-center gap-4 px-6 text-center"
        >
          <p className="text-sm leading-6 text-muted-foreground">
            {sourcePreview.status === "loading"
              ? "正在载入原件预览…"
              : sourcePreview.message}
          </p>
          {sourcePreview.status === "error" ? (
            <div className="flex flex-wrap justify-center gap-2">
              {sourcePreview.retryable ? (
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  className="min-h-11 min-w-11"
                  onClick={() => void loadSourcePreview(
                    sourcePreview.hasPageNumber ? sourcePreview.pageNumber : null,
                  )}
                >
                  重试
                </Button>
              ) : null}
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="min-h-11 min-w-11"
                onClick={closeSourcePreview}
              >
                关闭，继续编辑
              </Button>
            </div>
          ) : null}
        </div>
      ) : null}
      {sourcePreview.status === "ready" && sourcePreview.objectUrl ? (
        <div className="flex min-h-0 flex-1 flex-col gap-3 px-4 pb-4">
          {!sourcePreview.hasPageNumber ? (
            <p className="text-xs leading-5 text-muted-foreground">
              未能精确定位，以下为原件参考页
            </p>
          ) : null}
          <div className="min-h-0 flex-1 overflow-hidden bg-surface-raised">
            {sourcePreview.mime === "application/pdf" ? (
              <iframe
                title="原件 PDF 预览"
                sandbox=""
                src={`${sourcePreview.objectUrl}#page=${sourcePreview.pageNumber}`}
                className="h-full min-h-[24rem] w-full border-0"
              />
            ) : (
              // Browser-owned Blob URLs must bypass Next's remote image optimizer.
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={sourcePreview.objectUrl}
                alt={filename?.trim() ? `${filename.trim()} 原件预览` : "当前材料的原件预览"}
                className="h-full min-h-[24rem] w-full object-contain"
              />
            )}
          </div>
        </div>
      ) : null}
    </div>
  );

  return (
    <section
      data-testid="content-check-panel"
      aria-labelledby="content-check-title"
      className="flex h-[calc(100dvh-12rem)] min-h-96 max-h-[calc(100dvh-2rem)] w-full flex-col overflow-hidden bg-surface motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-200 motion-reduce:animate-none lg:h-[calc(100dvh-8rem)]"
    >
      <header className="shrink-0 border-b border-hairline px-4 py-3 sm:px-6">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="content-check-title" className="font-sans text-lg font-semibold text-ink">
            确认识别出的正文
          </h2>
          <p className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
            <FileText aria-hidden className="size-4" />
            {filename?.trim() ? `来源：${filename.trim()}` : "来源：粘贴文本"}
          </p>
        </div>
        <p className="mt-1 text-xs text-subtle">正文可直接修改，修改会自动保存</p>
      </header>

      {state.phase === "conflict" ? (
        <div data-testid="content-check-conflict" role="alert" className="shrink-0 border-b border-feedback-warning/40 bg-feedback-warning-soft px-4 py-3 sm:px-6">
          <p className="text-sm font-semibold text-ink">
            {state.errorMessage ?? "草稿已被其他更新抢先保存。"}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button type="button" variant="secondary" size="sm" onClick={() => void handleReloadLatest()} className="max-lg:min-h-11">
              载入最新版本（放弃我的修改）
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={() => void retryWithLatestRevision()} className="max-lg:min-h-11">
              以我的修改重试
            </Button>
          </div>
        </div>
      ) : null}

      {draft ? (
        <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-hairline px-4 py-2 sm:px-6">
          <p className="text-xs font-medium text-muted-foreground">{statusSummary}</p>
          {routineChecks.length > 0 ? (
            <TextAction
              data-testid="content-check-keep-all-plain"
              className="max-lg:min-h-11"
              onClick={() => confirmIssues(routineChecks.map(({ item }) => item.issue_id))}
            >
              确认全部普通建议
            </TextAction>
          ) : null}
        </div>
      ) : null}

      <div className={cn(
        "relative",
        "flex min-h-0 flex-1 overflow-hidden",
        isDesktop && hasRail && "grid grid-cols-[minmax(0,1fr)_21rem]",
      )}>
        {isDesktop && desktopSourceOpen && sourcePreviewIsCurrent ? (
          <aside
            data-testid="source-preview-drawer"
            aria-labelledby="source-preview-title"
            className="absolute inset-y-0 left-0 z-20 flex w-[clamp(20rem,32vw,30rem)] max-w-full flex-col border-r border-hairline bg-surface shadow-[var(--cl-shadow-3)] motion-safe:animate-in motion-safe:slide-in-from-left-4 motion-safe:duration-200 motion-reduce:animate-none"
          >
            <div className="flex shrink-0 items-center justify-between gap-3 border-b border-hairline px-4 py-3">
              <h3 id="source-preview-title" className="text-sm font-semibold text-ink">
                参考原件对比
              </h3>
              <Button
                ref={sourceCloseButtonRef}
                type="button"
                variant="ghost"
                size="sm"
                aria-label="关闭原件预览"
                className="size-11 p-0"
                onClick={closeSourcePreview}
              >
                <X aria-hidden className="size-4" />
              </Button>
            </div>
            {sourcePreviewContent}
          </aside>
        ) : null}
        <div
          data-testid="content-check-document"
          inert={sheetOpen || undefined}
          className="min-w-0 overflow-y-auto px-4 py-4 sm:px-6"
        >
          <label htmlFor="content-check-editor" className="sr-only">
            待确认正文预览与编辑
          </label>
          {draft ? (
            <div className="mx-auto flex min-h-full w-full max-w-[75ch] items-stretch">
              {markerEntries.length > 0 ? (
                <nav
                  aria-label="正文批注标记"
                  className="relative w-11 shrink-0 border-r border-hairline pr-2"
                  style={{ minHeight: markerDocumentHeight }}
                >
                  {markerEntries.map(({ item }) => (
                    <button
                      key={item.issue_id}
                      type="button"
                      data-testid="content-check-gutter-marker"
                      data-issue-id={item.issue_id}
                      aria-label={`定位批注：${guidanceForContentCheckCode(item.code).title}`}
                      onClick={() => revealIssue(item)}
                      style={{ top: markerPositions.get(item.issue_id)?.top }}
                      className="focus-ring absolute left-0 inline-flex size-11 -translate-y-1/2 items-center justify-center rounded-[var(--cl-radius-control-sm)] text-lens-blue hover:bg-surface-raised"
                    >
                      <MapPin aria-hidden className="size-4" />
                    </button>
                  ))}
                </nav>
              ) : null}
              <MarkdownTextInput
                ref={editorRef}
                key={draft.sourceDocumentId}
                id="content-check-editor"
                ariaLabelledBy="content-check-title"
                initialValue={draft.savedMarkdown}
                onChange={handleDocumentEdit}
                onSubmit={handleConfirm}
                className="min-h-[28rem] flex-1 bg-reader-stage px-5 py-6 font-sans text-base leading-[1.68] text-reader-reading-ink selection:bg-lens-blue/15 selection:text-ink sm:px-8"
              />
            </div>
          ) : null}
        </div>

        {isDesktop && hasRail ? (
          <aside data-testid="content-check-summary-rail" aria-label="审查批注" className="min-h-0 w-[21rem] overflow-y-auto border-l border-hairline bg-surface-raised px-4 py-3">
            {reviewRail}
          </aside>
        ) : null}
      </div>

      {!isDesktop && hasRail ? (
        <div className="shrink-0 border-t border-hairline bg-surface px-3 py-2 lg:hidden">
          <Sheet
            open={sheetOpen}
            onOpenChange={(open) => {
              if (open) {
                setMobileSheet(visibleMobileSheet ?? "review");
                return;
              }
              if (visibleMobileSheet === "source") {
                releaseSourcePreview();
                setSourcePreview(EMPTY_SOURCE_PREVIEW);
              }
              setMobileSheet(null);
            }}
          >
            <SheetTrigger asChild>
              <Button
                type="button"
                variant="secondary"
                aria-expanded={sheetOpen}
                className="min-h-11 w-full justify-between"
              >
                <span>
                  {sheetOpen
                    ? "收起审查批注面板"
                    : `展开审查批注面板，还有 ${attentionChecks.length} 项需要确认`}
                </span>
                <PanelRightOpen aria-hidden className="size-4" />
              </Button>
            </SheetTrigger>
            <SheetContent
              side="bottom"
              aria-modal="true"
              className="max-h-[85dvh] overflow-hidden p-0 [&>button]:size-11 sm:inset-y-0 sm:left-auto sm:right-0 sm:mt-0 sm:h-dvh sm:max-h-none sm:w-[24rem] sm:rounded-none sm:border-l sm:border-t-0"
            >
              {visibleMobileSheet === "source" ? (
                <>
                  <SheetHeader className="shrink-0 border-b border-hairline px-5 py-4 pr-14">
                    <SheetTitle className="font-sans text-lg">参考原件对比</SheetTitle>
                    <SheetDescription>原件仅供比对，不影响正文编辑与确认。</SheetDescription>
                    <Button
                      ref={mobileSourceBackRef}
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="min-h-11 w-fit"
                      onClick={() => {
                        releaseSourcePreview();
                        setSourcePreview(EMPTY_SOURCE_PREVIEW);
                        setMobileSheet("review");
                      }}
                    >
                      返回审查批注
                    </Button>
                  </SheetHeader>
                  {sourcePreviewContent}
                </>
              ) : (
                <>
                  <SheetHeader className="shrink-0 border-b border-hairline px-5 py-4 pr-14">
                    <SheetTitle className="font-sans text-lg">审查批注</SheetTitle>
                    <SheetDescription>{statusSummary}</SheetDescription>
                  </SheetHeader>
                  <div className="min-h-0 flex-1 overflow-y-auto px-5 py-3">
                    {reviewRail}
                  </div>
                </>
              )}
            </SheetContent>
          </Sheet>
        </div>
      ) : null}

      <footer className="shrink-0 border-t border-hairline px-4 py-3 sm:px-6">
        <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p data-testid="content-check-save-status" aria-live="polite" className="min-w-0 text-xs font-medium text-muted-foreground">
            {interactionMessage ??
              (state.phase === "saving"
                ? "正在保存并重新检查…"
                : state.errorMessage && state.phase !== "conflict"
                  ? state.errorMessage
                  : state.infoMessage
                    ? state.infoMessage
                    : state.dirty
                      ? "有未保存的修改…"
                      : draft
                        ? "已自动保存"
                        : "")}
          </p>
          <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
            <TextAction disabled={isBusy} className="max-lg:min-h-11" onClick={() => void handleDefer()}>
              稍后处理
            </TextAction>
            {origin === "submit" ? (
              <TextAction disabled={isBusy} className="max-lg:min-h-11" onClick={() => onBackToInput(flushEditor())}>
                重新输入
              </TextAction>
            ) : null}
            {state.errorMessage && state.dirty && state.phase === "ready" ? (
              <Button type="button" variant="secondary" size="sm" disabled={isBusy} className="max-lg:min-h-11" onClick={() => void saveNow()}>
                重试保存
              </Button>
            ) : null}
            <Button
              type="button"
              variant="primary-ink"
              size="sm"
              data-testid="content-check-confirm-button"
              disabled={
                isBusy ||
                state.dirty ||
                (isRejected && !state.dirty) ||
                attentionChecks.length > 0 ||
                !canAttemptConfirm
              }
              className="max-lg:min-h-11"
              onClick={handleConfirm}
            >
              {primaryLabel}
              {state.phase !== "confirming" ? (
                <ArrowRight aria-hidden className="ml-1 size-4" />
              ) : null}
            </Button>
          </div>
        </div>
        <p className="mt-1 text-right text-xs text-subtle">
          确认后正文冻结，将进入阅读
        </p>
      </footer>
    </section>
  );
}
