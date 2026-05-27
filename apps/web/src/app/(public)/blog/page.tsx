import { PublicSiteHeader } from "@/components/layout";
import { blogRoute } from "@/lib/routes";

export default function BlogPage() {
  return (
    <main className="min-h-screen bg-[oklch(97%_0.012_84)] px-6 py-6 text-ink">
      <PublicSiteHeader currentHref={blogRoute} />
      <section className="mx-auto mt-16 max-w-3xl">
        <h1 className="text-4xl font-semibold tracking-normal">Blog</h1>
        <p className="mt-5 text-lg leading-8 text-[var(--muted)]">
          编辑性内容会在 Reader 和公开内容区稳定后补齐。当前公共区仍以 Daily、示例和产品入口为主。
        </p>
      </section>
    </main>
  );
}
