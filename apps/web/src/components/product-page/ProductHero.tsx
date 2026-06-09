import type { CSSProperties } from "react";
import type { Route } from "next";
import Image from "next/image";
import Link from "next/link";
import { ArrowRight, BookOpenCheck } from "lucide-react";

const lensLocks = [
  {
    term: "tested information",
    label: "考点信息",
    note: "定位句子里真正被考查的部分。",
    before: "the sentence hides the",
    after: "inside clauses",
  },
  {
    term: "not that... but that...",
    label: "结构锁定",
    note: "先看 but that 后面的强调内容。",
    before: "the difficulty is often",
    after: "a sentence hides the focus",
  },
  {
    term: "while",
    label: "关系信号",
    note: "识别转折、让步或并列对照。",
    before: "one clause gives context",
    after: "the next carries the point",
  },
  {
    term: "anchored reading object",
    label: "阅读锚点",
    note: "解释始终回到原文句子。",
    before: "Claread keeps every note as an",
    after: "instead of a detached answer",
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
    <section className="relative isolate overflow-hidden px-5 pb-14 pt-14 sm:px-6 sm:pb-16 sm:pt-16 lg:min-h-[calc(100svh-5.5rem)] lg:pb-20 lg:pt-20">
      <div className="mx-auto grid max-w-7xl gap-10 lg:grid-cols-[0.82fr_1.18fr] lg:items-center xl:gap-14">
        <div className="relative z-10 max-w-3xl">
          <p className="mb-5 inline-flex items-center gap-2 border-l-2 border-lens-blue pl-3 text-xs font-semibold tracking-[0.08em] text-muted">
            Claread Reading Lens
          </p>
          <h1 className="max-w-4xl font-headline text-[clamp(3rem,7vw,5.9rem)] font-semibold leading-[0.94] tracking-normal text-ink [text-wrap:balance]">
            Read Deeply, Understand Clearly
          </h1>
          <p className="mt-6 max-w-2xl text-lg leading-8 text-muted sm:text-xl sm:leading-9">
            透读英文文章，一句一句看清语法、结构和意思。不是替你跳过原文，而是把理解锚定在句子上。
          </p>
          <div className="mt-9 flex flex-wrap gap-3">
            <Link
              href={primaryHref}
              className="focus-ring inline-flex min-h-12 items-center gap-3 rounded-pill bg-ink px-6 text-sm font-semibold text-[rgb(255,255,255)] transition-opacity hover:opacity-90"
              style={{ color: "#ffffff" }}
            >
              {primaryLabel}
              <ArrowRight aria-hidden="true" className="h-4 w-4" />
            </Link>
            <Link
              href={secondaryHref}
              className="focus-ring inline-flex min-h-12 items-center rounded-pill border border-hairline bg-surface/70 px-6 text-sm font-semibold text-ink transition-colors hover:border-lens-blue/40 hover:text-lens-blue"
            >
              打开 Daily
            </Link>
          </div>
          <div className="mt-10 hidden max-w-xl items-start gap-3 border-t border-hairline pt-5 text-sm leading-6 text-ink-soft sm:flex">
            <BookOpenCheck aria-hidden="true" className="mt-0.5 h-5 w-5 flex-none text-lens-blue" />
            <p>Claread = clarify。先锁定句子里的关键词和结构，再展开解释。</p>
          </div>
        </div>

        <HeroApertureLens />
      </div>
    </section>
  );
}

