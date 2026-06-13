"use client";

import { motion, useMotionTemplate, useMotionValue, useTransform } from "framer-motion";
import { cn } from "@/lib/cn";
import { ReactNode, MouseEvent } from "react";
import { Sparkles, Replace, BookOpenText, ScissorsLineDashed } from "lucide-react";

// --- Bento Grid Container ---

export function ProductBentoGrid() {
  return (
    <section className="relative mx-auto w-full max-w-[76rem] px-5 py-24 sm:px-6 lg:px-8">
      <div className="mb-16 flex flex-col items-center text-center">
        <h2 className="font-headline text-[clamp(2rem,5vw,2.75rem)] font-semibold leading-tight text-ink tracking-tight">
          四种标注，按阅读卡点逐个出现。
        </h2>
        <p className="mt-5 text-[1.1rem] text-ink-soft max-w-2xl font-medium">
          摒弃传统的全文翻译和词典堆砌。我们通过物理级的解构，让复杂的长难句瞬间变得清晰可读。
        </p>
      </div>

      <div className="grid w-full grid-cols-1 gap-6 md:grid-cols-3 md:auto-rows-[26rem]">
        {/* Item 1: Context Gloss (Wide) */}
        <MagicBentoCard
          className="md:col-span-2"
          name="查了单词，还是不清楚意思"
          description="超越基础词典。精准提取当前语境下的真实含义，即使是熟词生义或习语短语也一目了然。"
          Icon={Sparkles}
          graphic={(props) => <ContextGlossGraphic {...props} />}
        />

        {/* Item 2: Grammar Note (Square) */}
        <MagicBentoCard
          className="md:col-span-1"
          name="看懂大意，但不知道为什么"
          description="清晰标注并列、倒装、从句等复杂语法结构，帮你看透句子的骨架。"
          Icon={BracesIcon}
          graphic={(props) => <GrammarNoteGraphic {...props} />}
        />

        {/* Item 3: Sentence Analysis (Square) */}
        <MagicBentoCard
          className="md:col-span-1"
          name="长句不是更长的词表"
          description="物理级意群切分。将令人窒息的英文长句，瞬间化解为可消化的阅读片段。"
          Icon={ScissorsLineDashed}
          graphic={(props) => <SentenceChunkGraphic {...props} />}
        />

        {/* Item 4: Translation Alignment (Wide) */}
        <MagicBentoCard
          className="md:col-span-2"
          name="译文只负责校准理解"
          description="摒弃大段铺满的传统翻译。译文严格跟随原句意群，逐块对照，绝不喧宾夺主。"
          Icon={Replace}
          graphic={(props) => <TranslationGraphic {...props} />}
        />
      </div>
    </section>
  );
}

function BracesIcon(props: any) {
  return (
    <svg
      {...props}
      xmlns="http://www.w3.org/2000/svg"
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5c0 1.1.9 2 2 2h1" />
      <path d="M16 21h1a2 2 0 0 0 2-2v-5c0-1.1.9-2 2-2a2 2 0 0 1-2-2V5a2 2 0 0 0-2-2h-1" />
    </svg>
  );
}

// --- The Magic Bento Card Wrapper ---

function MagicBentoCard({
  name,
  description,
  Icon,
  graphic,
  className,
}: {
  name: string;
  description: string;
  Icon: any;
  graphic: (props: { mouseX: any; mouseY: any }) => ReactNode;
  className?: string;
}) {
  const mouseX = useMotionValue(0);
  const mouseY = useMotionValue(0);

  function handleMouseMove({ currentTarget, clientX, clientY }: MouseEvent) {
    const { left, top } = currentTarget.getBoundingClientRect();
    mouseX.set(clientX - left);
    mouseY.set(clientY - top);
  }

  return (
    <div
      onMouseMove={handleMouseMove}
      className={cn(
        "group relative flex flex-col justify-end overflow-hidden rounded-[24px] bg-white",
        "shadow-[0_0_0_1px_rgba(0,0,0,.04),0_4px_12px_rgba(0,0,0,.03),0_24px_48px_rgba(0,0,0,.03)]",
        "transform-gpu transition-all duration-300 dark:bg-black",
        className
      )}
    >
      {/* Spotlight Effect on Hover */}
      <motion.div
        className="pointer-events-none absolute -inset-px rounded-[24px] opacity-0 transition-opacity duration-500 group-hover:opacity-100 z-20 mix-blend-overlay"
        style={{
          background: useMotionTemplate`
            radial-gradient(
              600px circle at ${mouseX}px ${mouseY}px,
              rgba(120,119,198,0.1),
              transparent 80%
            )
          `,
        }}
      />

      {/* Graphic Area (Background) */}
      <div className="absolute inset-0 z-0 overflow-hidden bg-surface/20">
        {graphic({ mouseX, mouseY })}
        
        {/* Fade Mask to blend graphic into text area seamlessly */}
        <div className="absolute inset-x-0 bottom-0 h-[60%] bg-gradient-to-t from-white via-white/95 to-transparent pointer-events-none" />
      </div>

      {/* Text Area (Foreground) */}
      <div className="pointer-events-none z-10 flex transform-gpu flex-col gap-1.5 p-8 transition-transform duration-500 ease-[cubic-bezier(0.22,1,0.36,1)] group-hover:-translate-y-2 mt-auto">
        <div className="mb-2 flex h-10 w-10 items-center justify-center rounded-xl bg-surface/50 border border-hairline/60 shadow-sm">
          <Icon className="h-5 w-5 text-ink-soft transition-all duration-500 ease-[cubic-bezier(0.22,1,0.36,1)] group-hover:text-ink group-hover:scale-110" />
        </div>
        <h3 className="text-[1.15rem] font-semibold tracking-tight text-ink">
          {name}
        </h3>
        <p className="max-w-md text-[0.9rem] leading-[1.6] text-ink-soft">
          {description}
        </p>
      </div>
    </div>
  );
}

