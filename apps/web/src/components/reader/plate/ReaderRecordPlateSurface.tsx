"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Plate, usePlateEditor } from "platejs/react";
import type { RenderElement, RenderLeaf } from "platejs/react";

import { firstMeaning, firstPartOfSpeech } from "@/components/reader/dictionary/contracts";
import type { DictLookupTypeDto, WebDictResult } from "@/types/api/dict";
import {
  projectReaderPlateSnapshotToReaderRecordPlateDocument,
  type ReaderRecordPlateAnchorSegmentNode,
  type ReaderRecordPlateCue,
  type ReaderRecordPlateProgress,
  type ReaderRecordPlateProgressLayer,
  type ReaderRecordPlateSeparatorLeaf,
  type ReaderRecordPlateSourceBlockNode,
  type ReaderRecordPlateTextLeaf,
  type ReaderRecordPlateTranslationBlockNode,
  type ReaderRecordPlateUnitNode,
} from "@/lib/reader-plate/projection/reader-record-plate-document";
import {
  readReaderRecordSelectionAnchorDrafts,
  type ReaderRecordSelectionAnchorBridgeResult,
} from "@/lib/reader-plate/projection/reader-record-dom-selection";
import type { ReaderPlateSnapshotDto } from "@/types/api/reader-plate";

import { Editor, EditorContainer } from "../../ui/editor";

export interface ReaderRecordPlateSurfaceProps {
  snapshot: ReaderPlateSnapshotDto;
  className?: string;
  columnClassName?: string;
  readingClassName?: string;
}

type ReaderRecordPlateElement =
  | ReaderRecordPlateUnitNode
  | ReaderRecordPlateSourceBlockNode
  | ReaderRecordPlateAnchorSegmentNode
  | ReaderRecordPlateTranslationBlockNode;

type ReaderRecordPlateLeaf =
  | ReaderRecordPlateTextLeaf
  | ReaderRecordPlateSeparatorLeaf
  | { text: string; owner?: string; sourceRole?: string };

type ReaderRecordLookupState =
  | { kind: "idle" }
  | { kind: "loading"; query: string }
  | { kind: "ready"; query: string; result: WebDictResult }
  | { kind: "error"; query: string; message: string };

type ReaderRecordCopyStatus = "idle" | "copied" | "error";

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

function layerLabel(layer: ReaderRecordPlateProgressLayer) {
  switch (layer.capability) {
    case "translation":
      return "译文";
    case "vocabulary":
      return "词汇";
    case "grammar":
      return "语法";
    default:
      return layer.capability;
  }
}

function layerToneClass(status: ReaderRecordPlateProgressLayer["status"]) {
  switch (status) {
    case "succeeded":
      return "bg-emerald-500";
    case "failed":
      return "bg-rose-500";
    case "processing":
      return "bg-amber-500";
    case "queued":
      return "bg-slate-400";
    case "action_required":
      return "bg-violet-500";
    default:
      return "bg-slate-300";
  }
}

