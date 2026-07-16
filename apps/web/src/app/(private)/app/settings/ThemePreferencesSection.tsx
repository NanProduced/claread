"use client";

import { useAppearance } from "@/components/providers/appearance-provider";
import type { ThemePreference } from "@/lib/appearance";

interface ThemeOption {
  value: ThemePreference;
  label: string;
  english: string;
  description: string;
  previewable: boolean;
}

const themeOptions: ThemeOption[] = [
  {
    value: "system",
    label: "跟随系统",
    english: "Follow system",
    description: "跟随操作系统的浅色 / 深色模式自动切换",
    previewable: false,
  },
  {
    value: "light",
    label: "浅色",
    english: "Light",
    description: "中性偏冷的工作面",
    previewable: true,
  },
  {
    value: "dark",
    label: "深色",
    english: "Dark",
    description: "夜读与低光环境",
    previewable: true,
  },
];

export function ThemePreferencesSection() {
  const { themePreference, resolvedTheme, setThemePreference } = useAppearance();

  const systemDescription =
    resolvedTheme === "dark"
      ? "当前系统在深色模式 — 跟随系统实际呈现为深色界面。"
      : "当前系统在浅色模式 — 跟随系统实际呈现为浅色界面。";

  return (
    <div className="grid gap-6 sm:grid-cols-2 pt-4">
      {themeOptions.map((option) => {
        const active = themePreference === option.value;
        const showSystemCard = option.value === "system";

        if (!option.previewable) {
          return (
            <button
              key={option.value}
              type="button"
              aria-label={`${option.label} ${option.english}。${option.description}`}
              aria-pressed={active}
              className={`group flex flex-col text-left transition-all`}
              onClick={() => setThemePreference(option.value)}
            >
              <div
                aria-hidden="true"
                className={`flex w-full min-h-[10.25rem] flex-col justify-between rounded-[1rem] border px-5 py-5 transition-all duration-300 ${
                  active
                    ? "border-lens-blue/40 ring-2 ring-lens-blue/20 ring-offset-2 ring-offset-background bg-surface-canvas"
                    : "border-hairline/60 hover:border-hairline hover:shadow-md bg-surface-canvas"
                }`}
              >
                <div className="text-xs font-semibold uppercase tracking-[0.08em] text-text-secondary">
                  Follow system
                </div>
                <div className="space-y-1.5">
                  <div className="text-[1.25rem] font-semibold leading-tight text-text-primary">
                    {resolvedTheme === "dark" ? "深色界面" : "浅色界面"}
                  </div>
                  <p className="text-[0.72rem] leading-5 text-subtle">
                    {systemDescription}
                  </p>
                </div>
              </div>
              <div className="mt-4 px-1">
                <div className="flex items-baseline gap-2">
                  <span
                    className={`text-sm font-semibold transition-colors ${
                      active ? "text-lens-blue" : "text-ink"
                    }`}
                  >
                    {option.label}
                  </span>
                  <span
                    className={`text-[0.65rem] font-medium tracking-[0.08em] transition-colors ${
                      active ? "text-lens-blue/70" : "text-muted-foreground"
                    }`}
                  >
                    {option.english}
                  </span>
                </div>
                <p className="mt-1 text-[0.7rem] text-subtle">{option.description}</p>
              </div>
            </button>
          );
        }

        const previewThemeClass = option.value === "dark" ? "dark" : "light";

        return (
          <button
            key={option.value}
            type="button"
            aria-label={`${option.label} ${option.english}。${option.description}`}
            aria-pressed={active}
            className={`group flex flex-col text-left transition-all`}
            onClick={() => setThemePreference(option.value)}
          >
            <div
              aria-hidden="true"
              className={`theme-preview-surface theme-preview-surface--${previewThemeClass} w-full rounded-[1rem] border px-3 py-3 shadow-[var(--app-panel-shadow-quiet)] transition-all duration-300 ${
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
                <span
                  className={`text-sm font-semibold transition-colors ${
                    active ? "text-lens-blue" : "text-ink"
                  }`}
                >
                  {option.label}
                </span>
                <span
                  className={`text-[0.65rem] font-medium tracking-[0.08em] transition-colors ${
                    active ? "text-lens-blue/70" : "text-muted-foreground"
                  }`}
                >
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
