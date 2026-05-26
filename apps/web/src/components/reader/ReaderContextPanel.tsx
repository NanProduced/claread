"use client";

import type { ReactNode } from "react";
import { Highlighter, Languages, MessageSquare, NotebookPen, Palette, Quote } from "lucide-react";

import type { UserAnnotationColorDto } from "@/types/api/annotations";
import type { SentenceModel } from "@/types/view/ReaderMockVm";
import { cn } from "@/lib/cn";
import { readerInlineFocusRing, readerPanelItem, readerTransitionFast } from "./interaction";
import { Kbd } from "@/components/primitives";

export type AnnotationSaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; message: string }
  | { kind: "error"; message: string };

const colorOptions: Array<{ value: UserAnnotationColorDto; label: string; className: string }> = [
  { value: "warm_yellow", label: "暖黄", className: "bg-vocab-amber/60" },
  { value: "soft_blue", label: "雾青", className: "bg-context-blue/55" },
  { value: "sage_green", label: "灰绿", className: "bg-structure-green/35" },
];

export interface ReaderContextPanelProps {
  sentence: SentenceModel | null;
  translationText?: string | null;
  className?: string;
  color: UserAnnotationColorDto;
  saveState: AnnotationSaveState;
  onColorChange: (value: UserAnnotationColorDto) => void;
  onSelectSentence?: () => void;
  onHighlight: () => void;
  onNote: () => void;
  onAsk: () => void;
  onAskTranslation?: () => void;
  onClose?: () => void;
  hasHighlight?: boolean;
}

export function ReaderContextPanel({
  sentence,
  translationText,
  className,
  color,
  saveState,
  hasHighlight,
  onColorChange,
  onSelectSentence,
  onHighlight,
  onNote,
  onAsk,
  onAskTranslation,
  onClose,
}: ReaderContextPanelProps) {
  if (!sentence) {
    return null;
  }

  return (
    <section
      role="dialog"
      aria-modal="false"
      className={cn(
        "w-[14rem] rounded-xl border border-hairline/80 bg-surface-warm/95 p-1.5 text-ink shadow-[0_14px_44px_rgba(28,24,18,0.11)] backdrop-blur-md",
        className
      )}
    >
      <div className="flex flex-col gap-0.5">
        {onSelectSentence ? (
          <MenuItem
            icon={<Quote className="h-4 w-4" />}
            label="整句选区"
            onClick={onSelectSentence}
          />
        ) : null}

        <MenuItem
          icon={<Highlighter className="h-4 w-4" />}
          label="高亮"
          shortcut="H"
          onClick={onHighlight}
          disabled={saveState.kind === "saving"}
        />

        <MenuItem
          icon={<NotebookPen className="h-4 w-4" />}
          label="笔记"
          shortcut="E"
          onClick={onNote}
        />

        <MenuItem
          icon={<MessageSquare className="h-4 w-4 text-lens-blue" />}
          label="原句 Ask"
          onClick={onAsk}
        />

        {translationText?.trim() && onAskTranslation ? (
          <MenuItem
            icon={<Languages className="h-4 w-4" />}
            label="译文 Ask"
            onClick={onAskTranslation}
          />
        ) : null}

        {hasHighlight ? (
          <>
            <div className="mx-2 my-1.5 h-px bg-hairline/70" />
            
            <div className="flex items-center justify-between px-2.5 py-1.5">
              <span className="flex items-center gap-2.5 text-[11px] font-bold tracking-[0.02em] text-muted/80">
                <Palette className="h-3.5 w-3.5" /> 颜色
              </span>
              <div className="flex items-center gap-1.5">
                {colorOptions.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    className={cn(
                      "h-4 w-4 rounded-full ring-1 ring-inset ring-hairline hover:ring-2 hover:ring-lens-blue/20",
                      readerInlineFocusRing,
                      readerTransitionFast,
                      "hover:scale-110",
                      option.className,
                      color === option.value ? "ring-2 ring-ring ring-offset-1 ring-offset-background" : ""
                    )}
                    onClick={() => onColorChange(option.value)}
                    title={option.label}
                  />
                ))}
              </div>
            </div>
          </>
        ) : null}
      </div>

      {saveState.kind === "saved" || saveState.kind === "error" ? (
        <div
          className={`mt-3 rounded-lg border px-3 py-2 text-xs ${
            saveState.kind === "error"
              ? "border-destructive/20 bg-destructive/10 text-destructive"
              : "border-border/65 bg-background/85 text-muted-foreground"
          }`}
        >
          {saveState.message}
        </div>
      ) : null}

      {onClose ? (
        <div className="mt-2.5 border-t border-hairline/70 px-2.5 pt-2.5 text-[0.68rem] text-muted/80">
          <span className="inline-flex items-center gap-2">
            <Kbd className="text-[0.62rem] min-h-4 min-w-4 px-1">Esc</Kbd>
            <span>关闭</span>
          </span>
        </div>
      ) : null}
    </section>
  );
}

interface MenuItemProps {
  icon: ReactNode;
  label: string;
  shortcut?: string;
  onClick?: () => void;
  disabled?: boolean;
}

function MenuItem({ icon, label, shortcut, onClick, disabled }: MenuItemProps) {
  return (
    <button
      type="button"
      className={cn(
        "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[0.82rem] font-medium text-ink transition-colors",
        "disabled:pointer-events-none disabled:opacity-40",
        "hover:bg-lens-blue-soft/50 dark:hover:bg-zinc-800/80 hover:shadow-[inset_0_0_0_1px_rgba(21,92,255,0.06)]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lens-blue/20"
      )}
      onClick={onClick}
      disabled={disabled}
    >
      <span className="text-muted/80 size-4 flex items-center justify-center shrink-0">{icon}</span>
      <span>{label}</span>
      {shortcut ? <Kbd className="ml-auto text-[0.62rem] min-h-4 min-w-4 px-1">{shortcut}</Kbd> : null}
    </button>
  );
}
