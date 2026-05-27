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
        className="app-segmented-surface inline-flex max-w-full flex-wrap items-center gap-1 rounded-[1.1rem] border border-hairline p-1"
      >
        {items.map((item) => {
          const active = item.value === activeValue;
          const className = cn(
            "app-segmented-item focus-ring inline-flex min-h-10 items-center rounded-[0.82rem] border px-4 text-sm font-semibold tracking-[0.01em]",
            active
              ? "app-segmented-item--active border-hairline/90 text-ink"
              : "app-segmented-item--inactive border-transparent bg-transparent text-ink-soft hover:text-ink",
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
