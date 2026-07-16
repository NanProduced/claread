import { type ReactNode } from "react";
import { BookMarked, CircleHelp, Highlighter, Search } from "lucide-react";
import Image from "next/image";
import { BentoCard, BentoGrid } from "@/components/ui/bento-grid";

const features = [
  {
    Icon: CircleHelp,
    name: "Ask Claread",
    description: "围绕当前句子的指代、语法和含义追问，答案仍回到原文坐标。",
    className: "col-span-3 lg:col-span-2",
    background: <AskClareadIllustration />,
  },
  {
    Icon: Search,
    name: "点词查询",
    description: "词义从原句里轻轻抬起，不需要离开阅读位置。",
    className: "col-span-3 lg:col-span-1",
    background: <WordLookupIllustration />,
  },
  {
    Icon: Highlighter,
    name: "高亮 / 笔记",
    description: "把自己的判断贴回句子边缘，复看时仍能找到上下文。",
    className: "col-span-3 lg:col-span-1",
    background: <HighlightNoteIllustration />,
  },
  {
    Icon: BookMarked,
    name: "生词本",
    description: "从文章中留下的词片自动归档，复习仍带着原文来源。",
    className: "col-span-3 lg:col-span-2",
    background: <VocabularyNotebookIllustration />,
  },
];

export function ProductUtilityBento() {
  return (
    <section className="relative overflow-hidden bg-[#F7F5F0] px-5 py-24 sm:px-6 lg:py-32">
      <div className="mx-auto max-w-[76rem]">
        <div className="mb-12 max-w-3xl">
          <p className="text-sm font-semibold text-lens-blue">
            Reading Tools
          </p>
          <h2 className="mt-4 max-w-3xl font-headline text-[clamp(2.15rem,4.2vw,3.9rem)] font-semibold leading-[1.08] tracking-normal text-ink [text-wrap:balance]">
            读到哪里，工具就在哪里。
          </h2>
          <p className="mt-5 max-w-2xl text-base leading-8 text-muted-foreground">
            查词、追问、收藏和笔记，都贴着原文展开。
          </p>
        </div>

        <BentoGrid className="auto-rows-[21rem] gap-px overflow-hidden rounded-[16px] border border-[#DED8CC] bg-[#DED8CC] shadow-[0_14px_36px_rgba(28,24,18,0.04)] lg:auto-rows-[23rem]">
          {features.map((feature) => (
            <BentoCard
              key={feature.name}
              {...feature}
              className={`${feature.className} rounded-none border-0 bg-[#FBFAF6] shadow-none [box-shadow:none] dark:[box-shadow:none]`}
            />
          ))}
        </BentoGrid>
      </div>
    </section>
  );
}

function WordLookupIllustration() {
  return (
    <IllustrationSurface viewBox="0 0 420 250" heightClass="h-[70%]">
      <WindowFrame x={54} y={54} width={236} height={130}>
        <MiniLine x={78} y={88} width={126} />
        <MiniLine x={78} y={114} width={178} />
        <MiniLine x={78} y={140} width={112} />
        <path
          d="M143 114H211"
          stroke="var(--vocab-amber)"
          strokeWidth="7"
          strokeLinecap="round"
          className="opacity-30 transition-[stroke-dashoffset,opacity] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] [stroke-dasharray:72] [stroke-dashoffset:18] group-hover:opacity-60 group-hover:[stroke-dashoffset:0] motion-reduce:[stroke-dashoffset:0]"
        />
        <circle
          cx="212"
          cy="114"
          r="4"
          fill="var(--vocab-amber)"
          className="origin-center opacity-75 transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] group-hover:scale-125 motion-reduce:transform-none"
        />
      </WindowFrame>

      <path
        d="M181 108C181 86 166 82 166 70"
        stroke="var(--vocab-amber)"
        strokeLinecap="round"
        strokeOpacity=".42"
        className="transition-[stroke-dashoffset,opacity] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] [stroke-dasharray:62] [stroke-dashoffset:24] group-hover:opacity-100 group-hover:[stroke-dashoffset:0] motion-reduce:[stroke-dashoffset:0]"
      />
      <g className="origin-center transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] group-hover:-translate-y-2 motion-reduce:transform-none">
        <rect
          x="126"
          y="28"
          width="136"
          height="50"
          rx="10"
          fill="#FFFFFF"
          stroke="#E0C889"
        />
        <path
          d="M146 46H190M146 60H224"
          stroke="var(--ink)"
          strokeLinecap="round"
          strokeOpacity=".34"
        />
        <circle cx="240" cy="53" r="4" fill="var(--vocab-amber)" fillOpacity=".78" />
      </g>

      <g className="transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] group-hover:translate-x-1 motion-reduce:transform-none">
        <rect x="304" y="80" width="58" height="84" rx="12" fill="#FFFFFF" stroke="var(--hairline)" />
        <path d="M322 104H344M322 124H338M322 144H350" stroke="var(--ink)" strokeLinecap="round" strokeOpacity=".23" />
        <path d="M304 122H362" stroke="var(--hairline)" />
        <circle cx="348" cy="104" r="3" fill="var(--vocab-amber)" fillOpacity=".62" />
      </g>
    </IllustrationSurface>
  );
}

