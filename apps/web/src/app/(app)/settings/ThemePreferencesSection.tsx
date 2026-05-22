"use client";

import { useEffect, useState } from "react";
import { LaptopMinimal, MoonStar, Palette, SunMedium } from "lucide-react";
import { SegmentedControl } from "@/components/composed";
import { useAppearance } from "@/components/providers/appearance-provider";
import {
  persistDefaultReaderPaperTheme,
  readStoredDefaultReaderPaperTheme,
  type ReaderPaperTheme,
} from "@/components/reader/settings/shared";

const appearanceOptions = [
  { value: "light", label: "浅色", description: "暖纸感白天界面" },
  { value: "dark", label: "深色", description: "暖炭灰夜间界面" },
  { value: "system", label: "跟随系统", description: "自动匹配系统明暗" },
] as const;

const paperOptions: Array<{
  value: ReaderPaperTheme;
  label: string;
  hint: string;
  paperClass: string;
  borderColor: string;
  inkColor: string;
}> = [
  {
    value: "warm",
    label: "暖纸",
    hint: "默认精读风格",
    paperClass: "reading-paper",
    borderColor: "#ded3bf",
    inkColor: "#302c25",
  },
  {
    value: "cool",
    label: "冷纸",
    hint: "更干净，更接近校对",
    paperClass: "reading-paper-cool",
    borderColor: "#d8d8d4",
    inkColor: "#2f3136",
  },
  {
    value: "sage",
    label: "鼠尾草",
    hint: "适合标注更密的文章",
    paperClass: "reading-paper-sage",
    borderColor: "#b7c9be",
    inkColor: "#2c372d",
  },
];

export function ThemePreferencesSection() {
  const { appearance, resolvedAppearance, isSystem, setAppearance } = useAppearance();
  const [readerPaperTheme, setReaderPaperTheme] = useState<ReaderPaperTheme>("warm");

  useEffect(() => {
    setReaderPaperTheme(readStoredDefaultReaderPaperTheme());
  }, []);

  function selectReaderPaperTheme(next: ReaderPaperTheme) {
    setReaderPaperTheme(next);
    persistDefaultReaderPaperTheme(next);
  }

  return (
    <div className="space-y-6">
      <div className="grid gap-3 border-t border-hairline py-5">
        <div className="flex items-start gap-3">
          <div className="app-panel-surface flex h-11 w-11 shrink-0 items-center justify-center rounded-[0.95rem] border border-hairline">
            {appearance === "light" ? (
              <SunMedium aria-hidden="true" className="h-5 w-5 text-lens-blue" />
            ) : appearance === "dark" ? (
              <MoonStar aria-hidden="true" className="h-5 w-5 text-lens-blue" />
            ) : (
              <LaptopMinimal aria-hidden="true" className="h-5 w-5 text-lens-blue" />
            )}
          </div>
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-ink">外观</h3>
            <p className="mt-1 text-xs leading-5 text-muted">
              控制导航、功能页、弹层和 Reader 外部壳层。
              {isSystem ? ` 当前按系统显示为${resolvedAppearance === "dark" ? "深色" : "浅色"}。` : null}
            </p>
          </div>
        </div>
        <SegmentedControl
          value={appearance}
          onValueChange={(next) => setAppearance(next)}
          options={appearanceOptions.map((option) => ({
            value: option.value,
            label: option.label,
            description: option.description,
          }))}
        />
      </div>

      <div className="grid gap-3 border-t border-hairline py-5">
        <div className="flex items-start gap-3">
          <div className="app-panel-surface flex h-11 w-11 shrink-0 items-center justify-center rounded-[0.95rem] border border-hairline">
            <Palette aria-hidden="true" className="h-5 w-5 text-lens-blue" />
          </div>
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-ink">Reader 默认纸面</h3>
            <p className="mt-1 text-xs leading-5 text-muted">
              影响新打开 Reader 时的初始纸面风格；进入文章后仍可在“阅读显示”里即时微调。
            </p>
          </div>
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          {paperOptions.map((option) => {
            const active = readerPaperTheme === option.value;
            return (
              <button
                key={option.value}
                type="button"
                className={`focus-ring rounded-[1rem] border p-3 text-left transition-[border-color,box-shadow,transform] ${
                  active
                    ? "border-lens-blue/22 bg-lens-blue-soft/25 shadow-[0_10px_22px_rgba(37,99,235,0.08)]"
                    : "border-hairline/80 bg-background/40 hover:border-[var(--app-control-border-hover)]"
                }`}
                onClick={() => selectReaderPaperTheme(option.value)}
              >
                <div
                  className={`${option.paperClass} rounded-[0.9rem] border px-3 py-3 shadow-[var(--app-panel-shadow-quiet)]`}
                  style={{ borderColor: option.borderColor }}
                >
                  <p className="reader-serif text-[0.82rem] leading-6" style={{ color: option.inkColor }}>
                    These words will open on a quieter page.
                  </p>
                  <div className="mt-3 rounded-[0.7rem] border border-hairline/70 bg-background/70 px-2 py-1 text-[0.66rem] text-muted">
                    译文与句后卡随纸面联动
                  </div>
                </div>
                <div className="mt-3">
                  <div className="text-sm font-semibold text-ink">{option.label}</div>
                  <div className="mt-1 text-[0.68rem] leading-5 text-subtle">{option.hint}</div>
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
