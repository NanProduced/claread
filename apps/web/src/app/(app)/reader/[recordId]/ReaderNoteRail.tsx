"use client";

import { MessageSquare, PencilLine, Quote, Trash2, X } from "lucide-react";

import type { WebReaderNoteVm } from "@/types/api/reader-notes";
import type { SentenceModel } from "@/types/view/ReaderMockVm";

type ReaderNoteSaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; message: string }
  | { kind: "error"; message: string };

export interface ReaderNoteRailGroup {
  sentence: SentenceModel;
  sentenceIndex: number;
  notes: WebReaderNoteVm[];
}

export interface ReaderNoteRailDraft {
  targetKey: string;
  quoteMode: WebReaderNoteVm["quoteMode"];
  selectedText: string;
}

export interface ReaderNoteRailProps {
  open: boolean;
  groups: ReaderNoteRailGroup[];
  activeNote: WebReaderNoteVm | null;
  draft: ReaderNoteRailDraft | null;
  draftText: string;
  saveState: ReaderNoteSaveState;
  onClose: () => void;
  onSelectNote: (note: WebReaderNoteVm) => void;
  onDraftTextChange: (value: string) => void;
  onSave: () => void;
  onDelete: () => void;
  onAsk?: (note: WebReaderNoteVm) => void;
}

function trimQuote(value: string, limit = 96) {
  const normalized = value.trim().replace(/\s+/g, " ");
  if (!normalized) {
    return "";
  }
  if (normalized.length <= limit) {
    return normalized;
  }
  return `${normalized.slice(0, limit)}...`;
}

function shouldHideQuote(quoteMode: WebReaderNoteVm["quoteMode"], selectedText: string, sentenceText?: string) {
  if (quoteMode !== "sentence") {
    return false;
  }
  if (!sentenceText) {
    return true;
  }
  return sentenceText.trim() === selectedText.trim();
}

function composerTitle(activeNote: WebReaderNoteVm | null, draft: ReaderNoteRailDraft | null) {
  if (activeNote) {
    return "编辑笔记";
  }
  if (draft) {
    return "新建笔记";
  }
  return "笔记";
}

