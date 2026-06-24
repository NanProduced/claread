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
  type ReaderRecordPlateMark,
  type ReaderRecordPlateProgress,
  type ReaderRecordPlateProgressLayer,
  type ReaderRecordPlateSeparatorLeaf,
  type ReaderRecordPlateSourceBlockNode,
  type ReaderRecordPlateTextLeaf,
  type ReaderRecordPlateTranslationBlockNode,
  type ReaderRecordPlateUnitNode,
  type ReaderRecordPlateVocabularyMark,
} from "@/lib/reader-plate/projection/reader-record-plate-document";
import {
  readReaderRecordSelectionAnchorDrafts,
  type ReaderRecordSelectionAnchorBridgeResult,
} from "@/lib/reader-plate/projection/reader-record-dom-selection";
import type { ReaderRecordAnchorDraft } from "@/lib/reader-plate/projection/reader-record-anchor-draft";
import type { ReaderPlateSnapshotDto } from "@/types/api/reader-plate";

import { Editor, EditorContainer } from "../../ui/editor";

export interface ReaderRecordPlateSurfaceProps {
  snapshot: ReaderPlateSnapshotDto;
  className?: string;
  columnClassName?: string;
  readingClassName?: string;
  onRequestSnapshotReload?: () => void | Promise<void>;
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

type ReaderRecordWriteAction = "highlight" | "note";

type ReaderRecordWriteState =
  | { kind: "idle" }
  | { kind: "saving"; action: ReaderRecordWriteAction }
  | { kind: "saved"; action: ReaderRecordWriteAction; message: string }
  | { kind: "error"; action: ReaderRecordWriteAction; message: string };

type ReaderRecordActiveAnchor =
  | { source: "mark"; mark: ReaderRecordPlateMark; marks: ReaderRecordPlateMark[] }
  | { source: "cue"; cue: ReaderRecordPlateCue };

type ReaderRecordActiveAnchorState = {
  snapshotKey: string;
  activeAnchor: ReaderRecordActiveAnchor;
};

const ACTIVE_ANCHOR_INSPECTOR_ID =
  "reader-record-plate-active-anchor-inspector";

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

function CompactProgress({
  progress,
  title,
}: {
  progress: ReaderRecordPlateProgress;
  title?: string;
}) {
  const layers = progress.layers.slice(0, 6);
  const statusLabel = overallProgressLabel(progress.overallStatus);
  const layerCountLabel = layers.length > 0 ? `增强层 ${layers.length}` : "暂无增强层";
  return (
    <header
      data-testid="reader-record-plate-progress"
      data-reader-record-progress="compact"
      data-reader-record-reading-header="compact"
      className="mb-6 border-b border-border/60 pb-3"
      role="status"
      aria-label={`阅读状态：${statusLabel}，${layerCountLabel}`}
    >
      {title ? (
        <h1
          data-reader-record-reading-title
          className="reader-serif mb-2 text-xl leading-tight text-ink sm:text-2xl"
        >
          {title}
        </h1>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
        <span className="inline-flex items-center gap-2">
          <span className="h-1.5 w-1.5 rounded-full bg-lens-blue" />
          <span
            data-reader-record-progress-status={progress.overallStatus}
            className="font-medium text-foreground"
          >
            {statusLabel}
          </span>
        </span>
        {layers.length > 0 ? (
          <span className="text-muted">{layerCountLabel}</span>
        ) : null}
      </div>
      {layers.length > 0 ? (
        <div
          data-testid="reader-record-plate-progress-strip"
          className="mt-2 flex h-1 overflow-hidden rounded-full bg-muted"
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
    </header>
  );
}

function lookupTypeForSelection(text: string): DictLookupTypeDto {
  return /\s/.test(text.trim()) ? "phrase" : "word";
}

function singleRangeDraft(
  selection: ReaderRecordSelectionAnchorBridgeResult | null,
): ReaderRecordAnchorDraft | null {
  return selection?.supportedSingleRange ? (selection.drafts[0] ?? null) : null;
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

function SelectionActionStrip({
  copyStatus,
  lookupState,
  selection,
  writeState,
  noteComposerOpen,
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
      className="mb-5 flex flex-wrap items-center gap-2 border-b border-border/50 pb-3 text-xs text-muted"
      aria-label="Reader Record Plate 操作"
    >
      <span
        data-reader-record-action-hint
        className={singleRangeReady ? "mr-1 font-medium text-foreground" : "mr-1"}
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
          <span
            data-reader-record-coming-soon-actions="ask-feedback"
            className="text-muted/70"
          >
            Ask / 反馈 即将推出
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
            查词
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
          aria-label="关闭查词"
          onClick={onDismiss}
        >
          关闭
        </button>
      </div>
      {lookupState.kind === "loading" ? (
        <p className="mt-3 text-muted">查询中...</p>
      ) : null}
      {entryResult ? (
        <p className="mt-3 leading-6 text-ink-soft">
          {partOfSpeech ? <span className="mr-2 text-muted">{partOfSpeech}</span> : null}
          {firstDefinition || "该词条暂无简明释义。"}
        </p>
      ) : null}
      {disambiguationResult ? (
        <p className="mt-3 leading-6 text-muted">
          发现多个词典候选：{disambiguationResult.candidates.length}
        </p>
      ) : null}
      {notFoundResult ? (
        <p className="mt-3 leading-6 text-muted">未找到词典条目。</p>
      ) : null}
      {errorMessage ? (
        <p className="mt-3 leading-6 text-rose-700">{errorMessage}</p>
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
      className="mb-5 rounded-md border border-border bg-background px-3 py-3 text-sm shadow-sm"
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

function activeAnchorSegmentId(activeAnchor: ReaderRecordActiveAnchor) {
  if (activeAnchor.source === "mark") {
    return activeAnchor.mark.anchor.anchorSegmentId;
  }
  return cueAnchorSegmentId(activeAnchor.cue);
}

function activeAnchorSelectedText(activeAnchor: ReaderRecordActiveAnchor) {
  if (activeAnchor.source === "mark") {
    return activeAnchor.mark.anchor.selectedText;
  }
  if (activeAnchor.cue.type === "reader_record_sentence_analysis_cue") {
    return activeAnchor.cue.selectedText;
  }
  return activeAnchor.cue.anchor.selectedText;
}

function activeMarkIsCurrent(
  activeAnchor: ReaderRecordActiveAnchor | null,
  mark: ReaderRecordPlateMark,
) {
  return (
    activeAnchor?.source === "mark" &&
    activeAnchor.marks.some((activeMark) => activeMark.id === mark.id)
  );
}

function activeCueIsCurrent(
  activeAnchor: ReaderRecordActiveAnchor | null,
  cue: ReaderRecordPlateCue,
) {
  return activeAnchor?.source === "cue" && activeAnchor.cue.id === cue.id;
}

function isVocabularyMark(
  mark: ReaderRecordPlateMark,
): mark is ReaderRecordPlateVocabularyMark {
  return mark.kind !== "grammar_note" && mark.kind !== "user_highlight";
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

function VocabularyAnchorDetails({
  mark,
}: {
  mark: ReaderRecordPlateVocabularyMark;
}) {
  const vocabulary = mark.vocabulary;
  const gloss =
    vocabulary.itemType === "vocab_highlight"
      ? vocabulary.briefExplanation
      : vocabulary.gloss;
  const reason =
    vocabulary.itemType === "vocab_highlight" ||
    vocabulary.itemType === "context_gloss"
      ? vocabulary.reason
      : null;
  const example =
    vocabulary.itemType === "phrase_gloss" ? vocabulary.example : null;

  return (
    <>
      <div className="text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-muted">
        词汇
      </div>
      <h3 className="mt-1 reader-serif text-xl leading-tight text-ink">
        {vocabularyTitle(mark)}
      </h3>
      {gloss ? <p className="mt-3 leading-6 text-ink-soft">{gloss}</p> : null}
      {example ? (
        <p className="mt-2 leading-6 text-muted">例句：{example}</p>
      ) : null}
      {reason ? (
        <p className="mt-2 leading-6 text-muted">原因：{reason}</p>
      ) : null}
    </>
  );
}

function GrammarAnchorDetails({
  grammarPoint,
  note,
  pattern,
}: {
  grammarPoint: string;
  note: string;
  pattern?: string | null;
}) {
  return (
    <>
      <div className="text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-muted">
        语法
      </div>
      <h3 className="mt-1 reader-serif text-xl leading-tight text-ink">
        {grammarPoint}
      </h3>
      {pattern ? <p className="mt-3 leading-6 text-muted">{pattern}</p> : null}
      <p className="mt-2 leading-6 text-ink-soft">{note}</p>
    </>
  );
}

function SentenceAnalysisAnchorDetails({
  cue,
}: {
  cue: Extract<ReaderRecordPlateCue, { type: "reader_record_sentence_analysis_cue" }>;
}) {
  return (
    <>
      <div className="text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-muted">
        句子结构
      </div>
      <h3 className="mt-1 reader-serif text-xl leading-tight text-ink">
        {cue.label}
      </h3>
      <p className="mt-3 leading-6 text-ink-soft">{cue.analysis}</p>
      {cue.chunks.length > 0 ? (
        <ul className="mt-3 space-y-2 text-sm leading-6 text-ink-soft">
          {cue.chunks.slice(0, 5).map((chunk) => (
            <li
              key={`${chunk.order}:${chunk.label}:${chunk.text}`}
              className="border-l border-border pl-3"
            >
              <span className="mr-2 font-medium text-foreground">
                {chunk.order}. {chunk.label}
              </span>
              {chunk.text}
            </li>
          ))}
        </ul>
      ) : null}
    </>
  );
}

function UserHighlightAnchorDetails({
  mark,
}: {
  mark: Extract<ReaderRecordPlateMark, { kind: "user_highlight" }>;
}) {
  return (
    <>
      <div className="text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-muted">
        用户高亮
      </div>
      <h3 className="mt-1 reader-serif text-xl leading-tight text-ink">
        用户高亮
      </h3>
      <p className="mt-3 leading-6 text-ink-soft">{mark.anchor.selectedText}</p>
      <p className="mt-2 text-xs text-muted">资产 {mark.assetId}</p>
    </>
  );
}

function UserCommentAnchorDetails({
  cue,
}: {
  cue: Extract<ReaderRecordPlateCue, { type: "reader_record_user_comment_cue" }>;
}) {
  return (
    <>
      <div className="text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-muted">
        笔记/评论
      </div>
      <h3 className="mt-1 reader-serif text-xl leading-tight text-ink">
        笔记/评论
      </h3>
      {cue.noteText ? (
        <p className="mt-3 leading-6 text-ink-soft">{cue.noteText}</p>
      ) : (
        <p className="mt-3 leading-6 text-muted">该笔记暂未提供正文。</p>
      )}
      <p className="mt-2 text-xs text-muted">资产 {cue.assetId}</p>
    </>
  );
}

function MarkAnchorDetails({ mark }: { mark: ReaderRecordPlateMark }) {
  if (mark.kind === "user_highlight") {
    return <UserHighlightAnchorDetails mark={mark} />;
  }
  if (mark.kind === "grammar_note") {
    return (
      <GrammarAnchorDetails
        grammarPoint={mark.grammarPoint}
        note={mark.note}
        pattern={mark.pattern}
      />
    );
  }
  if (isVocabularyMark(mark)) {
    return <VocabularyAnchorDetails mark={mark} />;
  }
  return null;
}

function ActiveAnchorDetails({
  activeAnchor,
}: {
  activeAnchor: ReaderRecordActiveAnchor;
}) {
  if (activeAnchor.source === "mark") {
    return (
      <div
        data-reader-record-active-mark-stack-size={activeAnchor.marks.length}
        className="space-y-4"
      >
        {activeAnchor.marks.length > 1 ? (
          <p
            data-reader-record-active-mark-stack="true"
            className="text-xs text-muted"
          >
            {activeAnchor.marks.length} 处重叠标注
          </p>
        ) : null}
        {activeAnchor.marks.map((mark) => (
          <section
            key={mark.id}
            data-reader-record-active-stack-mark-id={mark.id}
            data-reader-record-active-stack-mark-kind={mark.kind}
            className={
              activeAnchor.marks.length > 1
                ? "border-b border-border/60 pb-4 last:border-b-0 last:pb-0"
                : undefined
            }
          >
            <MarkAnchorDetails mark={mark} />
          </section>
        ))}
      </div>
    );
  }

  const cue = activeAnchor.cue;
  if (cue.type === "reader_record_grammar_cue") {
    return (
      <GrammarAnchorDetails
        grammarPoint={cue.grammarPoint}
        note={cue.note}
        pattern={cue.pattern}
      />
    );
  }
  if (cue.type === "reader_record_sentence_analysis_cue") {
    return <SentenceAnalysisAnchorDetails cue={cue} />;
  }
  return <UserCommentAnchorDetails cue={cue} />;
}

function ActiveAnchorInspector({
  activeAnchor,
  onClose,
}: {
  activeAnchor: ReaderRecordActiveAnchor | null;
  onClose: () => void;
}) {
  if (!activeAnchor) {
    return null;
  }

  const anchorSegmentId = activeAnchorSegmentId(activeAnchor);
  const selectedText = activeAnchorSelectedText(activeAnchor);

  return (
    <aside
      id={ACTIVE_ANCHOR_INSPECTOR_ID}
      data-testid="reader-record-active-anchor-inspector"
      data-reader-record-active-source={activeAnchor.source}
      data-reader-record-active-anchor-segment-id={anchorSegmentId}
      data-reader-record-active-selected-text={selectedText}
      className="mb-5 rounded-md border border-border bg-background px-3 py-3 text-sm shadow-sm"
      role="region"
      aria-live="polite"
      aria-label="锚点详情"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <ActiveAnchorDetails activeAnchor={activeAnchor} />
          <p className="mt-3 text-xs text-muted">
            锚点 {anchorSegmentId} · {selectedText}
          </p>
        </div>
        <button
          type="button"
          className="rounded-sm px-2 py-1 text-xs text-muted hover:bg-muted/50 focus:outline-none focus:ring-2 focus:ring-lens-blue/30"
          aria-label="关闭锚点详情"
          onClick={onClose}
        >
          关闭
        </button>
      </div>
    </aside>
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

function cueMarkerLabel(cue: ReaderRecordPlateCue) {
  if (cue.type === "reader_record_user_comment_cue") {
    return "笔记";
  }
  if (cue.type === "reader_record_grammar_cue") {
    return "G";
  }
  return "S";
}

function cueMarkerClassName(cue: ReaderRecordPlateCue) {
  if (cue.type === "reader_record_user_comment_cue") {
    return "border-amber-200 bg-amber-50 text-amber-900";
  }
  if (cue.type === "reader_record_grammar_cue") {
    return "border-emerald-200 bg-emerald-50 text-emerald-800";
  }
  return "border-sky-200 bg-sky-50 text-sky-800";
}

function CueMarkers({
  activeAnchor,
  cues,
  onActivateCue,
}: {
  activeAnchor: ReaderRecordActiveAnchor | null;
  cues: ReaderRecordPlateCue[];
  onActivateCue: (cue: ReaderRecordPlateCue) => void;
}) {
  if (cues.length === 0) {
    return null;
  }
  const summary = cues.map(cueLabel).join("；");
  return (
    <span
      data-reader-record-cues="inline"
      data-reader-record-cue-display="marker"
      className="group relative ml-1 inline-flex items-center gap-0.5 align-super font-sans"
      aria-label={`阅读提示：${summary}`}
    >
      {cues.map((cue) => {
        const active = activeCueIsCurrent(activeAnchor, cue);
        return (
          <button
            key={cue.id}
            type="button"
            data-reader-record-cue-id={cue.id}
            data-reader-record-cue-type={cue.type}
            data-reader-record-cue-active={active ? "true" : undefined}
            data-reader-record-user-asset-id={
              cue.type === "reader_record_user_comment_cue" ? cue.assetId : undefined
            }
            data-anchor-segment-id={cueAnchorSegmentId(cue)}
            className={`inline-flex min-h-4 min-w-4 items-center justify-center rounded-full border px-1 text-[0.58rem] font-semibold leading-none opacity-70 transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-lens-blue/30 ${
              active ? "opacity-100 ring-2 ring-lens-blue/30" : ""
            } ${cueMarkerClassName(cue)}`}
            aria-controls={ACTIVE_ANCHOR_INSPECTOR_ID}
            aria-expanded={active ? "true" : "false"}
            aria-label={cueLabel(cue)}
            title={cueLabel(cue)}
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              onActivateCue(cue);
            }}
            onFocus={(event) => {
              event.stopPropagation();
              onActivateCue(cue);
            }}
          >
            {cueMarkerLabel(cue)}
          </button>
        );
      })}
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

function markStackClassName(marks: ReaderRecordPlateMark[]) {
  return sortedMarkStack(marks).map(markClassName).join(" ");
}

function renderMarkedLeaf(
  leaf: ReaderRecordPlateTextLeaf,
  children: ReactNode,
  activeAnchor: ReaderRecordActiveAnchor | null,
  onActivateMarkStack: (marks: ReaderRecordPlateMark[]) => void,
) {
  if (leaf.marks.length === 0) {
    return children;
  }

  const markStack = sortedMarkStack(leaf.marks);
  const primaryMark = markStack[0];
  const active = markStack.some((mark) => activeMarkIsCurrent(activeAnchor, mark));
  const userAssetIds = markStack
    .filter((mark) => mark.kind === "user_highlight")
    .map((mark) => mark.assetId);

  return (
    <span
      role="button"
      tabIndex={0}
      data-reader-record-mark-entry="stack"
      data-reader-record-mark-id={primaryMark.id}
      data-reader-record-mark-ids={markStack.map((mark) => mark.id).join(" ")}
      data-reader-record-mark-kind={primaryMark.kind}
      data-reader-record-mark-kinds={markStack.map((mark) => mark.kind).join(" ")}
      data-reader-record-mark-owner={
        markStack.every((mark) => mark.owner === primaryMark.owner)
          ? primaryMark.owner
          : "mixed"
      }
      data-reader-record-mark-active={active ? "true" : undefined}
      data-reader-record-mark-stack-size={String(markStack.length)}
      data-reader-record-user-asset-id={userAssetIds[0] ?? undefined}
      data-reader-record-user-asset-ids={
        userAssetIds.length > 0 ? userAssetIds.join(" ") : undefined
      }
      data-anchor-segment-id={primaryMark.anchor.anchorSegmentId}
      data-selected-text={primaryMark.anchor.selectedText}
      className={`${markStackClassName(markStack)} cursor-pointer focus:outline-none focus:ring-2 focus:ring-lens-blue/30 ${
        active ? "ring-2 ring-lens-blue/30" : ""
      }`}
      aria-controls={ACTIVE_ANCHOR_INSPECTOR_ID}
      aria-expanded={active ? "true" : "false"}
      aria-label={markStackLabel(markStack)}
      title={markStackLabel(markStack)}
      onClick={(event) => {
        event.stopPropagation();
        onActivateMarkStack(markStack);
      }}
      onFocus={(event) => {
        event.stopPropagation();
        onActivateMarkStack(markStack);
      }}
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== " ") {
          return;
        }
        event.preventDefault();
        event.stopPropagation();
        onActivateMarkStack(markStack);
      }}
    >
      {children}
    </span>
  );
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
  activeAnchor,
  props,
  children,
  onActivateCue,
}: {
  activeAnchor: ReaderRecordActiveAnchor | null;
  props: Parameters<RenderElement>[0];
  children: ReactNode;
  onActivateCue: (cue: ReaderRecordPlateCue) => void;
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
      <CueMarkers
        activeAnchor={activeAnchor}
        cues={element.cues}
        onActivateCue={onActivateCue}
      />
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
      data-reader-record-translation-display="supporting-paragraph"
      data-layer-id={element.layerId}
      className="mt-2 border-l border-border/70 pl-3 font-sans text-[0.95rem] leading-7 text-ink-soft"
    >
      <span className="mr-2 text-[0.72rem] font-medium text-muted">
        译文
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
  const [activeAnchorState, setActiveAnchorState] =
    useState<ReaderRecordActiveAnchorState | null>(null);
  const plateDocument = useMemo(
    () => projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot),
    [snapshot],
  );
  const snapshotKey = `${plateDocument.record.generation}:${plateDocument.snapshot.lastEventSequence}:${plateDocument.snapshot.snapshotId}`;
  const activeAnchor =
    activeAnchorState?.snapshotKey === snapshotKey
      ? activeAnchorState.activeAnchor
      : null;
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
      setWriteState((current) => (current.kind === "saving" ? current : { kind: "idle" }));
    }

    window.document.addEventListener("selectionchange", handleSelectionChange);
    return () => {
      window.document.removeEventListener("selectionchange", handleSelectionChange);
    };
  }, [snapshot]);

  useEffect(() => {
    if (!activeAnchor) {
      return;
    }

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setActiveAnchorState(null);
      }
    };

    window.document.addEventListener("keydown", handleEscape);
    return () => {
      window.document.removeEventListener("keydown", handleEscape);
    };
  }, [activeAnchor]);

  const handleActivateMarkStack = useCallback(
    (marks: ReaderRecordPlateMark[]) => {
      const markStack = sortedMarkStack(marks);
      const primaryMark = markStack[0];
      if (!primaryMark) {
        return;
      }
      setActiveAnchorState({
        snapshotKey,
        activeAnchor: { source: "mark", mark: primaryMark, marks: markStack },
      });
    },
    [snapshotKey],
  );

  const handleActivateCue = useCallback(
    (cue: ReaderRecordPlateCue) => {
      setActiveAnchorState({
        snapshotKey,
        activeAnchor: { source: "cue", cue },
      });
    },
    [snapshotKey],
  );

  const handleCloseActiveAnchor = useCallback(() => {
    setActiveAnchorState(null);
  }, []);

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

    setLookupState({ kind: "loading", query });

    try {
      const params = new URLSearchParams({
        word: query,
        type: lookupTypeForSelection(query),
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
          message: "词典查询失败。",
        });
        return;
      }

      if (!response.ok && payload.kind !== "error") {
        setLookupState({
          kind: "error",
          query,
          message: "词典查询失败。",
        });
        return;
      }

      setLookupState({ kind: "ready", query, result: payload });
    } catch (error) {
      console.warn("[ReaderRecordPlateSurface] dictionary lookup failed", error);
      setLookupState({
        kind: "error",
        query,
        message: "词典查询失败，请稍后重试。",
      });
    }
  }, [activeSelection]);

  const handleHighlight = useCallback(async () => {
    const draft = singleRangeDraft(activeSelection);
    if (!draft || writeState.kind === "saving") {
      return;
    }

    setWriteState({ kind: "saving", action: "highlight" });

    try {
      await postReadingRecordUserAsset("/api/web/reading-record/highlights", {
        anchor: draft,
        selectedText: draft.selected_text,
        color: "soft_green",
      });
      setWriteState({
        kind: "saved",
        action: "highlight",
        message: "高亮已保存",
      });
      await onRequestSnapshotReload?.();
    } catch (error) {
      console.warn("[ReaderRecordPlateSurface] highlight save failed", error);
      setWriteState({
        kind: "error",
        action: "highlight",
        message: "高亮保存失败，请稍后重试。",
      });
    }
  }, [activeSelection, onRequestSnapshotReload, writeState.kind]);

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
      setWriteState({
        kind: "error",
        action: "note",
        message: "笔记保存失败，请稍后重试。",
      });
    }
  }, [noteAnchorDraft, noteDraft, onRequestSnapshotReload, writeState.kind]);

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
            <AnchorSegmentElement
              activeAnchor={activeAnchor}
              props={props}
              onActivateCue={handleActivateCue}
            >
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
    [activeAnchor, handleActivateCue, readingClassName],
  );

  const renderLeaf = useCallback(
    (props: Parameters<RenderLeaf>[0]) => {
      const leaf = props.leaf as unknown as ReaderRecordPlateLeaf;
      if (
        "sourceRole" in leaf &&
        leaf.sourceRole === "segment_text" &&
        "marks" in leaf
      ) {
        return (
          <span
            {...props.attributes}
            data-reader-record-leaf="segment_text"
            data-anchor-segment-id={leaf.anchorSegmentId}
          >
            {renderMarkedLeaf(
              leaf,
              props.children,
              activeAnchor,
              handleActivateMarkStack,
            )}
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
    },
    [activeAnchor, handleActivateMarkStack],
  );

  return (
    <section
      ref={surfaceRef}
      data-testid="reader-record-plate-surface"
      data-reader-record-surface="plate-readonly-reading"
      className={className}
    >
      <div className={columnClassName}>
        <CompactProgress
          progress={plateDocument.progress}
          title={plateDocument.record.title}
        />
        <SelectionActionStrip
          copyStatus={copyStatus}
          lookupState={lookupState}
          selection={activeSelection}
          writeState={writeState}
          noteComposerOpen={noteAnchorDraft !== null}
          onCopy={handleCopy}
          onHighlight={handleHighlight}
          onLookup={handleLookup}
          onOpenNoteComposer={handleOpenNoteComposer}
        />
        <ReaderRecordLookupPanel
          lookupState={lookupState}
          onDismiss={() => setLookupState({ kind: "idle" })}
        />
        {noteAnchorDraft ? (
          <ReaderRecordNoteComposer
            noteDraft={noteDraft}
            saving={writeState.kind === "saving" && writeState.action === "note"}
            onCancel={handleCancelNote}
            onChange={setNoteDraft}
            onSave={handleSaveNote}
          />
        ) : null}
        <ActiveAnchorInspector
          activeAnchor={activeAnchor}
          onClose={handleCloseActiveAnchor}
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
