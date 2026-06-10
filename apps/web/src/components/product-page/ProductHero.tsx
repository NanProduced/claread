import type { Route } from "next";
import type { ReactNode } from "react";
import Image from "next/image";
import Link from "next/link";
import {
  ArrowRight,
  BookOpenText,
  Crosshair,
  Layers3,
  Network,
  Sparkles,
} from "lucide-react";

type FeatureItem = {
  title: string;
  copy: string;
  icon: ReactNode;
};

const heroFeatures: FeatureItem[] = [
  {
    title: "精读模式",
    copy: "语法拆解、词句精讲，逐层理解。",
    icon: <Crosshair aria-hidden="true" className="h-7 w-7" />,
  },
  {
    title: "回到原文",
    copy: "解释始终与原句一一对应。",
    icon: <BookOpenText aria-hidden="true" className="h-7 w-7" />,
  },
  {
    title: "结构看得见",
    copy: "词汇、语法、逻辑清晰呈现。",
    icon: <Layers3 aria-hidden="true" className="h-7 w-7" />,
  },
  {
    title: "真正能理解",
    copy: "把知识变成可迁移的能力。",
    icon: <Network aria-hidden="true" className="h-7 w-7" />,
  },
];

export function ProductHero({
  primaryHref,
  primaryLabel,
  secondaryHref,
}: {
  primaryHref: Route;
  primaryLabel: string;
  secondaryHref: Route;
}) {
  return (
    <section className="relative isolate overflow-hidden px-5 pb-0 pt-8 sm:px-6 sm:pt-10 lg:px-10 lg:pt-12">
      <div className="absolute inset-0 -z-30 bg-[radial-gradient(circle_at_50%_6%,rgba(255,255,255,0.84),transparent_31%),linear-gradient(180deg,rgba(255,255,255,0.46),rgba(248,244,234,0.28)_58%,rgba(255,255,255,0.18))]" />

      <HeroApertureBackdrop />

      <div className="relative z-10 mx-auto flex w-full max-w-[98rem] flex-col items-center">
        <HeroTypographyBlock primaryHref={primaryHref} primaryLabel={primaryLabel} secondaryHref={secondaryHref} />
        <RealReaderMock />
        <HeroFeatureStrip />
      </div>
    </section>
  );
}

function HeroTypographyBlock({
  primaryHref,
  primaryLabel,
  secondaryHref,
}: {
  primaryHref: Route;
  primaryLabel: string;
  secondaryHref: Route;
}) {
  return (
    <div className="relative z-20 flex max-w-5xl flex-col items-center text-center">
      <h1 className="font-headline text-ink [text-wrap:balance]">
        <span className="relative inline-block text-[clamp(3.5rem,7.5vw,6rem)] font-semibold leading-[0.95] tracking-normal sm:text-[clamp(4.5rem,7.2vw,6rem)]">
          透读英文
          <span className="relative ml-[0.04em] inline-block px-[0.06em]">
            <span aria-hidden="true" className="absolute inset-x-0 bottom-[0.12em] top-[0.14em] -z-10 rounded-sm bg-vocab-amber/22" />
            文章
          </span>
        </span>
      </h1>

      <p className="mt-5 font-sans text-[0.72rem] font-semibold tracking-[0.52em] text-lens-blue sm:text-[0.82rem]">
        READ DEEPLY, UNDERSTAND CLEARLY
      </p>

      <p className="mt-5 max-w-2xl text-base leading-7 text-ink-soft sm:text-lg">
        让语法、结构和意思回到同一句原文。
      </p>

      <div className="mt-7 flex flex-wrap justify-center gap-3">
        <Link
          href={primaryHref}
          className="focus-ring inline-flex min-h-12 items-center gap-2.5 rounded-lg bg-ink px-7 text-sm font-semibold text-[rgb(255,255,255)] shadow-[0_8px_14px_rgba(17,17,17,0.12)] transition hover:-translate-y-0.5 hover:shadow-[0_10px_16px_rgba(17,17,17,0.16)]"
          style={{ color: "#ffffff" }}
        >
          {primaryLabel}
          <ArrowRight aria-hidden="true" className="h-4 w-4 text-white" />
        </Link>
        <Link
          href={secondaryHref}
          className="focus-ring inline-flex min-h-12 items-center rounded-lg border border-ink/25 bg-surface/55 px-7 text-sm font-semibold text-ink transition hover:-translate-y-0.5 hover:border-ink/40 hover:bg-surface/80"
        >
          查看公开示例
        </Link>
      </div>
    </div>
  );
}

