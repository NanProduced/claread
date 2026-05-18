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
        <legend className="mb-3 text-[0.72rem] font-semibold tracking-[0.14em] text-muted">
          {label}
        </legend>
      ) : null}
      <div className="inline-flex max-w-full flex-wrap gap-1 rounded-[1.1rem] border border-hairline bg-[linear-gradient(180deg,rgba(244,241,233,0.72),rgba(251,249,244,0.9))] p-1 shadow-[inset_0_1px_0_rgba(255,255,255,0.55)]">
        {options.map((option) => {
          const active = option.value === value;
          return (
            <button
              key={option.value}
              type="button"
              className={cn(
                "focus-ring min-h-10 rounded-[0.82rem] border px-4 text-sm font-semibold tracking-[0.01em] transition-[background-color,border-color,color,box-shadow]",
                active
                  ? "border-[rgba(214,209,197,0.88)] bg-[linear-gradient(180deg,rgba(255,255,255,0.98),rgba(250,248,243,1))] text-ink shadow-[0_8px_18px_rgba(17,17,17,0.05),inset_0_1px_0_rgba(255,255,255,0.82)]"
                  : "border-transparent bg-transparent text-ink-soft hover:bg-[rgba(255,255,255,0.58)] hover:text-ink",
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
