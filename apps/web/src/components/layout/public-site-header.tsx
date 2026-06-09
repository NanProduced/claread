import type { Route } from "next";
import Link from "next/link";
import { BrandLockup } from "@/components/brand/BrandMarks";
import {
  aboutRoute,
  blogRoute,
  dailyRoute,
  examplesRoute,
  helpRoute,
  homeRoute,
} from "@/lib/routes";
import { appCtaForSession, getProjectedWebSession } from "@/services/bff/session";

const navItems: Array<{ href: Route; label: string }> = [
  { href: dailyRoute, label: "每日精读" },
  { href: examplesRoute, label: "公开示例" },
  { href: aboutRoute, label: "About" },
  { href: helpRoute, label: "Help" },
  { href: blogRoute, label: "Blog" },
];

export async function PublicSiteHeader({
  currentHref,
  showCta = true,
  priority = false,
}: {
  currentHref?: Route;
  showCta?: boolean;
  priority?: boolean;
}) {
  const session = await getProjectedWebSession();
  const cta = appCtaForSession(session);

  return (
    <header className="mx-auto flex max-w-7xl items-center justify-between gap-6">
      <BrandLockup href={homeRoute} priority={priority} />
      <div className="flex items-center gap-3">
        <nav className="hidden items-center gap-2 text-sm font-semibold text-muted md:flex">
          {navItems.map((item) => {
            const active = currentHref === item.href;

            return (
              <Link
                key={item.href}
                href={item.href}
                className={`focus-ring rounded-pill px-3 py-2 transition-colors ${
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
            className="focus-ring inline-flex min-h-10 items-center rounded-pill bg-ink px-4 text-sm font-semibold text-[rgb(255,255,255)] transition-opacity hover:opacity-90"
            style={{ color: "#ffffff" }}
          >
            {cta.label}
          </Link>
        ) : null}
      </div>
    </header>
  );
}
