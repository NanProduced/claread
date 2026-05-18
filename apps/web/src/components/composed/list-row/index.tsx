import type { ComponentProps, ReactNode } from "react";
import Link from "next/link";
import { cn } from "@/lib/cn";

export interface ListRowProps {
  title: ReactNode;
  description?: ReactNode;
  meta?: ReactNode;
  trailing?: ReactNode;
  href?: ComponentProps<typeof Link>["href"];
  className?: string;
  contentClassName?: string;
  titleClassName?: string;
  bodyClassName?: string;
}

export function ListRow({
  title,
  description,
  meta,
  trailing,
  href,
  className,
  contentClassName,
  titleClassName,
  bodyClassName,
}: ListRowProps) {
  return (
    <article
      className={cn(
        "grid gap-4 py-6 transition-colors hover:bg-reader-paper/62 md:grid-cols-[minmax(0,1fr)_auto] md:items-center md:px-4",
        className,
      )}
    >
      <div className={cn("min-w-0", contentClassName)}>
        {href ? (
          <Link
            href={href}
            className={cn(
              "focus-ring inline-block rounded-note px-1 py-1 font-headline text-[1.35rem] font-semibold leading-snug text-ink transition-colors hover:text-lens-blue",
              titleClassName,
            )}
          >
            {title}
          </Link>
        ) : (
          <div className={cn("font-headline text-[1.35rem] font-semibold leading-snug text-ink", titleClassName)}>
            {title}
          </div>
        )}
        {description ? <div className={cn("mt-2 text-sm leading-6 text-muted", bodyClassName)}>{description}</div> : null}
        {meta ? <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted">{meta}</div> : null}
      </div>
      {trailing ? <div className="flex items-center justify-between gap-3 md:justify-end">{trailing}</div> : null}
    </article>
  );
}