function HeroApertureBackdrop() {
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-0 top-24 -z-20 mx-auto w-full max-w-[118rem]" aria-hidden="true">
      <div className="absolute -right-[13rem] top-[9.5rem] h-[38rem] w-[42rem] opacity-95 sm:-right-[10rem] lg:-right-[5rem] lg:top-[8rem] lg:h-[43rem] lg:w-[48rem]">
        <Image
          src="/brand/landing/hero-aperture-corner-v2.png"
          alt=""
          fill
          sizes="(max-width: 1023px) 80vw, 48rem"
          className="select-none object-contain object-right-top"
          priority
        />
      </div>
      <div className="absolute -bottom-[13rem] -left-[16rem] h-[40rem] w-[48rem] opacity-95 sm:-left-[12rem] lg:-left-[7rem] lg:h-[45rem] lg:w-[55rem]">
        <Image
          src="/brand/landing/hero-aperture-foreground-v2.png"
          alt=""
          fill
          sizes="(max-width: 1023px) 88vw, 55rem"
          className="select-none object-contain object-left-bottom"
          priority
        />
      </div>
    </div>
  );
}

function RealReaderMock() {
  return (
    <div className="relative z-10 mt-12 w-full max-w-[82rem] overflow-hidden rounded-xl border border-hairline bg-reader-paper shadow-[0_14px_32px_rgba(28,24,18,0.14)] lg:mt-14">
      <div className="grid min-h-[33rem] grid-cols-[4.5rem_1fr] bg-[linear-gradient(180deg,rgba(255,255,255,0.5),rgba(248,244,234,0.08))]">
        <MockRail />
        <div className="min-w-0">
          <MockHeader />
          <MockArticle />
        </div>
      </div>
    </div>
  );
}

function MockRail() {
  return (
    <aside className="flex flex-col items-center border-r border-hairline bg-[rgba(249,246,238,0.76)] py-4 text-muted">
      <Image
        src="/brand/claread-icon-fullcolor.png"
        alt=""
        width={34}
        height={34}
        className="brand-aperture-mark h-8 w-8"
      />
      <div className="mt-12 flex flex-1 flex-col items-center gap-6">
        <span className="h-4 w-4 rounded-full border border-muted/55" />
        <span className="relative h-4 w-4 before:absolute before:left-1/2 before:top-0 before:h-full before:w-px before:-translate-x-1/2 before:bg-muted/70 after:absolute after:left-0 after:top-1/2 after:h-px after:w-full after:-translate-y-1/2 after:bg-muted/70" />
        <span className="grid h-4 w-4 grid-cols-3 items-end gap-[2px]">
          <i className="h-2 w-px bg-muted/80" />
          <i className="h-4 w-px bg-muted/80" />
          <i className="h-3 w-px bg-muted/80" />
        </span>
        <span className="h-4 w-4 rounded-sm border border-muted/60" />
        <span className="h-4 w-4 rounded-full border border-muted/60 before:block before:h-2 before:w-2 before:translate-x-[0.18rem] before:translate-y-[0.18rem] before:rounded-full before:border before:border-muted/45" />
      </div>
      <span className="mb-5 h-4 w-4 rounded-full border border-muted/60" />
      <span className="text-lg leading-none text-muted/80">»</span>
    </aside>
  );
}

function MockHeader() {
  return (
    <header className="px-8 pt-7 sm:px-12 lg:px-16">
      <p className="text-sm font-semibold text-lens-blue">
        精读模式 <span className="text-muted">· 2026年6月10日</span>
      </p>
      <h2 className="mt-7 max-w-[52rem] font-headline text-[clamp(2rem,3.2vw,3.55rem)] font-semibold leading-[1.08] text-ink [text-wrap:balance]">
        超越GDP：政府应以公民幸福为终极目标
      </h2>

      <div className="mt-8 flex flex-wrap items-center gap-x-5 gap-y-3 border-y border-hairline py-3 text-[0.82rem] font-semibold text-muted">
        <span className="inline-flex items-center gap-2 rounded-md border border-hairline bg-surface/82 px-3 py-1.5 text-ink shadow-[0_2px_6px_rgba(28,24,18,0.06)]">
          <Sparkles aria-hidden="true" className="h-3.5 w-3.5 text-vocab-amber" />
          解析结果
        </span>
        <span>25 句</span>
        <span>备考精读</span>
        <span className="ml-auto hidden text-ink-soft md:inline">收藏</span>
        <span className="border-b-2 border-vocab-amber px-4 py-2 text-vocab-amber">精读</span>
        <span className="hidden md:inline">沉浸</span>
        <span className="hidden md:inline">阅读设置</span>
      </div>

      <div className="mt-5 flex items-center justify-between text-[0.78rem] text-muted">
        <span>来源 粘贴导入 · 2026年6月10日 · 约 5 分钟阅读</span>
        <span className="hidden md:inline">粘贴导入</span>
      </div>
    </header>
  );
}

