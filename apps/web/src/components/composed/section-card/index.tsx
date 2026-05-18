import type { HTMLAttributes, ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";

export interface SectionCardProps extends HTMLAttributes<HTMLElement> {
  title?: string;
  description?: ReactNode;
  icon?: LucideIcon;
  children: ReactNode;
  footer?: ReactNode;
  className?: string;
  contentClassName?: string;
}

export function SectionCard({
  title,
  description,
  icon: Icon,
  children,
  footer,
  className,
  contentClassName,
  ...props
}: SectionCardProps) {
  return (
    <section
      className={cn(
        "rounded-panel border border-hairline bg-[linear-gradient(180deg,rgba(255,255,255,0.95),rgba(251,250,246,0.98))] p-5 shadow-surface-quiet sm:p-6",
        className,
      )}
      {...props}
    >
      {title ? (
        <div className="mb-5 border-b border-hairline/90 pb-4">
          <div className="flex items-center gap-2">
            {Icon ? <Icon aria-hidden="true" className="h-4 w-4 text-lens-blue" /> : null}
            <h2 className="text-base font-semibold text-ink">{title}</h2>
          </div>
          {description ? <div className="mt-2 text-sm leading-6 text-muted">{description}</div> : null}
        </div>
      ) : null}
      <div className={contentClassName}>{children}</div>
      {footer ? <div className="mt-5 border-t border-hairline/90 pt-5">{footer}</div> : null}
    </section>
  );
}
