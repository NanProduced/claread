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
 * setOption / tf.comment.setDraft / draft mark 清理能力暴露给父组件。
 * 父组件（ReaderRecordPlateSurface）在 <Plate> 外部通过 ref 控制 activeId。
 *
 * 注意：虽然 FloatingPortal 会把 DOM 移到 document.body，但 React context
 * 仍由 React tree 决定，所以 usePluginOption 仍能正确读取 editor state。
 */
import * as React from "react";
import { KEYS } from "platejs";
import { useEditorPlugin, usePluginOption } from "platejs/react";
import { getCommentCount, getDraftCommentKey } from "@platejs/comment";
import { Check, MessageSquare, Pencil, Trash2, X } from "lucide-react";

import { commentPlugin } from "@/components/editor/plugins/comment-kit";
import type { ReaderRecordPlateUserNoteMark } from "@/lib/reader-plate/projection/reader-record-plate-document";
import {
  ReaderFloatingSurface,
} from "@/components/reader/ReaderFloatingLayer";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

// --- CommentPlugin Bridge ---

export interface CommentPluginApi {
  setActiveId: (id: string | null) => void;
  setDraft: () => void;
  removeMark: () => void;
  removeDraftMark: () => void;
}

/**
 * CommentPluginBridge — 在 <Plate> 内部渲染，把 CommentKit API 暴露给父组件。
 *
 * 父组件通过 ref 调用：
 * - `apiRef.current?.setDraft()` — 创建 draft comment mark + 设置 activeId
 * - `apiRef.current?.setActiveId(id)` — 设置 activeId（用于 existing note / 关闭）
 * - `apiRef.current?.removeMark()` — 移除当前 selection 下的 comment mark
 * - `apiRef.current?.removeDraftMark()` — 不依赖当前 selection，定点移除 draft comment mark
 */
export function CommentPluginBridge({
  apiRef,
  onReadyChange,
}: {
  apiRef: React.MutableRefObject<CommentPluginApi | null>;
  onReadyChange?: (ready: boolean) => void;
}) {
  const { api, editor, setOption, tf } = useEditorPlugin(commentPlugin);

  React.useEffect(() => {
    apiRef.current = {
      setActiveId: (id) => setOption("activeId", id),
      setDraft: () => tf.comment.setDraft(),
      removeMark: () => tf.comment.removeMark(),
      removeDraftMark: () => {
        const draftKey = getDraftCommentKey();
        const draftNodes = api.comment.nodes({ at: [], isDraft: true });
        if (draftNodes.length === 0) {
          setOption("activeId", null);
          setOption("hoverId", null);
          setOption("commentingBlock", null);
          return;
        }

        editor.tf.withoutNormalizing(() => {
          for (const [node] of draftNodes) {
            const hasSavedComment = getCommentCount(node) > 0;
            editor.tf.unsetNodes(
              hasSavedComment ? draftKey : [KEYS.comment, draftKey],
              {
                at: [],
                match: (candidate) => candidate === node,
              },
            );
          }
        });
        setOption("activeId", null);
        setOption("hoverId", null);
        setOption("commentingBlock", null);
      },
    };
    onReadyChange?.(true);
    return () => {
      apiRef.current = null;
      onReadyChange?.(false);
    };
  }, [api, apiRef, editor, onReadyChange, setOption, tf]);

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
    "inline-flex h-8 items-center justify-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors focus-visible:outline-none";
  return enabled
    ? `${base} border-border/80 bg-background text-foreground hover:border-lens-blue/40 hover:bg-transparent`
    : `${base} border-transparent bg-transparent text-muted-foreground/60`;
}

function ghostActionClassName(tone: "default" | "danger" = "default") {
  return tone === "danger"
    ? "grid h-8 w-8 place-items-center rounded-md text-muted-foreground transition-[color,transform] hover:bg-transparent hover:text-rose-600 active:scale-[0.96] focus-visible:outline-none focus-visible:text-rose-600"
    : "grid h-8 w-8 place-items-center rounded-md text-muted-foreground transition-[color,transform] hover:bg-transparent hover:text-foreground active:scale-[0.96] focus-visible:outline-none focus-visible:text-foreground";
}

