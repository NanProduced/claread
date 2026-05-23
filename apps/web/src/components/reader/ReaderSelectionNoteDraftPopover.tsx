"use client";

import { MessageSquare, PencilLine, Quote, X } from "lucide-react";
import type { CSSProperties } from "react";

import type { WebReaderNoteCreateRequest } from "@/types/api/reader-notes";
import { ReaderFloatingSurface } from "./ReaderFloatingLayer";

type ReaderNoteSaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; message: string }
  | { kind: "error"; message: string };

interface ReaderSelectionNoteDraftPopoverProps {
  draft: WebReaderNoteCreateRequest | null;
  draftText: string;
  saveState: ReaderNoteSaveState;
  style?: CSSProperties;
  floatingRef?: (node: HTMLDivElement | null) => void;
  onDraftTextChange: (value: string) => void;
  onSave: () => void;
  onClose: () => void;
}



function quoteBadgeLabel(quoteMode: WebReaderNoteCreateRequest["quoteMode"]) {
  if (quoteMode === "sentence") {
    return "整句";
  }
  if (quoteMode === "multi_text") {
    return "跨句";
  }
  return "片段";
}

export function ReaderSelectionNoteDraftPopover({
  draft,
  draftText,
  floatingRef,
  saveState,
  style,
  onClose,
  onDraftTextChange,
  onSave,
}: ReaderSelectionNoteDraftPopoverProps) {
  if (!draft) {
    return null;
  }

  return (
    <ReaderFloatingSurface
      floatingRef={floatingRef}
      style={style}
      role="dialog"
      aria-modal="false"
      className="w-[min(23rem,calc(100vw-1rem))] rounded-[1.05rem] border border-border/80 bg-popover/98 p-4 text-popover-foreground shadow-xl"
      onClick={(event) => event.stopPropagation()}
      onPointerDown={(event) => event.stopPropagation()}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          onClose();
        }
      }}
      data-selection-note-input="true"
    >
      <div className="flex items-center justify-between pb-3">
        <span className="rounded-md bg-muted/40 px-2 py-0.5 text-[0.68rem] font-medium text-muted-foreground">
          {quoteBadgeLabel(draft.quoteMode)}
        </span>
        <button
          type="button"
          className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
          aria-label="关闭笔记草稿"
          onClick={onClose}
        >
          <X aria-hidden="true" className="h-4 w-4" />
        </button>
      </div>
      <div className="space-y-3">
        <label className="block text-xs font-medium text-foreground">
          内容
        </label>
        <textarea
          value={draftText}
          onChange={(event) => onDraftTextChange(event.target.value)}
          placeholder="写下你对这段内容的理解、疑问或提醒。"
          maxLength={500}
          data-selection-note-input="true"
          className="focus-ring min-h-28 w-full resize-y rounded-xl border border-border bg-background px-3 py-2.5 text-[0.92rem] leading-7 text-foreground outline-none transition-colors focus:border-ring"
        />
        <div className="flex items-center justify-between gap-3">
          <span className="text-xs text-muted-foreground">
            {saveState.kind === "saved" || saveState.kind === "error"
              ? saveState.message
              : `${draftText.length}/500`}
          </span>
          <button
            type="button"
            className="focus-ring inline-flex min-h-11 items-center gap-2 rounded-full border border-transparent bg-primary px-4 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/92 disabled:cursor-not-allowed disabled:opacity-60"
            onClick={onSave}
            disabled={saveState.kind === "saving" || draftText.trim().length === 0}
          >
            <PencilLine aria-hidden="true" className="h-3.5 w-3.5" />
            保存笔记
          </button>
        </div>
      </div>
    </ReaderFloatingSurface>
  );
}
