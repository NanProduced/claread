"use client";

/**
 * InlineCommentPanel — Plate CommentKit 驱动的浮动 comment 面板
 *
 * 取代旧的 noteMenu 浮层 + ReaderRecordNoteComposer。
 * 通过 `usePluginOption(commentPlugin, 'activeId')` 读取 CommentKit 状态：
 * - activeId === null → 不渲染
 * - activeId === getDraftCommentKey() → 显示新建笔记 composer
 * - activeId === note.assetId → 显示已有笔记 view/edit
 *
 * 浮动定位：通过 ReaderFloatingSurface (FloatingPortal) 渲染到 body，
 * floatingRef + floatingStyles 由父组件通过 useReaderFloatingLayer 计算，
 * 锚定到当前选区 rect（draft 模式）或笔记 mark DOM（existing 模式）。
 *
 * CommentPluginBridge 在 <Plate> 内部渲染，通过 ref 把 CommentKit 的
 * setOption / tf.comment.setDraft / tf.comment.removeMark 暴露给父组件。
 * 父组件（ReaderRecordPlateSurface）在 <Plate> 外部通过 ref 控制 activeId。
 *
 * 注意：虽然 FloatingPortal 会把 DOM 移到 document.body，但 React context
 * 仍由 React tree 决定，所以 usePluginOption 仍能正确读取 editor state。
 */
import * as React from "react";
import { useEditorPlugin, usePluginOption } from "platejs/react";
import { getDraftCommentKey } from "@platejs/comment";
import { MessageSquare, Pencil, Trash2, X } from "lucide-react";

import { commentPlugin } from "@/components/editor/plugins/comment-kit";
import type { ReaderRecordPlateUserNoteMark } from "@/lib/reader-plate/projection/reader-record-plate-document";
import {
  ReaderFloatingSurface,
} from "@/components/reader/ReaderFloatingLayer";

// --- CommentPlugin Bridge ---

export interface CommentPluginApi {
  setActiveId: (id: string | null) => void;
  setDraft: () => void;
  removeMark: () => void;
}

/**
 * CommentPluginBridge — 在 <Plate> 内部渲染，把 CommentKit API 暴露给父组件。
 *
 * 父组件通过 ref 调用：
 * - `apiRef.current?.setDraft()` — 创建 draft comment mark + 设置 activeId
 * - `apiRef.current?.setActiveId(id)` — 设置 activeId（用于 existing note / 关闭）
 * - `apiRef.current?.removeMark()` — 移除 draft comment mark（取消新建）
 */
export function CommentPluginBridge({
  apiRef,
  onReadyChange,
}: {
  apiRef: React.MutableRefObject<CommentPluginApi | null>;
  onReadyChange?: (ready: boolean) => void;
}) {
  const { setOption, tf } = useEditorPlugin(commentPlugin);

  React.useEffect(() => {
    apiRef.current = {
      setActiveId: (id) => setOption("activeId", id),
      setDraft: () => tf.comment.setDraft(),
      removeMark: () => tf.comment.removeMark(),
    };
    onReadyChange?.(true);
    return () => {
      apiRef.current = null;
      onReadyChange?.(false);
    };
  }, [apiRef, onReadyChange, setOption, tf]);

  return null;
}

// --- InlineCommentPanel ---

export interface InlineCommentPanelProps {
  // Draft mode (new note) — activeId === getDraftCommentKey()
  draftText: string;
  draftQuoteText: string | null;
  onDraftTextChange: (value: string) => void;
  onSaveDraft: () => void;
  onCancelDraft: () => void;
  duplicateNote: ReaderRecordPlateUserNoteMark | null;
  duplicateAcknowledged: boolean;
  onViewDuplicateNote: () => void;
  onContinueDuplicateNote: () => void;

  // Existing note — activeId === activeNote?.assetId
  activeNote: ReaderRecordPlateUserNoteMark | null;
  noteEditMode: "view" | "edit";
  noteEditDraft: string;
  onNoteEditDraftChange: (value: string) => void;
  onStartEditNote: () => void;
  onCancelEditNote: () => void;
  onSaveNoteEdit: () => void;
  onDeleteNote: () => void;
  onAskFromNote: () => void;

