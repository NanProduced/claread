import { MessageSquare, PenLine, Type, X } from "lucide-react";
import type { UserAnnotationColorDto, WebAnnotationVm } from "@/types/api/annotations";
import type { SentenceModel } from "@/types/view/ReaderMockVm";

export type LowerPanelMode = "sentence" | "settings";
export type ReadingDensity = "calm" | "roomy";
export type ReaderTheme = "paper" | "white" | "green";
export type MarkVisibility = "full" | "quiet";
export type ReaderFontSize = "compact" | "normal" | "large";

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

export interface ReaderContextPanelProps {
  mode: LowerPanelMode;
  sentence: SentenceModel | null;
  note: string;
  color: UserAnnotationColorDto;
  saveState: AnnotationSaveState;
  sentenceAnnotations: WebAnnotationVm[];
  showTranslation: boolean;
  fontSize: ReaderFontSize;
  density: ReadingDensity;
  theme: ReaderTheme;
  markVisibility: MarkVisibility;
  onModeChange: (mode: LowerPanelMode) => void;
  onNoteChange: (value: string) => void;
  onColorChange: (value: UserAnnotationColorDto) => void;
  onSaveAnnotation: (useNote: boolean) => void;
  onAsk: () => void;
  onShowTranslationChange: (value: boolean) => void;
  onFontSizeChange: (value: ReaderFontSize) => void;
  onDensityChange: (value: ReadingDensity) => void;
  onThemeChange: (value: ReaderTheme) => void;
  onMarkVisibilityChange: (value: MarkVisibility) => void;
  onClose?: () => void;
}

