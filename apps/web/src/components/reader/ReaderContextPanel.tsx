"use client";

import { Highlighter, MessageSquare, NotebookPen, Palette, X } from "lucide-react";
import { useId } from "react";

import type { UserAnnotationColorDto } from "@/types/api/annotations";
import type { SentenceModel } from "@/types/view/ReaderMockVm";
import {
  ReaderToolbarActionButton,
  ReaderToolbarMenu,
  ReaderToolbarMenuContent,
  ReaderToolbarMenuLabel,
  ReaderToolbarMenuRadioGroup,
  ReaderToolbarMenuRadioItem,
  ReaderToolbarMenuSeparator,
  ReaderToolbarMenuTrigger,
  ReaderToolbarIconButton,
  ReaderToolbarRoot,
} from "./plate-ui-adapter";

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
  selectedText?: string | null;
  annotationScope?: "sentence" | "text_range";
  color: UserAnnotationColorDto;
  saveState: AnnotationSaveState;
  onColorChange: (value: UserAnnotationColorDto) => void;
  onHighlight: () => void;
  onNote: () => void;
  onAsk: () => void;
  onClose?: () => void;
}

function scopeLabel(scope: NonNullable<ReaderContextPanelProps["annotationScope"]>, hasSelectedText: boolean) {
  if (scope === "text_range" && hasSelectedText) {
    return "当前选区";
  }
  return "当前句子";
}

export function ReaderContextPanel({
  sentence,
  selectedText,
  annotationScope = "sentence",
  color,
  saveState,
  onColorChange,
  onHighlight,
  onNote,
  onAsk,
  onClose,
}: ReaderContextPanelProps) {
  const titleId = useId();
  const descriptionId = useId();
  const activeSelectedText = selectedText?.trim() ? selectedText : null;
  const previewText = activeSelectedText ?? sentence?.text ?? "";

  if (!sentence) {
    return null;
  }

  return (
    <section
      role="dialog"
      aria-modal="false"
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      className="w-[min(23rem,calc(100vw-1rem))] rounded-[1.05rem] border border-border/80 bg-popover/98 p-4 text-popover-foreground shadow-xl"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id={titleId} className="text-sm font-semibold text-foreground">
            {scopeLabel(annotationScope, Boolean(activeSelectedText))}
          </h2>
          <p id={descriptionId} className="mt-1 text-xs leading-5 text-muted-foreground">
            高亮与笔记分离。高亮直接作用于正文，笔记会进入右侧 note rail。
          </p>
        </div>
        {onClose ? (
          <button
            type="button"
            className="focus-ring inline-flex h-10 w-10 items-center justify-center rounded-md border border-border bg-background text-muted-foreground transition-colors hover:border-border/80 hover:bg-muted/55 hover:text-foreground"
            onClick={onClose}
            aria-label="关闭当前句操作"
          >
            <X aria-hidden="true" className="h-4 w-4" />
          </button>
        ) : null}
      </div>

      <div className="mt-3 rounded-xl border border-border/80 bg-background px-3 py-3">
        <p className="line-clamp-4 reader-serif text-[0.98rem] leading-7 text-foreground">{previewText}</p>
      </div>

      <ReaderToolbarRoot className="mt-3 max-w-none justify-between p-1">
        <ReaderToolbarActionButton
          className="min-h-10 flex-1 justify-center gap-2 rounded-lg border-transparent bg-primary text-primary-foreground hover:border-transparent hover:bg-primary/92 hover:text-primary-foreground"
          disabled={saveState.kind === "saving"}
          onClick={onHighlight}
        >
          <Highlighter aria-hidden="true" className="h-4 w-4" />
          高亮
        </ReaderToolbarActionButton>

        <ReaderToolbarActionButton
          className="min-h-10 flex-1 justify-center gap-2 rounded-lg"
          onClick={onNote}
        >
          <NotebookPen aria-hidden="true" className="h-4 w-4" />
          笔记
        </ReaderToolbarActionButton>

        <ReaderToolbarActionButton
          className="min-h-10 flex-1 justify-center gap-2 rounded-lg"
          onClick={onAsk}
        >
          <MessageSquare aria-hidden="true" className="h-4 w-4 text-lens-blue" />
          Ask
        </ReaderToolbarActionButton>

        <ReaderToolbarMenu>
          <ReaderToolbarMenuTrigger asChild>
            <ReaderToolbarIconButton
              aria-label="高亮颜色"
              title="高亮颜色"
              className="h-10 min-w-10 rounded-lg"
            >
              <Palette aria-hidden="true" className="h-4 w-4" />
            </ReaderToolbarIconButton>
          </ReaderToolbarMenuTrigger>
          <ReaderToolbarMenuContent align="end" className="w-48">
            <ReaderToolbarMenuLabel>高亮颜色</ReaderToolbarMenuLabel>
            <ReaderToolbarMenuSeparator />
            <ReaderToolbarMenuRadioGroup value={color}>
              {colorOptions.map((option) => (
                <ReaderToolbarMenuRadioItem
                  key={option.value}
                  value={option.value}
                  onSelect={(event) => {
                    event.preventDefault();
                    onColorChange(option.value);
                  }}
                >
                  <span
                    aria-hidden="true"
                    className={`mr-1.5 h-3.5 w-3.5 rounded-[4px] ring-1 ring-inset ring-border/60 ${option.className}`}
                  />
                  {option.label}
                </ReaderToolbarMenuRadioItem>
              ))}
            </ReaderToolbarMenuRadioGroup>
          </ReaderToolbarMenuContent>
        </ReaderToolbarMenu>
      </ReaderToolbarRoot>

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
