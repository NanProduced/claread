"use client";

import type { ThemePreference } from "@/lib/appearance";
import { cn } from "@/lib/cn";
import { readerCommandControl, readerSegmentedOption, readerTransitionStandard } from "../interaction";
import {
  type ReaderFontFamily,
  type ReaderFontScale,
  type ReaderSettingsState,
} from "./shared";

interface ReaderSettingsPanelProps {
  themePreference: ThemePreference;
  value: ReaderSettingsState;
  onChange: (next: ReaderSettingsState) => void;
  onThemeChange: (next: ThemePreference) => void;
  onClose?: () => void;
  variant?: "default" | "floating";
}

const fontScaleOptions: Array<{ value: ReaderFontScale; label: string }> = [
  { value: "sm", label: "小" },
  { value: "md", label: "中" },
  { value: "lg", label: "大" },
];

const fontFamilyOptions: Array<{
  value: ReaderFontFamily;
  label: string;
  english: string;
}> = [
  { value: "editorial", label: "编辑衬线", english: "Editorial" },
  { value: "book", label: "书页衬线", english: "Book" },
  { value: "sans", label: "极简无衬线", english: "Sans" },
];

const themeOptions: Array<{
  value: ThemePreference;
  label: string;
  english: string;
  description: string;
  previewClass: string;
}> = [
  {
    value: "system",
    label: "跟随系统",
    english: "System",
    description: "随操作系统浅色 / 深色自动切换。",
    previewClass: "bg-surface-warm text-ink",
  },
  {
    value: "light",
    label: "浅色",
    english: "Light",
    description: "更偏功能的明亮工作面。",
    previewClass: "bg-[#fbfbfb] text-[#1f2937]/80",
  },
  {
    value: "dark",
    label: "深色",
    english: "Dark",
    description: "为夜读调好的暗色舞台。",
    previewClass: "bg-[#18181c] text-[#a1a1a6]",
  },
];

function updateField<K extends keyof ReaderSettingsState>(
  current: ReaderSettingsState,
  key: K,
  nextValue: ReaderSettingsState[K],
): ReaderSettingsState {
  return {
    ...current,
    [key]: nextValue,
  };
}