function MockArticle() {
  return (
    <div className="px-8 pb-10 pt-10 sm:px-12 lg:px-16">
      <div className="mx-auto max-w-[45rem] border-l border-hairline pl-12">
        <MockSentence index="01 / 07">
          <span className="bg-phrase-lavender/34 text-grammar-violet">GDP growth</span> is not a good{" "}
          <span className="bg-vocab-amber/28">indicator</span> of{" "}
          <span className="border-b border-dashed border-grammar-violet/75 bg-grammar-violet/8">
            how well a country is performing
          </span>
          , and should not be the primary goal of governments.
          <Translation>国内生产总值（GDP）增长并不是衡量一个国家表现良好的良好指标，也不应成为政府的主要目标。</Translation>
        </MockSentence>

        <MockNote title="语法旁注 · 宾语从句" tone="grammar">
          <p>
            <strong>how</strong> 引导的宾语从句，作为介词 <strong>of</strong> 的宾语。注意语序是陈述语序，且{" "}
            <strong>how well</strong> 修饰 <strong>performing</strong>，表示“表现得多好”。
          </p>
        </MockNote>

        <CollapsedNote title="句子拆解 · 并列谓语 + 宾语从句" />

        <MockSentence index="02 / 07" className="mt-8">
          Unlimited growth is not <span className="bg-vocab-amber/28">sustainable</span>, and economic thinking is
          moving toward{" "}
          <span className="border-b border-dashed border-grammar-violet/70">
            the idea that we should aim for sustainability in our economic models
          </span>
          .
          <Translation>无限的增长是不可持续的，经济思维正转向这样一种理念，即我们应该在经济模式中追求可持续性。</Translation>
        </MockSentence>
      </div>
    </div>
  );
}

function MockSentence({
  index,
  children,
  className = "",
}: {
  index: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`relative ${className}`}>
      <span className="absolute -left-8 top-1.5 -translate-x-full whitespace-nowrap text-[0.72rem] font-semibold text-muted">
        {index}
      </span>
      <p className="font-reading text-[clamp(1.18rem,1.75vw,1.42rem)] leading-[1.76] tracking-normal text-ink">
        {children}
      </p>
    </div>
  );
}

function Translation({ children }: { children: ReactNode }) {
  return <span className="mt-3 block font-sans text-[0.78rem] leading-6 text-muted">{children}</span>;
}

function MockNote({
  title,
  tone,
  children,
}: {
  title: string;
  tone: "grammar";
  children: ReactNode;
}) {
  const toneClass = tone === "grammar" ? "border-grammar-violet/30 text-grammar-violet" : "";

  return (
    <article className={`mt-6 rounded-lg border bg-surface/74 p-5 shadow-[0_4px_8px_rgba(28,24,18,0.06)] ${toneClass}`}>
      <h3 className="text-[0.86rem] font-bold text-grammar-violet">{title}</h3>
      <div className="mt-4 border-t border-hairline pt-4 text-[0.86rem] leading-7 text-ink-soft">{children}</div>
    </article>
  );
}

function CollapsedNote({ title }: { title: string }) {
  return (
    <div className="mt-4 flex min-h-11 items-center justify-between rounded-lg border border-structure-green/24 bg-structure-green/7 px-4 text-[0.84rem] font-semibold text-structure-green">
      <span>{title}</span>
      <span className="text-lg leading-none text-structure-green/70">⌄</span>
    </div>
  );
}

function HeroFeatureStrip() {
  return (
    <div className="relative z-20 -mt-2 w-full rounded-t-xl border border-hairline bg-surface/88 px-8 py-7 shadow-[0_-2px_10px_rgba(28,24,18,0.06)] backdrop-blur-sm sm:px-10">
      <div className="grid gap-7 md:grid-cols-2 xl:grid-cols-4">
        {heroFeatures.map((feature, index) => (
          <div
            key={feature.title}
            className={`flex items-center gap-4 text-left ${index > 0 ? "xl:border-l xl:border-hairline xl:pl-8" : ""}`}
          >
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-ink-soft">
              {feature.icon}
            </span>
            <span>
              <strong className="block text-[0.98rem] font-semibold text-ink">{feature.title}</strong>
              <span className="mt-1 block text-[0.82rem] leading-5 text-muted">{feature.copy}</span>
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
