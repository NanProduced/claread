import Link from "next/link";
import { BookMarked, FileText, GraduationCap, Library, MessageSquareText, Sparkles } from "lucide-react";
import { PublicSiteHeader } from "@/components/layout";
import { ProductHero } from "@/components/product-page/ProductHero";
import { ProductReaderDemo } from "@/components/product-page/ProductReaderDemo";
import { appReadRoute, dailyRoute, examplesRoute, homeRoute } from "@/lib/routes";
import { appCtaForSession, getProjectedWebSession } from "@/services/bff/session";

const readingModes = [
  {
    title: "日常阅读",
    copy: "适合新闻、专栏和长文。遇到不确定的短语和句子关系时，再展开解释。",
    icon: BookMarked,
  },
  {
    title: "考试阅读",
    copy: "围绕四六级、考研、雅思托福等题型，突出定位、改写、结构和信号词。",
    icon: GraduationCap,
  },
  {
    title: "Academic",
    copy: "把术语、逻辑关系和解释性笔记放回原文论证，而不是生成脱离上下文的摘要。",
    icon: FileText,
  },
];

const workflowSteps = [
  ["01", "导入原文", "粘贴文章、打开每日精读，或进入工作区保存自己的阅读材料。"],
  ["02", "保留句子", "Claread 先把句子边界和段落关系稳定下来，让解释始终能回到原文。"],
  ["03", "按目标展开", "词义、句法、题目信号和学术逻辑按阅读目标出现，不把界面塞满。"],
  ["04", "沉淀资产", "重要词汇、批注和阅读记录会进入后续复习与个人阅读资产。"],
];

const comparisonPoints = [
  "解释锚定在具体句子，不从聊天框里凭空出现。",
  "同一篇文章可以切换日常、考试和学术阅读目标。",
  "词义、语法、逻辑和题目信号有不同视觉层级。",
  "公开示例先让用户看到产品效果，再进入个人工作区。",
];

export default async function HomePage() {
  const session = await getProjectedWebSession();
  const cta = appCtaForSession(session);
  const primaryLabel = session.hasAppAccess ? "解读我的第一篇文章" : "登录后开始透读";

  return (
    <main className="min-h-screen overflow-hidden bg-web-canvas text-ink">
      <div className="px-5 pt-5 sm:px-6">
        <PublicSiteHeader currentHref={homeRoute} priority />
      </div>

      <ProductHero primaryHref={cta.href} primaryLabel={primaryLabel} secondaryHref={dailyRoute} />

      <section className="border-y border-hairline bg-surface/45">
        <div className="mx-auto grid max-w-7xl gap-8 px-5 py-14 sm:px-6 lg:grid-cols-[0.8fr_1.2fr] lg:items-center">
          <div>
            <h2 className="font-headline text-3xl font-semibold leading-tight text-ink sm:text-4xl">
              Claread 的视觉重点不是 AI，而是阅读过程。
            </h2>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            {comparisonPoints.map((point) => (
              <div key={point} className="flex gap-3 border-t border-hairline pt-4 text-sm leading-6 text-ink-soft">
                <Sparkles aria-hidden="true" className="mt-1 h-4 w-4 flex-none text-lens-blue" />
                <span>{point}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <ProductReaderDemo />

      <section className="mx-auto max-w-7xl px-5 py-16 sm:px-6 lg:py-24">
        <div className="max-w-3xl">
          <h2 className="font-headline text-4xl font-semibold leading-tight text-ink sm:text-5xl">
            三种阅读目标，保持同一个产品骨架。
          </h2>
          <p className="mt-5 text-base leading-8 text-muted">
            不同模式改变解释重点，不改变 Claread 的基本原则：原文在场、句子可追踪、批注可回源。
          </p>
        </div>
        <div className="mt-10 grid gap-5 md:grid-cols-3">
          {readingModes.map(({ title, copy, icon: Icon }) => (
            <article key={title} className="rounded-2xl border border-hairline bg-surface/70 p-6">
              <div className="inline-flex h-10 w-10 items-center justify-center rounded-lg bg-lens-blue-soft text-lens-blue">
                <Icon aria-hidden="true" className="h-5 w-5" />
              </div>
              <h3 className="mt-5 text-lg font-semibold text-ink">{title}</h3>
              <p className="mt-3 text-sm leading-7 text-muted">{copy}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="bg-ink text-surface">
        <div className="mx-auto grid max-w-7xl gap-10 px-5 py-16 sm:px-6 lg:grid-cols-[0.85fr_1.15fr] lg:py-24">
          <div>
            <h2 className="font-headline text-4xl font-semibold leading-tight sm:text-5xl">
              从一篇文章，到可复用的阅读资产。
            </h2>
            <p className="mt-5 text-base leading-8 text-surface/72">
              产品页不只展示功能点，而要让用户理解 Claread 的长期价值：每一次透读都会留下词汇、批注、例句和复习线索。
            </p>
          </div>
          <div className="grid gap-4">
            {workflowSteps.map(([index, title, copy]) => (
              <div key={index} className="grid gap-4 border-t border-surface/15 pt-5 sm:grid-cols-[4rem_1fr]">
                <span className="font-headline text-3xl text-lens-blue-soft">{index}</span>
                <span>
                  <strong className="text-lg font-semibold text-surface">{title}</strong>
                  <span className="mt-2 block text-sm leading-7 text-surface/70">{copy}</span>
                </span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="mx-auto grid max-w-7xl gap-8 px-5 py-16 sm:px-6 lg:grid-cols-2 lg:py-24">
        <Link
          href={dailyRoute}
          className="focus-ring group rounded-2xl border border-hairline bg-surface/70 p-7 transition-colors hover:border-lens-blue/40"
        >
          <Library aria-hidden="true" className="h-6 w-6 text-lens-blue" />
          <h2 className="mt-5 font-headline text-3xl font-semibold text-ink">每日精读</h2>
          <p className="mt-3 text-sm leading-7 text-muted">
            先用公开文章体验 Claread 的阅读节奏，再决定把自己的材料放进工作区。
          </p>
          <span className="mt-5 inline-flex text-sm font-semibold text-lens-blue group-hover:underline">
            打开每日精读
          </span>
        </Link>
        <Link
          href={examplesRoute}
          className="focus-ring group rounded-2xl border border-hairline bg-surface/70 p-7 transition-colors hover:border-lens-blue/40"
        >
          <MessageSquareText aria-hidden="true" className="h-6 w-6 text-lens-blue" />
          <h2 className="mt-5 font-headline text-3xl font-semibold text-ink">公开示例</h2>
          <p className="mt-3 text-sm leading-7 text-muted">
            查看文章、解释、分享态的组合方式，理解 Claread 如何从原文生成可阅读的解释层。
          </p>
          <span className="mt-5 inline-flex text-sm font-semibold text-lens-blue group-hover:underline">
            查看公开示例
          </span>
        </Link>
      </section>

      <section className="px-5 pb-20 sm:px-6">
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
    </main>
  );
}
