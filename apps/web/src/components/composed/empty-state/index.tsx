import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";

export interface EmptyStateProps {
  title: string;
  description: string;
  icon: LucideIcon;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({ title, description, icon: Icon, action, className }: EmptyStateProps) {
  return (
    <section className={cn("border-t border-hairline py-10", className)}>
      <div className="max-w-xl">
        <div className="flex h-11 w-11 items-center justify-center rounded-full bg-lens-blue-soft text-lens-blue">
          <Icon aria-hidden="true" className="h-5 w-5" />
        </div>
        <h2 className="mt-4 font-headline text-2xl font-semibold text-ink">{title}</h2>
        <p className="mt-3 text-sm leading-6 text-muted">{description}</p>
        {action ? <div className="mt-5">{action}</div> : null}
      </div>
    </section>
  );
}
