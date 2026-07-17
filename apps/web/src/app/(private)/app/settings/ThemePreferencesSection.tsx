"use client";

import { cn } from "@/lib/cn";
import { useAppearance } from "@/components/providers/appearance-provider";
import type { ThemePreference } from "@/lib/appearance";

interface ThemeOption {
  value: ThemePreference;
  label: string;
}

const themeOptions: ThemeOption[] = [
  { value: "system", label: "跟随系统" },
  { value: "light", label: "浅色" },
  { value: "dark", label: "深色" },
];

export function ThemePreferencesSection() {
  const { themePreference, resolvedTheme, setThemePreference } = useAppearance();

  return (
    <fieldset className="space-y-2">
      <legend className="sr-only">主题偏好</legend>
      {themeOptions.map((option) => {
        const active = themePreference === option.value;

        return (
          <label
            key={option.value}
            className={cn(
              "flex min-h-11 cursor-pointer items-center gap-3 rounded-lg border px-3 py-2 transition-colors focus-within:ring-2 focus-within:ring-lens-blue focus-within:ring-offset-2 focus-within:ring-offset-background",
              active
                ? "border-lens-blue bg-surface-canvas text-ink"
                : "border-hairline/60 bg-background text-muted-foreground hover:border-hairline hover:text-ink",
            )}
          >
            <input
              type="radio"
              name="theme-preference"
              value={option.value}
              checked={active}
              onChange={() => setThemePreference(option.value)}
              className="sr-only"
            />
            <span
              className={cn(
                "flex size-4 items-center justify-center rounded-full border",
                active ? "border-lens-blue" : "border-muted-foreground",
              )}
              aria-hidden="true"
            >
              {active && <span className="size-2 rounded-full bg-lens-blue" />}
            </span>
            <span className="text-sm font-medium">{option.label}</span>
          </label>
        );
      })}
      {themePreference === "system" && (
        <p className="text-xs text-muted-foreground">
          当前显示：{resolvedTheme === "dark" ? "深色" : "浅色"}
        </p>
      )}
    </fieldset>
  );
}