  // Status
  isSaving: boolean;
  statusMessage: string | null;

  // Close
  onClose: () => void;

  // Floating layer — 父组件通过 useReaderFloatingLayer 计算
  floatingRef?: (node: HTMLDivElement | null) => void;
  floatingStyles?: React.CSSProperties;
}

function actionButtonClassName(enabled: boolean) {
  const base =
    "inline-flex items-center justify-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-lens-blue/30";
  return enabled
    ? `${base} border-border/80 bg-background/80 text-foreground hover:border-lens-blue/40 hover:bg-lens-blue/5`
    : `${base} border-transparent bg-transparent text-muted/60`;
}

function ghostActionClassName(tone: "default" | "danger" = "default") {
  return tone === "danger"
    ? "inline-flex items-center justify-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-rose-600 transition-colors hover:bg-rose-50 focus:outline-none focus:ring-2 focus:ring-rose-200"
    : "inline-flex items-center justify-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-lens-blue transition-colors hover:bg-lens-blue/5 focus:outline-none focus:ring-2 focus:ring-lens-blue/25";
}

function QuoteBlock({ text }: { text: string | null }) {
  if (!text) {
    return null;
  }
  return (
    <div
      data-reader-record-note-quote="true"
      className="border-y border-border/60 bg-muted/20 px-3 py-2.5"
    >
      <div className="mb-1 text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-muted">
        选中文本
      </div>
      <blockquote className="line-clamp-4 border-l-2 border-lens-blue/35 pl-2.5 text-[0.82rem] leading-5 text-ink-soft">
        {text}
      </blockquote>
    </div>
  );
}

export function InlineCommentPanel(props: InlineCommentPanelProps) {
  const activeId = usePluginOption(commentPlugin, "activeId");
  const { setOption } = useEditorPlugin(commentPlugin);
  const draftKey = getDraftCommentKey();

  const hasDraftState = props.draftQuoteText !== null;
  const hasExistingNoteState = props.activeNote != null;

  React.useEffect(() => {
    if (props.activeNote && activeId !== props.activeNote.assetId) {
      setOption("activeId", props.activeNote.assetId);
      return;
    }
    if (!props.activeNote && props.draftQuoteText && activeId !== draftKey) {
      setOption("activeId", draftKey);
    }
  }, [activeId, draftKey, props.activeNote, props.draftQuoteText, setOption]);

  const isDraft =
    activeId === draftKey || (hasDraftState && !hasExistingNoteState);
  const isExistingNote = hasExistingNoteState && !hasDraftState;

  if (!isDraft && !isExistingNote) return null;
  const quoteText = isDraft
    ? props.draftQuoteText
    : props.activeNote?.anchor.selectedText ?? null;

  const handleClose = () => {
    setOption("activeId", null);
    props.onClose();
  };

  return (
    <ReaderFloatingSurface
      floatingRef={props.floatingRef}
      style={props.floatingStyles}
      className="w-[23rem] max-w-[calc(100vw-2rem)] overflow-hidden rounded-lg border border-border/70 bg-background/98 shadow-[0_16px_40px_rgba(15,23,42,0.16),0_2px_6px_rgba(15,23,42,0.08)] backdrop-blur-sm"
      data-testid="reader-record-inline-comment-panel"
      data-reader-record-floating-toolbar="note-menu"
      data-reader-record-comment-mode={isDraft ? "draft" : "view"}
      data-plate-focus="true"
      onPointerDown={(event) => event.stopPropagation()}
    >
      <div className="flex items-center justify-between px-3 py-2.5">
        <div className="min-w-0">
          <div className="text-[0.72rem] font-semibold uppercase tracking-[0.12em] text-muted">
            笔记
          </div>
          <div className="mt-0.5 truncate text-xs text-muted/75">
            {isDraft ? "新建个人笔记" : "个人笔记"}
          </div>
        </div>
        <button
          type="button"
          aria-label="关闭笔记面板"
          onClick={handleClose}
          className="rounded-md p-1 text-muted/60 transition-colors hover:bg-muted/10 focus:outline-none focus:ring-2 focus:ring-lens-blue/25"
        >
          <X className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </div>
      <QuoteBlock text={quoteText} />

      {isDraft ? (
        <DraftComposer
          draftText={props.draftText}
          onDraftTextChange={props.onDraftTextChange}
          onSave={props.onSaveDraft}
          onCancel={props.onCancelDraft}
          duplicateNote={props.duplicateNote}
          duplicateAcknowledged={props.duplicateAcknowledged}
          onViewDuplicateNote={props.onViewDuplicateNote}
          onContinueDuplicateNote={props.onContinueDuplicateNote}
          isSaving={props.isSaving}
          statusMessage={props.statusMessage}
        />
      ) : isExistingNote && props.activeNote ? (
        <NoteView
          note={props.activeNote}
          mode={props.noteEditMode}
          editDraft={props.noteEditDraft}
          onEditDraftChange={props.onNoteEditDraftChange}
          onStartEdit={props.onStartEditNote}
          onCancelEdit={props.onCancelEditNote}
          onSaveEdit={props.onSaveNoteEdit}
          onDelete={props.onDeleteNote}
          onAsk={props.onAskFromNote}
          isSaving={props.isSaving}
          statusMessage={props.statusMessage}
        />
      ) : null}
    </ReaderFloatingSurface>
  );
}

