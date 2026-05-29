import Link from "next/link";
import { PublicSiteHeader } from "@/components/layout";
import { appCtaForSession, getProjectedWebSession } from "@/services/bff/session";
import { dailyRoute, homeRoute, shareDemoRoute } from "@/lib/routes";

export default async function HomePage() {
  const session = await getProjectedWebSession();
  const cta = appCtaForSession(session);

  return (
    <main className="min-h-screen overflow-hidden bg-[oklch(97%_0.012_84)] px-6 py-6 text-ink">
      <PublicSiteHeader currentHref={homeRoute} priority />
      <section className="mx-auto grid min-h-[70vh] max-w-6xl content-center gap-8 py-20">
        <div className="max-w-3xl">
          <p className="text-xs font-semibold tracking-[0.18em] text-lens-blue">
            Claread Web
          </p>
          <h1 className="mt-4 text-5xl font-semibold leading-tight tracking-normal text-[var(--foreground)]">
            一套围绕文章组织起来的英文阅读系统
          </h1>
          <p className="mt-5 max-w-2xl text-lg leading-8 text-[var(--muted)]">
            Claread 先把英文文章读清楚，再让词汇、句子拆解、批注和生词本围绕它出现。公开区可以直接阅读示例，进入工作区后再保存自己的阅读资产。
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link
              href={cta.href}
              className="rounded-md bg-[var(--accent)] px-5 py-3 text-sm font-medium text-[var(--accent-foreground)]"
            >
              {cta.label}
            </Link>
            <Link
              href={dailyRoute}
              className="rounded-md border border-[var(--border)] px-5 py-3 text-sm font-medium"
            >
              打开每日精读
            </Link>
            <Link
              href={shareDemoRoute}
              className="rounded-md border border-[var(--border)] px-5 py-3 text-sm font-medium"
            >
              查看分享占位页
            </Link>
          </div>
        </div>
      </section>
    </main>
  );
}
