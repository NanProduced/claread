import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";

export interface InfoCardProps {
  title: string;
  description?: ReactNode;
  icon?: LucideIcon;
  children?: ReactNode;
  footer?: ReactNode;
  className?: string;
  tone?: "default" | "paper";
}

export function InfoCard({
  title,
  description,
  icon: Icon,
  children,
  footer,
  className,
  tone = "default",
}: InfoCardProps) {
  return (
    <section
      className={cn(
        "rounded-panel border border-hairline p-5",
        tone === "paper"
          ? "app-panel-surface--muted"
          : "app-panel-surface",
        className,
      )}
    >
      <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
        {Icon ? <Icon aria-hidden="true" className="h-4 w-4 text-lens-blue" /> : null}
        {title}
      </h2>
      {description ? <div className="mt-2 text-sm leading-6 text-muted">{description}</div> : null}
      {children ? <div className="mt-4">{children}</div> : null}
      {footer ? <div className="mt-5 border-t border-hairline pt-4">{footer}</div> : null}
    </section>
  );
}
