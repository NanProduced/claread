import type { ReactNode } from "react";

interface SettingsSectionLayoutProps {
  id?: string;
  title: string;
  children: ReactNode;
}

export function SettingsSectionLayout({ id, title, children }: SettingsSectionLayoutProps) {
  return (
    <section
      id={id}
      className="group flex flex-col md:grid md:grid-cols-[140px_1fr] md:gap-x-16 gap-y-6 py-14"
    >
      <div className="shrink-0 pt-1.5">
        <h2 className="text-[0.7rem] font-bold tracking-[0.25em] text-subtle md:text-right">
          {title}
        </h2>
      </div>
      <div className="min-w-0 max-w-2xl">{children}</div>
    </section>
  );
}