function saveActionClassName(enabled: boolean) {
  return enabled
    ? "grid h-8 w-8 place-items-center rounded-md bg-foreground text-background transition-[background-color,transform] hover:bg-foreground/90 active:scale-[0.96] focus-visible:bg-foreground/90 focus-visible:outline-none"
    : "grid h-8 w-8 place-items-center rounded-md text-muted-foreground/40";
}

function IconTooltipButton({
  children,
  className,
  label,
  tooltip = label,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
  tooltip?: React.ReactNode;
}) {
  const button = (
    <button
      type="button"
      aria-label={label}
      className={className}
      {...props}
    >
      {children}
      <span className="sr-only">{label}</span>
    </button>
  );

  if (props.disabled) {
    return button;
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>{button}</TooltipTrigger>
      <TooltipContent side="top" className="text-xs">
        {tooltip}
      </TooltipContent>
    </Tooltip>
  );
}

function QuoteBlock({ text }: { text: string | null }) {
  if (!text) {
    return null;
  }
  return (
    <div
      data-reader-record-note-quote="true"
      className="mx-3 border-l-2 border-lens-blue/25 py-1.5 pl-3"
    >
      <blockquote className="line-clamp-2 text-[0.82rem] leading-5 text-muted-foreground">
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
    if (isDraft) {
      props.onCancelDraft();
      return;
    }
    setOption("activeId", null);
    props.onClose();
  };

  return (
    <ReaderFloatingSurface
      floatingRef={props.floatingRef}
      style={props.floatingStyles}
      className="w-[24rem] max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-border/60 bg-popover/98 text-popover-foreground shadow-lg shadow-black/5 backdrop-blur-md supports-[backdrop-filter]:bg-popover/95"
      data-testid="reader-record-inline-comment-panel"
      data-reader-record-floating-toolbar="note-menu"
      data-reader-record-comment-mode={isDraft ? "draft" : "view"}
      data-plate-focus="true"
      onPointerDown={(event) => event.stopPropagation()}
    >
      <TooltipProvider delayDuration={200}>
        <div className="flex justify-end px-3 pt-2">
          <IconTooltipButton
            label="关闭笔记面板"
            tooltip="关闭"
            onClick={handleClose}
            className="grid h-7 w-7 place-items-center rounded-md text-muted-foreground transition-[color,transform] hover:bg-transparent hover:text-foreground active:scale-[0.96] focus-visible:outline-none focus-visible:text-foreground"
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </IconTooltipButton>
        </div>
        <QuoteBlock text={quoteText} />

        {isDraft ? (
          <DraftComposer
            draftText={props.draftText}
            onDraftTextChange={props.onDraftTextChange}
            onSave={props.onSaveDraft}
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
      </TooltipProvider>
    </ReaderFloatingSurface>
  );
}

// --- Draft Composer (new note) ---

function DraftComposer({
  draftText,
  onDraftTextChange,
  onSave,
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
    <div className="space-y-3 px-3 pb-3 pt-2">
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
          className="rounded-md border border-border/60 bg-muted/20 px-2.5 py-2 text-xs leading-5 text-muted-foreground"
        >
          将在同一选区新增一条独立笔记。
        </div>
      ) : null}
      <textarea
        id="reader-record-plate-note-input"
        data-testid="reader-record-plate-note-input"
        value={draftText}
        rows={3}
        placeholder="写下你的想法..."
        maxLength={500}
        aria-describedby={duplicateBlocked ? "reader-record-note-duplicate-help" : undefined}
        className="min-h-[5.75rem] w-full resize-none border-0 bg-transparent px-0 py-2 text-sm leading-6 text-foreground outline-none placeholder:text-muted-foreground/65 focus:ring-0"
        onChange={(e) => onDraftTextChange(e.currentTarget.value)}
      />
      {duplicateBlocked ? (
        <p id="reader-record-note-duplicate-help" className="text-xs text-amber-800">
          先选择查看已有笔记，或确认仍新增一条后再保存。
        </p>
      ) : null}
      <div className="flex items-center justify-between border-t border-border/50 pt-2">
        {statusMessage ? (
          <span className="text-xs text-muted-foreground">{statusMessage}</span>
        ) : (
          <span className="text-xs text-muted-foreground/60">{draftText.length}/500</span>
        )}
        <IconTooltipButton
          label={isSaving ? "保存中" : "保存笔记"}
          tooltip={isSaving ? "保存中" : "保存"}
          disabled={saveDisabled}
          className={saveActionClassName(!saveDisabled)}
          onPointerDown={(e) => e.preventDefault()}
          onClick={onSave}
        >
          <Check className="h-3.5 w-3.5" aria-hidden="true" />
        </IconTooltipButton>
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
  const [deleteConfirming, setDeleteConfirming] = React.useState(false);

  React.useEffect(() => {
    setDeleteConfirming(false);
  }, [mode, note.assetId]);

  if (mode === "view") {
    return (
      <div className="px-3 pb-3 pt-2">
        <div data-reader-record-note-content="view" className="px-0.5">
          <p className="whitespace-pre-wrap break-words text-sm leading-6 text-foreground">
            {note.noteText}
          </p>
        </div>
        <div className="mt-3 flex items-center justify-between border-t border-border/50 pt-2">
          {deleteConfirming ? (
            <span className="text-xs font-medium text-rose-600">确认删除？</span>
          ) : statusMessage ? (
            <span className="text-xs text-muted-foreground">{statusMessage}</span>
          ) : (
            <span aria-hidden="true" />
          )}
          <div className="flex items-center gap-1">
            {deleteConfirming ? (
              <>
                <IconTooltipButton
                  label="取消删除"
                  tooltip="取消"
                  data-reader-record-note-action="cancel-delete"
                  onClick={() => setDeleteConfirming(false)}
                  className={ghostActionClassName()}
                >
                  <X className="h-3.5 w-3.5" aria-hidden="true" />
                </IconTooltipButton>
                <IconTooltipButton
                  label="确认删除笔记"
                  tooltip="确认删除"
                  data-reader-record-note-action="confirm-delete"
                  onClick={onDelete}
                  disabled={isSaving}
                  className={ghostActionClassName("danger")}
                >
                  <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                </IconTooltipButton>
              </>
            ) : (
              <>
                <IconTooltipButton
                  label="Ask 关于这条笔记"
                  tooltip="Ask"
                  data-reader-record-note-action="ask"
                  onClick={onAsk}
                  className={ghostActionClassName()}
                >
                  <MessageSquare className="h-3.5 w-3.5" aria-hidden="true" />
                </IconTooltipButton>
                <IconTooltipButton
                  label="编辑笔记"
                  tooltip="编辑"
                  data-reader-record-note-action="edit"
                  onClick={onStartEdit}
                  className={ghostActionClassName()}
                >
                  <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                </IconTooltipButton>
                <IconTooltipButton
                  label="删除笔记"
                  tooltip="删除"
                  data-reader-record-note-action="delete"
                  onClick={() => setDeleteConfirming(true)}
                  className={ghostActionClassName("danger")}
                >
                  <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                </IconTooltipButton>
              </>
            )}
          </div>
        </div>
      </div>
    );
  }

  const saveDisabled = isSaving || editDraft.trim().length === 0;

  return (
    <div className="space-y-3 px-3 pb-3 pt-2">
      <textarea
        aria-label="编辑笔记内容"
        data-reader-record-note-input="edit"
        value={editDraft}
        rows={3}
        className="min-h-[5.75rem] w-full resize-none border-0 bg-transparent px-0 py-2 text-sm leading-6 text-foreground outline-none focus:ring-0"
        onChange={(e) => onEditDraftChange(e.currentTarget.value)}
      />
      <div className="flex items-center justify-between border-t border-border/50 pt-2">
        <span className="text-xs text-muted-foreground/60">{editDraft.length}/500</span>
        <div className="flex items-center gap-1">
          <IconTooltipButton
            label="取消编辑笔记"
            tooltip="取消"
            data-reader-record-note-action="cancel-edit"
            onClick={onCancelEdit}
            className={ghostActionClassName()}
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </IconTooltipButton>
          <IconTooltipButton
            label={isSaving ? "保存中" : "保存笔记"}
            tooltip={isSaving ? "保存中" : "保存"}
            data-reader-record-note-action="save"
            onClick={onSaveEdit}
            disabled={saveDisabled}
            className={saveActionClassName(!saveDisabled)}
          >
            <Check className="h-3.5 w-3.5" aria-hidden="true" />
          </IconTooltipButton>
        </div>
      </div>
    </div>
  );
}
