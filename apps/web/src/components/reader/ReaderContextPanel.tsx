"use client";

import type { ReactNode } from "react";
import { Highlighter, Languages, MessageSquare, NotebookPen, Palette, Quote } from "lucide-react";

import type { UserAnnotationColorDto } from "@/types/api/annotations";
import type { SentenceModel } from "@/types/view/ReaderMockVm";
import { cn } from "@/lib/cn";

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
}: ReaderContextPanelProps) {
  if (!sentence) {
    return null;
  }

  return (
    <section
      role="dialog"
      aria-modal="false"
      className={cn(
        "w-[14.5rem] rounded-[14px] border border-border/60 bg-background/95 p-1.5 text-foreground shadow-xl backdrop-blur-md",
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
          onClick={onHighlight}
          disabled={saveState.kind === "saving"}
        />

        <MenuItem
          icon={<NotebookPen className="h-4 w-4" />}
          label="笔记"
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
            <div className="mx-2 my-1 h-px bg-border/50" />
            
            <div className="flex items-center justify-between px-2.5 py-1.5">
              <span className="flex items-center gap-2.5 text-[13px] font-medium text-muted-foreground/80">
                <Palette className="h-4 w-4" /> 颜色
              </span>
              <div className="flex items-center gap-1.5">
                {colorOptions.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    className={cn(
                      "h-4 w-4 rounded-full ring-1 ring-inset ring-border/50 transition-transform hover:scale-110",
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
    </section>
  );
}

interface MenuItemProps {
  icon: ReactNode;
  label: string;
  onClick?: () => void;
  disabled?: boolean;
}

function MenuItem({ icon, label, onClick, disabled }: MenuItemProps) {
  return (
    <button
      type="button"
      className="flex w-full items-center gap-2.5 rounded-[8px] px-2.5 py-1.5 text-left text-[13px] font-medium text-foreground transition-colors hover:bg-muted/80 disabled:pointer-events-none disabled:opacity-50"
      onClick={onClick}
      disabled={disabled}
    >
      <span className="text-muted-foreground/80">{icon}</span>
      {label}
    </button>
  );
}
