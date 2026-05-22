"use client";

import {
  AlignLeft,
  BookOpen,
  Eye,
  EyeOff,
  Highlighter,
  Layers3,
  Palette,
  ScanText,
  Type,
  X,
} from "lucide-react";
import {
  MODE_PRESETS,
  type ReaderAnnotationVisibilityGroups,
  type ReaderColumnWidth,
  type ReaderFontSize,
  type ReaderPaperTheme,
  type ReaderSettingsState,
  type ReadingDensity,
  type ReadingMode,
  type TranslationDisplay,
  withCustomReadingMode,
} from "./shared";

interface ReaderSettingsPanelProps {
  value: ReaderSettingsState;
  onChange: (next: ReaderSettingsState) => void;
  onClose?: () => void;
}

const modeOptions: Array<{
  value: ReadingMode;
  label: string;
}> = [
  { value: "annotated", label: "精读" },
  { value: "immersive", label: "沉浸" },
  { value: "custom", label: "自定义" },
];

const paperThemes: Array<{
  value: ReaderPaperTheme;
  label: string;
  dotClassName: string;
}> = [
  { value: "warm", label: "暖纸", dotClassName: "bg-[#d6bd8a]" },
  { value: "cool", label: "冷纸", dotClassName: "bg-[#b8becb]" },
  { value: "sage", label: "鼠尾草", dotClassName: "bg-[#9bb7a4]" },
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

function chipClass(active: boolean) {
  return `reader-settings-chip focus-ring rounded-[0.85rem] border px-3 py-2 text-sm font-semibold ${
    active
      ? "reader-settings-chip--active border-hairline text-ink shadow-[var(--app-panel-shadow-quiet)]"
      : "reader-settings-chip--inactive border-hairline/75 bg-background/38 text-ink-soft hover:text-ink"
  }`;
}

function sectionHeaderClass() {
  return "mb-2.5 flex items-center justify-between gap-2";
}

function sectionLabelClass() {
  return "flex items-center gap-2 text-[0.8rem] font-semibold text-subtle";
}

function activeValueChip(label: string) {
  return (
    <span className="rounded-pill border border-hairline bg-background/72 px-2 py-0.5 text-[0.66rem] font-semibold text-muted">
      {label}
    </span>
  );
}

function updateGroups(
  current: ReaderSettingsState,
  groups: ReaderAnnotationVisibilityGroups,
): ReaderSettingsState {
  return withCustomReadingMode(current, {
    annotationVisibilityGroups: groups,
  });
}

function updateField<K extends keyof ReaderSettingsState>(
  current: ReaderSettingsState,
  key: K,
  nextValue: ReaderSettingsState[K],
): ReaderSettingsState {
  return withCustomReadingMode(current, {
    [key]: nextValue,
  } as Partial<ReaderSettingsState>);
}

export function ReaderSettingsPanel({
  onChange,
  onClose,
  value,
}: ReaderSettingsPanelProps) {
  const activeModeLabel = modeOptions.find((option) => option.value === value.readingMode)?.label ?? "自定义";
  const activePaperLabel = paperThemes.find((theme) => theme.value === value.readerPaperTheme)?.label ?? "暖纸";

  function switchMode(nextMode: Exclude<ReadingMode, "custom">) {
    const preset = MODE_PRESETS[nextMode];
    onChange({
      ...value,
      ...preset,
      readingMode: nextMode,
    });
  }

  return (
    <section className="reader-tool-panel reader-settings-panel flex w-full flex-col overflow-hidden md:w-[29rem]">
      <div className="flex items-start justify-between gap-3 border-b border-hairline px-4 py-3.5">
        <div>
          <div className="flex items-center gap-2">
            <Type aria-hidden="true" className="h-4 w-4 text-lens-blue" />
            <h2 className="text-base font-semibold text-ink">阅读显示</h2>
          </div>
          <div className="mt-1.5">
            <span className="rounded-pill border border-lens-blue/12 bg-lens-blue-soft/55 px-2 py-0.5 text-[0.66rem] font-semibold text-lens-blue">
              即时生效
            </span>
          </div>
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
              <BookOpen aria-hidden="true" className="h-3.5 w-3.5" />
              阅读预设
            </legend>
            {activeValueChip(activeModeLabel)}
          </div>
          <div className={segmentContainerClass()}>
            {modeOptions.map((option) => {
              const active = value.readingMode === option.value;
              const disabled = option.value === "custom";
              return (
                <button
                  key={option.value}
                  type="button"
                  disabled={disabled}
                  className={`${optionClass(active)} flex-1 text-center ${disabled ? "cursor-default opacity-100" : ""}`}
                  onClick={() => {
                    if (!disabled) {
                      switchMode(option.value as Exclude<ReadingMode, "custom">);
                    }
                  }}
                >
                  <span className="block text-sm font-semibold">{option.label}</span>
                </button>
              );
            })}
          </div>
        </fieldset>

        <fieldset className={sectionClass()}>
          <div className={sectionHeaderClass()}>
            <legend className={sectionLabelClass()}>
              {value.translationDisplay === "visible" ? (
                <Eye aria-hidden="true" className="h-3.5 w-3.5" />
              ) : (
                <EyeOff aria-hidden="true" className="h-3.5 w-3.5" />
              )}
              译文
            </legend>
            {activeValueChip(
              value.translationDisplay === "visible"
                ? "显示"
                : value.translationDisplay === "muted"
                  ? "淡显"
                  : "隐藏",
            )}
          </div>
          <div className={segmentContainerClass()}>
            {([
              { value: "visible" as TranslationDisplay, label: "显示" },
              { value: "muted" as TranslationDisplay, label: "淡显" },
              { value: "hidden" as TranslationDisplay, label: "隐藏" },
            ]).map((option) => (
              <button
                key={option.value}
                type="button"
                className={`${optionClass(value.translationDisplay === option.value)} flex-1 text-center`}
                onClick={() => onChange(updateField(value, "translationDisplay", option.value))}
              >
                <span className="block text-sm font-semibold">{option.label}</span>
              </button>
            ))}
          </div>
        </fieldset>

        <section className={`${sectionClass()} grid gap-3 sm:grid-cols-3`}>
          <fieldset className="sm:border-r sm:border-hairline/65 sm:pr-3">
            <div className={sectionHeaderClass()}>
              <legend className={sectionLabelClass()}>
                <Type aria-hidden="true" className="h-3.5 w-3.5" />
                字号
              </legend>
            </div>
            <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-1">
              {([
                { value: "compact", label: "小" },
                { value: "normal", label: "中" },
                { value: "large", label: "大" },
                { value: "xlarge", label: "特大" },
              ] satisfies Array<{ value: ReaderFontSize; label: string }>).map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={chipClass(value.fontSize === option.value)}
                  onClick={() => onChange(updateField(value, "fontSize", option.value))}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset className="sm:border-r sm:border-hairline/65 sm:px-3">
            <div className={sectionHeaderClass()}>
              <legend className={sectionLabelClass()}>
                <Layers3 aria-hidden="true" className="h-3.5 w-3.5" />
                行距
              </legend>
            </div>
            <div className="grid grid-cols-3 gap-1.5 sm:grid-cols-1">
              {([
                { value: "compact", label: "紧" },
                { value: "calm", label: "中" },
                { value: "roomy", label: "舒" },
              ] satisfies Array<{ value: ReadingDensity; label: string }>).map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={chipClass(value.density === option.value)}
                  onClick={() => onChange(updateField(value, "density", option.value))}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset className="sm:pl-3">
            <div className={sectionHeaderClass()}>
              <legend className={sectionLabelClass()}>
                <AlignLeft aria-hidden="true" className="h-3.5 w-3.5" />
                版心
              </legend>
            </div>
            <div className="grid grid-cols-3 gap-1.5 sm:grid-cols-1">
              {([
                { value: "narrow", label: "窄" },
                { value: "standard", label: "中" },
                { value: "wide", label: "宽" },
              ] satisfies Array<{ value: ReaderColumnWidth; label: string }>).map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={chipClass(value.columnWidth === option.value)}
                  onClick={() => onChange(updateField(value, "columnWidth", option.value))}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </fieldset>
        </section>

        <fieldset className={sectionClass()}>
          <div className={sectionHeaderClass()}>
            <legend className={sectionLabelClass()}>
              <Palette aria-hidden="true" className="h-3.5 w-3.5" />
              纸面主题
            </legend>
            {activeValueChip(activePaperLabel)}
          </div>
          <div className="grid grid-cols-3 gap-1.5">
            {paperThemes.map((theme) => {
              const active = value.readerPaperTheme === theme.value;

              return (
                <button
                  key={theme.value}
                  type="button"
                  className={`${chipClass(active)} inline-flex items-center justify-center gap-2`}
                  onClick={() => onChange(updateField(value, "readerPaperTheme", theme.value))}
                >
                  <span className={`h-2 w-2 rounded-full ${theme.dotClassName}`} />
                  <span>{theme.label}</span>
                </button>
              );
            })}
          </div>
        </fieldset>

        <fieldset className={sectionClass()}>
          <div className={sectionHeaderClass()}>
            <legend className={sectionLabelClass()}>
              <Highlighter aria-hidden="true" className="h-3.5 w-3.5" />
              标注层
            </legend>
          </div>
          <div className="grid gap-1.5 sm:grid-cols-3">
            {([
              { key: "lexical", label: "词汇 / 短语" },
              { key: "analysis", label: "语法 / 逻辑" },
              { key: "userAssets", label: "我的高亮与笔记" },
            ] satisfies Array<{ key: keyof ReaderAnnotationVisibilityGroups; label: string }>).map((group) => {
              const enabled = value.annotationVisibilityGroups[group.key];
              return (
                <button
                  key={group.key}
                  type="button"
                  className={`${chipClass(enabled)} inline-flex items-center justify-center gap-2 text-center`}
                  onClick={() =>
                    onChange(
                      updateGroups(value, {
                        ...value.annotationVisibilityGroups,
                        [group.key]: !enabled,
                      }),
                    )
                  }
                >
                  <span
                    className={`h-2 w-2 rounded-full transition-colors ${enabled ? "bg-structure-green" : "bg-hairline"}`}
                  />
                  {group.label}
                </button>
              );
            })}
          </div>
        </fieldset>
      </div>
    </section>
  );
}
