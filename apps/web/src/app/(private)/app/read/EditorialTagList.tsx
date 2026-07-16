"use client";

import { useMemo, useState } from "react";
import { cn } from "@/lib/cn";

function formatTagLabel(tag: string) {
  return tag
    .trim()
    .replace(/[-_]+/g, " ")
    .replace(/\s+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

type EditorialTagListProps = {
  tags?: string[] | null;
  className?: string;
};

export function EditorialTagList({ tags, className }: EditorialTagListProps) {
  const [pinned, setPinned] = useState(false);

  const formattedTags = useMemo(
    () => (tags ?? []).map(formatTagLabel).filter(Boolean),
    [tags],
  );

  if (formattedTags.length === 0) {
    return null;
  }

  const primary = formattedTags[0];
  const secondary = formattedTags[1];
  const hiddenTags = formattedTags.slice(secondary && secondary.length <= 18 ? 2 : 1);
  const visibleTags = pinned
    ? formattedTags
    : secondary && secondary.length <= 18
      ? [primary, secondary]
      : [primary];
  const hiddenCount = pinned ? 0 : hiddenTags.length;

  return (
    <div
      className={cn("flex flex-wrap items-center gap-x-1.5 gap-y-1 font-sans text-[0.66rem] font-bold tracking-[0.1em] text-muted-foreground", className)}
    >
      {visibleTags.map((tag, index) => (
        <span key={tag} className="inline-flex items-center">
          <span className="truncate max-w-[12rem]">{tag}</span>
          {index < visibleTags.length - 1 && (
            <span className="ml-1.5 text-hairline/80 font-normal">&middot;</span>
          )}
        </span>
      ))}

      {hiddenCount > 0 ? (
        <>
          <span className="ml-1 text-hairline/80 font-normal">&middot;</span>
          <button
            type="button"
            className="ml-1.5 inline-flex items-center transition-colors hover:text-ink focus-ring"
            onClick={(e) => {
              e.preventDefault();
              setPinned((value) => !value);
            }}
          >
            +{hiddenCount}
          </button>
        </>
      ) : formattedTags.length > 2 ? (
        <>
          <span className="ml-1 text-hairline/80 font-normal">&middot;</span>
          <button
            type="button"
            className="ml-1.5 inline-flex items-center transition-colors hover:text-ink focus-ring"
            onClick={(e) => {
              e.preventDefault();
              setPinned((value) => !value);
            }}
          >
            {pinned ? "收起" : "全部"}
          </button>
        </>
      ) : null}
    </div>
  );
}
