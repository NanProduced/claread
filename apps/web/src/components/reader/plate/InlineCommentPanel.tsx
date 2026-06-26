"use client";

/**
 * InlineCommentPanel — Plate CommentKit 驱动的 inline comment 面板
 *
 * 取代旧的 noteMenu 浮层 + ReaderRecordNoteComposer。
 * 通过 `usePluginOption(commentPlugin, 'activeId')` 读取 CommentKit 状态：
 * - activeId === null → 不渲染
 * - activeId === getDraftCommentKey() → 显示新建笔记 composer
 * - activeId === note.assetId → 显示已有笔记 view/edit
 *
 * CommentPluginBridge 在 <Plate> 内部渲染，通过 ref 把 CommentKit 的
 * setOption / tf.comment.setDraft / tf.comment.removeMark 暴露给父组件。
 * 父组件（ReaderRecordPlateSurface）在 <Plate> 外部通过 ref 控制 activeId。
 */
import * as React from "react";
import { useEditorPlugin, usePluginOption } from "platejs/react";
import { getDraftCommentKey } from "@platejs/comment";
import { X } from "lucide-react";

import { commentPlugin } from "@/components/editor/plugins/comment-kit";
import type { ReaderRecordPlateUserNoteMark } from "@/lib/reader-plate/projection/reader-record-plate-document";

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
}: {
  apiRef: React.MutableRefObject<CommentPluginApi | null>;
}) {
  const { setOption, tf } = useEditorPlugin(commentPlugin);

  React.useEffect(() => {
    apiRef.current = {
      setActiveId: (id) => setOption("activeId", id),
      setDraft: () => tf.comment.setDraft(),
      removeMark: () => tf.comment.removeMark(),
    };
    return () => {
      apiRef.current = null;
    };
  }, [apiRef, setOption, tf]);

  return null;
}

// --- InlineCommentPanel ---

export interface InlineCommentPanelProps {
  // Draft mode (new note) — activeId === getDraftCommentKey()
  draftText: string;
  onDraftTextChange: (value: string) => void;
  onSaveDraft: () => void;
  onCancelDraft: () => void;

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
}

function actionButtonClassName(enabled: boolean) {
  const base =
    "rounded-full border px-2.5 py-1 text-xs transition-colors focus:outline-none focus:ring-2 focus:ring-lens-blue/30";
  return enabled
    ? `${base} border-border/80 bg-background/80 text-foreground hover:border-lens-blue/40 hover:bg-lens-blue/5`
    : `${base} border-transparent bg-transparent text-muted/60`;
}

export function InlineCommentPanel(props: InlineCommentPanelProps) {
  const activeId = usePluginOption(commentPlugin, "activeId");
  const { setOption } = useEditorPlugin(commentPlugin);

  if (!activeId) return null;

  const isDraft = activeId === getDraftCommentKey();
  const isExistingNote =
    props.activeNote != null && activeId === props.activeNote.assetId;

  if (!isDraft && !isExistingNote) return null;

  const handleClose = () => {
    setOption("activeId", null);
    props.onClose();
  };

  return (
    <div
      data-testid="reader-record-inline-comment-panel"
      data-reader-record-floating-toolbar="note-menu"
      className="mt-4 rounded-lg border border-border/60 bg-background/95 p-3 shadow-md backdrop-blur-sm"
    >
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-muted">
          笔记
        </span>
        <button
          type="button"
          aria-label="关闭笔记面板"
          onClick={handleClose}
          className="rounded-md p-1 text-muted/60 transition-colors hover:bg-muted/10"
        >
          <X className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </div>

      {isDraft ? (
        <DraftComposer
          draftText={props.draftText}
          onDraftTextChange={props.onDraftTextChange}
          onSave={props.onSaveDraft}
          onCancel={props.onCancelDraft}
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
    </div>
  );
}

// --- Draft Composer (new note) ---

function DraftComposer({
  draftText,
  onDraftTextChange,
  onSave,
  onCancel,
  isSaving,
  statusMessage,
}: {
  draftText: string;
  onDraftTextChange: (value: string) => void;
  onSave: () => void;
  onCancel: () => void;
  isSaving: boolean;
  statusMessage: string | null;
}) {
  const saveDisabled = isSaving || draftText.trim().length === 0;

  return (
    <div className="space-y-2">
      <textarea
        id="reader-record-plate-note-input"
        data-testid="reader-record-plate-note-input"
        value={draftText}
        rows={3}
        placeholder="写下你对这段内容的理解、疑问或提醒..."
        maxLength={500}
        className="w-full resize-y rounded-md border border-border bg-background px-3 py-2 text-sm leading-6 text-ink outline-none focus:border-lens-blue"
        onChange={(e) => onDraftTextChange(e.currentTarget.value)}
      />
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
      <div className="space-y-2">
        <p
          data-reader-record-note-content="view"
          className="whitespace-pre-wrap break-words text-sm leading-6 text-ink"
        >
          {note.noteText}
        </p>
        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            aria-label="Ask 关于这条笔记"
            data-reader-record-note-action="ask"
            onClick={onAsk}
            className="rounded-md px-2 py-0.5 text-xs text-lens-blue transition-colors hover:bg-lens-blue/5"
          >
            Ask
          </button>
          <button
            type="button"
            aria-label="编辑笔记"
            data-reader-record-note-action="edit"
            onClick={onStartEdit}
            className="rounded-md px-2 py-0.5 text-xs text-lens-blue transition-colors hover:bg-lens-blue/5"
          >
            编辑
          </button>
          <button
            type="button"
            aria-label="删除笔记"
            data-reader-record-note-action="delete"
            onClick={onDelete}
            className="rounded-md px-2 py-0.5 text-xs text-rose-600 transition-colors hover:bg-rose-50"
          >
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
    <div className="space-y-2">
      <textarea
        aria-label="编辑笔记内容"
        data-reader-record-note-input="edit"
        value={editDraft}
        rows={3}
        className="w-full resize-y rounded-md border border-border bg-background px-2.5 py-1.5 text-sm leading-6 text-ink outline-none focus:border-lens-blue"
        onChange={(e) => onEditDraftChange(e.currentTarget.value)}
      />
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