// --- Micro-interaction Graphics with Parallax ---

function ContextGlossGraphic({ mouseX, mouseY }: { mouseX: any; mouseY: any }) {
  // Parallax constraints
  const cardX = useTransform(mouseX, [0, 800], [-8, 8]);
  const cardY = useTransform(mouseY, [0, 800], [-8, 8]);
  const textX = useTransform(mouseX, [0, 800], [-4, 4]);

  return (
    <div className="absolute inset-0 flex items-center justify-center p-6">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top_right,rgba(123,97,255,0.06),transparent_50%)]" />
      
      <div className="relative w-full max-w-[340px] flex flex-col items-center -mt-8">
        {/* Mock Reader Window */}
        <motion.div style={{ x: textX }} className="w-full rounded-xl border border-hairline/80 bg-white px-6 py-5 shadow-sm">
          <p className="reader-serif text-[0.95rem] leading-[1.8] text-ink/80 text-center">
            Nationally, students who miss out on{" "}
            <span className="relative whitespace-nowrap inline-block text-ink font-medium">
              <motion.span 
                className="absolute inset-0 bg-[#e7d8ff]/70 dark:bg-phrase-lavender/30 -z-10 rounded-[4px]"
                initial={{ scaleX: 0 }}
                whileInView={{ scaleX: 1 }}
                viewport={{ once: false, margin: "-100px" }}
                transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1], delay: 0.2 }}
                style={{ originX: 0 }}
              />
              excused or unexcused absences
            </span>{" "}
            lose time with the material.
          </p>
        </motion.div>

        {/* The Inspect Card popping up (Parallax applied) */}
        <motion.div 
          style={{ x: cardX, y: cardY }}
          className="mt-3 w-[90%] rounded-[12px] border border-hairline/85 bg-white/95 px-4 py-4 shadow-[0_8px_32px_rgba(0,0,0,0.08)] backdrop-blur-xl relative z-10"
          initial={{ opacity: 0, y: 12, scale: 0.98 }}
          whileInView={{ opacity: 1, y: 0, scale: 1 }}
          viewport={{ once: false, margin: "-100px" }}
          transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1], delay: 0.8 }}
        >
          <div className="flex items-center gap-1.5">
            <span className="text-[0.6rem] font-bold tracking-[0.12em] text-phrase-lavender">
              PHRASE GLOSS
            </span>
          </div>
          <h3 className="mt-1 reader-serif text-[1rem] leading-tight text-ink">
            excused or unexcused absences
          </h3>
          <p className="mt-1 text-[0.8rem] leading-relaxed text-ink-soft">
            无论是否有请假手续的缺勤。在这里强调了任何形式的缺课都会产生不利影响。
          </p>
        </motion.div>
      </div>
    </div>
  );
}

