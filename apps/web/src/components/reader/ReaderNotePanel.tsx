"use client";

import type { CSSProperties } from "react";
import { useEffect, useRef, useState } from "react";
import { MoreHorizontal, Pencil, Sparkles, Trash2, X } from "lucide-react";

import type { WebReaderNoteCreateRequest, WebReaderNoteVm } from "@/types/api/reader-notes";
import type { SentenceModel } from "@/types/view/ReaderMockVm";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/primitives/dropdown-menu";
import { ReaderFloatingSurface } from "./ReaderFloatingLayer";

type ReaderNoteSaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; message: string }
  | { kind: "error"; message: string };

interface ReaderNotePanelProps {
  open: boolean;
  sentence: SentenceModel;
  sentenceIndex: number;
  notes: WebReaderNoteVm[];
  activeNote: WebReaderNoteVm | null;
  draft: WebReaderNoteCreateRequest | null;
  draftText: string;
  saveState: ReaderNoteSaveState;
  style?: CSSProperties;
  floatingRef?: (node: HTMLDivElement | null) => void;
  onClose: () => void;
  onSelectNote: (note: WebReaderNoteVm) => void;
  onDraftTextChange: (value: string) => void;
  onSave: () => void;
  onDelete?: () => void;
  onDeleteNote?: (note: WebReaderNoteVm) => void;
  onAsk?: (note: WebReaderNoteVm) => void;
}

function trimQuote(value: string, limit = 120) {
  const normalized = value.trim().replace(/\s+/g, " ");
  if (!normalized) return "";
  return normalized.length <= limit ? normalized : `${normalized.slice(0, limit)}...`;
}

function shouldHideQuote(
  quoteMode: WebReaderNoteVm["quoteMode"] | WebReaderNoteCreateRequest["quoteMode"],
  selectedText: string,
  sentenceText?: string,
) {
  if (quoteMode !== "sentence") return false;
  if (!sentenceText) return true;
  return sentenceText.trim() === selectedText.trim();
}

