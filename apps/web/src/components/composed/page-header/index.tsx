import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface PageHeaderProps {
  eyebrow: string;
  title: string;
  description: ReactNode;
  message?: ReactNode;
  actions?: ReactNode;
  className?: string;
}

export function PageHeader({
  eyebrow,
  title,
  description,
  message,
  actions,
  className,
}: PageHeaderProps) {
  return (
    <header
      className={cn(
        "mb-8 flex flex-col gap-6 border-b border-hairline/90 pb-7 md:flex-row md:items-start md:justify-between",
        className,
      )}
    >
      <div className="max-w-[46rem]">
        <p className="mb-3 text-[0.72rem] font-semibold tracking-[0.16em] text-muted">{eyebrow}</p>
        <h1 className="max-w-[20ch] font-headline text-[2.35rem] font-semibold leading-[1.04] tracking-[-0.02em] text-ink sm:text-[3rem]">
          {title}
        </h1>
        <div className="mt-4 max-w-[42rem] text-[0.95rem] leading-7 text-muted">{description}</div>
        {message ? <div className="mt-3 max-w-[42rem] text-sm leading-6 text-muted">{message}</div> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap gap-2 md:pt-2">{actions}</div> : null}
    </header>
  );
}
