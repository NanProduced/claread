"use client";

import { cn } from "@/lib/cn";

export interface SettingsChoiceOption<T extends string> {
  value: T;
  label: string;
}

interface SettingsChoiceGroupProps<T extends string> {
  name: string;
  value: T;
  options: readonly SettingsChoiceOption<T>[];
  onValueChange: (value: T) => void;
  label: string;
  description?: string;
  className?: string;
}

export function SettingsChoiceGroup<T extends string>({
  name,
  value,
  options,
  onValueChange,
  label,
  description,
  className,
}: SettingsChoiceGroupProps<T>) {
  return (
    <fieldset className={cn("space-y-2", className)}>
      <legend className="text-sm font-medium text-ink">{label}</legend>
      {description ? <p className="text-xs leading-5 text-muted-foreground">{description}</p> : null}
      <div className="flex flex-wrap overflow-hidden rounded-[var(--cl-radius-control-sm)] border border-hairline bg-surface-raised/60 p-0.5">
        {options.map((option, index) => {
          const active = option.value === value;
          return (
            <label
              key={option.value}
              className={cn(
                "focus-within:ring-lens-blue relative flex min-h-10 min-w-0 flex-1 cursor-pointer items-center justify-center gap-2 rounded-[calc(var(--cl-radius-control-sm)-2px)] px-3 py-2 text-sm font-medium transition-colors focus-within:z-10 focus-within:ring-2 focus-within:ring-offset-2 focus-within:ring-offset-background",
                index > 0 && "border-l border-hairline/70",
                active
                  ? "bg-surface text-ink"
                  : "text-muted-foreground hover:bg-[var(--interactive-quiet-hover)] hover:text-ink",
              )}
            >
              <input
                type="radio"
                name={name}
                value={option.value}
                checked={active}
                onChange={() => onValueChange(option.value)}
                className="sr-only"
              />
              <span
                className={cn(
                  "flex size-3.5 items-center justify-center rounded-full border",
                  active ? "border-lens-blue" : "border-muted-foreground/70",
                )}
                aria-hidden="true"
              >
                {active ? <span className="size-1.5 rounded-full bg-lens-blue" /> : null}
              </span>
              <span>{option.label}</span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
