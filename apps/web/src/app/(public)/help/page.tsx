import { PublicSiteHeader } from "@/components/layout";
import { helpRoute } from "@/lib/routes";

export default function HelpPage() {
  return (
    <main className="min-h-screen bg-[oklch(97%_0.012_84)] px-6 py-6 text-ink">
      <PublicSiteHeader currentHref={helpRoute} />
      <section className="mx-auto mt-16 max-w-3xl">
        <h1 className="text-4xl font-semibold tracking-normal">Help</h1>
        <p className="mt-5 text-lg leading-8 text-[var(--muted)]">
          当前 Web 已支持公开阅读、登录入口和私有阅读工作区。遇到会话不可用、上游服务不可用或调试态限制时，页面会直接给出明确提示，不再用匿名空态混过去。
        </p>
      </section>
    </main>
  );
}
