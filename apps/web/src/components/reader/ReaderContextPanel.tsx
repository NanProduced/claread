"use client";

import { Highlighter, MessageSquare, MoreHorizontal, NotebookPen, Palette, X } from "lucide-react";
import { useEffect, useId, useState } from "react";
import type { UserAnnotationColorDto, WebAnnotationVm } from "@/types/api/annotations";
import type { WebFavoriteTargetVm } from "@/types/api/favorites";
import type { SentenceModel } from "@/types/view/ReaderMockVm";
import {
  ReaderToolbarActionButton,
  ReaderToolbarIconButton,
  ReaderToolbarMenu,
  ReaderToolbarMenuContent,
  ReaderToolbarMenuItem,
  ReaderToolbarMenuLabel,
  ReaderToolbarMenuRadioGroup,
  ReaderToolbarMenuRadioItem,
  ReaderToolbarMenuSeparator,
  ReaderToolbarMenuTrigger,
  ReaderToolbarPopoverCard,
  ReaderToolbarRoot,
} from "./plate-ui-adapter";

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
  const titleId = useId();
  const descriptionId = useId();
  const noteId = useId();
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
      <section
        role="dialog"
        aria-modal="false"
        className="w-[min(22rem,calc(100vw-1rem))] rounded-xl border border-border/80 bg-popover/98 p-4 text-popover-foreground shadow-md"
      >
        <p className="text-sm leading-6 text-muted-foreground">通过句侧句柄打开当前句操作，或直接选中文本进入选区工具条。</p>
      </section>
    );
  }

  return (
    <section
      role="dialog"
      aria-modal="false"
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      className="w-[min(24rem,calc(100vw-1rem))] max-h-[min(32rem,calc(100vh-7rem))] overflow-y-auto rounded-xl border border-border/80 bg-popover/98 p-4 text-popover-foreground shadow-md"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id={titleId} className="text-sm font-semibold text-foreground">{isTextRange ? "当前选区" : "当前句"}</h2>
          <p id={descriptionId} className="mt-1 text-xs leading-5 text-muted-foreground">只保留当前句的最小动作，已保存资产放到次级层。</p>
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

      <div className="mt-3 rounded-lg border border-border/80 bg-background px-3 py-3">
        <p className="line-clamp-3 reader-serif text-[0.98rem] leading-7 text-foreground">{previewText}</p>
      </div>

      <ReaderToolbarRoot className="mt-3 max-w-none justify-between p-1">
        <ReaderToolbarActionButton
          className="min-h-10 flex-1 justify-center gap-2 rounded-lg border-transparent bg-primary text-primary-foreground hover:border-transparent hover:bg-primary/92 hover:text-primary-foreground"
          disabled={saveState.kind === "saving"}
          onClick={() => onSaveAnnotation(false)}
        >
          <Highlighter aria-hidden="true" className="h-4 w-4" />
          高亮
        </ReaderToolbarActionButton>
        <ReaderToolbarActionButton
          className="min-h-10 flex-1 justify-center gap-2 rounded-lg"
          active={noteOpen}
          onClick={() => setNoteOpen((value) => !value)}
        >
          <NotebookPen aria-hidden="true" className="h-4 w-4" />
          笔记
        </ReaderToolbarActionButton>
        <ReaderToolbarMenu>
          <ReaderToolbarMenuTrigger asChild>
            <ReaderToolbarIconButton
              aria-label="更多句级操作"
              title="更多句级操作"
              className="h-10 min-w-10 rounded-lg"
            >
              <MoreHorizontal aria-hidden="true" className="h-4 w-4" />
            </ReaderToolbarIconButton>
          </ReaderToolbarMenuTrigger>
          <ReaderToolbarMenuContent align="end" className="w-56">
            <ReaderToolbarMenuLabel>句级操作</ReaderToolbarMenuLabel>
            <ReaderToolbarMenuSeparator />
            <ReaderToolbarMenuLabel className="pb-1 text-xs font-medium text-muted-foreground">
              <span className="inline-flex items-center gap-2">
                <Palette aria-hidden="true" className="h-3.5 w-3.5" />
                高亮颜色
              </span>
            </ReaderToolbarMenuLabel>
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
            <ReaderToolbarMenuSeparator />
            <ReaderToolbarMenuItem onSelect={onAsk}>
              <MessageSquare aria-hidden="true" className="h-4 w-4" />
              带入 Ask
            </ReaderToolbarMenuItem>
            {hasSavedAssets ? (
              <ReaderToolbarMenuItem onSelect={() => setSavedAssetsOpen((value) => !value)}>
                查看已保存资产
              </ReaderToolbarMenuItem>
            ) : null}
          </ReaderToolbarMenuContent>
        </ReaderToolbarMenu>
      </ReaderToolbarRoot>

      {noteOpen ? (
        <ReaderToolbarPopoverCard className="mt-3 w-full rounded-lg p-3">
          <div className="mb-2 flex items-center justify-between gap-3">
            <label htmlFor={noteId} className="text-xs font-medium text-foreground">笔记</label>
            <span className="text-[0.68rem] text-muted-foreground">{note.length}/500</span>
          </div>
          <textarea
            id={noteId}
            aria-label={isTextRange ? "当前选区笔记" : "当前句笔记"}
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
              className="focus-ring inline-flex min-h-10 items-center justify-center rounded-md border border-border bg-muted/45 px-3 text-xs font-medium text-foreground transition-colors hover:border-border/80 hover:bg-muted/72 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={saveState.kind === "saving" || note.trim().length === 0}
              onClick={() => onSaveAnnotation(true)}
            >
              保存笔记
            </button>
          </div>
        </ReaderToolbarPopoverCard>
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
              <ReaderToolbarPopoverCard key={item.id} className="mt-0 w-full rounded-lg px-3 py-2.5">
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
              </ReaderToolbarPopoverCard>
            ))}
            {sentenceFavorites.map((item) => (
              <ReaderToolbarPopoverCard key={item.id} className="mt-0 w-full rounded-lg px-3 py-2.5">
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
              </ReaderToolbarPopoverCard>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}
