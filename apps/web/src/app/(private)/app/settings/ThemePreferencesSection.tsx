"use client";

import { Palette } from "lucide-react";
import { useAppearance } from "@/components/providers/appearance-provider";
import type { ThemeName } from "@/lib/appearance";

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
    description: "Claread 的默认母主题，纸感最完整。",
  },
  {
    value: "light",
    label: "浅色",
    english: "Light",
    description: "更功能化的工作面，暖意更淡。",
  },
  {
    value: "dark",
    label: "深色",
    english: "Dark",
    description: "夜读版本，保留 Claread 的结构感。",
  },
];

export function ThemePreferencesSection() {
  const { themeName, setThemeName } = useAppearance();

  return (
    <div className="grid gap-3 border-t border-hairline py-5">
      <div className="flex items-start gap-3">
        <div className="app-panel-surface flex h-11 w-11 shrink-0 items-center justify-center rounded-[0.95rem] border border-hairline">
          <Palette aria-hidden="true" className="h-5 w-5 text-lens-blue" />
        </div>
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-ink">主题</h3>
          <p className="mt-1 text-xs leading-5 text-muted">
            全站与 Reader 使用同一套主题系统。Reader 内也可直接切换，仍然与全站保持同步。
          </p>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-3">
        {themeOptions.map((option) => {
          const active = themeName === option.value;
          return (
            <button
              key={option.value}
              type="button"
              aria-label={`${option.label} ${option.english}。${option.description}`}
              aria-pressed={active}
              className={`focus-ring rounded-[1rem] border p-3 text-left transition-[border-color,box-shadow,transform] ${
                active
                  ? "border-lens-blue/22 bg-lens-blue-soft/25 shadow-[0_10px_22px_rgba(37,99,235,0.08)]"
                  : "border-hairline/80 bg-background/40 hover:border-[var(--app-control-border-hover)]"
              }`}
              onClick={() => setThemeName(option.value)}
            >
              <div
                aria-hidden="true"
                className={`theme-preview-surface theme-preview-surface--${option.value} rounded-[0.9rem] border border-hairline px-3 py-3 shadow-[var(--app-panel-shadow-quiet)]`}
              >
                <div className="theme-preview-surface__eyebrow">
                  <span className="theme-preview-surface__eyebrow-label">Claread</span>
                  <span className="theme-preview-surface__eyebrow-dot" />
                </div>
                <div className="mt-3 space-y-2">
                  <div className="theme-preview-surface__line theme-preview-surface__line--title" />
                  <div className="theme-preview-surface__line theme-preview-surface__line--body" />
                  <div className="theme-preview-surface__line theme-preview-surface__line--body theme-preview-surface__line--short" />
                </div>
                <div className="theme-preview-surface__chip mt-3">
                  Reader 同步
                </div>
              </div>
              <div className="mt-3">
                <div className="flex items-baseline gap-2">
                  <span className="text-sm font-semibold text-ink">{option.label}</span>
                  <span className="text-[0.72rem] font-medium uppercase tracking-[0.08em] text-subtle">
                    {option.english}
                  </span>
                </div>
                <div className="mt-1 text-[0.68rem] leading-5 text-subtle">{option.description}</div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
