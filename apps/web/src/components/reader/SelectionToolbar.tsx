import {
  Eraser,
  Highlighter,
  MessageSquare,
  NotebookPen,
  Quote,
  Search,
} from "lucide-react";
import { forwardRef, type CSSProperties } from "react";
import { cn } from "../../lib/cn";
import {
  ReaderToolbarIconButton,
  ReaderToolbarRoot,
  ReaderToolbarSeparator,
} from "./plate-ui-adapter";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

export type SelectionToolbarAction =
  | "ask"
  | "selectSentence"
  | "highlight"
  | "note"
  | "lookup"
  | "clear";

export type SelectionToolbarColorValue = string;

export interface SelectionToolbarColorOption {
  value: SelectionToolbarColorValue;
  label: string;
  swatchClassName: string;
  disabled?: boolean;
}

export interface SelectionToolbarDisabledStates {
  ask?: boolean;
  selectSentence?: boolean;
  highlight?: boolean;
  note?: boolean;
  lookup?: boolean;
  clear?: boolean;
}

export interface SelectionToolbarProps {
  selectedText: string;
  selectionMode?: "text_range" | "sentence" | "multi_text";
  colorOptions?: SelectionToolbarColorOption[];
  activeColor?: SelectionToolbarColorValue | null;
  hasAnnotation?: boolean;
  hasHighlight?: boolean;
  hasNote?: boolean;
  disabled?: SelectionToolbarDisabledStates;
  statusMessage?: string;
  statusKind?: "saving" | "saved" | "error";
  className?: string;
  style?: CSSProperties;
  onAsk?: (selectedText: string) => void;
  onSelectSentence?: (selectedText: string) => void;
  onHighlight?: (
    color: SelectionToolbarColorValue,
    selectedText: string,
    option: SelectionToolbarColorOption,
  ) => void;
  onNote?: (selectedText: string) => void;
  onClearAnnotation?: () => void;
  onLookup?: (selectedText: string) => void;
  canToggleHighlightPalette?: boolean;
  highlightPaletteOpen?: boolean;
  onToggleHighlightPalette?: () => void;
}

export const defaultSelectionToolbarColorOptions: SelectionToolbarColorOption[] = [
  {
    value: "warm_yellow",
    label: "暖黄",
    swatchClassName: "bg-vocab-amber/75 ring-vocab-amber/25",
  },
  {
    value: "soft_blue",
    label: "雾青",
    swatchClassName: "bg-context-blue/65 ring-context-blue/25",
  },
  {
    value: "sage_green",
    label: "灰绿",
    swatchClassName: "bg-structure-green/45 ring-structure-green/25",
  },
];

