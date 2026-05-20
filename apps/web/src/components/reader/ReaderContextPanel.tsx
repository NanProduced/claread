"use client";

import { MessageSquare, Palette, Sparkles, X } from "lucide-react";
import { useEffect, useState } from "react";
import type { UserAnnotationColorDto, WebAnnotationVm } from "@/types/api/annotations";
import type { WebFavoriteTargetVm } from "@/types/api/favorites";
import type { SentenceModel } from "@/types/view/ReaderMockVm";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../ui/dropdown-menu";

export type AnnotationSaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; message: string }
  | { kind: "error"; message: string };

const colorOptions: Array<{ value: UserAnnotationColorDto; label: string; className: string }> = [
  { value: "warm_yellow", label: "暖黄", className: "bg-vocab-amber/55" },
  { value: "soft_green", label: "绿", className: "bg-structure-green/50" },
  { value: "soft_blue", label: "蓝", className: "bg-context-blue/45" },
  { value: "soft_purple", label: "紫", className: "bg-phrase-lavender/70" },
  { value: "sage_green", label: "鼠尾草", className: "bg-structure-green/30" },
];

function annotationSummaryLabel(item: WebAnnotationVm) {
  const scopeLabel = item.anchorType === "text_range" ? "选区" : item.anchorType === "paragraph" ? "段落" : "整句";
  if (item.note && item.type === "highlight") {
    return `${scopeLabel}高亮 + 笔记`;
  }
  if (item.note) {
    return `${scopeLabel}笔记`;
  }
  return `${scopeLabel}高亮`;
}

export interface ReaderContextPanelProps {
  sentence: SentenceModel | null;
  selectedText?: string | null;
  annotationScope?: "sentence" | "text_range";
  note: string;
  color: UserAnnotationColorDto;
  saveState: AnnotationSaveState;
  sentenceAnnotations: WebAnnotationVm[];
  sentenceFavorites?: WebFavoriteTargetVm[];
  onNoteChange: (value: string) => void;
  onColorChange: (value: UserAnnotationColorDto) => void;
  onSaveAnnotation: (useNote: boolean) => void;
  onAsk: () => void;
  onAnnotationJump?: (annotation: WebAnnotationVm) => void;
  onAnnotationAsk?: (annotation: WebAnnotationVm) => void;
  onFavoriteJump?: (favorite: WebFavoriteTargetVm) => void;
  onFavoriteAsk?: (favorite: WebFavoriteTargetVm) => void;
  onClose?: () => void;
}