export function ReaderNoteRail({
  open,
  groups,
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
}: ReaderNoteRailProps) {
  if (!open) {
    return null;
  }

  const composerQuoteMode = activeNote?.quoteMode ?? draft?.quoteMode ?? null;
  const composerSelectedText = activeNote?.selectedText ?? draft?.selectedText ?? "";
  const activeSentence = groups.find((group) =>
    group.notes.some((item) => item.id === activeNote?.id),
  )?.sentence;
  const hideComposerQuote =
    composerQuoteMode === null
      ? true
      : shouldHideQuote(composerQuoteMode, composerSelectedText, activeSentence?.text);

  return (
    <aside className="fixed inset-x-3 bottom-3 z-50 flex max-h-[78vh] flex-col overflow-hidden rounded-[1.1rem] border border-border/70 bg-background/96 shadow-2xl backdrop-blur xl:inset-y-3 xl:right-3 xl:left-auto xl:w-[24rem] xl:max-h-none">
      <div className="flex items-center justify-between gap-3 border-b border-border/70 px-4 py-3">
        <div>
          <p className="text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Reader Notes
          </p>
          <h2 className="mt-1 text-sm font-semibold text-foreground">{composerTitle(activeNote, draft)}</h2>
        </div>
        <button
          type="button"
          className="focus-ring inline-flex h-9 w-9 items-center justify-center rounded-full border border-border/70 bg-muted/45 text-muted-foreground transition-colors hover:bg-muted/72 hover:text-foreground"
          aria-label="关闭笔记栏"
          onClick={onClose}
        >
          <X aria-hidden="true" className="h-4 w-4" />
        </button>
      </div>

      <div className="border-b border-border/70 px-4 py-4">
        {composerSelectedText.trim() ? (
          hideComposerQuote ? null : (
            <div className="rounded-xl border border-amber-200/75 bg-amber-50/95 px-3 py-2 text-sm leading-6 text-amber-900 shadow-sm">
              <div className="mb-1 flex items-center gap-2 text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-amber-700">
                <Quote aria-hidden="true" className="h-3.5 w-3.5" />
                Quote
              </div>
              <p>{trimQuote(composerSelectedText)}</p>
            </div>
          )
        ) : (
          <div className="rounded-xl border border-dashed border-border/80 bg-muted/20 px-3 py-4 text-sm leading-6 text-muted-foreground">
            选中一句或一个片段后即可在这里写笔记。
          </div>
        )}

        <div className="mt-3">
          <label className="mb-2 block text-xs font-medium text-foreground">内容</label>
          <textarea
            value={draftText}
            onChange={(event) => onDraftTextChange(event.target.value)}
            placeholder="写下你对这段内容的理解、疑问或提醒。"
            maxLength={500}
            className="focus-ring min-h-28 w-full resize-y rounded-xl border border-border bg-background px-3 py-2.5 text-[0.92rem] leading-7 text-foreground outline-none transition-colors focus:border-ring"
          />
          <div className="mt-3 flex items-center justify-between gap-3">
            <span className="text-xs text-muted-foreground">
              {saveState.kind === "saved" || saveState.kind === "error"
                ? saveState.message
                : `${draftText.length}/500`}
            </span>
            <div className="flex items-center gap-2">
              {activeNote ? (
                <>
                  {onAsk ? (
                    <button
                      type="button"
                      className="focus-ring inline-flex min-h-10 items-center gap-2 rounded-full border border-border bg-background px-3 text-xs font-medium text-lens-blue transition-colors hover:border-border/80 hover:text-foreground"
                      onClick={() => onAsk(activeNote)}
                    >
                      <MessageSquare aria-hidden="true" className="h-3.5 w-3.5" />
                      Ask
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="focus-ring inline-flex min-h-10 items-center gap-2 rounded-full border border-border bg-muted/45 px-3 text-xs font-medium text-foreground transition-colors hover:border-border/80 hover:bg-muted/72"
                    onClick={onDelete}
                    disabled={saveState.kind === "saving"}
                  >
                    <Trash2 aria-hidden="true" className="h-3.5 w-3.5" />
                    删除
                  </button>
                </>
              ) : null}
              <button
                type="button"
                className="focus-ring inline-flex min-h-10 items-center gap-2 rounded-full border border-transparent bg-primary px-3 text-xs font-semibold text-primary-foreground transition-colors hover:bg-primary/92 disabled:cursor-not-allowed disabled:opacity-60"
                onClick={onSave}
                disabled={saveState.kind === "saving" || draftText.trim().length === 0 || !composerSelectedText.trim()}
              >
                <PencilLine aria-hidden="true" className="h-3.5 w-3.5" />
                {activeNote ? "保存修改" : "保存笔记"}
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {groups.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border/70 bg-muted/20 px-4 py-6 text-sm leading-6 text-muted-foreground">
            当前文章还没有笔记。
          </div>
        ) : (
          <div className="space-y-4">
            {groups.map((group) => (
              <section key={group.sentence.sentenceId} className="space-y-2">
                <div className="px-1">
                  <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                    句子 {group.sentenceIndex}
                  </p>
                  <p className="mt-1 line-clamp-2 text-sm leading-6 text-foreground/82">
                    {group.sentence.text}
                  </p>
                </div>

                <div className="space-y-2">
                  {group.notes.map((note) => {
                    const active = note.id === activeNote?.id;
                    const hideQuote = shouldHideQuote(note.quoteMode, note.selectedText, group.sentence.text);
                    return (
                      <button
                        key={note.id}
                        type="button"
                        className={`focus-ring flex w-full flex-col rounded-[1rem] border px-3 py-3 text-left transition-colors ${
                          active
                            ? "border-amber-300 bg-amber-50/95 shadow-sm"
                            : "border-border/70 bg-background hover:border-border hover:bg-muted/25"
                        }`}
                        onClick={() => onSelectNote(note)}
                      >
                        {!hideQuote ? (
                          <span className="rounded-md bg-amber-100/90 px-2 py-1 text-[0.78rem] leading-5 text-amber-950">
                            {trimQuote(note.selectedText, 84)}
                          </span>
                        ) : null}
                        <span className={`${hideQuote ? "" : "mt-2 "}whitespace-pre-wrap text-sm leading-6 text-foreground`}>
                          {note.noteText}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </section>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}
