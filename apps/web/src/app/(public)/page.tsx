import Link from "next/link";
import { PublicSiteHeader } from "@/components/layout";
import { ProductHero } from "@/components/product-page/ProductHero";
import { ProductCoreFeatures } from "@/components/product-page/ProductCoreFeatures";
import { ProductPainPoints } from "@/components/product-page/ProductPainPoints";
import { ProductReaderDemo } from "@/components/product-page/ProductReaderDemo";
import { ProductFooter } from "@/components/product-page/ProductFooter";
import { appReadRoute, homeRoute } from "@/lib/routes";
import { appCtaForSession, getProjectedWebSession } from "@/services/bff/session";

export default async function HomePage() {
  const session = await getProjectedWebSession();
  const cta = appCtaForSession(session);
  const primaryLabel = "打开 Claread";

  return (
    <main className="min-h-screen bg-web-canvas text-ink">
      <div className="sticky top-0 z-50 border-b border-hairline/80 bg-web-canvas/88 px-5 backdrop-blur-md sm:px-6 lg:px-8">
        <div className="mx-auto max-w-[76rem]">
          <PublicSiteHeader currentHref={homeRoute} priority ctaLabelOverride="打开 Claread" wide />
        </div>
      </div>

      <ProductHero ctaHref={session.hasAppAccess ? appReadRoute : cta.href} ctaLabel={primaryLabel} />
      <ProductPainPoints />
      <ProductCoreFeatures />
      <ProductReaderDemo />

      <section className="px-5 pb-20 pt-16 sm:px-6">
        <div className="mx-auto flex max-w-7xl flex-col items-start justify-between gap-8 rounded-[2rem] border border-hairline bg-reader-paper p-8 sm:p-10 lg:flex-row lg:items-center">
          <div className="max-w-2xl">
            <h2 className="font-headline text-4xl font-semibold leading-tight text-ink">
              选一篇英文，开始透读。
            </h2>
            <p className="mt-4 text-base leading-8 text-muted">
              从公开示例开始，或进入工作区解读自己的第一篇文章。
            </p>
          </div>
          <Link
            href={session.hasAppAccess ? appReadRoute : cta.href}
            className="focus-ring inline-flex min-h-12 items-center rounded-pill bg-lens-blue px-6 text-sm font-semibold text-[rgb(255,255,255)] transition-opacity hover:opacity-90"
            style={{ color: "#ffffff" }}
          >
            {primaryLabel}
          </Link>
        </div>
      </section>

      <ProductFooter />
    </main>
  );
}
