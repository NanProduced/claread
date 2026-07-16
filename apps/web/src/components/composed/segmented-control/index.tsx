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
        <legend className="mb-3 text-[0.66rem] font-bold tracking-[0.2em] text-subtle">
          {label}
        </legend>
      ) : null}
      <div className="inline-flex flex-wrap items-center rounded-xl bg-hairline/40 p-1 shadow-inner">
        {options.map((option) => {
          const active = option.value === value;
          return (
            <button
              key={option.value}
              type="button"
              className={cn(
                "focus-ring relative flex h-8 items-center justify-center rounded-lg px-5 text-[0.8rem] font-medium tracking-[0.02em] transition-all duration-200",
                active
                  ? "bg-surface text-ink shadow-[0_1px_3px_rgba(0,0,0,0.1)] ring-1 ring-black/5"
                  : "text-muted-foreground hover:text-ink",
                option.disabled && "cursor-not-allowed opacity-45",
              )}
              disabled={option.disabled}
              onClick={() => onValueChange(option.value)}
              aria-pressed={active}
              title={option.description}
            >
              <span className="relative z-10">{option.label}</span>
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}
