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
      {label ? (
        <legend className="mb-4 text-[0.66rem] font-bold uppercase tracking-[0.2em] text-subtle">
          {label}
        </legend>
      ) : null}
      <div className="flex flex-wrap gap-2.5">
        {options.map((option) => {
          const active = option.value === value;
          return (
            <button
              key={option.value}
              type="button"
              className={cn(
                "focus-ring inline-flex h-9 items-center justify-center rounded-full px-4 text-[0.8rem] font-medium tracking-[0.02em] transition-colors",
                active
                  ? "bg-ink text-surface shadow-sm"
                  : "bg-surface-raised text-muted hover:bg-surface-raised/70 hover:text-ink border border-hairline/40",
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
