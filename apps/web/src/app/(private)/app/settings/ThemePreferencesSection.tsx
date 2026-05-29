"use client";

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
    description: "默认的母主题",
  },
  {
    value: "light",
    label: "浅色",
    english: "Light",
    description: "功能化的工作面",
  },
  {
    value: "dark",
    label: "深色",
    english: "Dark",
    description: "夜读结构",
  },
];

export function ThemePreferencesSection() {
  const { themeName, setThemeName } = useAppearance();

  return (
    <div className="grid gap-6 sm:grid-cols-2 md:grid-cols-3 pt-4">
      {themeOptions.map((option) => {
        const active = themeName === option.value;
        return (
          <button
            key={option.value}
            type="button"
            aria-label={`${option.label} ${option.english}。${option.description}`}
            aria-pressed={active}
            className={`group flex flex-col text-left transition-all`}
            onClick={() => setThemeName(option.value)}
          >
            <div
              aria-hidden="true"
              className={`w-full theme-preview-surface theme-preview-surface--${option.value} rounded-[1rem] border px-3 py-3 shadow-[var(--app-panel-shadow-quiet)] transition-all duration-300 ${
                active 
                  ? "border-lens-blue/40 ring-2 ring-lens-blue/20 ring-offset-2 ring-offset-background" 
                  : "border-hairline/60 hover:border-hairline hover:shadow-md"
              }`}
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
              <div className="theme-preview-surface__chip mt-3 opacity-90">
                {option.english}
              </div>
            </div>
            <div className="mt-4 px-1">
              <div className="flex items-baseline gap-2">
                <span className={`text-sm font-semibold transition-colors ${active ? "text-lens-blue" : "text-ink"}`}>{option.label}</span>
                <span className={`text-[0.65rem] font-medium uppercase tracking-[0.08em] transition-colors ${active ? "text-lens-blue/70" : "text-muted"}`}>
                  {option.english}
                </span>
              </div>
              <p className="mt-1 text-[0.7rem] text-subtle">{option.description}</p>
            </div>
          </button>
        );
      })}
    </div>
  );
}
