"use client";

import { useAppearance } from "@/components/providers/appearance-provider";

const themeOptions = [
  { value: "system", label: "跟随系统" },
  { value: "light", label: "浅色" },
  { value: "dark", label: "深色" },
] as const;

export function ThemePreferencesSection() {
  const { themePreference, resolvedTheme, setThemePreference } = useAppearance();

  return (
    <div className="divide-y divide-hairline">
      <div className="flex min-h-16 items-center justify-between gap-6 py-4 first:pt-0">
        <div className="min-w-0">
          <label htmlFor="theme-preference" className="text-sm font-medium text-ink">
            主题
          </label>

        </div>
        <div className="shrink-0">
          <select
            id="theme-preference"
            value={themePreference}
            onChange={(event) =>
              setThemePreference(event.target.value as (typeof themeOptions)[number]["value"])
            }
            className="min-h-10 rounded-[var(--cl-radius-control-sm)] border border-hairline bg-surface px-3 text-sm text-ink outline-none transition-colors hover:bg-surface-raised focus-visible:ring-2 focus-visible:ring-lens-blue focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          >
            {themeOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          {themePreference === "system" ? (
            <p className="mt-1.5 text-right text-xs text-muted-foreground">
              当前：{resolvedTheme === "dark" ? "深色" : "浅色"}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}