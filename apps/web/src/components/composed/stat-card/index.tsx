import { cn } from "@/lib/cn";

export interface StatCardItem {
  label: string;
  value: React.ReactNode;
}

export interface StatCardProps {
  title: string;
  items: StatCardItem[];
  className?: string;
}

export function StatCard({ title, items, className }: StatCardProps) {
  return (
    <section className={cn("app-panel-surface rounded-panel border border-hairline p-5", className)}>
      <h2 className="text-sm font-semibold text-ink">{title}</h2>
      <dl className="mt-4 grid grid-cols-2 gap-x-5 gap-y-4 text-sm">
        {items.map((item) => (
          <div key={item.label}>
            <dt className="text-xs text-muted-foreground">{item.label}</dt>
            <dd className="mt-1 font-semibold text-ink">{item.value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
