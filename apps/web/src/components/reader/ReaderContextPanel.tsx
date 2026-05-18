import { MessageSquare, X } from "lucide-react";
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

function readerPanelOptionClass(active: boolean) {
  return `focus-ring min-h-11 rounded-[0.9rem] border px-3.5 py-2 text-left transition-[background-color,border-color,color,box-shadow] ${
    active
      ? "border-lens-blue/18 bg-[linear-gradient(180deg,rgba(255,255,255,0.9),rgba(234,241,255,0.94))] text-ink shadow-[0_8px_18px_rgba(37,99,235,0.08),inset_0_1px_0_rgba(255,255,255,0.75)]"
      : "border-transparent bg-transparent text-ink-soft hover:bg-[rgba(255,255,255,0.58)] hover:text-ink"
  }`;
}

export interface ReaderContextPanelProps {
  mode: LowerPanelMode;
  sentence: SentenceModel | null;
  selectedText?: string | null;
  annotationScope?: "sentence" | "text_range";
  note: string;
  color: UserAnnotationColorDto;
  saveState: AnnotationSaveState;
  sentenceAnnotations: WebAnnotationVm[];
  showTranslation: boolean;
  fontSize: ReaderFontSize;
  density: ReadingDensity;
  theme: ReaderTheme;
  markVisibility: MarkVisibility;
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
  selectedText,
  annotationScope = "sentence",
  note,
  color,
  saveState,
  sentenceAnnotations,
  showTranslation,
  fontSize,
  density,
  theme,
  markVisibility,
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
  const activeSelectedText = selectedText?.trim() ? selectedText : null;
  const previewText = activeSelectedText ?? sentence?.text ?? "";
  const isTextRange = annotationScope === "text_range" && Boolean(activeSelectedText);
  const settingsSectionClass =
    "rounded-[1rem] border border-hairline/90 bg-[linear-gradient(180deg,rgba(255,255,255,0.58),rgba(248,245,238,0.8))] px-4 py-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.65)]";

