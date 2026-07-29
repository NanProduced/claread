"use client";

import { useEffect, useRef, type KeyboardEvent } from "react";

import { cn } from "@/lib/cn";
import {
  DEFAULT_READING_VARIANT_BY_GOAL,
  READING_GOAL_OPTIONS,
  READING_VARIANT_OPTIONS,
  getReadingVariantOption,
  type ReaderRecordReadingGoal,
  type ReaderRecordReadingVariant,
  type ReadingDefaultState,
} from "@/lib/reading-defaults";

const READING_GOAL_ORDER: readonly ReaderRecordReadingGoal[] = [
  "daily_reading",
  "exam",
];

export interface ReadingPlanFieldsProps {
  value: ReadingDefaultState;
  onValueChange: (value: ReadingDefaultState) => void;
  layout?: "compact" | "settings";
  disabled?: boolean;
  idPrefix?: string;
}

export function ReadingPlanFields({
  value,
  onValueChange,
  layout = "compact",
  disabled = false,
  idPrefix = "reading-plan",
}: ReadingPlanFieldsProps) {
  const goalButtonRefs = useRef<
    Partial<Record<ReaderRecordReadingGoal, HTMLButtonElement | null>>
  >({});
  const variantButtonRefs = useRef<
    Partial<Record<ReaderRecordReadingVariant, HTMLButtonElement | null>>
  >({});
  const rememberedVariants = useRef<
    Record<ReaderRecordReadingGoal, ReaderRecordReadingVariant>
  >({
    daily_reading: DEFAULT_READING_VARIANT_BY_GOAL.daily_reading,
    exam: DEFAULT_READING_VARIANT_BY_GOAL.exam,
  });

  useEffect(() => {
    rememberedVariants.current[value.readingGoal] = value.readingVariant;
  }, [value.readingGoal, value.readingVariant]);

  const selectedVariant = getReadingVariantOption(
    value.readingGoal,
    value.readingVariant,
  );
  const variantOptions = READING_VARIANT_OPTIONS[value.readingGoal];
  const variantLabel = "阅读方案";

  function selectGoal(nextGoal: ReaderRecordReadingGoal) {
    if (nextGoal === value.readingGoal) return;
    onValueChange({
      readingGoal: nextGoal,
      readingVariant: rememberedVariants.current[nextGoal],
    });
  }

  function handleGoalKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const direction =
      event.key === "ArrowRight" || event.key === "ArrowDown"
        ? 1
        : event.key === "ArrowLeft" || event.key === "ArrowUp"
          ? -1
          : 0;
    if (!direction) return;

    event.preventDefault();
    const currentIndex = READING_GOAL_ORDER.indexOf(value.readingGoal);
    const nextIndex =
      (currentIndex + direction + READING_GOAL_ORDER.length) %
      READING_GOAL_ORDER.length;
    const nextGoal = READING_GOAL_ORDER[nextIndex];
    selectGoal(nextGoal);
    goalButtonRefs.current[nextGoal]?.focus();
  }

  function selectVariant(nextVariant: ReaderRecordReadingVariant) {
    const allowed = variantOptions.some(
      (option) => option.value === nextVariant,
    );
    if (!allowed) return;

    rememberedVariants.current[value.readingGoal] = nextVariant;
    onValueChange({
      readingGoal: value.readingGoal,
      readingVariant: nextVariant,
    });
  }

  function handleVariantKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const direction =
      event.key === "ArrowRight" || event.key === "ArrowDown"
        ? 1
        : event.key === "ArrowLeft" || event.key === "ArrowUp"
          ? -1
          : 0;
    if (!direction) return;

    event.preventDefault();
    const currentIndex = variantOptions.findIndex(
      (option) => option.value === value.readingVariant,
    );
    const nextIndex =
      (currentIndex + direction + variantOptions.length) %
      variantOptions.length;
    const nextVariant = variantOptions[nextIndex].value;
    selectVariant(nextVariant);
    variantButtonRefs.current[nextVariant]?.focus();
  }

  const labelClass =
    "text-[13px] font-medium leading-none text-muted-foreground";

  const fieldClass =
    layout === "settings"
      ? "grid gap-2 border-b border-hairline py-4 first:pt-0 sm:grid-cols-[auto_1fr] sm:items-start sm:gap-6"
      : "space-y-2";

  const settingsLabelOffset = layout === "settings" ? "sm:pt-2.5" : "";

  const cellBase =
    "focus-ring grid min-h-9 place-items-center rounded-[var(--cl-radius-control-sm)] px-1 text-[13px] leading-none text-center transition-all duration-200 ease-out active:scale-[0.96] active:duration-75 disabled:cursor-not-allowed disabled:opacity-45 max-md:min-h-11 motion-reduce:transition-none motion-reduce:active:scale-100";

  const cellActive =
    "bg-surface-raised font-medium text-ink";

  const cellInactive =
    "text-muted-foreground hover:bg-surface-raised/50 hover:text-ink";

  return (
    <div
      className={cn(
        layout === "settings" ? "space-y-0" : "space-y-4",
      )}
    >
      <section className={fieldClass}>
        <h4
          id={`${idPrefix}-goal-label`}
          className={cn(labelClass, settingsLabelOffset)}
        >
          阅读目标
        </h4>

        <div
          role="radiogroup"
          aria-labelledby={`${idPrefix}-goal-label`}
          onKeyDown={handleGoalKeyDown}
          className="grid max-w-[11rem] grid-cols-2 gap-1"
        >
          {READING_GOAL_OPTIONS.map((option) => {
            const checked = option.value === value.readingGoal;
            return (
              <button
                key={option.value}
                ref={(node) => {
                  goalButtonRefs.current[option.value] = node;
                }}
                type="button"
                role="radio"
                aria-checked={checked}
                tabIndex={checked ? 0 : -1}
                disabled={disabled}
                onClick={() => selectGoal(option.value)}
                className={cn(cellBase, checked ? cellActive : cellInactive)}
              >
                {option.label}
              </button>
            );
          })}
        </div>
      </section>

      <section className={fieldClass}>
        <h4
          id={`${idPrefix}-variant-label`}
          className={cn(labelClass, settingsLabelOffset)}
        >
          {variantLabel}
        </h4>

        <div className="min-w-0">
          <div
            role="radiogroup"
            aria-labelledby={`${idPrefix}-variant-label`}
            onKeyDown={handleVariantKeyDown}
            className={cn(
              "grid min-h-[4.75rem] grid-cols-3 content-start gap-1 max-md:min-h-[5.75rem]",
              layout === "settings" && "max-w-[18rem]",
            )}
          >
            {variantOptions.map((option) => {
              const checked = option.value === value.readingVariant;
              return (
                <button
                  key={option.value}
                  ref={(node) => {
                    variantButtonRefs.current[option.value] = node;
                  }}
                  type="button"
                  role="radio"
                  aria-checked={checked}
                  tabIndex={checked ? 0 : -1}
                  disabled={disabled}
                  onClick={() => selectVariant(option.value)}
                  className={cn(cellBase, checked ? cellActive : cellInactive)}
                >
                  {option.label}
                </button>
              );
            })}
          </div>
          <p className="mt-2 text-xs leading-5 text-muted-foreground transition-opacity duration-200 motion-reduce:transition-none">
            {selectedVariant?.description}
          </p>
        </div>
      </section>
    </div>
  );
}
