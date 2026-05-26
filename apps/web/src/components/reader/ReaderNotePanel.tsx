"use client";

import { useState, type CSSProperties } from "react";
import { Sparkles, Trash2, X, Pencil, MoreHorizontal } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/primitives/dropdown-menu";

import type { WebReaderNoteCreateRequest, WebReaderNoteVm } from "@/types/api/reader-notes";
import type { SentenceModel } from "@/types/view/ReaderMockVm";
import { cn } from "@/lib/cn";
import { FloatingPortal } from "@floating-ui/react";
import { readerCommandControl, readerPanelItem } from "./interaction";

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

function formatSentenceIndex(index: number) {
  if (!index || Number.isNaN(index)) {
    return null;
  }
  return String(index).padStart(2, "0");
}

function composerMeta(
  sentenceText: string,
  draft: WebReaderNoteCreateRequest | null,
) {
  if (draft) {
    return {
      quoteMode: draft.quoteMode,
      quoteText: draft.selectedText,
      hideQuote: shouldHideQuote(draft.quoteMode, draft.selectedText, sentenceText),
    };
  }

  return {
    quoteMode: "sentence" as const,
    quoteText: sentenceText,
    hideQuote: true,
  };
}

function ComposerCard({
  sentenceText,
  draft,
  draftText,
  saveState,
  onDraftTextChange,
  onSave,
}: {
  sentenceText: string;
  draft: WebReaderNoteCreateRequest | null;
  draftText: string;
  saveState: ReaderNoteSaveState;
  onDraftTextChange: (value: string) => void;
  onSave: () => void;
}) {
  const meta = composerMeta(sentenceText, draft);
  const canSave = draftText.trim().length > 0 && saveState.kind !== "saving";

  return (
    <section className="overflow-hidden rounded-[1rem] border border-border/50 bg-white shadow-sm">
      {!meta.hideQuote ? (
        <div className="px-4 py-3">
          <div className="border-l-[3px] border-amber-400/80 pl-3 py-0.5 text-[0.88rem] leading-6 text-muted-foreground/90">
            {trimQuote(meta.quoteText, 140)}
          </div>
        </div>
      ) : null}

      <div className={cn("px-4 pb-3", meta.hideQuote ? "pt-4" : "pt-0")}>
        <textarea
          value={draftText}
          onChange={(event) => onDraftTextChange(event.target.value)}
          placeholder="写下你对这段内容的理解、疑问或提醒..."
          maxLength={500}
          className="min-h-[8rem] w-full resize-none bg-transparent text-[0.95rem] leading-relaxed text-foreground outline-none placeholder:text-muted-foreground/48"
        />
      </div>

      <div className="flex items-center justify-between border-t border-border/30 px-4 py-3">
        <span className="text-xs font-medium text-muted-foreground/60">
          {saveState.kind === "saved" || saveState.kind === "error"
            ? saveState.message
            : `${draftText.length}/500`}
        </span>
        <button
          type="button"
          className={cn(readerCommandControl, "min-h-[2.3rem] rounded-[0.5rem] bg-ink px-5 text-white hover:bg-ink/90")}
          onClick={onSave}
          disabled={!canSave}
        >
          保存笔记
        </button>
      </div>
    </section>
  );
}

