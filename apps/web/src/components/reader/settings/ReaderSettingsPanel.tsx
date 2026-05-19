"use client";

import { Type, X } from "lucide-react";
import {
  type ReaderAnnotationVisibilityGroups,
  type ReaderColumnWidth,
  type ReaderFontSize,
  type ReaderSettingsState,
  type ReaderTheme,
  type ReadingDensity,
} from "./shared";

interface ReaderSettingsPanelProps {
  value: ReaderSettingsState;
  onChange: (next: ReaderSettingsState) => void;
  onClose?: () => void;
}

function optionClass(active: boolean) {
  return `focus-ring min-h-11 rounded-[0.9rem] border px-3.5 py-2 text-left transition-[background-color,border-color,color,box-shadow] ${
    active
      ? "border-lens-blue/18 bg-[linear-gradient(180deg,rgba(255,255,255,0.9),rgba(234,241,255,0.94))] text-ink shadow-[0_8px_18px_rgba(37,99,235,0.08),inset_0_1px_0_rgba(255,255,255,0.75)]"
      : "border-transparent bg-transparent text-ink-soft hover:bg-[rgba(255,255,255,0.58)] hover:text-ink"
  }`;
}

function sectionClass() {
  return "rounded-[1rem] border border-hairline/90 bg-[linear-gradient(180deg,rgba(255,255,255,0.58),rgba(248,245,238,0.8))] px-4 py-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.65)]";
}