export const SelectionToolbar = forwardRef<HTMLDivElement, SelectionToolbarProps>(function SelectionToolbar(
  {
    selectedText,
    selectionMode = "text_range",
    colorOptions = defaultSelectionToolbarColorOptions,
    activeColor = null,
    hasAnnotation = false,
    hasHighlight = false,
    hasNote = false,
    disabled,
    statusMessage,
    statusKind,
    className,
    style,
    onAsk,
    onSelectSentence,
    onHighlight,
    onNote,
    onClearAnnotation,
    onLookup,
    onToggleHighlightPalette,
    highlightPaletteOpen,
  },
  ref,
) {
  const hasSelection = selectedText.trim().length > 0;
  const askComingSoon = Boolean(disabled?.ask);
  const askDisabled = !hasSelection || askComingSoon || !onAsk;
  const selectSentenceDisabled =
    selectionMode !== "text_range" || !hasSelection || Boolean(disabled?.selectSentence) || !onSelectSentence;
  const highlightDisabled = !hasSelection || Boolean(disabled?.highlight) || !onHighlight;
  const noteDisabled = !hasSelection || Boolean(disabled?.note) || !onNote;
  const lookupDisabled = !hasSelection || Boolean(disabled?.lookup) || !onLookup;
  const clearDisabled = !hasAnnotation || Boolean(disabled?.clear) || !onClearAnnotation;

  const handleHighlight = () => {
    if (!hasHighlight) {
      const defaultOption =
        colorOptions.find((option) => option.value === activeColor && !option.disabled) ??
        colorOptions.find((option) => !option.disabled);
      if (defaultOption) {
        onHighlight?.(defaultOption.value, selectedText, defaultOption);
        if (!highlightPaletteOpen) {
          onToggleHighlightPalette?.();
        }
      }
    } else {
      onToggleHighlightPalette?.();
    }
  };

  return (
    <div
      ref={ref}
      style={style}
      className={cn("w-max max-w-[calc(100vw-1rem)] text-ink", className)}
    >
      <TooltipProvider delayDuration={200}>
        <div className="relative flex flex-col items-center gap-2">
          {/* Floating Color Palette */}
          {hasHighlight && highlightPaletteOpen && (
            <div className="flex items-center gap-3 rounded-[10px] border border-border/60 bg-background/95 p-2 px-3 shadow-sm backdrop-blur-sm animate-in fade-in zoom-in-95 slide-in-from-bottom-2">
              {colorOptions.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  aria-label={option.label}
                  disabled={Boolean(option.disabled)}
                  onClick={(e) => {
                    e.preventDefault();
                    onHighlight?.(option.value, selectedText, option);
                  }}
                  className={cn(
                    "h-4 w-4 rounded-[4px] ring-1 ring-inset ring-border/70 transition-all hover:scale-110",
                    option.swatchClassName,
                    activeColor === option.value ? "ring-ring ring-offset-2 ring-offset-background" : ""
                  )}
                />
              ))}
            </div>
          )}

          <ReaderToolbarRoot>
            {/* Highlight Action */}
            <Tooltip>
              <TooltipTrigger asChild>
                <ReaderToolbarIconButton
                  active={hasHighlight}
                  disabled={highlightDisabled}
                  onClick={handleHighlight}
                >
                  <Highlighter aria-hidden="true" className="h-4 w-4" />
                </ReaderToolbarIconButton>
              </TooltipTrigger>
              <TooltipContent side="top" className="text-xs">
                {hasHighlight ? "更换高亮颜色" : "高亮"}
              </TooltipContent>
            </Tooltip>

          <Tooltip>
            <TooltipTrigger asChild>
              <ReaderToolbarIconButton
                disabled={noteDisabled}
                active={hasNote}
                onClick={() => onNote?.(selectedText)}
              >
                <NotebookPen aria-hidden="true" className="h-4 w-4" />
              </ReaderToolbarIconButton>
            </TooltipTrigger>
            <TooltipContent side="top" className="text-xs">
              {hasNote ? "编辑笔记" : "新建笔记"}
            </TooltipContent>
          </Tooltip>

          <ReaderToolbarSeparator aria-hidden="true" />

          {/* Flattened Menu Items */}
          <Tooltip>
            <TooltipTrigger asChild>
              <ReaderToolbarIconButton
                disabled={selectSentenceDisabled}
                onClick={() => onSelectSentence?.(selectedText)}
              >
                <Quote aria-hidden="true" className="h-4 w-4" />
              </ReaderToolbarIconButton>
            </TooltipTrigger>
            <TooltipContent side="top" className="text-xs">扩展到整句</TooltipContent>
          </Tooltip>

          <Tooltip>
            <TooltipTrigger asChild>
              <ReaderToolbarIconButton
                disabled={lookupDisabled}
                onClick={() => onLookup?.(selectedText)}
              >
                <Search aria-hidden="true" className="h-4 w-4" />
              </ReaderToolbarIconButton>
            </TooltipTrigger>
            <TooltipContent side="top" className="text-xs">查词</TooltipContent>
          </Tooltip>

          <Tooltip>
            <TooltipTrigger asChild>
              <ReaderToolbarIconButton
                disabled={clearDisabled}
                onClick={onClearAnnotation}
              >
                <Eraser aria-hidden="true" className="h-4 w-4" />
              </ReaderToolbarIconButton>
            </TooltipTrigger>
            <TooltipContent side="top" className="text-xs">取消标注</TooltipContent>
          </Tooltip>
          
          <ReaderToolbarSeparator aria-hidden="true" />

          <Tooltip>
            <TooltipTrigger asChild>
              <ReaderToolbarIconButton
                disabled={askDisabled}
                onClick={() => onAsk?.(selectedText)}
                className="text-lens-blue/80 hover:bg-lens-blue/10 hover:text-lens-blue"
              >
                <MessageSquare aria-hidden="true" className="h-4 w-4" />
              </ReaderToolbarIconButton>
            </TooltipTrigger>
            <TooltipContent side="top" className="text-xs">
              {askComingSoon ? "Ask Claread (稍后开放)" : "Ask Claread"}
            </TooltipContent>
          </Tooltip>
        </ReaderToolbarRoot>
        </div>
      </TooltipProvider>

      {statusMessage ? (
        <div
          className={cn(
            "mt-2 rounded-md border px-3 py-2 text-xs font-medium shadow-sm",
            statusKind === "error"
              ? "border-destructive/20 bg-destructive/10 text-destructive"
              : "border-border/65 bg-background/92 text-muted-foreground",
          )}
        >
          {statusMessage}
        </div>
      ) : null}
    </div>
  );
});
