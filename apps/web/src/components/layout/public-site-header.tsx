import type { Route } from "next";
import Link from "next/link";
import { BrandLockup } from "@/components/brand/BrandMarks";
import {
  aboutRoute,
  dailyRoute,
  helpRoute,
  homeRoute,
} from "@/lib/routes";
import { appCtaForSession, getProjectedWebSession } from "@/services/bff/session";

const navItems: Array<{ href: Route; label: string }> = [
  { href: dailyRoute, label: "每日精读" },
  { href: helpRoute, label: "文档" },
  { href: aboutRoute, label: "关于" },
];

export async function PublicSiteHeader({
  currentHref,
  showCta = true,
  priority = false,
  ctaLabelOverride,
  wide = false,
}: {
  currentHref?: Route;
  showCta?: boolean;
  priority?: boolean;
  ctaLabelOverride?: string;
  wide?: boolean;
}) {
  const session = await getProjectedWebSession();
  const cta = appCtaForSession(session);

  return (
    <header className={`mx-auto flex h-14 items-center justify-between gap-5 ${wide ? "max-w-[76rem]" : "max-w-7xl"}`}>
      <BrandLockup href={homeRoute} priority={priority} imageClassName="!w-28 sm:!w-36" />
      <div className="flex items-center gap-3">
        <nav className="hidden items-center gap-2 text-[0.78rem] font-semibold text-muted md:flex">
          {navItems.map((item) => {
            const active = currentHref === item.href;

            return (
              <Link
                key={item.href}
                href={item.href}
                className={`focus-ring rounded-full px-3 py-1.5 transition-all active:scale-95 ${
                  active ? "bg-ink/5 text-ink font-bold" : "hover:bg-ink/5 hover:text-ink"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="flex items-center gap-1">
          {showCta ? (
            <Link
              href={cta.href}
              className="focus-ring ml-1 inline-flex min-h-8 items-center whitespace-nowrap rounded-full bg-ink px-4 text-[0.78rem] font-semibold text-[rgb(255,255,255)] shadow-sm transition-all hover:scale-105 hover:bg-ink/90 hover:shadow-md active:scale-95"
              style={{ color: "#ffffff" }}
            >
              {ctaLabelOverride ?? cta.label}
            </Link>
          ) : null}
        </div>
      </div>
    </header>
  );
}