  return (
    <section className="reader-tool-panel flex max-h-[min(42vh,22rem)] flex-col overflow-hidden md:max-h-[min(54vh,22rem)]">
      <div className="flex items-start justify-between gap-3 border-b border-hairline px-5 py-3">
        <div>
          <h2 className="text-base font-semibold text-ink">
            {mode === "settings" ? "阅读显示" : isTextRange ? "当前选区" : "当前句"}
          </h2>
          <p className="mt-1 text-xs leading-5 text-muted">
            {mode === "settings" ? "只影响本地阅读视图，不改动文章内容。" : "笔记和高亮会回到原文。"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {onClose ? (
            <button
              type="button"
              className="focus-ring inline-flex h-10 w-10 items-center justify-center rounded-[0.95rem] border border-hairline bg-[linear-gradient(180deg,rgba(255,255,255,0.92),rgba(249,247,241,0.98))] text-muted shadow-[0_8px_18px_rgba(17,17,17,0.04),inset_0_1px_0_rgba(255,255,255,0.76)] transition-colors hover:border-muted hover:text-ink"
              onClick={onClose}
              aria-label="关闭面板"
            >
              <X aria-hidden="true" className="h-4 w-4" />
            </button>
          ) : null}
        </div>
      </div>

      {mode === "settings" ? (
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain px-5 py-4">
          <fieldset className={settingsSectionClass}>
            <legend className="text-[0.72rem] font-semibold tracking-[0.14em] text-muted">译文</legend>
            <p className="mt-1 text-xs leading-5 text-muted">控制中文解释是否常显，适合切换纯阅读和双语对照。</p>
            <div className="mt-3 inline-flex w-full flex-wrap gap-1 rounded-[1rem] border border-hairline/90 bg-[linear-gradient(180deg,rgba(244,241,233,0.72),rgba(251,249,244,0.9))] p-1 shadow-[inset_0_1px_0_rgba(255,255,255,0.55)]">
              {[true, false].map((value) => (
                <button
                  key={String(value)}
                  type="button"
                  className={`${readerPanelOptionClass(showTranslation === value)} flex-1`}
                  onClick={() => onShowTranslationChange(value)}
                >
                  <span className="block text-sm font-semibold">{value ? "显示译文" : "原文模式"}</span>
                  <span className="mt-1 block text-[0.68rem] leading-none text-subtle">
                    {value ? "句后保留中文解释" : "只留下英文原文"}
                  </span>
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset className={settingsSectionClass}>
            <legend className="text-[0.72rem] font-semibold tracking-[0.14em] text-muted">字号与行距</legend>
            <p className="mt-1 text-xs leading-5 text-muted">只调整当前本地阅读视图，不影响文章内容本身。</p>
            <div className="mt-3 grid gap-3">
              <div>
                <div className="mb-2 text-[0.68rem] font-semibold tracking-[0.08em] text-subtle">字号</div>
                <div className="inline-flex w-full flex-wrap gap-1 rounded-[1rem] border border-hairline/90 bg-[linear-gradient(180deg,rgba(244,241,233,0.72),rgba(251,249,244,0.9))] p-1 shadow-[inset_0_1px_0_rgba(255,255,255,0.55)]">
                  {(["compact", "normal", "large"] as const).map((value) => (
                    <button
                      key={value}
                      type="button"
                      className={`${readerPanelOptionClass(fontSize === value)} flex-1`}
                      onClick={() => onFontSizeChange(value)}
                    >
                      <span className="block text-sm font-semibold">
                        {value === "compact" ? "小" : value === "normal" ? "中" : "大"}
                      </span>
                      <span className="mt-1 block text-[0.68rem] leading-none text-subtle">
                        {value === "compact" ? "更紧凑" : value === "normal" ? "默认阅读" : "更宽松"}
                      </span>
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <div className="mb-2 text-[0.68rem] font-semibold tracking-[0.08em] text-subtle">行距</div>
                <div className="inline-flex w-full flex-wrap gap-1 rounded-[1rem] border border-hairline/90 bg-[linear-gradient(180deg,rgba(244,241,233,0.72),rgba(251,249,244,0.9))] p-1 shadow-[inset_0_1px_0_rgba(255,255,255,0.55)]">
                  {(["calm", "roomy"] as const).map((value) => (
                    <button
                      key={value}
                      type="button"
                      className={`${readerPanelOptionClass(density === value)} flex-1`}
                      onClick={() => onDensityChange(value)}
                    >
                      <span className="block text-sm font-semibold">{value === "calm" ? "标准" : "舒展"}</span>
                      <span className="mt-1 block text-[0.68rem] leading-none text-subtle">
                        {value === "calm" ? "日常阅读" : "留出更多呼吸"}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </fieldset>

          <fieldset className={settingsSectionClass}>
            <legend className="text-[0.72rem] font-semibold tracking-[0.14em] text-muted">阅读背景</legend>
            <p className="mt-1 text-xs leading-5 text-muted">选择纸面基调，保持正文版心不变。</p>
            <div className="mt-3 inline-flex w-full flex-wrap gap-1 rounded-[1rem] border border-hairline/90 bg-[linear-gradient(180deg,rgba(244,241,233,0.72),rgba(251,249,244,0.9))] p-1 shadow-[inset_0_1px_0_rgba(255,255,255,0.55)]">
              {(["paper", "white", "green"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  className={`${readerPanelOptionClass(theme === value)} flex-1`}
                  onClick={() => onThemeChange(value)}
                >
                  <span className="block text-sm font-semibold">
                    {value === "paper" ? "纸张" : value === "white" ? "纯白" : "护眼"}
                  </span>
                  <span className="mt-1 block text-[0.68rem] leading-none text-subtle">
                    {value === "paper" ? "暖纸质感" : value === "white" ? "更干净" : "更柔和"}
                  </span>
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset className={settingsSectionClass}>
            <legend className="text-[0.72rem] font-semibold tracking-[0.14em] text-muted">标注显示</legend>
            <p className="mt-1 text-xs leading-5 text-muted">决定词汇、短语和语法标记在正文里的存在感。</p>
            <div className="mt-3 inline-flex w-full flex-wrap gap-1 rounded-[1rem] border border-hairline/90 bg-[linear-gradient(180deg,rgba(244,241,233,0.72),rgba(251,249,244,0.9))] p-1 shadow-[inset_0_1px_0_rgba(255,255,255,0.55)]">
              {(["full", "quiet"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  className={`${readerPanelOptionClass(markVisibility === value)} flex-1`}
                  onClick={() => onMarkVisibilityChange(value)}
                >
                  <span className="block text-sm font-semibold">{value === "full" ? "完整" : "安静"}</span>
                  <span className="mt-1 block text-[0.68rem] leading-none text-subtle">
                    {value === "full" ? "保留完整提示" : "减少视觉干扰"}
                  </span>
                </button>
              ))}
            </div>
          </fieldset>
        </div>
      ) : (
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain px-5 py-4">
          {sentence ? (
            <>
              <div className="rounded-[12px] bg-surface px-3.5 py-3 ring-1 ring-structure-green/18" title={isTextRange ? "当前选中的原文片段" : "当前选中的句子"}>
                <p className="line-clamp-3 reader-serif text-[1.02rem] leading-7 text-ink">{previewText}</p>
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
                  {sentenceAnnotations.length > 0 ? `已保存 ${sentenceAnnotations.length}` : isTextRange ? "选区级" : "句子级"}
                </span>
              </div>
              <label className="block">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold text-ink">笔记 (可选)</span>
                  <span className="text-[0.65rem] text-muted">{note.length}/500</span>
                </div>
                <textarea
                  className="min-h-16 w-full resize-y rounded-xl border border-hairline bg-surface px-4 py-3 text-[0.9375rem] leading-[1.65] text-ink outline-none transition-colors focus:border-muted shadow-surface-quiet"
                  placeholder={isTextRange ? "写一句和这个选区绑定的笔记。" : "写一句和这句话绑定的笔记。"}
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
                        <span className="font-semibold text-ink">{annotationSummaryLabel(item)}</span>
                        {item.anchorType === "text_range" ? (
                          <span className="ml-2 text-muted">“{item.selectedText}”</span>
                        ) : null}
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