export function ReaderContextPanel({
  sentence,
  selectedText,
  annotationScope = "sentence",
  note,
  color,
  saveState,
  sentenceAnnotations,
  sentenceFavorites = [],
  onNoteChange,
  onColorChange,
  onSaveAnnotation,
  onAsk,
  onAnnotationAsk,
  onAnnotationJump,
  onFavoriteAsk,
  onFavoriteJump,
  onClose,
}: ReaderContextPanelProps) {
  const [noteOpen, setNoteOpen] = useState(false);
  const [savedAssetsOpen, setSavedAssetsOpen] = useState(false);
  const activeSelectedText = selectedText?.trim() ? selectedText : null;
  const previewText = activeSelectedText ?? sentence?.text ?? "";
  const isTextRange = annotationScope === "text_range" && Boolean(activeSelectedText);
  const hasSavedAssets = sentenceAnnotations.length > 0 || sentenceFavorites.length > 0;

  useEffect(() => {
    if (note.trim().length > 0) {
      setNoteOpen(true);
    }
  }, [note]);

  useEffect(() => {
    if (!hasSavedAssets) {
      setSavedAssetsOpen(false);
    }
  }, [hasSavedAssets]);

  if (!sentence) {
    return (
      <section className="w-[min(22rem,calc(100vw-1rem))] rounded-xl border border-border/80 bg-popover/98 p-4 text-popover-foreground shadow-md">
        <p className="text-sm leading-6 text-muted-foreground">通过句侧句柄打开当前句操作，或直接选中文本进入选区工具条。</p>
      </section>
    );
  }

  return (
    <section className="w-[min(24rem,calc(100vw-1rem))] rounded-xl border border-border/80 bg-popover/98 p-4 text-popover-foreground shadow-md">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-foreground">{isTextRange ? "当前选区" : "当前句"}</h2>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">句级动作保持轻量，正文仍是主舞台。</p>
        </div>
        {onClose ? (
          <button
            type="button"
            className="focus-ring inline-flex h-8 w-8 items-center justify-center rounded-md border border-border bg-background text-muted-foreground transition-colors hover:border-border/80 hover:bg-muted/55 hover:text-foreground"
            onClick={onClose}
            aria-label="关闭当前句操作"
          >
            <X aria-hidden="true" className="h-4 w-4" />
          </button>
        ) : null}
      </div>

      <div className="mt-3 rounded-lg border border-border/80 bg-background px-3 py-3">
        <p className="line-clamp-3 reader-serif text-[0.98rem] leading-7 text-foreground">{previewText}</p>
      </div>

      <div className="mt-3 grid grid-cols-3 gap-2">
        <button
          type="button"
          className="focus-ring inline-flex min-h-10 items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/92 disabled:cursor-not-allowed disabled:opacity-60"
          disabled={saveState.kind === "saving"}
          onClick={() => onSaveAnnotation(false)}
        >
          高亮
        </button>
        <button
          type="button"
          className="focus-ring inline-flex min-h-10 items-center justify-center gap-2 rounded-md border border-border bg-background px-3 text-sm font-medium text-foreground transition-colors hover:border-border/80 hover:bg-muted/55"
          onClick={() => setNoteOpen((value) => !value)}
        >
          笔记
        </button>
        <DropdownMenu modal={false}>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className="focus-ring inline-flex min-h-10 items-center justify-center gap-2 rounded-md border border-border bg-background px-3 text-sm font-medium text-muted-foreground transition-colors hover:border-border/80 hover:bg-muted/55 hover:text-foreground"
            >
              <Sparkles aria-hidden="true" className="h-4 w-4" />
              更多
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel>句级操作</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuLabel className="pb-1 text-xs font-medium text-muted-foreground">
              <span className="inline-flex items-center gap-2">
                <Palette aria-hidden="true" className="h-3.5 w-3.5" />
                高亮颜色
              </span>
            </DropdownMenuLabel>
            <DropdownMenuRadioGroup value={color}>
              {colorOptions.map((option) => (
                <DropdownMenuRadioItem
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
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={onAsk}>
              <MessageSquare aria-hidden="true" className="h-4 w-4" />
              带入 Ask
            </DropdownMenuItem>
            {hasSavedAssets ? (
              <DropdownMenuItem onSelect={() => setSavedAssetsOpen((value) => !value)}>
                查看已保存资产
              </DropdownMenuItem>
            ) : null}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {noteOpen ? (
        <div className="mt-3 rounded-lg border border-border/80 bg-background p-3">
          <div className="mb-2 flex items-center justify-between gap-3">
            <span className="text-xs font-medium text-foreground">笔记</span>
            <span className="text-[0.68rem] text-muted-foreground">{note.length}/500</span>
          </div>
          <textarea
            className="focus-ring min-h-24 w-full resize-y rounded-md border border-border bg-background px-3 py-2 text-[0.92rem] leading-[1.65] text-foreground outline-none transition-colors focus:border-ring"
            placeholder={isTextRange ? "写一句和这个选区绑定的笔记。" : "写一句和这句话绑定的笔记。"}
            value={note}
            maxLength={500}
            onChange={(event) => onNoteChange(event.target.value)}
          />
          <div className="mt-3 flex items-center justify-between gap-3">
            <span className="text-xs text-muted-foreground">
              {saveState.kind === "saved" || saveState.kind === "error" ? saveState.message : "保存后会直接回到原文锚点。"}
            </span>
            <button
              type="button"
              className="focus-ring inline-flex min-h-8 items-center justify-center rounded-md border border-border bg-muted/45 px-3 text-xs font-medium text-foreground transition-colors hover:border-border/80 hover:bg-muted/72 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={saveState.kind === "saving" || note.trim().length === 0}
              onClick={() => onSaveAnnotation(true)}
            >
              保存笔记
            </button>
          </div>
        </div>
      ) : null}

      {hasSavedAssets && savedAssetsOpen ? (
        <div className="mt-3 border-t border-border/80 pt-3">
          <div className="mb-2 flex items-center justify-between gap-3">
            <p className="text-xs font-medium text-foreground">已保存资产</p>
            <span className="rounded-full border border-border bg-background px-2 py-0.5 text-[0.68rem] text-muted-foreground">
              {sentenceAnnotations.length + sentenceFavorites.length}
            </span>
          </div>
          <div className="space-y-2">
            {sentenceAnnotations.map((item) => (
              <div key={item.id} className="rounded-lg border border-border bg-background px-3 py-2.5">
                <p className="text-sm leading-6 text-muted-foreground">
                  <span className="font-semibold text-foreground">{annotationSummaryLabel(item)}</span>
                  {item.anchorType === "text_range" ? <span className="ml-2 text-muted-foreground">“{item.selectedText}”</span> : null}
                  {item.note ? <span className="ml-2 text-muted-foreground">{item.note}</span> : null}
                </p>
                {(onAnnotationJump || onAnnotationAsk) ? (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {onAnnotationJump ? (
                      <button
                        type="button"
                        className="focus-ring rounded-full border border-border bg-muted/35 px-2.5 py-1 text-[0.7rem] font-medium text-muted-foreground transition-colors hover:border-border/80 hover:text-foreground"
                        onClick={() => onAnnotationJump(item)}
                      >
                        跳转
                      </button>
                    ) : null}
                    {onAnnotationAsk ? (
                      <button
                        type="button"
                        className="focus-ring rounded-full border border-border bg-background px-2.5 py-1 text-[0.7rem] font-medium text-lens-blue transition-colors hover:border-border/80 hover:text-foreground"
                        onClick={() => onAnnotationAsk(item)}
                      >
                        带入 Ask
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </div>
            ))}
            {sentenceFavorites.map((item) => (
              <div key={item.id} className="rounded-lg border border-border bg-background px-3 py-2.5">
                <p className="text-sm leading-6 text-muted-foreground">
                  <span className="font-semibold text-foreground">收藏锚点</span>
                  {item.selectedText ? <span className="ml-2 text-muted-foreground">“{item.selectedText}”</span> : null}
                </p>
                {(onFavoriteJump || onFavoriteAsk) ? (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {onFavoriteJump ? (
                      <button
                        type="button"
                        className="focus-ring rounded-full border border-border bg-muted/35 px-2.5 py-1 text-[0.7rem] font-medium text-muted-foreground transition-colors hover:border-border/80 hover:text-foreground"
                        onClick={() => onFavoriteJump(item)}
                      >
                        跳转
                      </button>
                    ) : null}
                    {onFavoriteAsk ? (
                      <button
                        type="button"
                        className="focus-ring rounded-full border border-border bg-background px-2.5 py-1 text-[0.7rem] font-medium text-lens-blue transition-colors hover:border-border/80 hover:text-foreground"
                        onClick={() => onFavoriteAsk(item)}
                      >
                        带入 Ask
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}
