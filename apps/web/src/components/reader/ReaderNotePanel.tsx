"use client";

import type { CSSProperties } from "react";
import { MessageSquare, Quote, Trash2, X } from "lucide-react";

import type { WebReaderNoteCreateRequest, WebReaderNoteVm } from "@/types/api/reader-notes";
import type { SentenceModel } from "@/types/view/ReaderMockVm";
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
  onDelete: () => void;
  onAsk?: (note: WebReaderNoteVm) => void;
}

function trimQuote(value: string, limit = 90) {
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

function quoteBadgeLabel(
  quoteMode: WebReaderNoteVm["quoteMode"] | WebReaderNoteCreateRequest["quoteMode"],
) {
  if (quoteMode === "sentence") return "整句";
  if (quoteMode === "multi_text") return "跨句";
  return "片段";
}

function CommentCard({
  note,
  sentenceText,
  active,
  draftText,
  saveState,
  onSelect,
  onDraftTextChange,
  onSave,
  onDelete,
  onAsk,
}: {
  note: WebReaderNoteVm;
  sentenceText: string;
  active: boolean;
  draftText: string;
  saveState: ReaderNoteSaveState;
  onSelect: () => void;
  onDraftTextChange: (value: string) => void;
  onSave: () => void;
  onDelete: () => void;
  onAsk?: () => void;
}) {
  const hideQuote = shouldHideQuote(note.quoteMode, note.selectedText, sentenceText);

  return (
    <article
      className={`rounded-[1rem] border bg-white/96 px-4 py-3 transition-[border-color,box-shadow,background-color] ${
        active
          ? "border-[rgba(232,196,79,0.66)] shadow-[0_14px_28px_rgba(17,17,17,0.06)]"
          : "border-border/65 hover:border-border"
      }`}
    >
      <button type="button" className="w-full text-left" onClick={onSelect}>
        <div className="flex items-center gap-2">
          <span className="inline-flex h-2.5 w-2.5 rounded-full bg-[rgba(138,119,255,0.65)]" />
          <span className="text-xs font-medium text-muted-foreground">comment</span>
          <span className="text-xs text-muted-foreground">{quoteBadgeLabel(note.quoteMode)}</span>
        </div>
      </button>

      {!hideQuote ? (
        <div className="mt-2 rounded-md border-l-2 border-[rgba(240,196,59,0.95)] pl-3 text-[0.92rem] leading-7 text-foreground">
          {trimQuote(note.selectedText, 120)}
        </div>
      ) : null}

      {active ? (
        <>
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
              {onAsk ? (
                <button
                  type="button"
                  className="focus-ring inline-flex min-h-11 items-center rounded-full px-3 text-sm font-medium text-lens-blue transition-colors hover:text-foreground"
                  onClick={onAsk}
                >
                  Ask
                </button>
              ) : null}
              <button
                type="button"
                className="focus-ring inline-flex h-11 w-11 items-center justify-center rounded-full border border-border bg-background text-muted-foreground transition-colors hover:border-border/80 hover:text-foreground"
                onClick={onDelete}
                aria-label="删除当前笔记"
              >
                <Trash2 aria-hidden="true" className="h-4 w-4" />
              </button>
              <button
                type="button"
                className="focus-ring inline-flex min-h-11 items-center rounded-full bg-ink px-4 text-sm font-semibold text-white transition-colors hover:bg-ink/92 disabled:opacity-60"
                onClick={onSave}
                disabled={saveState.kind === "saving" || draftText.trim().length === 0}
              >
                保存修改
              </button>
            </div>
          </div>
        </>
      ) : (
        <button
          type="button"
          className="mt-3 w-full text-left text-[0.92rem] leading-7 text-ink-soft"
          onClick={onSelect}
        >
          {note.noteText}
        </button>
      )}
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
    <article className="rounded-[1rem] border border-[rgba(232,196,79,0.5)] bg-white/98 px-4 py-3 shadow-[0_14px_28px_rgba(17,17,17,0.05)]">
      <div className="flex items-center gap-2">
        <span className="inline-flex h-2.5 w-2.5 rounded-full bg-[rgba(138,119,255,0.65)]" />
        <span className="text-xs font-medium text-muted-foreground">draft</span>
        <span className="text-xs text-muted-foreground">{quoteBadgeLabel(draft.quoteMode)}</span>
      </div>
      {!hideQuote ? (
        <div className="mt-2 rounded-md border-l-2 border-[rgba(240,196,59,0.95)] pl-3 text-[0.92rem] leading-7 text-foreground">
          {trimQuote(draft.selectedText, 120)}
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
        <button
          type="button"
          className="focus-ring inline-flex min-h-11 items-center rounded-full bg-ink px-4 text-sm font-semibold text-white transition-colors hover:bg-ink/92 disabled:opacity-60"
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
  onDelete,
  onAsk,
}: Omit<ReaderNotePanelProps, "open" | "style" | "floatingRef">) {
  return (
    <div className="flex max-h-[min(38rem,calc(100vh-2rem))] w-[22rem] flex-col overflow-hidden rounded-[1.1rem] border border-border/75 bg-popover/98 text-popover-foreground shadow-[0_18px_44px_rgba(17,17,17,0.08)]">
      <div className="flex items-start justify-between gap-3 border-b border-border/70 px-4 py-3">
        <div className="min-w-0">
          <p className="text-[0.72rem] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
            Comments
          </p>
          <h3 className="mt-1 text-lg font-semibold text-foreground">句子 {sentenceIndex}</h3>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">{trimQuote(sentence.text, 96)}</p>
        </div>
        <button
          type="button"
          className="focus-ring inline-flex h-11 w-11 items-center justify-center rounded-full border border-border bg-background text-muted-foreground transition-colors hover:border-border/80 hover:text-foreground"
          aria-label="关闭笔记面板"
          onClick={onClose}
        >
          <X aria-hidden="true" className="h-4 w-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4">
        <div className="space-y-3">
          {notes.map((note) => (
            <CommentCard
              key={note.id}
              note={note}
              sentenceText={sentence.text}
              active={note.id === activeNote?.id}
              draftText={draftText}
              saveState={saveState}
              onSelect={() => onSelectNote(note)}
              onDraftTextChange={onDraftTextChange}
              onSave={onSave}
              onDelete={onDelete}
              onAsk={onAsk ? () => onAsk(note) : undefined}
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
