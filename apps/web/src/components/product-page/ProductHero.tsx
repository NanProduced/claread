import type { CSSProperties } from "react";
import Image from "next/image";

import { HeroAppStage } from "@/components/product-page/hero/HeroAppStage";

export function ProductHero() {
  return (
    <section className="relative isolate overflow-hidden px-5 pb-20 pt-16 sm:px-6 sm:pt-20 lg:px-8 lg:pt-24 xl:pt-28">
      <div className="absolute inset-0 -z-30 bg-[radial-gradient(circle_at_50%_6%,rgba(255,255,255,0.84),transparent_31%),linear-gradient(180deg,rgba(255,255,255,0.46),rgba(248,244,234,0.28)_58%,rgba(255,255,255,0.18))]" />

      <HeroApertureBackdrop />

      <div className="relative z-10 mx-auto flex w-full max-w-[98rem] flex-col items-center">
        <HeroTypographyBlock />
        <HeroAppStage />
      </div>
    </section>
  );
}

function HeroTypographyBlock() {
  return (
    <div className="relative z-20 flex w-full max-w-[76rem] flex-col items-start text-left">
      <style>{`
        @keyframes hero-mark-draw {
          to { stroke-dashoffset: 0; }
        }
        @keyframes hero-highlight-in {
          from { transform: scaleX(0) rotate(-1.1deg); }
          to { transform: scaleX(1) rotate(-1.1deg); }
        }
        @keyframes hero-pin-in {
          from {
            opacity: 0;
            transform: translateY(-0.35rem);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }
        .hero-draw-mark {
          animation: hero-mark-draw 780ms cubic-bezier(0.22, 1, 0.36, 1) forwards;
        }
        .hero-highlight-mark {
          transform: scaleX(0) rotate(-1.1deg);
          animation: hero-highlight-in 620ms cubic-bezier(0.16, 1, 0.3, 1) forwards;
        }
        .hero-proof-pin {
          animation: hero-pin-in 420ms cubic-bezier(0.22, 1, 0.36, 1) forwards;
        }
        @media (prefers-reduced-motion: reduce) {
          .hero-draw-mark,
          .hero-highlight-mark,
          .hero-proof-pin {
            animation-duration: 1ms !important;
            animation-delay: 0ms !important;
          }
        }
      `}</style>

      <h1 data-hero-type className="relative w-full font-headline text-ink">
        <span className="relative block w-fit max-w-full whitespace-nowrap text-[clamp(2.3rem,9.5vw,3.05rem)] font-semibold leading-[0.92] tracking-[0.01em] sm:text-[clamp(3.8rem,6.5vw,5.35rem)]">
          透
          <span className="relative inline-block">
            读
            <svg
              className="absolute -bottom-[0.16em] left-[0.02em] h-[0.2em] w-[0.9em] overflow-visible text-[#7b61ff]"
              viewBox="0 0 100 18"
              fill="none"
              preserveAspectRatio="none"
              xmlns="http://www.w3.org/2000/svg"
            >
              <path
                className="hero-draw-mark"
                d="M2 9 C13 2 24 2 35 9 S57 16 68 9 S90 2 98 9"
                stroke="currentColor"
                strokeWidth="4.5"
                strokeLinecap="round"
                strokeDasharray="120"
                strokeDashoffset="120"
                style={{ animationDelay: "180ms" }}
              />
            </svg>
          </span>
          英文
          <span className="relative z-10 ml-[0.1em] inline-block px-[0.08em]">
            <span
              aria-hidden="true"
              className="hero-highlight-mark absolute -inset-x-[0.05em] bottom-[0.08em] top-[0.12em] -z-10 origin-left rounded-[5px] bg-[rgba(255,214,112,0.72)]"
              style={{ animationDelay: "360ms" }}
            />
            文章
          </span>
          <HeroProofPin
            label="2"
            color="#7b61ff"
            className="left-[31.5%] top-[-0.38em]"
            lineClassName="h-[0.5em]"
            delay="430ms"
          />
          <HeroProofPin
            label="1"
            color="#f5a000"
            className="left-[77.4%] top-[-0.4em]"
            lineClassName="h-[0.52em]"
            delay="560ms"
          />
        </span>

        <span className="relative mt-4 block w-full text-[clamp(2.35rem,9vw,3.1rem)] font-normal leading-[0.92] tracking-[-0.025em] sm:mt-4 sm:text-[clamp(3.4rem,6.7vw,5.45rem)] sm:leading-[0.9]">
          <span className="block">
            Read It{" "}
            <span className="relative inline-block">
              Deeply,
              <svg
                className="absolute -bottom-[0.15em] left-[-0.03em] h-[0.18em] w-[106%] overflow-visible text-lens-blue"
                viewBox="0 0 120 14"
                fill="none"
                preserveAspectRatio="none"
                xmlns="http://www.w3.org/2000/svg"
              >
                <path
                  className="hero-draw-mark"
                  d="M3 11 C35 3 79 2 117 10"
                  stroke="currentColor"
                  strokeWidth="4.2"
                  strokeLinecap="round"
                  strokeDasharray="128"
                  strokeDashoffset="128"
                  style={{ animationDelay: "720ms" }}
                />
              </svg>
            </span>
          </span>
          <span className="mt-1 block max-w-full">
            <span className="inline-block">Understand</span>{" "}
            <span className="inline-block">Clearly.</span>
          </span>
          <HeroProofPin
            label="3"
            color="#1f5eff"
            className="left-[52.2%] top-[0.88em]"
            lineClassName="h-[0.98em]"
            labelPosition="end"
            delay="900ms"
          />
        </span>
      </h1>

      <div className="mt-7 flex w-full">
        <p className="max-w-[34rem] text-[clamp(1.06rem,1.45vw,1.22rem)] font-medium leading-8 text-ink-soft">
          一句一句看清语法、结构和意思。
        </p>
      </div>
    </div>
  );
}

