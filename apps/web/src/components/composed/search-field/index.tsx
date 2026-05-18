"use client";

import { Search, X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { IconButton } from "@/components/primitives/icon-button";

export interface SearchFieldProps {
  value: string;
  onValueChange: (value: string) => void;
  placeholder: string;
  label: string;
  summary?: ReactNode;
  className?: string;
  inputClassName?: string;
}

export function SearchField({
  value,
  onValueChange,
  placeholder,
  label,
  summary,
  className,
  inputClassName,
}: SearchFieldProps) {
  const hasValue = value.trim().length > 0;

  return (
    <div className={cn("flex flex-col gap-3 border-b border-hairline/90 pb-4 lg:flex-row lg:items-center lg:justify-between", className)}>
      <label className="focus-within:border-[rgba(98,101,109,0.35)] flex min-h-12 flex-1 items-center gap-3 rounded-pill border border-hairline bg-[linear-gradient(180deg,rgba(255,255,255,0.95),rgba(251,250,246,0.98))] px-4 shadow-surface-quiet transition-colors lg:max-w-xl">
        <Search aria-hidden="true" className="h-4 w-4 shrink-0 text-muted" />
        <span className="sr-only">{label}</span>
        <input
          className={cn(
            "w-full bg-transparent text-sm text-ink outline-none placeholder:text-subtle",
            inputClassName,
          )}
          placeholder={placeholder}
          value={value}
          onChange={(event) => onValueChange(event.target.value)}
        />
        {hasValue ? (
          <IconButton
            aria-label="清空搜索"
            variant="quiet"
            size="sm"
            className="-mr-1"
            onClick={() => onValueChange("")}
          >
            <X aria-hidden="true" className="h-4 w-4" />
          </IconButton>
        ) : null}
      </label>
      {summary ? <div className="rounded-pill border border-hairline bg-surface px-3 py-1 text-[0.72rem] font-semibold text-muted">{summary}</div> : null}
    </div>
  );
}