function NoteListItem({
  note,
  sentenceText,
  active,
  isEditing,
  draftText,
  saveState,
  onSelect,
  onEdit,
  onCancelEdit,
  onDraftTextChange,
  onSave,
  onAsk,
  onDelete,
}: {
  note: WebReaderNoteVm;
  sentenceText: string;
  active: boolean;
  isEditing: boolean;
  draftText: string;
  saveState: ReaderNoteSaveState;
  onSelect: () => void;
  onEdit: () => void;
  onCancelEdit: () => void;
  onDraftTextChange: (value: string) => void;
  onSave: () => void;
  onAsk?: () => void;
  onDelete?: () => void;
}) {
  const hideQuote = shouldHideQuote(note.quoteMode, note.selectedText, sentenceText);

  if (isEditing) {
    return (
      <article className="overflow-hidden rounded-[1rem] border border-border/80 bg-background shadow-md ring-1 ring-border/50">
        {!hideQuote ? (
          <div className="px-4 py-3">
            <div className="border-l-[3px] border-amber-400/80 pl-3 py-0.5 text-[0.88rem] leading-6 text-muted-foreground/90">
              {trimQuote(note.selectedText, 140)}
            </div>
          </div>
        ) : null}

        <div className={cn("px-4 pb-3", hideQuote ? "pt-4" : "pt-0")}>
          <textarea
            value={draftText}
            onChange={(event) => onDraftTextChange(event.target.value)}
            placeholder="写下你的笔记..."
            maxLength={500}
            className="min-h-[8rem] w-full resize-none bg-transparent text-[0.95rem] leading-relaxed text-foreground outline-none placeholder:text-muted-foreground/48"
          />
        </div>

        <div className="flex items-center justify-between border-t border-border/30 px-4 py-3">
          <span className="text-xs font-medium text-muted-foreground/60">
            {saveState.kind === "saved" || saveState.kind === "error"
              ? saveState.message
              : `${draftText.length}/500`}
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              className={cn(readerCommandControl, "min-h-[2.3rem] rounded-[0.5rem] px-4 text-muted-foreground/80 hover:bg-muted/10 hover:text-foreground")}
              onClick={onCancelEdit}
            >
              取消
            </button>
            <button
              type="button"
              className={cn(readerCommandControl, "min-h-[2.3rem] rounded-[0.5rem] bg-primary px-5 text-primary-foreground hover:bg-primary/90")}
              onClick={onSave}
              disabled={saveState.kind === "saving" || draftText.trim().length === 0}
            >
              保存修改
            </button>
          </div>
        </div>
      </article>
    );
  }

  return (
    <article className={cn("group rounded-[1rem] border border-border/50 bg-background transition-all", active ? "ring-1 ring-border/50 shadow-sm" : "shadow-sm hover:shadow-md")}>
      <div className="px-4 pb-4 pt-3.5">
        <div className="mb-2 flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            {!hideQuote ? (
              <div className="border-l-[3px] border-amber-400/80 pl-3 py-0.5 text-[0.88rem] leading-6 text-muted-foreground/90">
                {trimQuote(note.selectedText, 96)}
              </div>
            ) : <div className="h-2" />}
          </div>
          <div className="-mr-1 flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100 has-[[data-state=open]]:opacity-100 md:opacity-0 max-md:opacity-100">
            {onAsk ? (
              <button
                type="button"
                className={cn(readerPanelItem, "inline-flex h-8 w-8 justify-center rounded-full p-0 text-muted-foreground/60 hover:bg-muted/40")}
                onClick={(e) => { e.stopPropagation(); onAsk(); }}
                aria-label="将这条笔记带入 Ask"
              >
                <Sparkles aria-hidden="true" className="h-4 w-4" />
              </button>
            ) : null}

            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  type="button"
                  className={cn(readerPanelItem, "inline-flex h-8 w-8 justify-center rounded-full p-0 text-muted-foreground/60 hover:bg-muted/40 data-[state=open]:bg-muted/40 data-[state=open]:text-foreground")}
                  onClick={(e) => e.stopPropagation()}
                  aria-label="更多操作"
                >
                  <MoreHorizontal aria-hidden="true" className="h-4 w-4" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="min-w-[8rem] overflow-hidden rounded-xl border border-border/40 bg-popover/95 p-1.5 shadow-lg backdrop-blur-md" onClick={(e) => e.stopPropagation()}>
                <DropdownMenuItem className="cursor-pointer rounded-lg px-2.5 py-2 text-[0.88rem] font-medium transition-colors focus:bg-muted/50" onClick={onEdit}>
                  <Pencil className="mr-2 h-3.5 w-3.5 text-muted-foreground/70" />
                  <span>编辑笔记</span>
                </DropdownMenuItem>
                {onDelete ? (
                  <DropdownMenuItem className="cursor-pointer rounded-lg px-2.5 py-2 text-[0.88rem] font-medium text-destructive transition-colors focus:bg-destructive/10 focus:text-destructive" onClick={onDelete}>
                    <Trash2 className="mr-2 h-3.5 w-3.5 opacity-80" />
                    <span>删除笔记</span>
                  </DropdownMenuItem>
                ) : null}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
        
        <div
          className="cursor-pointer"
          onClick={onSelect}
        >
          <p className="whitespace-pre-wrap text-[0.93rem] leading-relaxed text-foreground/85">
            {note.noteText}
          </p>
        </div>
      </div>
    </article>
  );
}

