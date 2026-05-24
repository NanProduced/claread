"use client";

import type { ThemeName } from "@/lib/appearance";
import {
  type ReaderFontFamily,
  type ReaderFontScale,
  type ReaderSettingsState,
} from "./shared";

interface ReaderSettingsPanelProps {
  themeName: ThemeName;
  value: ReaderSettingsState;
  onChange: (next: ReaderSettingsState) => void;
  onThemeChange: (next: ThemeName) => void;
  onClose?: () => void;
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
  value: ThemeName;
  label: string;
  english: string;
  description: string;
}> = [
  {
    value: "paper",
    label: "纸质",
    english: "Paper",
    description: "默认母主题，纸感完整。",
  },
  {
    value: "light",
    label: "浅色",
    english: "Light",
    description: "更偏功能的明亮工作面。",
  },
  {
    value: "dark",
    label: "深色",
    english: "Dark",
    description: "为夜读调好的暗色舞台。",
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
  themeName,
  onChange,
  onClose,
  onThemeChange,
  value,
}: ReaderSettingsPanelProps) {
  return (
    <section className="reader-tool-panel reader-settings-panel relative flex w-full flex-col overflow-visible md:w-[22.5rem] shadow-surface-quiet bg-[#FDFBF7] dark:bg-[#1A1A1E]">
      
      {/* Tactile Folder Tab Index Handle (Decorative/Affordance) */}
      <div
        aria-hidden="true"
        className="absolute -right-4.5 top-1/2 -translate-y-1/2 hidden md:flex flex-col items-center justify-center w-4.5 h-20 bg-[#e4dcce] dark:bg-[#252422] border-t border-r border-b border-hairline rounded-r-[0.6rem] shadow-[3px_0_6px_rgba(0,0,0,0.04)] group cursor-pointer select-none transition-all duration-300 hover:translate-x-[2px] hover:bg-[#ded5c5] z-10"
      >
        <span className="text-[0.55rem] font-bold tracking-[0.08em] text-[#8e8574] dark:text-[#736c61] rotate-90 scale-90 opacity-80 group-hover:opacity-100 transition-opacity">
          ⚏
        </span>
      </div>

      {/* Header Section */}
      <div className="flex items-center justify-between gap-4 px-4.5 pt-4.5 pb-3.5 select-none">
        <div>
          <h2 className="font-headline text-[1.25rem] font-bold text-ink leading-none">阅读设置</h2>
          <p className="mt-1 text-[0.68rem] text-muted">
            为当前文章核准阅读体验
          </p>
        </div>
        
        {onClose ? (
          <button
            type="button"
            className="reader-settings-dismiss text-[1.5rem] font-light text-muted hover:text-ink transition-colors flex items-center justify-center cursor-pointer leading-none -mt-1 hover:scale-105"
            onClick={onClose}
            aria-label="关闭阅读设置"
          >
            ×
          </button>
        ) : null}
      </div>

      {/* Flat Content List (Flattened layout, eliminating nested cards) */}
      <div className="flex flex-col pb-4.5">
        
        {/* A01: Theme Section */}
        <fieldset className="border-t border-hairline/80 px-4.5 py-4">
          <div className="flex items-center justify-between mb-3.5 select-none">
            <legend className="flex items-center gap-1.5 text-[0.7rem] font-bold tracking-wide uppercase">
              <span className="text-muted/60 font-mono">A01</span>
              <span className="text-ink">主题</span>
            </legend>
            <span className="text-[0.55rem] font-mono tracking-[0.1em] font-semibold text-subtle uppercase">
              Theme
            </span>
          </div>

          <div className="grid grid-cols-3 gap-2.5">
            {themeOptions.map((option) => {
              const active = themeName === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  aria-pressed={active}
                  className={`focus-ring relative flex flex-col items-stretch rounded-[0.5rem] border p-1 transition-all duration-200 cursor-pointer select-none bg-background/40 hover:bg-ink/[0.01] ${
                    active
                      ? "border-vocab-amber/40 shadow-[0_2px_8px_rgba(195,155,98,0.06)]"
                      : "border-hairline hover:border-muted"
                  }`}
                  onClick={() => onThemeChange(option.value)}
                >
                  {/* Miniature article layout mockup */}
                  <div
                    className={`h-14 md:h-[3.8rem] rounded-[0.35rem] p-2 flex flex-col gap-1.5 justify-center relative overflow-hidden transition-colors border border-hairline/20 shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] ${
                      option.value === "paper"
                        ? "bg-[#f4f0e6] text-[#4a3e3d]/80"
                        : option.value === "light"
                          ? "bg-[#fbfbfb] text-[#1f2937]/80"
                          : "bg-[#18181c] text-[#a1a1a6]"
                    }`}
                  >
                    <div className="flex flex-col gap-1.5 w-full opacity-60">
                      {/* Real-looking text paragraph mockup */}
                      <div className="h-[3px] w-2/5 bg-current rounded-full mb-0.5 opacity-70" />
                      <div className="h-[2px] w-full bg-current rounded-full opacity-35" />
                      <div className="h-[2px] w-5/6 bg-current rounded-full opacity-35" />
                    </div>

                    {active && (
                      <span className="absolute top-1 right-1 h-3 w-3 rounded-full bg-vocab-amber flex items-center justify-center text-[0.5rem] font-bold text-white shadow-[0_1px_2px_rgba(0,0,0,0.15)]">
                        ✓
                      </span>
                    )}
                  </div>

                  {/* Labels */}
                  <span className="block text-center mt-2 pb-0.5">
                    <span className="block text-[0.78rem] font-semibold text-ink leading-none">{option.label}</span>
                    <span className="mt-0.5 block text-[0.55rem] uppercase tracking-[0.08em] font-sans font-medium text-subtle leading-none">
                      {option.english}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>

          {/* Radio-style pagination indicators */}
          <div className="flex items-center justify-center gap-1.2 mt-3 select-none">
            {themeOptions.map((opt) => (
              <span
                key={opt.value}
                className={`h-1 w-1 rounded-full transition-all duration-300 ${
                  themeName === opt.value
                    ? "bg-vocab-amber w-2.5"
                    : "bg-hairline hover:bg-muted"
                }`}
              />
            ))}
          </div>
        </fieldset>

        {/* A02: Font Scale Section */}
        <fieldset className="border-t border-hairline/80 px-4.5 py-4">
          <div className="flex items-center justify-between mb-3.5 select-none">
            <legend className="flex items-center gap-1.5 text-[0.7rem] font-bold tracking-wide uppercase">
              <span className="text-muted/60 font-mono">A02</span>
              <span className="text-ink">字号</span>
            </legend>
            <span className="text-[0.55rem] font-mono tracking-[0.1em] font-semibold text-subtle uppercase">
              Size
            </span>
          </div>

          <div className="flex w-full items-center gap-1 rounded-[0.6rem] border border-hairline p-0.5 bg-background/20">
            {fontScaleOptions.map((option) => {
              const active = value.fontScale === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  className={`focus-ring flex-1 min-h-[2.1rem] flex items-center justify-center rounded-[0.45rem] border transition-all duration-200 cursor-pointer select-none leading-none ${
                    active
                      ? "bg-vocab-amber/8 border-vocab-amber/30 text-vocab-amber font-bold shadow-[0_1px_2px_rgba(0,0,0,0.02)]"
                      : "border-transparent bg-transparent text-muted hover:text-ink font-semibold text-[0.8rem]"
                  }`}
                  onClick={() => onChange(updateField(value, "fontScale", option.value))}
                >
                  <span className="block text-[0.8rem]">{option.label}</span>
                </button>
              );
            })}
          </div>
        </fieldset>

        {/* A03: Font Family Section */}
        <fieldset className="border-t border-hairline/80 px-4.5 py-4">
          <div className="flex items-center justify-between mb-3.5 select-none">
            <legend className="flex items-center gap-1.5 text-[0.7rem] font-bold tracking-wide uppercase">
              <span className="text-muted/60 font-mono">A03</span>
              <span className="text-ink">字体</span>
            </legend>
            <span className="text-[0.55rem] font-mono tracking-[0.1em] font-semibold text-subtle uppercase">
              Typeface
            </span>
          </div>

          <div className="grid grid-cols-3 gap-2.5">
            {fontFamilyOptions.map((option) => {
              const active = value.fontFamily === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  className={`focus-ring relative flex flex-col items-center justify-center min-h-[3.6rem] rounded-[0.6rem] border p-2.5 transition-all duration-200 cursor-pointer select-none bg-background/40 hover:bg-ink/[0.01] ${
                    active
                      ? "bg-vocab-amber/8 border-vocab-amber/30 text-vocab-amber shadow-[0_1px_3px_rgba(0,0,0,0.02)]"
                      : "border-hairline bg-transparent text-ink hover:text-ink-soft hover:border-muted"
                  }`}
                  onClick={() => onChange(updateField(value, "fontFamily", option.value))}
                >
                  <span className="block text-[0.82rem] font-bold tracking-tight">{option.label}</span>
                  <span className="mt-0.5 block text-[0.55rem] uppercase tracking-[0.08em] font-sans font-medium text-subtle leading-none">
                    {option.english}
                  </span>
                  {active && (
                    <span className="absolute top-1 right-1 h-3 w-3 rounded-full bg-vocab-amber flex items-center justify-center text-[0.5rem] font-bold text-white shadow-[0_1px_2px_rgba(0,0,0,0.15)]">
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
