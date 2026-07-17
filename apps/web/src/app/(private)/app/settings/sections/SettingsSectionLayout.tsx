import type { ReactNode } from "react";

const SECTION_LABELS: Record<string, string> = {
  account: "账户",
  preferences: "偏好",
  quota: "用量与积分",
  support: "支持",
};

interface SettingsSectionLayoutProps {
  id?: string;
  title: string;
  children: ReactNode;
}

export function SettingsSectionLayout({ id, title, children }: SettingsSectionLayoutProps) {
  const label = SECTION_LABELS[title.toLowerCase()] ?? title;

  return (
    <section
      id={id}
      className="group flex flex-col gap-y-6 py-14 md:grid md:grid-cols-[7rem_1fr] md:gap-x-12"
    >
      <div className="shrink-0 pt-0.5">
        <h2 className="text-sm font-medium text-muted-foreground md:text-right">
          {label}
        </h2>
      </div>
      <div className="min-w-0 max-w-2xl">{children}</div>
    </section>
  );
}