function AskClareadIllustration() {
  return (
    <RasterIllustration
      src="/product/utility-bento/ask-claread.png"
      heightClass="h-[72%]"
      imageClassName="scale-[1.26]"
    >
      <svg className="absolute inset-0 z-10 h-full w-full" viewBox="0 0 720 260" fill="none">
        <path
          d="M266 132C318 132 326 90 372 90"
          stroke="var(--lens-blue)"
          strokeWidth="1.4"
          strokeLinecap="round"
          className="opacity-0 transition-[stroke-dashoffset,opacity] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] [stroke-dasharray:132] [stroke-dashoffset:52] group-hover:opacity-70 group-hover:[stroke-dashoffset:0] motion-reduce:[stroke-dashoffset:0]"
        />
        <path
          d="M510 206H566"
          stroke="var(--lens-blue)"
          strokeWidth="1.2"
          strokeLinecap="round"
          className="opacity-0 transition-opacity delay-75 duration-300 group-hover:opacity-55"
        />
        <circle
          cx="266"
          cy="132"
          r="4"
          fill="var(--lens-blue)"
          className="origin-center opacity-0 transition-[opacity,transform] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] group-hover:scale-125 group-hover:opacity-80 motion-reduce:transform-none"
        />
        <circle
          cx="570"
          cy="206"
          r="7"
          fill="var(--lens-blue)"
          fillOpacity=".18"
          className="origin-center opacity-0 transition-[opacity,transform] delay-75 duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] group-hover:scale-110 group-hover:opacity-100 motion-reduce:transform-none"
        />
      </svg>
    </RasterIllustration>
  );
}

function HighlightNoteIllustration() {
  return (
    <IllustrationSurface viewBox="0 0 420 250" heightClass="h-[70%]">
      <WindowFrame x={50} y={52} width={236} height={136}>
        <MiniLine x={76} y={88} width={166} />
        <MiniLine x={76} y={116} width={132} />
        <MiniLine x={76} y={144} width={178} />
        <path
          d="M112 116H218"
          stroke="var(--vocab-amber)"
          strokeWidth="7"
          strokeLinecap="round"
          className="opacity-30 transition-[stroke-dashoffset,opacity] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] [stroke-dasharray:110] [stroke-dashoffset:34] group-hover:opacity-[.62] group-hover:[stroke-dashoffset:0] motion-reduce:[stroke-dashoffset:0]"
        />
      </WindowFrame>
      <path
        d="M218 116C248 116 244 140 272 140"
        stroke="var(--grammar-violet)"
        strokeLinecap="round"
        strokeOpacity=".42"
        className="transition-[stroke-dashoffset] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] [stroke-dasharray:78] [stroke-dashoffset:28] group-hover:[stroke-dashoffset:0] motion-reduce:[stroke-dashoffset:0]"
      />
      <g className="origin-center transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] group-hover:-translate-y-1.5 group-hover:translate-x-1 motion-reduce:transform-none">
        <rect
          x="262"
          y="118"
          width="112"
          height="72"
          rx="11"
          fill="#FFFFFF"
          stroke="#D8D0E2"
        />
        <path d="M282 142H334M282 158H350" stroke="var(--ink)" strokeLinecap="round" strokeOpacity=".32" />
        <path d="M282 174H322" stroke="var(--grammar-violet)" strokeLinecap="round" strokeOpacity=".45" />
        <circle cx="354" cy="140" r="4" fill="var(--grammar-violet)" fillOpacity=".56" />
      </g>
      <circle cx="218" cy="116" r="4" fill="var(--vocab-amber)" fillOpacity=".72" />
    </IllustrationSurface>
  );
}