function PanelBody({
  sentence,
  sentenceIndex,
  notes,
  activeNote,
  draft,
  draftText,
  saveState,
  onClose,
  onSelectNote,
  onDraftTextChange,
  onSave,
  onDeleteNote,
  onAsk,
}: Omit<ReaderNotePanelProps, "open" | "style" | "floatingRef">) {
  const currentSentenceIndex = formatSentenceIndex(sentenceIndex);
  const [editingNoteId, setEditingNoteId] = useState<string | null>(null);

  return (
    <div className="flex max-h-[min(42rem,calc(100vh-2rem))] w-full xl:w-[23.5rem] flex-col overflow-hidden text-foreground animate-in slide-in-from-right-4 fade-in duration-300">
      <div className="px-1 py-4 pb-4">
        <div className="flex items-center justify-between">
          <div className="text-[0.68rem] font-semibold uppercase tracking-[0.2em] text-muted-foreground/60">
            {currentSentenceIndex ? `${currentSentenceIndex} · READER NOTES` : "READER NOTES"}
          </div>
          <button
            type="button"
            className={cn(readerPanelItem, "inline-flex h-7 w-7 justify-center rounded-full p-0 text-muted-foreground/60 hover:bg-muted/10")}
            aria-label="关闭笔记面板"
            onClick={onClose}
          >
            <X aria-hidden="true" className="h-4 w-4" />
          </button>
        </div>
      </div>

      <ScrollArea className="flex-1">
        <div className="flex flex-col gap-5 px-1 pb-6 pr-4">
          {draft ? (
            <ComposerCard
              sentenceText={sentence.text}
              draft={draft}
              draftText={draftText}
              saveState={saveState}
              onDraftTextChange={onDraftTextChange}
              onSave={onSave}
            />
          ) : null}

          {notes.length > 0 ? (
            <div className="flex flex-col gap-3">
              {notes.map((note) => (
                <NoteListItem
                  key={note.id}
                  note={note}
                  sentenceText={sentence.text}
                  active={note.id === activeNote?.id}
                  isEditing={note.id === editingNoteId}
                  draftText={draftText}
                  saveState={saveState}
                  onSelect={() => onSelectNote(note)}
                  onEdit={() => {
                    onSelectNote(note);
                    setEditingNoteId(note.id);
                  }}
                  onCancelEdit={() => setEditingNoteId(null)}
                  onDraftTextChange={onDraftTextChange}
                  onSave={() => {
                    onSave();
                    setEditingNoteId(null);
                  }}
                  onAsk={onAsk ? () => onAsk(note) : undefined}
                  onDelete={onDeleteNote ? () => onDeleteNote(note) : undefined}
                />
              ))}
            </div>
          ) : null}
        </div>
      </ScrollArea>
    </div>
  );
}

export function ReaderNotePanel(props: ReaderNotePanelProps) {
  if (!props.open) return null;

  return (
    <>
      <FloatingPortal>
        <div
          ref={props.floatingRef}
          style={props.style}
          className="hidden xl:block z-50 animate-in fade-in slide-in-from-right-2 duration-200 ease-out"
          role="dialog"
          onClick={(event) => event.stopPropagation()}
          onPointerDown={(event) => event.stopPropagation()}
        >
          <PanelBody {...props} />
        </div>
      </FloatingPortal>

      <div className="fixed inset-x-0 bottom-0 z-50 xl:hidden flex justify-center pb-4">
        <PanelBody {...props} />
      </div>
    </>
  );
}
