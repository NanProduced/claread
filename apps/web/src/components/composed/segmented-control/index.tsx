"use client";

import { cn } from "@/lib/cn";

export interface SegmentedControlOption<T extends string> {
  value: T;
  label: string;
  description?: string;
  disabled?: boolean;
}

export interface SegmentedControlProps<T extends string> {
  value: T;
  onValueChange: (value: T) => void;
  options: ReadonlyArray<SegmentedControlOption<T>>;
  label?: string;
  className?: string;
}

export function SegmentedControl<T extends string>({
  value,
  onValueChange,
  options,
  label,
  className,
}: SegmentedControlProps<T>) {
  return (
    <fieldset className={cn("min-w-0", className)}>
      {label ? <legend className="mb-2 text-sm font-medium text-ink">{label}</legend> : null}
      <div className="inline-flex min-h-11 max-w-full flex-wrap items-center overflow-hidden rounded-[var(--cl-radius-control-sm)] border border-hairline bg-surface-raised/60 p-0.5">
        {options.map((option) => {
          const active = option.value === value;
          return (
            <button
              key={option.value}
              type="button"
              className={cn(
                "focus-ring relative flex min-h-10 items-center justify-center rounded-[calc(var(--cl-radius-control-sm)-2px)] px-3 text-sm font-medium transition-colors",
                active
                  ? "bg-surface text-ink"
                  : "text-muted-foreground hover:bg-[var(--interactive-quiet-hover)] hover:text-ink",
                option.disabled && "cursor-not-allowed opacity-45",
              )}
              disabled={option.disabled}
              onClick={() => onValueChange(option.value)}
              aria-pressed={active}
              title={option.description}
            >
              {option.label}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}