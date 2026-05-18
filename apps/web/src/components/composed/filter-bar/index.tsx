import type { ComponentProps, ReactNode } from "react";
import Link from "next/link";
import { cn } from "@/lib/cn";

export interface FilterBarItem {
  label: string;
  value: string;
  href?: ComponentProps<typeof Link>["href"];
  disabled?: boolean;
}

export interface FilterBarProps {
  items: FilterBarItem[];
  activeValue: string;
  onValueChange?: (value: string) => void;
  summary?: ReactNode;
  className?: string;
}

export function FilterBar({ items, activeValue, onValueChange, summary, className }: FilterBarProps) {
  return (
    <div className={cn("flex flex-col gap-4", className)}>
      {summary ? <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">{summary}</div> : null}
      <nav
        aria-label="筛选器"
        className="inline-flex max-w-full flex-wrap items-center gap-1 rounded-[1.1rem] border border-hairline bg-[linear-gradient(180deg,rgba(244,241,233,0.72),rgba(251,249,244,0.9))] p-1 shadow-[inset_0_1px_0_rgba(255,255,255,0.55)]"
      >
        {items.map((item) => {
          const active = item.value === activeValue;
          const className = cn(
            "focus-ring inline-flex min-h-10 items-center rounded-[0.82rem] border px-4 text-sm font-semibold tracking-[0.01em] transition-[background-color,border-color,color,box-shadow]",
            active
              ? "border-[rgba(214,209,197,0.88)] bg-[linear-gradient(180deg,rgba(255,255,255,0.98),rgba(250,248,243,1))] text-ink shadow-[0_8px_18px_rgba(17,17,17,0.05),inset_0_1px_0_rgba(255,255,255,0.82)]"
              : "border-transparent bg-transparent text-ink-soft hover:bg-[rgba(255,255,255,0.58)] hover:text-ink",
            item.disabled && "cursor-not-allowed opacity-45",
          );

          if (item.href) {
            return (
              <Link key={item.value} href={item.href} className={className} aria-current={active ? "page" : undefined}>
                {item.label}
              </Link>
            );
          }

          return (
            <button
              key={item.value}
              type="button"
              className={className}
              disabled={item.disabled}
              onClick={() => onValueChange?.(item.value)}
              aria-pressed={active}
            >
              {item.label}
            </button>
          );
        })}
      </nav>
    </div>
  );
}