function CompactProgress({ progress }: { progress: ReaderRecordPlateProgress }) {
  const layers = progress.layers.slice(0, 6);
  return (
    <div
      data-testid="reader-record-plate-progress"
      data-reader-record-progress="compact"
      className="mb-5 border-b border-border/70 pb-3"
    >
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <span className="rounded-full border border-border bg-background px-2.5 py-1 font-medium text-foreground">
          {overallProgressLabel(progress.overallStatus)}
        </span>
        {layers.length > 0 ? (
          <span className="text-muted">增强层 {layers.length}</span>
        ) : null}
      </div>
      {layers.length > 0 ? (
        <div
          data-testid="reader-record-plate-progress-strip"
          className="mt-2 flex h-1.5 overflow-hidden rounded-full bg-muted"
          aria-label="增强层进度"
        >
          {layers.map((layer) => (
            <span
              key={layer.id}
              data-reader-record-progress-layer={layer.capability}
              data-reader-record-progress-status={layer.status}
              className={`min-w-4 flex-1 ${layerToneClass(layer.status)}`}
              title={`${layerLabel(layer)} · ${layer.status}`}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function lookupTypeForSelection(text: string): DictLookupTypeDto {
  return /\s/.test(text.trim()) ? "phrase" : "word";
}

function actionButtonClassName(enabled: boolean) {
  return enabled
    ? "rounded-full border border-border bg-background px-2.5 py-1 text-foreground hover:bg-muted/30"
    : "rounded-full border border-border bg-muted/40 px-2.5 py-1 text-muted";
}

function SelectionActionStrip({
  copyStatus,
  lookupState,
  selection,
  onCopy,
  onLookup,
}: {
  copyStatus: ReaderRecordCopyStatus;
  lookupState: ReaderRecordLookupState;
  selection: ReaderRecordSelectionAnchorBridgeResult | null;
  onCopy: () => void;
  onLookup: () => void;
}) {
  const draft = selection?.drafts[0] ?? null;
  const singleRangeReady = Boolean(selection?.supportedSingleRange && draft);
  const copyDisabled = !singleRangeReady;
  const lookupDisabled = !singleRangeReady || lookupState.kind === "loading";
  const disabledReason = selection
    ? "Multi-segment selection is not supported yet"
    : "Select stable source text to enable this action";

  return (
    <div
      data-testid="reader-record-plate-disabled-actions"
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
      className="mb-4 flex flex-wrap items-center gap-2 text-xs"
      aria-label="Reader Record Plate actions"
    >
      <button
        type="button"
        disabled={lookupDisabled}
        data-reader-record-action="lookup"
        className={actionButtonClassName(!lookupDisabled)}
        title={lookupDisabled ? disabledReason : "Lookup selected text"}
        onPointerDown={(event) => event.preventDefault()}
        onClick={onLookup}
      >
        {lookupState.kind === "loading" ? "Looking up" : "Lookup"}
      </button>
      <button
        type="button"
        disabled={copyDisabled}
        data-reader-record-action="copy"
        className={actionButtonClassName(!copyDisabled)}
        title={copyDisabled ? disabledReason : "Copy selected text"}
        onPointerDown={(event) => event.preventDefault()}
        onClick={onCopy}
      >
        Copy
      </button>
      {copyStatus !== "idle" ? (
        <span
          data-testid="reader-record-plate-copy-status"
          className={copyStatus === "copied" ? "text-emerald-700" : "text-rose-700"}
        >
          {copyStatus === "copied" ? "Copied" : "Copy failed"}
        </span>
      ) : null}
      {[
        ["Ask", "ask"],
        ["Highlight", "highlight"],
        ["Note", "note"],
        ["Feedback", "feedback"],
      ].map(([label, action]) => (
        <button
          key={label}
          type="button"
          disabled
          data-reader-record-action={action}
          className={actionButtonClassName(false)}
          title={`${label} is coming soon in this read-only surface`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function ReaderRecordLookupPanel({
  lookupState,
  onDismiss,
}: {
  lookupState: ReaderRecordLookupState;
  onDismiss: () => void;
}) {
  if (lookupState.kind === "idle") {
    return null;
  }

  const result = lookupState.kind === "ready" ? lookupState.result : null;
  const entryResult = result?.kind === "entry" ? result : null;
  const disambiguationResult = result?.kind === "disambiguation" ? result : null;
  const notFoundResult = result?.kind === "not_found" ? result : null;
  const errorMessage =
    lookupState.kind === "error"
      ? lookupState.message
      : result?.kind === "error"
        ? result.message
        : "";
  const firstDefinition = entryResult ? firstMeaning(entryResult) : "";
  const partOfSpeech = entryResult ? firstPartOfSpeech(entryResult) : null;

  return (
    <div
      data-testid="reader-record-plate-lookup-panel"
      className="mb-5 rounded-md border border-border bg-background px-3 py-3 text-sm shadow-sm"
      role="status"
      aria-live="polite"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-muted">
            Lookup
          </div>
          <div className="mt-1 break-words reader-serif text-lg leading-tight text-ink">
            {entryResult?.entry.word ?? lookupState.query}
          </div>
          {entryResult?.entry.phonetic ? (
            <div className="mt-1 text-xs text-muted">
              {entryResult.entry.phonetic}
            </div>
          ) : null}
        </div>
        <button
          type="button"
          className="rounded-sm px-2 py-1 text-xs text-muted hover:bg-muted/50"
          aria-label="Dismiss lookup"
          onClick={onDismiss}
        >
          Close
        </button>
      </div>
      {lookupState.kind === "loading" ? (
        <p className="mt-3 text-muted">Looking up...</p>
      ) : null}
      {entryResult ? (
        <p className="mt-3 leading-6 text-ink-soft">
          {partOfSpeech ? <span className="mr-2 text-muted">{partOfSpeech}</span> : null}
          {firstDefinition || "No concise definition is available for this entry."}
        </p>
      ) : null}
      {disambiguationResult ? (
        <p className="mt-3 leading-6 text-muted">
          Multiple dictionary candidates found: {disambiguationResult.candidates.length}
        </p>
      ) : null}
      {notFoundResult ? (
        <p className="mt-3 leading-6 text-muted">No dictionary entry found.</p>
      ) : null}
      {errorMessage ? (
        <p className="mt-3 leading-6 text-rose-700">{errorMessage}</p>
      ) : null}
    </div>
  );
}

function cueLabel(cue: ReaderRecordPlateCue) {
  if (cue.type === "reader_record_grammar_cue") {
    return `语法 · ${cue.grammarPoint}`;
  }
  if (cue.type === "reader_record_user_comment_cue") {
    return cue.label;
  }
  return `结构 · ${cue.label}`;
}

function cueAnchorSegmentId(cue: ReaderRecordPlateCue) {
  if (
    cue.type === "reader_record_grammar_cue" ||
    cue.type === "reader_record_user_comment_cue"
  ) {
    return cue.anchor.anchorSegmentId;
  }
  return cue.anchorSegmentId;
}

function cueClassName(cue: ReaderRecordPlateCue) {
  if (cue.type === "reader_record_user_comment_cue") {
    return "rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 font-sans text-[0.68rem] font-medium leading-none text-amber-900";
  }
  return "rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 font-sans text-[0.68rem] font-medium leading-none text-emerald-900";
}

function CueChips({ cues }: { cues: ReaderRecordPlateCue[] }) {
  if (cues.length === 0) {
    return null;
  }
  return (
    <span
      data-reader-record-cues="inline"
      className="ml-2 inline-flex flex-wrap items-center gap-1 align-middle"
    >
      {cues.map((cue) => (
        <span
          key={cue.id}
          data-reader-record-cue-id={cue.id}
          data-reader-record-cue-type={cue.type}
          data-reader-record-user-asset-id={
            cue.type === "reader_record_user_comment_cue" ? cue.assetId : undefined
          }
          data-anchor-segment-id={cueAnchorSegmentId(cue)}
          className={cueClassName(cue)}
          title={cueLabel(cue)}
        >
          {cueLabel(cue)}
        </span>
      ))}
    </span>
  );
}

function markLabel(mark: ReaderRecordPlateTextLeaf["marks"][number]) {
  if (mark.kind === "user_highlight") {
    return "用户高亮";
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

function markClassName(mark: ReaderRecordPlateTextLeaf["marks"][number]) {
  if (mark.kind === "user_highlight") {
    return "rounded-sm bg-amber-100/80 ring-1 ring-amber-200/80";
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

function renderMarkedLeaf(
  leaf: ReaderRecordPlateTextLeaf,
  children: ReactNode,
) {
  let content = children;
  [...leaf.marks].reverse().forEach((mark) => {
    content = (
      <span
        data-reader-record-mark-id={mark.id}
        data-reader-record-mark-kind={mark.kind}
        data-reader-record-mark-owner={mark.owner}
        data-reader-record-user-asset-id={
          mark.kind === "user_highlight" ? mark.assetId : undefined
        }
        data-anchor-segment-id={mark.anchor.anchorSegmentId}
        data-selected-text={mark.anchor.selectedText}
        className={markClassName(mark)}
        title={markLabel(mark)}
      >
        {content}
      </span>
    );
  });
  return content;
}

function LayerActivity({ layers }: { layers: ReaderRecordPlateProgressLayer[] }) {
  const activeLayers = layers.filter((layer) => layer.status !== "succeeded");
  if (activeLayers.length === 0) {
    return null;
  }
  return (
    <div
      data-reader-record-layer-activity="unit"
      className="mb-2 flex flex-wrap gap-1 font-sans text-[0.68rem] text-muted"
    >
      {activeLayers.slice(0, 3).map((layer) => (
        <span
          key={layer.id}
          className="rounded-full border border-border bg-muted/30 px-2 py-0.5"
        >
          {layerLabel(layer)} · {layer.status}
        </span>
      ))}
    </div>
  );
}

function UnitElement({
  props,
  children,
}: {
  props: Parameters<RenderElement>[0];
  children: ReactNode;
}) {
  const element = props.element as unknown as ReaderRecordPlateUnitNode;
  return (
    <section
      {...props.attributes}
      data-reader-record-node="unit"
      data-unit-id={element.unitId}
      data-unit-type={element.unitType}
      className="reader-record-plate-unit py-4"
    >
      <LayerActivity layers={element.progress} />
      {children}
    </section>
  );
}

function SourceBlockElement({
  props,
  children,
  readingClassName,
}: {
  props: Parameters<RenderElement>[0];
  children: ReactNode;
  readingClassName: string;
}) {
  const element = props.element as unknown as ReaderRecordPlateSourceBlockNode;
  return (
    <div
      {...props.attributes}
      data-reader-record-node="source-block"
      data-unit-id={element.unitId}
      className={`reader-record-plate-source ${readingClassName}`.trim()}
    >
      {children}
    </div>
  );
}

function AnchorSegmentElement({
  props,
  children,
}: {
  props: Parameters<RenderElement>[0];
  children: ReactNode;
}) {
  const element = props.element as unknown as ReaderRecordPlateAnchorSegmentNode;
  return (
    <span
      {...props.attributes}
      data-reader-record-node="anchor-segment"
      data-unit-id={element.unitId}
      data-anchor-segment-id={element.anchorSegmentId}
      data-sentence-id={element.sentenceId}
      data-segment-type={element.segmentType}
      className="reader-record-plate-anchor-segment"
    >
      {children}
      <CueChips cues={element.cues} />
    </span>
  );
}

function UnitTranslationElement({
  props,
  children,
}: {
  props: Parameters<RenderElement>[0];
  children: ReactNode;
}) {
  const element = props.element as unknown as ReaderRecordPlateTranslationBlockNode;
  return (
    <aside
      {...props.attributes}
      data-reader-record-node="unit-translation"
      data-reader-record-unit-translation={element.unitId}
      data-layer-id={element.layerId}
      className="mt-3 border-l-2 border-border/80 pl-4 font-sans text-[0.92rem] leading-7 text-muted"
    >
      <span className="mr-2 text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-muted">
        本段译文
      </span>
      {children}
    </aside>
  );
}

export function ReaderRecordPlateSurface({
  snapshot,
  className = "px-5 py-8 sm:px-8 lg:px-10",
  columnClassName = "mx-auto max-w-[72ch]",
  readingClassName = "reader-serif text-ink text-[1.2rem] leading-[1.9]",
}: ReaderRecordPlateSurfaceProps) {
  const surfaceRef = useRef<HTMLElement | null>(null);
  const [activeSelection, setActiveSelection] =
    useState<ReaderRecordSelectionAnchorBridgeResult | null>(null);
  const [copyStatus, setCopyStatus] = useState<ReaderRecordCopyStatus>("idle");
  const [lookupState, setLookupState] = useState<ReaderRecordLookupState>({
    kind: "idle",
  });
  const plateDocument = useMemo(
    () => projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot),
    [snapshot],
  );
  const value = plateDocument.children;
  const editor = usePlateEditor(
    {
      value: value as never[],
    },
    [],
  );

  useEffect(() => {
    if (editor.children !== value) {
      editor.tf.setValue(value as never[]);
    }
  }, [editor, value]);

  useEffect(() => {
    function handleSelectionChange() {
      const nextSelection = readReaderRecordSelectionAnchorDrafts(
        surfaceRef.current,
        snapshot,
      );
      setActiveSelection(nextSelection);
      setCopyStatus("idle");
    }

    window.document.addEventListener("selectionchange", handleSelectionChange);
    return () => {
      window.document.removeEventListener("selectionchange", handleSelectionChange);
    };
  }, [snapshot]);

  const handleCopy = useCallback(async () => {
    const draft = activeSelection?.drafts[0];
    if (!activeSelection?.supportedSingleRange || !draft) {
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
    const draft = activeSelection?.drafts[0];
    if (!activeSelection?.supportedSingleRange || !draft) {
      return;
    }

    const query = draft.selected_text.trim();
    if (!query) {
      return;
    }

    setLookupState({ kind: "loading", query });

    try {
      const params = new URLSearchParams({
        word: query,
        type: lookupTypeForSelection(query),
        context: activeSelection.contextSentence,
        sentenceId: activeSelection.sentenceId,
      });
      const response = await fetch(`/api/web/dict/lookup?${params.toString()}`);
      const payload = (await response.json().catch(() => null)) as
        | WebDictResult
        | null;

      if (!payload) {
        setLookupState({
          kind: "error",
          query,
          message: "Dictionary lookup failed.",
        });
        return;
      }

      if (!response.ok && payload.kind !== "error") {
        setLookupState({
          kind: "error",
          query,
          message: "Dictionary lookup failed.",
        });
        return;
      }

      setLookupState({ kind: "ready", query, result: payload });
    } catch (error) {
      setLookupState({
        kind: "error",
        query,
        message: error instanceof Error ? error.message : "Dictionary lookup failed.",
      });
    }
  }, [activeSelection]);

  const renderElement = useCallback(
    (props: Parameters<RenderElement>[0]) => {
      const element = props.element as unknown as ReaderRecordPlateElement;
      switch (element.type) {
        case "reader_record_unit":
          return <UnitElement props={props}>{props.children}</UnitElement>;
        case "reader_record_source_block":
          return (
            <SourceBlockElement props={props} readingClassName={readingClassName}>
              {props.children}
            </SourceBlockElement>
          );
        case "reader_record_anchor_segment":
          return (
            <AnchorSegmentElement props={props}>
              {props.children}
            </AnchorSegmentElement>
          );
        case "reader_record_unit_translation":
          return (
            <UnitTranslationElement props={props}>
              {props.children}
            </UnitTranslationElement>
          );
        default:
          return <div {...props.attributes}>{props.children}</div>;
      }
    },
    [readingClassName],
  );

  const renderLeaf = useCallback((props: Parameters<RenderLeaf>[0]) => {
    const leaf = props.leaf as unknown as ReaderRecordPlateLeaf;
    if ("sourceRole" in leaf && leaf.sourceRole === "segment_text" && "marks" in leaf) {
      return (
        <span
          {...props.attributes}
          data-reader-record-leaf="segment_text"
          data-anchor-segment-id={leaf.anchorSegmentId}
        >
          {renderMarkedLeaf(leaf, props.children)}
        </span>
      );
    }
    if ("sourceRole" in leaf && leaf.sourceRole === "separator") {
      return (
        <span {...props.attributes} data-reader-record-leaf="separator">
          {props.children}
        </span>
      );
    }
    return <span {...props.attributes}>{props.children}</span>;
  }, []);

  return (
    <section
      ref={surfaceRef}
      data-testid="reader-record-plate-surface"
      data-reader-record-surface="plate-readonly-scaffold"
      className={className}
    >
      <div className={columnClassName}>
        <CompactProgress progress={plateDocument.progress} />
        <SelectionActionStrip
          copyStatus={copyStatus}
          lookupState={lookupState}
          selection={activeSelection}
          onCopy={handleCopy}
          onLookup={handleLookup}
        />
        <ReaderRecordLookupPanel
          lookupState={lookupState}
          onDismiss={() => setLookupState({ kind: "idle" })}
        />
        <Plate editor={editor} readOnly>
          <EditorContainer className="h-auto cursor-default overflow-visible rounded-none bg-transparent px-0 py-0 [&_.slate-selection-area]:hidden">
            <Editor
              readOnly
              disableDefaultStyles
              className="space-y-3 px-0 py-0 outline-none"
              renderElement={renderElement as never}
              renderLeaf={renderLeaf as never}
            />
          </EditorContainer>
        </Plate>
      </div>
    </section>
  );
}