function GrammarNoteGraphic({ mouseX, mouseY }: { mouseX: any; mouseY: any }) {
  const lineX = useTransform(mouseX, [0, 800], [-6, 6]);
  const lineY = useTransform(mouseY, [0, 800], [-6, 6]);

  return (
    <div className="absolute inset-0 flex justify-center items-center p-6">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top_left,rgba(34,197,94,0.06),transparent_50%)]" />
      <div className="relative w-full max-w-[320px] -mt-12">
        <motion.div style={{ x: lineX, y: lineY }} className="w-full rounded-xl border border-hairline/80 bg-white px-6 py-5 shadow-sm">
          <p className="reader-serif text-[1rem] leading-[2.2] text-ink/70">
            <span className="relative inline-block text-ink">
              <motion.span 
                className="absolute -bottom-1 left-0 right-0 h-[1.5px] bg-emerald-500/60 rounded-full"
                initial={{ scaleX: 0 }}
                whileInView={{ scaleX: 1 }}
                viewport={{ once: false, margin: "-100px" }}
                transition={{ duration: 0.5, delay: 0.2, ease: [0.22, 1, 0.36, 1] }}
                style={{ originX: 0 }}
              />
              Not that
            </span>{" "}
            every word is unknown,{" "}
            <span className="relative inline-block text-ink">
               <motion.span 
                className="absolute -bottom-1 left-0 right-0 h-[1.5px] bg-emerald-500/60 rounded-full"
                initial={{ scaleX: 0 }}
                whileInView={{ scaleX: 1 }}
                viewport={{ once: false, margin: "-100px" }}
                transition={{ duration: 0.5, delay: 0.6, ease: [0.22, 1, 0.36, 1] }}
                style={{ originX: 0 }}
              />
              but that
              <motion.div
                className="absolute left-1/2 -translate-x-1/2 top-full mt-1.5 z-10"
                initial={{ opacity: 0, y: -4 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: false, margin: "-100px" }}
                transition={{ duration: 0.4, delay: 1 }}
              >
                <div className="bg-emerald-50 border border-emerald-200 text-emerald-800 text-[0.65rem] tracking-wide font-medium px-2.5 py-0.5 rounded-full whitespace-nowrap shadow-sm">
                  并列倒装结构
                </div>
              </motion.div>
            </span>{" "}
            a sentence hides the tested information inside clauses.
          </p>
        </motion.div>
      </div>
    </div>
  );
}

function SentenceChunkGraphic({ mouseX, mouseY }: { mouseX: any; mouseY: any }) {
  const chunks = [
    "Most importantly,",
    "what people expect of an object",
    "will be largely determined",
    "by their past experiences."
  ];

  const groupX = useTransform(mouseX, [0, 800], [-5, 5]);

  return (
    <div className="absolute inset-0 flex justify-center items-center p-6">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top_right,rgba(245,160,0,0.05),transparent_50%)]" />
      <motion.div style={{ x: groupX }} className="relative w-full max-w-[320px] -mt-12">
        <div className="w-full rounded-xl border border-hairline/80 bg-white px-6 py-5 shadow-sm">
          <p className="reader-serif text-[1rem] leading-[2.1] text-ink-soft">
            {chunks.map((chunk, i) => (
              <motion.span
                key={i}
                className="inline-block relative"
                initial={{ color: "var(--color-ink-soft)" }}
                whileInView={{ color: "var(--color-ink)" }}
                viewport={{ once: false, margin: "-100px" }}
                transition={{ duration: 0.5, delay: 0.2 + i * 0.4 }}
              >
                {chunk}
                {i < chunks.length - 1 && (
                  <motion.span
                    className="mx-1.5 text-amber-500/70 font-sans font-medium opacity-0"
                    initial={{ opacity: 0, x: -4, rotate: -10 }}
                    whileInView={{ opacity: 1, x: 0, rotate: 0 }}
                    viewport={{ once: false, margin: "-100px" }}
                    transition={{ duration: 0.4, delay: 0.4 + i * 0.4, type: "spring", bounce: 0.4 }}
                  >
                    /
                  </motion.span>
                )}
              </motion.span>
            ))}
          </p>
        </div>
      </motion.div>
    </div>
  );
}

function TranslationGraphic({ mouseX, mouseY }: { mouseX: any; mouseY: any }) {
  const alignments = [
    { en: "In fact,", zh: "事实上，" },
    { en: "the process of learning", zh: "学习的过程" },
    { en: "is not just a matter of accumulating information,", zh: "不仅仅是积累信息，" },
    { en: "but of restructuring it.", zh: "而是重组信息。" }
  ];

  const groupY = useTransform(mouseY, [0, 800], [-6, 6]);

  return (
    <div className="absolute inset-0 flex justify-center items-center p-6 md:p-8">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_bottom_right,rgba(31,94,255,0.06),transparent_50%)]" />
      <motion.div style={{ y: groupY }} className="relative w-full max-w-[500px] flex flex-wrap gap-x-4 gap-y-4 -mt-8">
        {alignments.map((item, i) => (
          <div key={i} className="flex flex-col gap-1 min-w-[45%] flex-1 rounded-xl border border-hairline/80 bg-white px-5 py-4 shadow-sm">
            <motion.p 
              className="reader-serif text-[0.95rem] leading-relaxed text-ink"
              initial={{ opacity: 0, y: 4 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: false, margin: "-100px" }}
              transition={{ duration: 0.5, delay: 0.2 + i * 0.5, ease: [0.22, 1, 0.36, 1] }}
            >
              {item.en}
            </motion.p>
            <motion.p 
              className="text-[0.85rem] text-muted font-medium"
              initial={{ opacity: 0, filter: "blur(4px)" }}
              whileInView={{ opacity: 1, filter: "blur(0px)" }}
              viewport={{ once: false, margin: "-100px" }}
              transition={{ duration: 0.6, delay: 0.4 + i * 0.5, ease: [0.22, 1, 0.36, 1] }}
            >
              {item.zh}
            </motion.p>
          </div>
        ))}
      </motion.div>
    </div>
  );
}