export function ReaderContextPanel({
  mode,
  sentence,
  note,
  color,
  saveState,
  sentenceAnnotations,
  showTranslation,
  fontSize,
  density,
  theme,
  markVisibility,
  onModeChange,
  onNoteChange,
  onColorChange,
  onSaveAnnotation,
  onAsk,
  onShowTranslationChange,
  onFontSizeChange,
  onDensityChange,
  onThemeChange,
  onMarkVisibilityChange,
  onClose,
}: ReaderContextPanelProps) {
  return (
    <section className="reader-tool-panel flex max-h-[min(42vh,22rem)] flex-col overflow-hidden md:max-h-[min(54vh,22rem)]">
      <div className="flex items-start justify-between gap-3 border-b border-hairline px-5 py-3">
        <div>
          <h2 className="text-base font-semibold text-ink">
            {mode === "settings" ? "显示" : "当前句"}
          </h2>
          <p className="mt-1 text-xs leading-5 text-muted">
            {mode === "settings" ? "只影响本地阅读视图。" : "笔记和高亮会回到原文。"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="grid grid-cols-2 rounded-pill border border-hairline bg-reader-paper p-1">
            <button
              type="button"
              className={`focus-ring inline-flex h-9 w-10 items-center justify-center rounded-pill text-muted transition-colors hover:text-ink disabled:cursor-not-allowed disabled:opacity-40 ${
                mode === "sentence" ? "bg-surface text-ink shadow-surface-quiet" : ""
              }`}
              onClick={() => onModeChange("sentence")}
              disabled={!sentence}
              aria-label="句子操作"
            >
              <PenLine aria-hidden="true" className="h-4 w-4" />
            </button>
            <button
              type="button"
              className={`focus-ring inline-flex h-9 w-10 items-center justify-center rounded-pill text-muted transition-colors hover:text-ink ${
                mode === "settings" ? "bg-surface text-ink shadow-surface-quiet" : ""
              }`}
              onClick={() => onModeChange("settings")}
              aria-label="阅读显示"
            >
              <Type aria-hidden="true" className="h-4 w-4" />
            </button>
          </div>
          {onClose ? (
            <button
              type="button"
              className="focus-ring inline-flex h-10 w-10 items-center justify-center rounded-full border border-hairline bg-surface text-muted transition-colors hover:border-muted hover:text-ink"
              onClick={onClose}
              aria-label="关闭面板"
            >
              <X aria-hidden="true" className="h-4 w-4" />
            </button>
          ) : null}
        </div>
      </div>

      {mode === "settings" ? (
        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto overscroll-contain px-5 py-4">
          <fieldset>
            <legend className="text-xs font-semibold text-muted">译文</legend>
            <div className="mt-2 grid grid-cols-2 gap-2">
              {[true, false].map((value) => (
                <button
                  key={String(value)}
                  type="button"
                  className={`focus-ring min-h-10 rounded-pill border px-3 text-sm font-semibold transition-colors ${
                    showTranslation === value
                      ? "border-lens-blue bg-lens-blue-soft text-lens-blue"
                      : "border-hairline bg-reader-paper text-ink-soft hover:border-muted"
                  }`}
                  onClick={() => onShowTranslationChange(value)}
                >
                  {value ? "显示译文" : "原文模式"}
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset>
            <legend className="text-xs font-semibold text-muted">字号</legend>
            <div className="mt-2 grid grid-cols-3 gap-2">
              {(["compact", "normal", "large"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  className={`focus-ring min-h-10 rounded-pill border px-3 text-sm font-semibold transition-colors ${
                    fontSize === value
                      ? "border-lens-blue bg-lens-blue-soft text-lens-blue"
                      : "border-hairline bg-reader-paper text-ink-soft hover:border-muted"
                  }`}
                  onClick={() => onFontSizeChange(value)}
                >
                  {value === "compact" ? "小" : value === "normal" ? "中" : "大"}
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset>
            <legend className="text-xs font-semibold text-muted">行距</legend>
            <div className="mt-2 grid grid-cols-2 gap-2">
              {(["calm", "roomy"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  className={`focus-ring min-h-10 rounded-pill border px-3 text-sm font-semibold transition-colors ${
                    density === value
                      ? "border-lens-blue bg-lens-blue-soft text-lens-blue"
                      : "border-hairline bg-reader-paper text-ink-soft hover:border-muted"
                  }`}
                  onClick={() => onDensityChange(value)}
                >
                  {value === "calm" ? "标准" : "舒展"}
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset>
            <legend className="text-xs font-semibold text-muted">阅读背景</legend>
            <div className="mt-2 grid grid-cols-3 gap-2">
              {(["paper", "white", "green"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  className={`focus-ring min-h-10 rounded-pill border px-3 text-sm font-semibold transition-colors ${
                    theme === value
                      ? "border-lens-blue bg-lens-blue-soft text-lens-blue"
                      : "border-hairline bg-reader-paper text-ink-soft hover:border-muted"
                  }`}
                  onClick={() => onThemeChange(value)}
                >
                  {value === "paper" ? "纸张" : value === "white" ? "纯白" : "护眼"}
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset>
            <legend className="text-xs font-semibold text-muted">标注显示</legend>
            <div className="mt-2 grid grid-cols-2 gap-2">
              {(["full", "quiet"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  className={`focus-ring min-h-10 rounded-pill border px-3 text-sm font-semibold transition-colors ${
                    markVisibility === value
                      ? "border-lens-blue bg-lens-blue-soft text-lens-blue"
                      : "border-hairline bg-reader-paper text-ink-soft hover:border-muted"
                  }`}
                  onClick={() => onMarkVisibilityChange(value)}
                >
                  {value === "full" ? "完整" : "安静"}
                </button>
              ))}
            </div>
          </fieldset>
        </div>
      ) : (
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain px-5 py-4">
          {sentence ? (
            <>
              <div className="rounded-[12px] bg-surface px-3.5 py-3 ring-1 ring-structure-green/18" title="当前选中的句子">
                <p className="line-clamp-2 reader-serif text-[1.02rem] leading-7 text-ink">{sentence.text}</p>
              </div>
              <div className="space-y-2">
                <div className="grid grid-cols-5 gap-2" aria-label="高亮颜色">
                  {colorOptions.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      className={`focus-ring flex h-10 w-10 items-center justify-center rounded-full border bg-surface transition-colors hover:border-muted ${
                        color === option.value ? "border-lens-blue ring-2 ring-lens-blue/18" : "border-hairline"
                      }`}
                      onClick={() => onColorChange(option.value)}
                      aria-label={`选择${option.label}高亮`}
                    >
                      <span className={`h-5 w-5 rounded-full ${option.className}`} />
                    </button>
                  ))}
                </div>
                <span className="inline-flex w-fit shrink-0 rounded-full border border-hairline bg-reader-paper px-2.5 py-1 text-[0.65rem] font-semibold text-muted">
                  {sentenceAnnotations.length > 0 ? `已保存 ${sentenceAnnotations.length}` : "句子级"}
                </span>
              </div>
              <label className="block">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold text-ink">笔记 (可选)</span>
                  <span className="text-[0.65rem] text-muted">{note.length}/500</span>
                </div>
                <textarea
                  className="min-h-16 w-full resize-y rounded-xl border border-hairline bg-surface px-4 py-3 text-[0.9375rem] leading-[1.65] text-ink outline-none transition-colors focus:border-muted shadow-surface-quiet"
                  placeholder="写一句和这句话绑定的笔记。"
                  value={note}
                  maxLength={500}
                  onChange={(event) => onNoteChange(event.target.value)}
                />
              </label>
              <div className="grid grid-cols-3 gap-2 pt-1">
                <button
                  type="button"
                  className="focus-ring inline-flex min-h-11 items-center justify-center gap-2 rounded-[10px] bg-ink px-3.5 text-sm font-semibold text-surface transition-colors hover:bg-ink-soft disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={saveState.kind === "saving"}
                  onClick={() => onSaveAnnotation(false)}
                >
                  高亮
                </button>
                <button
                  type="button"
                  className="focus-ring inline-flex min-h-11 items-center justify-center gap-2 rounded-[10px] border border-hairline bg-surface px-3.5 text-sm font-semibold text-ink transition-colors hover:border-muted hover:bg-reader-paper disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={saveState.kind === "saving" || note.trim().length === 0}
                  onClick={() => onSaveAnnotation(true)}
                >
                  保存
                </button>
                <button
                  type="button"
                  className="focus-ring inline-flex min-h-11 items-center justify-center gap-2 rounded-[10px] border border-lens-blue/20 bg-lens-blue-soft/45 px-3.5 text-sm font-semibold text-lens-blue transition-colors hover:bg-lens-blue-soft/75"
                  onClick={onAsk}
                >
                  <MessageSquare aria-hidden="true" className="h-4 w-4" />
                  追问
                </button>
              </div>
              {saveState.kind === "saved" ? (
                <p className="text-xs font-semibold text-structure-green">{saveState.message}</p>
              ) : null}
              {saveState.kind === "error" ? (
                <p className="text-xs font-semibold text-error-red">{saveState.message}</p>
              ) : null}
              {sentenceAnnotations.length > 0 ? (
                <div className="border-t border-hairline pt-3">
                  <p className="text-xs font-semibold text-muted">本句已保存</p>
                  <div className="mt-2 space-y-2">
                    {sentenceAnnotations.slice(0, 3).map((item) => (
                      <p key={item.id} className="text-sm leading-6 text-ink-soft">
                        <span className="font-semibold text-ink">{item.type === "note" ? "笔记" : "高亮"}</span>
                        {item.note ? <span className="ml-2 text-muted">{item.note}</span> : null}
                      </p>
                    ))}
                  </div>
                </div>
              ) : null}
            </>
          ) : (
            <p className="text-sm leading-6 text-muted">点击正文中的句子后，可以在这里写笔记或保存高亮。</p>
          )}
        </div>
      )}
    </section>
  );
}
