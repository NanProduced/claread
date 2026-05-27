import { PublicSiteHeader } from "@/components/layout";
import { aboutRoute } from "@/lib/routes";

export default function AboutPage() {
  return (
    <main className="min-h-screen bg-[oklch(97%_0.012_84)] px-6 py-6 text-ink">
      <PublicSiteHeader currentHref={aboutRoute} />
      <section className="mx-auto mt-16 max-w-3xl">
        <h1 className="text-4xl font-semibold tracking-normal">About Claread</h1>
        <p className="mt-5 text-lg leading-8 text-[var(--muted)]">
          Claread Web 不是营销站和功能页的拼接，而是一套以文章为中心的阅读产品。公开区先让用户看到 Daily 和示例，私有区再承接个人阅读记录、批注和词汇资产。
        </p>
      </section>
    </main>
  );
}