// --- Draft Composer (new note) ---

function DraftComposer({
  draftText,
  onDraftTextChange,
  onSave,
  onCancel,
  duplicateNote,
  duplicateAcknowledged,
  onViewDuplicateNote,
  onContinueDuplicateNote,
  isSaving,
  statusMessage,
}: {
  draftText: string;
  onDraftTextChange: (value: string) => void;
  onSave: () => void;
  onCancel: () => void;
  duplicateNote: ReaderRecordPlateUserNoteMark | null;
  duplicateAcknowledged: boolean;
  onViewDuplicateNote: () => void;
  onContinueDuplicateNote: () => void;
  isSaving: boolean;
  statusMessage: string | null;
}) {
  const duplicateBlocked = duplicateNote !== null && !duplicateAcknowledged;
  const saveDisabled = isSaving || draftText.trim().length === 0 || duplicateBlocked;

  return (
    <div className="space-y-3 px-3 py-3">
      {duplicateNote && !duplicateAcknowledged ? (
        <div
          data-testid="reader-record-note-duplicate-warning"
          data-reader-record-note-duplicate="blocked"
          className="rounded-md border border-amber-200/80 bg-amber-50/75 p-2.5 text-xs leading-5 text-amber-950"
        >
          <div className="font-semibold">这个选区已有笔记</div>
          <p className="mt-1 line-clamp-3 text-amber-900/85">
            {duplicateNote.noteText || "已有一条个人笔记。"}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button
              type="button"
              aria-label="查看/编辑已有笔记"
              className={actionButtonClassName(true)}
              onClick={onViewDuplicateNote}
            >
              查看/编辑已有笔记
            </button>
            <button
              type="button"
              aria-label="仍新增一条"
              className={actionButtonClassName(true)}
              onClick={onContinueDuplicateNote}
            >
              仍新增一条
            </button>
          </div>
        </div>
      ) : duplicateNote && duplicateAcknowledged ? (
        <div
          data-reader-record-note-duplicate="acknowledged"
          className="rounded-md border border-border/60 bg-muted/20 px-2.5 py-2 text-xs leading-5 text-muted"
        >
          将在同一选区新增一条独立笔记。
        </div>
      ) : null}
      <div className="rounded-md border border-border/70 bg-background">
        <div className="border-b border-border/50 px-3 py-2 text-xs font-medium text-muted">
          笔记内容
        </div>
        <textarea
          id="reader-record-plate-note-input"
          data-testid="reader-record-plate-note-input"
          value={draftText}
          rows={3}
          placeholder="写下你对这段内容的理解、疑问或提醒..."
          maxLength={500}
          aria-describedby={duplicateBlocked ? "reader-record-note-duplicate-help" : undefined}
          className="min-h-[5.75rem] w-full resize-y border-0 bg-transparent px-3 py-2 text-sm leading-6 text-ink outline-none placeholder:text-muted/65 focus:ring-0"
          onChange={(e) => onDraftTextChange(e.currentTarget.value)}
        />
      </div>
      {duplicateBlocked ? (
        <p id="reader-record-note-duplicate-help" className="text-xs text-amber-800">
          先选择查看已有笔记，或确认仍新增一条后再保存。
        </p>
      ) : null}
      <div className="flex items-center justify-between">
        {statusMessage ? (
          <span className="text-xs text-muted">{statusMessage}</span>
        ) : (
          <span className="text-xs text-muted/60">{draftText.length}/500</span>
        )}
        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={isSaving}
            className={actionButtonClassName(!isSaving)}
            onPointerDown={(e) => e.preventDefault()}
            onClick={onCancel}
          >
            取消
          </button>
          <button
            type="button"
            disabled={saveDisabled}
            className={actionButtonClassName(!saveDisabled)}
            onPointerDown={(e) => e.preventDefault()}
            onClick={onSave}
          >
            {isSaving ? "保存中" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}

// --- Note View (existing note, view/edit) ---

function NoteView({
  note,
  mode,
  editDraft,
  onEditDraftChange,
  onStartEdit,
  onCancelEdit,
  onSaveEdit,
  onDelete,
  onAsk,
  isSaving,
  statusMessage,
}: {
  note: ReaderRecordPlateUserNoteMark;
  mode: "view" | "edit";
  editDraft: string;
  onEditDraftChange: (value: string) => void;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  onSaveEdit: () => void;
  onDelete: () => void;
  onAsk: () => void;
  isSaving: boolean;
  statusMessage: string | null;
}) {
  if (mode === "view") {
    return (
      <div className="space-y-3 px-3 py-3">
        <div
          data-reader-record-note-content="view"
          className="rounded-md border border-border/70 bg-background px-3 py-2.5"
        >
          <div className="mb-1 text-xs font-medium text-muted">笔记内容</div>
          <p className="whitespace-pre-wrap break-words text-sm leading-6 text-ink">
            {note.noteText}
          </p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-1.5">
          <button
            type="button"
            aria-label="Ask 关于这条笔记"
            data-reader-record-note-action="ask"
            onClick={onAsk}
            className={ghostActionClassName()}
          >
            <MessageSquare className="h-3.5 w-3.5" aria-hidden="true" />
            Ask
          </button>
          <button
            type="button"
            aria-label="编辑笔记"
            data-reader-record-note-action="edit"
            onClick={onStartEdit}
            className={ghostActionClassName()}
          >
            <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
            编辑
          </button>
          <button
            type="button"
            aria-label="删除笔记"
            data-reader-record-note-action="delete"
            onClick={onDelete}
            className={ghostActionClassName("danger")}
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
            删除
          </button>
        </div>
        {statusMessage ? (
          <span className="block text-xs text-muted">{statusMessage}</span>
        ) : null}
      </div>
    );
  }

  const saveDisabled = isSaving || editDraft.trim().length === 0;

  return (
    <div className="space-y-3 px-3 py-3">
      <div className="rounded-md border border-border/70 bg-background">
        <div className="border-b border-border/50 px-3 py-2 text-xs font-medium text-muted">
          编辑笔记
        </div>
        <textarea
          aria-label="编辑笔记内容"
          data-reader-record-note-input="edit"
          value={editDraft}
          rows={3}
          className="min-h-[5.75rem] w-full resize-y border-0 bg-transparent px-3 py-2 text-sm leading-6 text-ink outline-none focus:ring-0"
          onChange={(e) => onEditDraftChange(e.currentTarget.value)}
        />
      </div>
      <div className="flex items-center justify-end gap-2">
        <button
          type="button"
          aria-label="取消编辑笔记"
          data-reader-record-note-action="cancel-edit"
          onClick={onCancelEdit}
          className={actionButtonClassName(!isSaving)}
        >
          取消
        </button>
        <button
          type="button"
          aria-label="保存笔记"
          data-reader-record-note-action="save"
          onClick={onSaveEdit}
          disabled={saveDisabled}
          className={actionButtonClassName(!saveDisabled)}
        >
          {isSaving ? "保存中" : "保存"}
        </button>
      </div>
    </div>
  );
}