function HeroProofPin({
  label,
  color,
  className,
  lineClassName,
  labelPosition = "start",
  delay,
}: {
  label: string;
  color: string;
  className: string;
  lineClassName: string;
  labelPosition?: "start" | "end";
  delay: string;
}) {
  const labelElement = (
    <span
      className="flex h-5 w-5 items-center justify-center rounded-full text-[0.72rem] font-bold leading-none text-white shadow-[0_5px_10px_rgba(17,17,17,0.12)]"
      style={{ backgroundColor: color }}
    >
      {label}
    </span>
  );
  const lineElement = (
    <span
      className={`block w-px border-l border-dashed ${lineClassName}`}
      style={{ borderColor: color }}
    />
  );

  return (
    <span
      aria-hidden="true"
      className={`hero-proof-pin pointer-events-none absolute hidden flex-col items-center opacity-0 sm:flex ${className}`}
      style={{ animationDelay: delay } satisfies CSSProperties}
    >
      {labelPosition === "start" ? (
        <>
          {labelElement}
          {lineElement}
        </>
      ) : (
        <>
          {lineElement}
          {labelElement}
        </>
      )}
    </span>
  );
}

function HeroApertureBackdrop() {
  return (
    <div
      className="pointer-events-none absolute inset-x-0 bottom-0 top-[24rem] -z-20 w-full overflow-hidden"
      aria-hidden="true"
    >
      <div className="absolute -right-[14rem] top-[6rem] h-[50rem] w-[55rem] opacity-95 sm:-right-[10rem] lg:-right-[8rem] lg:top-[10rem] lg:h-[68rem] lg:w-[76rem]">
        <Image
          src="/brand/landing/hero-aperture-corner-v2.png"
          alt=""
          fill
          sizes="(max-width: 1023px) 82vw, 76rem"
          className="select-none object-contain object-right-top"
          priority
        />
      </div>
      <div className="absolute -bottom-[9rem] -left-[18rem] h-[36rem] w-[48rem] opacity-90 sm:-left-[13rem] lg:-bottom-[8rem] lg:-left-[6rem] lg:h-[42rem] lg:w-[56rem]">
        <Image
          src="/brand/landing/hero-aperture-foreground-v2.png"
          alt=""
          fill
          sizes="(max-width: 1023px) 88vw, 55rem"
          className="select-none object-contain object-left-bottom"
        />
      </div>
    </div>
  );
}