export function ReaderSettingsPanel({
  themePreference,
  onChange,
  onClose,
  onThemeChange,
  value,
  variant = "default",
}: ReaderSettingsPanelProps) {
  const isFloating = variant === "floating";
  return (
    <section
      className={cn(
        "reader-tool-panel reader-settings-panel flex w-[min(20rem,calc(100vw-2rem))] flex-col overflow-hidden",
        !isFloating &&
          "border border-border/55 bg-[#FDFBF7]/98 shadow-xl shadow-black/10 backdrop-blur-xl dark:bg-[#1A1A1E]/98",
      )}
      data-reader-settings-panel={isFloating ? "floating" : "compact"}
    >
      <div className="flex items-start justify-between gap-4 px-4 py-3.5 select-none">
        <div>
          <h2 className="font-headline text-[1.05rem] font-bold leading-none text-ink">阅读设置</h2>
          <p className="mt-1 text-[0.66rem] leading-none text-muted-foreground">
            当前文章阅读体验
          </p>
        </div>

        {onClose ? (
          <button
            type="button"
            className={cn(readerCommandControl, "h-7 w-7 rounded-full p-0 text-[1.1rem] font-light leading-none text-muted-foreground hover:text-ink")}
            onClick={onClose}
            aria-label="关闭阅读设置"
          >
            ×
          </button>
        ) : null}
      </div>

      <div className="flex flex-col border-t border-hairline/70">
        <fieldset className="px-4 py-3.5">
          <div className="mb-2.5 flex items-center justify-between select-none">
            <legend className="text-[0.72rem] font-semibold tracking-wide text-ink">
              主题
            </legend>
            <span className="text-[0.58rem] font-mono font-semibold tracking-[0.12em] text-subtle">
              THEME
            </span>
          </div>

          <div className="grid grid-cols-3 gap-2">
            {themeOptions.map((option) => {
              const active = themePreference === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  aria-pressed={active}
                  className={cn(
                    readerSegmentedOption({ selected: active }),
                    "relative flex flex-col items-stretch rounded-[0.55rem] border bg-background/35 p-1 text-left",
                    active
                      ? "border-vocab-amber/45 bg-vocab-amber/6 shadow-[0_2px_8px_rgba(195,155,98,0.06)]"
                      : "border-hairline",
                  )}
                  onClick={() => onThemeChange(option.value)}
                >
                  <div
                    className={cn(
                      "relative flex h-10 flex-col justify-center gap-1.5 overflow-hidden rounded-[0.35rem] border border-hairline/20 p-2 shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)]",
                      readerTransitionStandard,
                      option.previewClass,
                    )}
                  >
                    <div className="flex w-full flex-col gap-1.5 opacity-60">
                      <div className="mb-0.5 h-[3px] w-2/5 rounded-full bg-current opacity-70" />
                      <div className="h-[2px] w-full rounded-full bg-current opacity-35" />
                      <div className="h-[2px] w-5/6 rounded-full bg-current opacity-35" />
                    </div>

                    {active && (
                      <span className="absolute right-1 top-1 flex h-3 w-3 items-center justify-center rounded-full bg-vocab-amber text-[0.5rem] font-bold text-white shadow-[0_1px_2px_rgba(0,0,0,0.15)]">
                        ✓
                      </span>
                    )}
                  </div>

                  <span className="mt-1.5 block pb-0.5 text-center">
                    <span className="block text-[0.72rem] font-semibold leading-none text-ink">{option.label}</span>
                    <span className="mt-0.5 block font-sans text-[0.52rem] font-medium leading-none tracking-[0.08em] text-subtle">
                      {option.english}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
        </fieldset>

        <fieldset className="border-t border-hairline/70 px-4 py-3.5">
          <div className="mb-2.5 flex items-center justify-between select-none">
            <legend className="text-[0.72rem] font-semibold tracking-wide text-ink">
              字号
            </legend>
            <span className="text-[0.58rem] font-mono font-semibold tracking-[0.12em] text-subtle">
              SIZE
            </span>
          </div>

          <div className="flex w-full items-center gap-1 rounded-[0.6rem] border border-hairline bg-background/20 p-0.5">
            {fontScaleOptions.map((option) => {
              const active = value.fontScale === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  className={cn(
                    readerSegmentedOption({ selected: active }),
                    "min-h-[2rem] flex-1 rounded-[0.45rem] border leading-none",
                    active
                      ? "border-vocab-amber/30 bg-vocab-amber/8 text-vocab-amber font-bold shadow-[0_1px_2px_rgba(0,0,0,0.02)]"
                      : "text-muted-foreground text-[0.8rem]",
                  )}
                  onClick={() => onChange(updateField(value, "fontScale", option.value))}
                >
                  <span className="block text-[0.8rem]">{option.label}</span>
                </button>
              );
            })}
          </div>
        </fieldset>

        <fieldset className="border-t border-hairline/70 px-4 py-3.5">
          <div className="mb-2.5 flex items-center justify-between select-none">
            <legend className="text-[0.72rem] font-semibold tracking-wide text-ink">
              字体
            </legend>
            <span className="text-[0.58rem] font-mono font-semibold tracking-[0.12em] text-subtle">
              TYPEFACE
            </span>
          </div>

          <div className="grid grid-cols-3 gap-2">
            {fontFamilyOptions.map((option) => {
              const active = value.fontFamily === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  className={cn(
                    readerSegmentedOption({ selected: active }),
                    "relative min-h-[3.15rem] flex-col rounded-[0.6rem] border bg-background/35 p-2",
                    active
                      ? "border-vocab-amber/35 bg-vocab-amber/8 text-vocab-amber shadow-[0_1px_3px_rgba(0,0,0,0.02)]"
                      : "border-hairline bg-transparent text-ink",
                  )}
                  onClick={() => onChange(updateField(value, "fontFamily", option.value))}
                >
                  <span className="block text-[0.76rem] font-bold tracking-tight">{option.label}</span>
                  <span className="mt-0.5 block font-sans text-[0.52rem] font-medium leading-none tracking-[0.08em] text-subtle">
                    {option.english}
                  </span>
                  {active && (
                    <span className="absolute right-1 top-1 flex h-3 w-3 items-center justify-center rounded-full bg-vocab-amber text-[0.5rem] font-bold text-white shadow-[0_1px_2px_rgba(0,0,0,0.15)]">
                      ✓
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </fieldset>
      </div>
    </section>
  );
}
