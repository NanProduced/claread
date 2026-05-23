"use client";

import { Palette, Settings2, Type, WholeWord, X } from "lucide-react";
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

function sectionClass() {
  return "reader-settings-section rounded-[1.05rem] border border-hairline/80 px-3.5 py-3";
}

function segmentContainerClass() {
  return "reader-settings-segmented inline-flex w-full flex-wrap gap-1 rounded-[0.95rem] border border-hairline p-1";
}

function optionClass(active: boolean) {
  return `reader-settings-option focus-ring min-h-[2.9rem] rounded-[0.82rem] border px-3 py-2 text-left ${
    active
      ? "reader-settings-option--active border-hairline/90 text-ink"
      : "reader-settings-option--inactive border-transparent bg-transparent text-ink-soft hover:text-ink"
  }`;
}

function sectionHeaderClass() {
  return "mb-2.5 flex items-center justify-between gap-2";
}

function sectionLabelClass() {
  return "flex items-center gap-2 text-[0.8rem] font-semibold text-subtle";
}

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
    <section className="reader-tool-panel reader-settings-panel flex w-full flex-col overflow-hidden md:w-[29rem]">
      <div className="flex items-start justify-between gap-3 border-b border-hairline px-4 py-3.5">
        <div>
          <div className="flex items-center gap-2">
            <Settings2 aria-hidden="true" className="h-4 w-4 text-lens-blue" />
            <h2 className="text-base font-semibold text-ink">阅读设置</h2>
          </div>
          <p className="mt-1.5 text-[0.72rem] leading-5 text-muted">
            主题、字号与字体会即时作用于当前页面。主题与全站同步，不改变精读 / 沉浸模式。
          </p>
        </div>
        {onClose ? (
          <button
            type="button"
            className="reader-settings-dismiss app-control-surface focus-ring inline-flex h-10 w-10 items-center justify-center rounded-[0.95rem] border border-hairline text-muted transition-colors hover:border-[var(--app-control-border-hover)] hover:text-ink"
            onClick={onClose}
            aria-label="关闭阅读设置"
          >
            <X aria-hidden="true" className="h-4 w-4" />
          </button>
        ) : null}
      </div>

      <div className="space-y-3 px-4 py-3.5">
        <fieldset className={sectionClass()}>
          <div className={sectionHeaderClass()}>
            <legend className={sectionLabelClass()}>
              <Palette aria-hidden="true" className="h-3.5 w-3.5" />
              主题
            </legend>
          </div>
          <div className="grid gap-1.5">
            {themeOptions.map((option) => {
              const active = themeName === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  aria-pressed={active}
                  className={optionClass(active)}
                  onClick={() => onThemeChange(option.value)}
                >
                  <span className="flex items-start gap-3">
                    <span
                      aria-hidden="true"
                      className={`reader-settings-theme-swatch reader-settings-theme-swatch--${option.value}`}
                    />
                    <span className="min-w-0">
                      <span className="block text-sm font-semibold text-ink">{option.label}</span>
                      <span className="mt-1 block text-[0.72rem] uppercase tracking-[0.08em] text-subtle">
                        {option.english}
                      </span>
                      <span className="mt-1.5 block text-[0.72rem] leading-5 text-muted">
                        {option.description}
                      </span>
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
        </fieldset>

        <fieldset className={sectionClass()}>
          <div className={sectionHeaderClass()}>
            <legend className={sectionLabelClass()}>
              <Type aria-hidden="true" className="h-3.5 w-3.5" />
              字号
            </legend>
          </div>
          <div className={segmentContainerClass()}>
            {fontScaleOptions.map((option) => (
              <button
                key={option.value}
                type="button"
                className={`${optionClass(value.fontScale === option.value)} flex-1 text-center`}
                onClick={() => onChange(updateField(value, "fontScale", option.value))}
              >
                <span className="block text-sm font-semibold">{option.label}</span>
              </button>
            ))}
          </div>
        </fieldset>

        <fieldset className={sectionClass()}>
          <div className={sectionHeaderClass()}>
            <legend className={sectionLabelClass()}>
              <WholeWord aria-hidden="true" className="h-3.5 w-3.5" />
              字体
            </legend>
          </div>
          <div className="grid gap-1.5">
            {fontFamilyOptions.map((option) => (
              <button
                key={option.value}
                type="button"
                className={optionClass(value.fontFamily === option.value)}
                onClick={() => onChange(updateField(value, "fontFamily", option.value))}
              >
                <span className="block text-sm font-semibold text-ink">{option.label}</span>
                <span className="mt-1 block text-[0.72rem] uppercase tracking-[0.08em] text-subtle">
                  {option.english}
                </span>
              </button>
            ))}
          </div>
        </fieldset>

      </div>
    </section>
  );
}