function updateGroups(
  current: ReaderSettingsState,
  groups: ReaderAnnotationVisibilityGroups,
): ReaderSettingsState {
  return {
    ...current,
    annotationVisibilityGroups: groups,
  };
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
  onChange,
  onClose,
  value,
}: ReaderSettingsPanelProps) {
  return (
    <section className="reader-tool-panel flex max-h-[min(54vh,30rem)] flex-col overflow-hidden">
      <div className="flex items-start justify-between gap-3 border-b border-hairline px-5 py-3">
        <div>
          <div className="flex items-center gap-2">
            <Type aria-hidden="true" className="h-4 w-4 text-lens-blue" />
            <h2 className="text-base font-semibold text-ink">阅读显示</h2>
          </div>
          <p className="mt-1 text-xs leading-5 text-muted">只影响当前浏览器里的 Reader 阅读体验。</p>
        </div>
        {onClose ? (
          <button
            type="button"
            className="focus-ring inline-flex h-10 w-10 items-center justify-center rounded-[0.95rem] border border-hairline bg-[linear-gradient(180deg,rgba(255,255,255,0.92),rgba(249,247,241,0.98))] text-muted shadow-[0_8px_18px_rgba(17,17,17,0.04),inset_0_1px_0_rgba(255,255,255,0.76)] transition-colors hover:border-muted hover:text-ink"
            onClick={onClose}
            aria-label="关闭阅读设置"
          >
            <X aria-hidden="true" className="h-4 w-4" />
          </button>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain px-5 py-4">
        <fieldset className={sectionClass()}>
          <legend className="text-[0.72rem] font-semibold tracking-[0.14em] text-muted">译文</legend>
          <p className="mt-1 text-xs leading-5 text-muted">控制中文解释是否常显。</p>
          <div className="mt-3 inline-flex w-full flex-wrap gap-1 rounded-[1rem] border border-hairline/90 bg-[linear-gradient(180deg,rgba(244,241,233,0.72),rgba(251,249,244,0.9))] p-1 shadow-[inset_0_1px_0_rgba(255,255,255,0.55)]">
            {[
              { value: true, label: "显示译文", hint: "句后保留中文解释" },
              { value: false, label: "原文模式", hint: "只留下英文原文" },
            ].map((option) => (
              <button
                key={String(option.value)}
                type="button"
                className={`${optionClass(value.showTranslation === option.value)} flex-1`}
                onClick={() => onChange(updateField(value, "showTranslation", option.value))}
              >
                <span className="block text-sm font-semibold">{option.label}</span>
                <span className="mt-1 block text-[0.68rem] leading-none text-subtle">{option.hint}</span>
              </button>
            ))}
          </div>
        </fieldset>

        <fieldset className={sectionClass()}>
          <legend className="text-[0.72rem] font-semibold tracking-[0.14em] text-muted">字号与行距</legend>
          <div className="mt-3 grid gap-3">
            <div>
              <div className="mb-2 text-[0.68rem] font-semibold tracking-[0.08em] text-subtle">字号</div>
              <div className="inline-flex w-full flex-wrap gap-1 rounded-[1rem] border border-hairline/90 bg-[linear-gradient(180deg,rgba(244,241,233,0.72),rgba(251,249,244,0.9))] p-1 shadow-[inset_0_1px_0_rgba(255,255,255,0.55)]">
                {([
                  { value: "compact", label: "小", hint: "更紧凑" },
                  { value: "normal", label: "中", hint: "默认阅读" },
                  { value: "large", label: "大", hint: "更宽松" },
                ] satisfies Array<{ value: ReaderFontSize; label: string; hint: string }>).map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    className={`${optionClass(value.fontSize === option.value)} flex-1`}
                    onClick={() => onChange(updateField(value, "fontSize", option.value))}
                  >
                    <span className="block text-sm font-semibold">{option.label}</span>
                    <span className="mt-1 block text-[0.68rem] leading-none text-subtle">{option.hint}</span>
                  </button>
                ))}
              </div>
            </div>

            <div>
              <div className="mb-2 text-[0.68rem] font-semibold tracking-[0.08em] text-subtle">行距</div>
              <div className="inline-flex w-full flex-wrap gap-1 rounded-[1rem] border border-hairline/90 bg-[linear-gradient(180deg,rgba(244,241,233,0.72),rgba(251,249,244,0.9))] p-1 shadow-[inset_0_1px_0_rgba(255,255,255,0.55)]">
                {([
                  { value: "calm", label: "标准", hint: "日常阅读" },
                  { value: "roomy", label: "舒展", hint: "留出更多呼吸" },
                ] satisfies Array<{ value: ReadingDensity; label: string; hint: string }>).map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    className={`${optionClass(value.density === option.value)} flex-1`}
                    onClick={() => onChange(updateField(value, "density", option.value))}
                  >
                    <span className="block text-sm font-semibold">{option.label}</span>
                    <span className="mt-1 block text-[0.68rem] leading-none text-subtle">{option.hint}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </fieldset>

        <fieldset className={sectionClass()}>
          <legend className="text-[0.72rem] font-semibold tracking-[0.14em] text-muted">版心宽度</legend>
          <p className="mt-1 text-xs leading-5 text-muted">控制正文最大列宽，不影响词典或 Ask 区域布局。</p>
          <div className="mt-3 inline-flex w-full flex-wrap gap-1 rounded-[1rem] border border-hairline/90 bg-[linear-gradient(180deg,rgba(244,241,233,0.72),rgba(251,249,244,0.9))] p-1 shadow-[inset_0_1px_0_rgba(255,255,255,0.55)]">
            {([
              { value: "narrow", label: "窄", hint: "更聚焦" },
              { value: "standard", label: "中", hint: "默认版心" },
              { value: "wide", label: "宽", hint: "更多横向空间" },
            ] satisfies Array<{ value: ReaderColumnWidth; label: string; hint: string }>).map((option) => (
              <button
                key={option.value}
                type="button"
                className={`${optionClass(value.columnWidth === option.value)} flex-1`}
                onClick={() => onChange(updateField(value, "columnWidth", option.value))}
              >
                <span className="block text-sm font-semibold">{option.label}</span>
                <span className="mt-1 block text-[0.68rem] leading-none text-subtle">{option.hint}</span>
              </button>
            ))}
          </div>
        </fieldset>

        <fieldset className={sectionClass()}>
          <legend className="text-[0.72rem] font-semibold tracking-[0.14em] text-muted">阅读背景</legend>
          <p className="mt-1 text-xs leading-5 text-muted">选择纸面基调，保持正文版心不变。</p>
          <div className="mt-3 inline-flex w-full flex-wrap gap-1 rounded-[1rem] border border-hairline/90 bg-[linear-gradient(180deg,rgba(244,241,233,0.72),rgba(251,249,244,0.9))] p-1 shadow-[inset_0_1px_0_rgba(255,255,255,0.55)]">
            {([
              { value: "paper", label: "纸张", hint: "暖纸质感" },
              { value: "white", label: "纯白", hint: "更干净" },
              { value: "green", label: "护眼", hint: "更柔和" },
            ] satisfies Array<{ value: ReaderTheme; label: string; hint: string }>).map((option) => (
              <button
                key={option.value}
                type="button"
                className={`${optionClass(value.theme === option.value)} flex-1`}
                onClick={() => onChange(updateField(value, "theme", option.value))}
              >
                <span className="block text-sm font-semibold">{option.label}</span>
                <span className="mt-1 block text-[0.68rem] leading-none text-subtle">{option.hint}</span>
              </button>
            ))}
          </div>
        </fieldset>

        <fieldset className={sectionClass()}>
          <legend className="text-[0.72rem] font-semibold tracking-[0.14em] text-muted">标注显示分组</legend>
          <p className="mt-1 text-xs leading-5 text-muted">控制不同类型的机器标注和用户资产在正文里的常态可见层。</p>
          <div className="mt-3 grid gap-2">
            {([
              {
                key: "lexical",
                label: "词汇 / 短语",
                hint: "词汇、短语、语境义等 inline 标注",
              },
              {
                key: "analysis",
                label: "语法 / 逻辑",
                hint: "语法、术语、逻辑和句后分析块",
              },
              {
                key: "userAssets",
                label: "用户资产",
                hint: "高亮、笔记 slip、收藏 marker",
              },
            ] satisfies Array<{ key: keyof ReaderAnnotationVisibilityGroups; label: string; hint: string }>).map((group) => {
              const enabled = value.annotationVisibilityGroups[group.key];
              return (
                <button
                  key={group.key}
                  type="button"
                  className={`${optionClass(enabled)} w-full`}
                  onClick={() =>
                    onChange(
                      updateGroups(value, {
                        ...value.annotationVisibilityGroups,
                        [group.key]: !enabled,
                      }),
                    )
                  }
                >
                  <span className="block text-sm font-semibold">{group.label}</span>
                  <span className="mt-1 block text-[0.68rem] leading-none text-subtle">{group.hint}</span>
                </button>
              );
            })}
          </div>
        </fieldset>
      </div>
    </section>
  );
}