function HeroApertureLens() {
  return (
    <div
      aria-hidden="true"
      className="relative z-0 order-last min-h-[21rem] overflow-visible sm:min-h-[30rem] md:min-h-[34rem] lg:min-h-[40rem]"
    >
      <div className="relative mx-auto aspect-[16/10] w-[92vw] max-w-[34rem] sm:w-[86vw] sm:max-w-[42rem] md:w-[74vw] lg:mx-0 lg:w-[66vw] lg:max-w-[62rem] lg:-translate-x-[12vw] xl:w-[68vw] xl:max-w-[68rem] xl:-translate-x-[10vw]">
        <Image
          src="/brand/hero-aperture-shell.png"
          alt=""
          fill
          priority
          sizes="(max-width: 767px) 92vw, (max-width: 1023px) 74vw, 68vw"
          className="select-none object-contain mix-blend-multiply [mask-image:linear-gradient(to_right,transparent_0%,transparent_18%,black_36%,black_100%)]"
        />
        <div
          data-hero-lens
          className="absolute left-[67%] top-[52%] aspect-square w-[35%] -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-full border border-white/70 bg-reader-paper shadow-[0_26px_80px_rgba(28,24,18,0.22),inset_0_0_0_1px_rgba(34,99,239,0.16)]"
        >
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_45%,rgba(255,255,255,0.95),rgba(244,240,229,0.88)_55%,rgba(215,224,255,0.48))]" />
          <div className="absolute inset-x-[12%] top-[12%] space-y-2 font-reading text-[clamp(0.42rem,1.15vw,0.94rem)] leading-relaxed text-ink/34 blur-[1.6px]">
            <p>
              For students preparing for exams, the difficulty is often not that every word is unknown, but that a
              sentence hides the tested information inside clauses and rewritten expressions.
            </p>
            <p>
              Claread keeps explanations anchored to the original sentence, so each note reveals how grammar,
              structure, and meaning work together.
            </p>
          </div>

          <div className="absolute inset-[13%] flex items-center justify-center">
            <div className="relative min-h-[58%] w-full">
              {lensLocks.map((lock, index) => (
                <div
                  key={lock.term}
                  className="claread-hero-lock absolute inset-0 flex flex-col items-center justify-center text-center"
                  style={{ animationDelay: `${index * 2}s` } as CSSProperties}
                >
                  <p className="font-reading text-[clamp(0.46rem,1.08vw,0.78rem)] font-semibold tracking-[0.08em] text-ink/54">
                    {lock.label}
                  </p>
                  <p className="mt-3 max-w-[14rem] font-reading text-[clamp(0.74rem,1.65vw,1.24rem)] leading-tight text-ink">
                    <span className="text-ink/46">{lock.before} </span>
                    <span className="rounded-[0.35em] bg-lens-blue/15 px-[0.18em] py-[0.02em] text-lens-blue shadow-[0_0_0_1px_rgba(34,99,239,0.14)]">
                      {lock.term}
                    </span>
                    <span className="text-ink/46"> {lock.after}</span>
                  </p>
                  <span className="mt-4 inline-flex max-w-[13rem] items-center justify-center rounded-pill border border-lens-blue/20 bg-white/72 px-3 py-1.5 text-[clamp(0.56rem,1.15vw,0.78rem)] font-semibold leading-snug text-ink-soft shadow-[0_12px_30px_rgba(28,24,18,0.08)]">
                    {lock.note}
                  </span>
                </div>
              ))}
            </div>
          </div>

          <div className="claread-hero-scan absolute left-[12%] right-[12%] h-px bg-lens-blue shadow-[0_0_18px_rgba(34,99,239,0.75)]" />
          <div className="pointer-events-none absolute inset-[10%] rounded-full border border-lens-blue/18" />
          <div className="pointer-events-none absolute left-1/2 top-[10%] h-[13%] w-px -translate-x-1/2 bg-lens-blue/50" />
          <div className="pointer-events-none absolute bottom-[10%] left-1/2 h-[13%] w-px -translate-x-1/2 bg-lens-blue/50" />
          <div className="pointer-events-none absolute left-[10%] top-1/2 h-px w-[13%] -translate-y-1/2 bg-lens-blue/50" />
          <div className="pointer-events-none absolute right-[10%] top-1/2 h-px w-[13%] -translate-y-1/2 bg-lens-blue/50" />
        </div>
      </div>

      <style>{`
        @keyframes clareadHeroLock {
          0%, 20% {
            opacity: 1;
            transform: translateY(0) scale(1);
            filter: blur(0);
          }
          23%, 100% {
            opacity: 0;
            transform: translateY(0.45rem) scale(0.985);
            filter: blur(2px);
          }
        }

        @keyframes clareadHeroScan {
          0% {
            opacity: 0;
            transform: translateY(2.2rem);
          }
          3%, 25%, 28%, 50%, 53%, 75%, 78% {
            opacity: 1;
          }
          15%, 40%, 65%, 90% {
            opacity: 1;
            transform: translateY(17.5rem);
          }
          18%, 43%, 68%, 93%, 100% {
            opacity: 0;
            transform: translateY(17.5rem);
          }
          25.1%, 50.1%, 75.1% {
            transform: translateY(2.2rem);
          }
        }

        .claread-hero-lock {
          opacity: 0;
          animation: clareadHeroLock 8s infinite;
        }

        .claread-hero-scan {
          top: 13%;
          animation: clareadHeroScan 8s linear infinite;
        }

        @media (max-width: 767px) {
          @keyframes clareadHeroScan {
            0% {
              opacity: 0;
              transform: translateY(1.15rem);
            }
            3%, 25%, 28%, 50%, 53%, 75%, 78% {
              opacity: 1;
            }
            15%, 40%, 65%, 90% {
              opacity: 1;
              transform: translateY(9rem);
            }
            18%, 43%, 68%, 93%, 100% {
              opacity: 0;
              transform: translateY(9rem);
            }
            25.1%, 50.1%, 75.1% {
              transform: translateY(1.15rem);
            }
          }
        }

        @media (prefers-reduced-motion: reduce) {
          .claread-hero-lock {
            animation: none;
            opacity: 0;
            transform: none;
            filter: none;
          }

          .claread-hero-lock:first-child {
            opacity: 1;
          }

          .claread-hero-scan {
            display: none;
          }
        }
      `}</style>
    </div>
  );
}
