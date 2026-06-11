import type { Route } from "next";
import Link from "next/link";
import { BrandLockup } from "@/components/brand/BrandMarks";
import {
  blogRoute,
  dailyRoute,
  helpRoute,
  homeRoute,
} from "@/lib/routes";
import { appCtaForSession, getProjectedWebSession } from "@/services/bff/session";

const navItems: Array<{ href: Route; label: string }> = [
  { href: dailyRoute, label: "每日精读" },
  { href: helpRoute, label: "透读方法" },
  { href: blogRoute, label: "Blog" },
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
                className={`focus-ring rounded-pill px-2 py-1 transition-colors ${
                  active ? "text-lens-blue" : "hover:text-ink"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        {showCta ? (
          <Link
            href={cta.href}
            className="focus-ring inline-flex min-h-8 items-center whitespace-nowrap rounded-[8px] bg-ink px-3 text-[0.78rem] font-semibold text-[rgb(255,255,255)] transition-opacity hover:opacity-90"
            style={{ color: "#ffffff" }}
          >
            {ctaLabelOverride ?? cta.label}
          </Link>
        ) : null}
      </div>
    </header>
  );
}