function NoteMenu({
  onEdit,
  onDelete,
}: {
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="relative">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className="focus-ring inline-flex h-8 w-8 items-center justify-center rounded-full text-muted-foreground opacity-0 transition-colors hover:bg-muted/40 hover:text-foreground group-hover:opacity-100 data-[state=open]:opacity-100 data-[state=open]:bg-muted/40 data-[state=open]:text-foreground"
            onClick={(event) => event.stopPropagation()}
            aria-label="更多操作"
          >
            <MoreHorizontal aria-hidden="true" className="h-4 w-4" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
          <DropdownMenuItem onClick={onEdit}>
            <Pencil aria-hidden="true" className="mr-2 h-3.5 w-3.5" />
            编辑
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={onDelete} className="text-destructive focus:bg-destructive/10 focus:text-destructive">
            <Trash2 aria-hidden="true" className="mr-2 h-3.5 w-3.5" />
            删除
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

function NoteItem({
  note,
  sentenceText,
  isActive,
  isEditing,
  draftText,
  saveState,
  onSelect,
  onEdit,
  onDelete,
  onAsk,
  onDraftTextChange,
  onSave,
  onCancelEdit,
}: {
  note: WebReaderNoteVm;
  sentenceText: string;
  isActive: boolean;
  isEditing: boolean;
  draftText: string;
  saveState: ReaderNoteSaveState;
  onSelect: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onAsk?: () => void;
  onDraftTextChange: (value: string) => void;
  onSave: () => void;
  onCancelEdit: () => void;
}) {
  const hideQuote = shouldHideQuote(note.quoteMode, note.selectedText, sentenceText);

  if (isEditing) {
    return (
      <article className="rounded-xl border border-amber-200/60 bg-white px-4 py-3 shadow-sm">
        {!hideQuote ? (
          <div className="mb-2 border-l-2 border-lens-blue/30 pl-3 reader-serif text-[0.85rem] leading-6 text-muted-foreground">
            {trimQuote(note.selectedText, 120)}
          </div>
        ) : null}
        <textarea
          value={draftText}
          onChange={(event) => onDraftTextChange(event.target.value)}
          placeholder="写下你的笔记"
          maxLength={500}
          className="focus-ring mt-3 min-h-24 w-full resize-y rounded-xl border border-border bg-background px-3 py-2.5 text-[0.92rem] leading-7 text-foreground outline-none transition-colors focus:border-ring"
        />
        <div className="mt-3 flex items-center justify-between gap-3">
          <span className="text-xs text-muted-foreground">
            {saveState.kind === "saved" || saveState.kind === "error"
              ? saveState.message
              : `${draftText.length}/500`}
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="focus-ring inline-flex min-h-9 items-center rounded-full px-3 text-sm text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
              onClick={onCancelEdit}
            >
              取消
            </button>
            <button
              type="button"
              className="focus-ring inline-flex min-h-9 items-center rounded-full bg-ink px-4 text-sm font-semibold text-white transition-colors hover:bg-ink/92 disabled:opacity-60"
              onClick={onSave}
              disabled={saveState.kind === "saving" || draftText.trim().length === 0}
            >
              保存
            </button>
          </div>
        </div>
      </article>
    );
  }

  return (
    <article
      className={`group relative rounded-xl px-4 py-3 transition-colors ${
        isActive ? "bg-surface-warm" : "hover:bg-surface-warm/60"
      }`}
      onClick={(event) => {
        // 避免点击菜单区域时触发 select
        if ((event.target as HTMLElement).closest("[data-note-menu]")) {
          return;
        }
        onSelect();
      }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          {!hideQuote ? (
            <div className="mb-2 border-l-2 border-lens-blue/30 pl-3 reader-serif text-[0.85rem] leading-6 text-muted-foreground">
              {trimQuote(note.selectedText, 120)}
            </div>
          ) : null}
          <p
            className={`mt-2 text-[0.95rem] leading-relaxed ${
              isActive ? "text-ink font-medium" : "text-ink"
            }`}
          >
            {note.noteText}
          </p>
        </div>
        <div className="shrink-0 pt-0.5 flex items-center gap-1" data-note-menu>
          {onAsk ? (
            <button
              type="button"
              className="focus-ring inline-flex h-8 w-8 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted/40 hover:text-foreground opacity-0 group-hover:opacity-100"
              onClick={(event) => {
                event.stopPropagation();
                onAsk();
              }}
              title="带入 Ask"
              aria-label="带入 Ask"
            >
              <Sparkles aria-hidden="true" className="h-4 w-4" />
            </button>
          ) : null}
          <NoteMenu onEdit={onEdit} onDelete={onDelete} />
        </div>
      </div>
    </article>
  );
}

function DraftCard({
  draft,
  sentenceText,
  draftText,
  saveState,
  onDraftTextChange,
  onSave,
}: {
  draft: WebReaderNoteCreateRequest;
  sentenceText: string;
  draftText: string;
  saveState: ReaderNoteSaveState;
  onDraftTextChange: (value: string) => void;
  onSave: () => void;
}) {
  const hideQuote = shouldHideQuote(draft.quoteMode, draft.selectedText, sentenceText);

  return (
    <article className="rounded-xl border border-amber-200/50 bg-white px-4 py-3 shadow-sm">
      {!hideQuote ? (
        <div className="mb-2 border-l-2 border-lens-blue/30 pl-3 reader-serif text-[0.85rem] leading-6 text-muted-foreground">
          {trimQuote(draft.selectedText, 120)}
        </div>
      ) : null}
      <textarea
        value={draftText}
        onChange={(event) => onDraftTextChange(event.target.value)}
        placeholder="写下你对这段内容的理解、疑问或提醒。"
        maxLength={500}
        className="focus-ring mt-3 min-h-24 w-full resize-y rounded-xl border border-border bg-background px-3 py-2.5 text-[0.92rem] leading-7 text-foreground outline-none transition-colors focus:border-ring"
      />
      <div className="mt-3 flex items-center justify-between gap-3">
        <span className="text-xs text-muted-foreground">
          {saveState.kind === "saved" || saveState.kind === "error"
            ? saveState.message
            : `${draftText.length}/500`}
        </span>
        <button
          type="button"
          className="focus-ring inline-flex min-h-9 items-center rounded-full bg-ink px-4 text-sm font-semibold text-white transition-colors hover:bg-ink/92 disabled:opacity-60"
          onClick={onSave}
          disabled={saveState.kind === "saving" || draftText.trim().length === 0}
        >
          保存笔记
        </button>
      </div>
    </article>
  );
}

function PanelBody({
  sentence,
  notes,
  activeNote,
  draft,
  draftText,
  saveState,
  onClose,
  onSelectNote,
  onDraftTextChange,
  onSave,
  onDelete,
  onDeleteNote,
  onAsk,
}: Omit<ReaderNotePanelProps, "open" | "style" | "floatingRef" | "sentenceIndex">) {
  const [editingNoteId, setEditingNoteId] = useState<string | null>(null);

  return (
    <div className="flex max-h-[min(38rem,calc(100vh-2rem))] w-[22rem] flex-col overflow-hidden rounded-[1.1rem] border border-border/75 bg-popover/98 text-popover-foreground shadow-[0_18px_44px_rgba(17,17,17,0.08)]">
      <div className="flex items-center justify-end border-b border-hairline/70 px-3 py-2">
        <button
          type="button"
          className="focus-ring inline-flex h-8 w-8 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted/40 hover:text-foreground"
          aria-label="关闭笔记面板"
          onClick={onClose}
        >
          <X aria-hidden="true" className="h-4 w-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-3">
        <div className="space-y-1">
          {notes.map((note) => (
            <NoteItem
              key={note.id}
              note={note}
              sentenceText={sentence.text}
              isActive={note.id === activeNote?.id}
              isEditing={note.id === editingNoteId}
              draftText={draftText}
              saveState={saveState}
              onSelect={() => {
                setEditingNoteId(null);
                onSelectNote(note);
              }}
              onEdit={() => {
                onSelectNote(note);
                setEditingNoteId(note.id);
              }}
              onDelete={() => {
                if (onDeleteNote) {
                  onDeleteNote(note);
                } else {
                  onSelectNote(note);
                  onDelete?.();
                }
              }}
              onAsk={onAsk ? () => onAsk(note) : undefined}
              onDraftTextChange={onDraftTextChange}
              onSave={() => {
                onSave();
                setEditingNoteId(null);
              }}
              onCancelEdit={() => setEditingNoteId(null)}
            />
          ))}
          {draft ? (
            <DraftCard
              draft={draft}
              sentenceText={sentence.text}
              draftText={draftText}
              saveState={saveState}
              onDraftTextChange={onDraftTextChange}
              onSave={onSave}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}

export function ReaderNotePanel(props: ReaderNotePanelProps) {
  if (!props.open) return null;

  return (
    <>
      <ReaderFloatingSurface
        floatingRef={props.floatingRef}
        style={props.style}
        className="hidden xl:block"
        role="dialog"
        onClick={(event) => event.stopPropagation()}
        onPointerDown={(event) => event.stopPropagation()}
      >
        <PanelBody {...props} />
      </ReaderFloatingSurface>

      <div className="fixed inset-x-3 bottom-3 z-50 xl:hidden">
        <PanelBody {...props} />
      </div>
    </>
  );
}