function VocabularyNotebookIllustration() {
  return (
    <RasterIllustration
      src="/product/utility-bento/vocabulary-notebook.png"
      heightClass="h-[72%]"
      imageClassName="scale-[1.26]"
    >
      <svg className="absolute inset-0 z-10 h-full w-full" viewBox="0 0 720 260" fill="none">
        <path
          d="M270 104C344 100 350 90 416 84"
          stroke="var(--structure-green)"
          strokeWidth="1.3"
          strokeLinecap="round"
          className="opacity-0 transition-[stroke-dashoffset,opacity] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] [stroke-dasharray:160] [stroke-dashoffset:58] group-hover:opacity-65 group-hover:[stroke-dashoffset:0] motion-reduce:[stroke-dashoffset:0]"
        />
        <path
          d="M252 130C334 128 350 132 416 132"
          stroke="var(--structure-green)"
          strokeWidth="1.3"
          strokeLinecap="round"
          className="opacity-0 transition-[stroke-dashoffset,opacity] delay-75 duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] [stroke-dasharray:176] [stroke-dashoffset:64] group-hover:opacity-45 group-hover:[stroke-dashoffset:0] motion-reduce:[stroke-dashoffset:0]"
        />
        <path
          d="M272 156C338 158 352 174 416 174"
          stroke="var(--structure-green)"
          strokeWidth="1.3"
          strokeLinecap="round"
          className="opacity-0 transition-[stroke-dashoffset,opacity] delay-100 duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] [stroke-dasharray:142] [stroke-dashoffset:52] group-hover:opacity-55 group-hover:[stroke-dashoffset:0] motion-reduce:[stroke-dashoffset:0]"
        />
        <circle
          cx="416"
          cy="84"
          r="4"
          fill="var(--structure-green)"
          className="origin-center opacity-0 transition-[opacity,transform] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] group-hover:scale-125 group-hover:opacity-80 motion-reduce:transform-none"
        />
        <circle
          cx="416"
          cy="132"
          r="4"
          fill="var(--structure-green)"
          className="origin-center opacity-0 transition-[opacity,transform] delay-75 duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] group-hover:scale-125 group-hover:opacity-65 motion-reduce:transform-none"
        />
        <circle
          cx="416"
          cy="174"
          r="4"
          fill="var(--structure-green)"
          className="origin-center opacity-0 transition-[opacity,transform] delay-100 duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] group-hover:scale-125 group-hover:opacity-70 motion-reduce:transform-none"
        />
      </svg>
    </RasterIllustration>
  );
}

function IllustrationSurface({
  children,
  heightClass,
  viewBox,
}: {
  children: ReactNode;
  heightClass: string;
  viewBox: string;
}) {
  return (
    <figure
      aria-hidden="true"
      className={`pointer-events-none absolute inset-x-0 top-0 overflow-hidden text-ink/22 ${heightClass}`}
    >
      <svg className="h-full w-full" viewBox={viewBox} fill="none">
        <path
          d="M0 1H720"
          stroke="currentColor"
          strokeOpacity=".18"
          vectorEffect="non-scaling-stroke"
        />
        {children}
      </svg>
    </figure>
  );
}

function RasterIllustration({
  children,
  heightClass,
  imageClassName = "",
  src,
}: {
  children: ReactNode;
  heightClass: string;
  imageClassName?: string;
  src: string;
}) {
  return (
    <figure
      aria-hidden="true"
      className={`pointer-events-none absolute inset-x-0 top-0 overflow-hidden ${heightClass}`}
    >
      <div className="absolute -inset-x-6 bottom-0 top-0 overflow-hidden sm:-inset-x-10">
        <Image
          alt=""
          fill
          className={`absolute inset-0 h-full w-full object-contain object-center opacity-[.97] transition-opacity duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] group-hover:opacity-100 ${imageClassName}`}
          draggable={false}
          loading="lazy"
          sizes="(min-width: 1024px) 52rem, 100vw"
          src={src}
        />
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgba(251,250,246,0)_48%,rgba(251,250,246,.58)_76%,#FBFAF6_100%)]" />
        {children}
      </div>
    </figure>
  );
}

function WindowFrame({
  children,
  height,
  width,
  x,
  y,
}: {
  children: ReactNode;
  height: number;
  width: number;
  x: number;
  y: number;
}) {
  return (
    <g className="transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] group-hover:-translate-y-1 motion-reduce:transform-none">
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        rx="13"
        fill="#FFFFFF"
        stroke="var(--hairline)"
      />
      <path d={`M${x} ${y + 28}H${x + width}`} stroke="var(--hairline)" />
      <circle cx={x + 20} cy={y + 14} r="3.5" fill="#E05243" fillOpacity=".72" />
      <circle cx={x + 34} cy={y + 14} r="3.5" fill="var(--lens-blue)" fillOpacity=".52" />
      <circle cx={x + 48} cy={y + 14} r="3.5" fill="var(--structure-green)" fillOpacity=".58" />
      {children}
    </g>
  );
}

function MiniLine({
  opacity = 0.24,
  width,
  x,
  y,
}: {
  opacity?: number;
  width: number;
  x: number;
  y: number;
}) {
  return (
    <path
      d={`M${x} ${y}H${x + width}`}
      stroke="var(--ink)"
      strokeLinecap="round"
      strokeOpacity={opacity}
    />
  );
}
