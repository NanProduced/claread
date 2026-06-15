"use client";

import { motion, useMotionTemplate, useMotionValue, useTransform, type MotionValue } from "framer-motion";
import { Braces, Languages, ListTree, Sparkles, type LucideIcon } from "lucide-react";
import type { CSSProperties, MouseEvent, ReactNode } from "react";
import { cn } from "@/lib/cn";

type BentoGraphicProps = {
  mouseX: MotionValue<number>;
  mouseY: MotionValue<number>;
};

type LexicalTone = "vocab" | "phrase" | "context";

interface LexicalMark {
  id: LexicalTone;
  label: string;
  markClassName: string;
  text: string;
  result: string;
}

interface AnalysisChunk {
  id: string;
  index: number;
  label: string;
  text: string;
}

const lexicalMarks: LexicalMark[] = [
  {
    id: "vocab",
    label: "单词",
    markClassName: "reader-mark reader-mark--vocab reader-mark--interactive",
    text: "Nationally",
    result: "在全国范围内",
  },
  {
    id: "phrase",
    label: "短语",
    markClassName: "reader-mark reader-mark--phrase reader-mark--interactive",
    text: "miss out on",
    result: "错过，失去获得机会",
  },
  {
    id: "context",
    label: "语境义",
    markClassName: "reader-mark reader-mark--context reader-mark--interactive",
    text: "excused or unexcused",
    result: "无论是否有正当理由",
  },
];

const sentenceChunks: AnalysisChunk[] = [
  {
    id: "main",
    index: 1,
    label: "主干",
    text: "This design reduces the risk",
  },
  {
    id: "clause",
    index: 2,
    label: "定语从句",
    text: "that an explanation becomes detached from the passage",
  },
  {
    id: "tail",
    index: 3,
    label: "伴随结构",
    text: "while keeping the original sentence in view.",
  },
];

const translationRows = [
  {
    id: "visible",
    source: "Claread keeps each sentence visible.",
    translation: "Claread 让每个句子仍然留在视野里。",
  },
  {
    id: "only-where-help",
    source: "It opens vocabulary, grammar, and meaning only where they help.",
    translation: "它只在真正有帮助的地方展开词汇、语法和含义。",
  },
];

export function ProductBentoGrid() {
  return (
    <section className="relative mx-auto w-full max-w-[76rem] px-5 py-24 sm:px-6 lg:px-8">
      <div className="mb-14 flex flex-col items-center text-center">
        <p className="mb-4 text-[0.78rem] font-semibold tracking-[0.18em] text-ink-soft/72">
          CLAREAD OUTPUTS
        </p>
        <h2 className="font-headline text-[clamp(2rem,5vw,2.8rem)] font-semibold leading-tight tracking-tight text-ink">
          四类输出标注，贴着原文展开。
        </h2>
        <p className="mt-5 max-w-2xl text-[1.04rem] font-medium leading-[1.75] text-ink-soft">
          词汇、语法、整句拆解和句级译文都锚定在 Reader 的原文位置。它们按需出现，解释完仍把注意力还给英文句子。
        </p>
      </div>

      <div className="grid w-full grid-cols-1 gap-5 md:auto-rows-[24rem] md:grid-cols-6">
        <MagicBentoCard
          className="md:col-span-3"
          name="三类词汇标注"
          description="vocab / phrase / context 分层处理：生词、短语和当前语境义互不混淆。"
          Icon={Sparkles}
          graphic={(props) => <LexicalMarksGraphic {...props} />}
        />

        <MagicBentoCard
          className="md:col-span-3"
          name="grammar_note"
          description="只在局部结构影响理解时出现，锚定原句证据，解释为什么这样读。"
          Icon={Braces}
          graphic={(props) => <GrammarNoteGraphic {...props} />}
        />

        <MagicBentoCard
          className="md:col-span-2"
          name="sentence_analysis"
          description="当整句读序卡住，句后拆出主干、从句和伴随结构，保持原文在上方。"
          Icon={ListTree}
          graphic={(props) => <SentenceAnalysisGraphic {...props} />}
        />

        <MagicBentoCard
          className="md:col-span-4"
          name="句级双语对照"
          description="中文落在原句下方做低声校准，不把页面变成全文翻译。"
          Icon={Languages}
          graphic={(props) => <TranslationLayerGraphic {...props} />}
        />
      </div>
    </section>
  );
}

function MagicBentoCard({
  className,
  description,
  graphic,
  Icon,
  name,
}: {
  className?: string;
  description: string;
  graphic: (props: BentoGraphicProps) => ReactNode;
  Icon: LucideIcon;
  name: string;
}) {
  const mouseX = useMotionValue(0);
  const mouseY = useMotionValue(0);
  const spotlightBackground = useMotionTemplate`
    radial-gradient(
      560px circle at ${mouseX}px ${mouseY}px,
      rgba(95,78,138,0.12),
      transparent 78%
    )
  `;

  function handleMouseMove({ currentTarget, clientX, clientY }: MouseEvent<HTMLDivElement>) {
    const { left, top } = currentTarget.getBoundingClientRect();
    mouseX.set(clientX - left);
    mouseY.set(clientY - top);
  }

  return (
    <article
      onMouseMove={handleMouseMove}
      className={cn(
        "group relative flex min-h-[24rem] flex-col justify-end overflow-hidden rounded-[24px] bg-white",
        "shadow-[0_0_0_1px_rgba(0,0,0,.045),0_8px_20px_rgba(0,0,0,.035),0_24px_52px_rgba(0,0,0,.04)]",
        "transform-gpu transition-[transform,box-shadow] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] hover:-translate-y-0.5",
        className,
      )}
    >
      <motion.div
        className="pointer-events-none absolute -inset-px z-20 rounded-[24px] opacity-0 mix-blend-overlay transition-opacity duration-500 group-hover:opacity-100"
        style={{ background: spotlightBackground }}
      />

      <div className="absolute inset-0 z-0 overflow-hidden bg-[linear-gradient(180deg,rgba(252,251,247,0.52),rgba(247,244,237,0.18))]">
        {graphic({ mouseX, mouseY })}
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-[58%] bg-gradient-to-t from-white via-white/96 to-transparent" />
      </div>

      <div className="pointer-events-none z-10 mt-auto flex transform-gpu flex-col gap-1.5 p-7 transition-transform duration-500 ease-[cubic-bezier(0.22,1,0.36,1)] group-hover:-translate-y-1.5 sm:p-8">
        <div className="mb-2 flex h-10 w-10 items-center justify-center rounded-xl border border-hairline/70 bg-white/72 shadow-sm backdrop-blur-md">
          <Icon
            aria-hidden="true"
            className="h-5 w-5 text-ink-soft transition-[color,transform] duration-500 ease-[cubic-bezier(0.22,1,0.36,1)] group-hover:scale-110 group-hover:text-ink"
          />
        </div>
        <h3 className="text-[1.16rem] font-semibold tracking-tight text-ink">
          {name}
        </h3>
        <p className="max-w-md text-[0.9rem] leading-[1.62] text-ink-soft">
          {description}
        </p>
      </div>
    </article>
  );
}

function LexicalMarksGraphic({ mouseX, mouseY }: BentoGraphicProps) {
  const excerptX = useTransform(mouseX, [0, 760], [-7, 7]);
  const lookupY = useTransform(mouseY, [0, 520], [-7, 7]);

  return (
    <div className="absolute inset-0 flex items-start justify-center px-6 pt-9">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top_left,rgba(246,214,122,0.14),transparent_48%),radial-gradient(ellipse_at_top_right,rgba(165,208,239,0.12),transparent_46%)]" />

      <div className="relative w-full max-w-[31rem]">
        <motion.div
          className="rounded-[18px] border border-hairline/80 bg-white/92 px-5 py-4 shadow-sm backdrop-blur-md"
          style={{ x: excerptX }}
        >
          <p className="reader-serif text-[0.96rem] leading-[1.82] text-ink/78">
            <span className={lexicalMarks[0].markClassName}>Nationally</span>
            {", students who "}
            <span className={lexicalMarks[1].markClassName}>miss out on</span>
            {" "}
            <span className={lexicalMarks[2].markClassName}>excused or unexcused</span>
            {" absences lose time with the material."}
          </p>
        </motion.div>

        <motion.div
          className="ml-auto mt-3 w-[88%] rounded-[16px] border border-hairline/85 bg-white/95 p-3.5 shadow-[0_16px_42px_rgba(24,24,27,0.08)] backdrop-blur-xl"
          initial={{ opacity: 0, y: 12 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-80px" }}
          transition={{ duration: 0.58, ease: [0.22, 1, 0.36, 1], delay: 0.14 }}
          style={{ y: lookupY }}
        >
          <div className="mb-2 flex items-center justify-between gap-3">
            <span className="text-[0.7rem] font-semibold tracking-[0.12em] text-ink-soft/72">
              LEXICAL LAYER
            </span>
            <span className="h-px flex-1 bg-hairline/70" />
          </div>
          <div className="grid gap-2">
            {lexicalMarks.map((item, index) => (
              <motion.div
                key={item.id}
                className="grid grid-cols-[4.4rem_minmax(0,1fr)] items-baseline gap-3 rounded-[10px] border border-hairline/55 bg-surface/34 px-3 py-2"
                initial={{ opacity: 0, x: -8 }}
                whileInView={{ opacity: 1, x: 0 }}
                viewport={{ once: true, margin: "-80px" }}
                transition={{ duration: 0.42, ease: [0.22, 1, 0.36, 1], delay: 0.24 + index * 0.11 }}
              >
                <span className="text-[0.72rem] font-semibold text-ink-soft">
                  {item.label}
                </span>
                <span className="min-w-0 truncate text-[0.82rem] font-medium text-ink">
                  {item.text} · {item.result}
                </span>
              </motion.div>
            ))}
          </div>
        </motion.div>
      </div>
    </div>
  );
}

function GrammarNoteGraphic({ mouseX, mouseY }: BentoGraphicProps) {
  const sentenceX = useTransform(mouseX, [0, 760], [-6, 6]);
  const noteY = useTransform(mouseY, [0, 520], [-7, 7]);

  return (
    <div className="absolute inset-0 flex items-start justify-center px-6 pt-9">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top_right,rgba(208,191,244,0.16),transparent_50%)]" />

      <div className="relative w-full max-w-[31rem]">
        <motion.div
          className="rounded-[18px] border border-hairline/80 bg-white/92 px-5 py-4 shadow-sm backdrop-blur-md"
          style={{ x: sentenceX }}
        >
          <p className="reader-serif text-[0.96rem] leading-[1.88] text-ink/78">
            <span className="reader-mark reader-mark--quiet reader-mark--grammar reader-mark--grammar-linked">
              Not that
            </span>
            {" every word is unknown, "}
            <span className="reader-mark reader-mark--quiet reader-mark--grammar reader-mark--grammar-pinned">
              but that
            </span>
            {" a sentence hides the tested information inside clauses."}
          </p>
        </motion.div>

        <motion.div
          className="mt-3 w-[90%] rounded-[16px] border border-[#8e779f]/20 bg-white/95 p-4 shadow-[0_16px_42px_rgba(95,78,138,0.09)] backdrop-blur-xl"
          initial={{ opacity: 0, y: 10 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-80px" }}
          transition={{ duration: 0.52, ease: [0.22, 1, 0.36, 1], delay: 0.18 }}
          style={{ y: noteY }}
        >
          <div className="flex items-center justify-between gap-3">
            <span className="truncate text-[0.78rem] font-bold tracking-wide text-grammar-violet">
              语法旁注 · not A, but B
            </span>
            <span className="font-sans text-[0.86rem] font-bold text-grammar-violet">①</span>
          </div>
          <p className="mt-3 border-t border-hairline/65 pt-3 text-[0.84rem] leading-[1.7] text-ink">
            先排除「不是每个词都不认识」，真正原因在 <span className="font-semibold text-grammar-violet">but that</span> 后面：信息藏在从句里。
          </p>
        </motion.div>
      </div>
    </div>
  );
}

function SentenceAnalysisGraphic({ mouseX, mouseY }: BentoGraphicProps) {
  const cardX = useTransform(mouseX, [0, 560], [-5, 5]);
  const listY = useTransform(mouseY, [0, 520], [-6, 6]);

  return (
    <div className="absolute inset-0 flex items-start justify-center px-6 pt-8">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top_left,rgba(39,108,77,0.13),transparent_48%)]" />

      <motion.div className="relative w-full max-w-[23rem]" style={{ x: cardX }}>
        <div className="rounded-[18px] border border-hairline/80 bg-white/92 px-4 py-4 shadow-sm backdrop-blur-md">
          <p className="reader-serif text-[0.9rem] leading-[1.78] text-ink/76">
            <span className="reader-analysis-atom reader-analysis-atom--1 reader-analysis-atom--active">
              This design reduces the risk
            </span>
            {" "}
            <span className="reader-analysis-atom reader-analysis-atom--2">
              that an explanation becomes detached from the passage
            </span>
            {" "}
            <span className="reader-analysis-atom reader-analysis-atom--3">
              while keeping the original sentence in view.
            </span>
          </p>
        </div>

        <motion.div
          className="reader-entry-analysis-list mt-3 rounded-[16px] border border-hairline/75 bg-white/95 p-4 shadow-[0_16px_42px_rgba(24,24,27,0.075)] backdrop-blur-xl"
          initial={{ opacity: 0, y: 12 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-80px" }}
          transition={{ duration: 0.56, ease: [0.22, 1, 0.36, 1], delay: 0.12 }}
          style={{ y: listY }}
        >
          {sentenceChunks.map((chunk, index) => (
            <motion.div
              key={chunk.id}
              className={cn(
                "reader-entry-analysis-item reader-entry-analysis-item-tint",
                index === 0 && "reader-entry-analysis-item--active",
              )}
              initial={{ opacity: 0, x: -8 }}
              whileInView={{ opacity: 1, x: 0 }}
              viewport={{ once: true, margin: "-80px" }}
              transition={{ duration: 0.38, ease: [0.22, 1, 0.36, 1], delay: 0.22 + index * 0.1 }}
              style={
                {
                  "--analysis-accent": `var(--reader-analysis-tone-${chunk.index})`,
                } as CSSProperties
              }
            >
              <div className="reader-entry-analysis-header">
                <div className={`reader-analysis-row-index reader-analysis-row-index--${chunk.index}`}>
                  {chunk.index}
                </div>
                <div className="reader-entry-analysis-label">
                  {chunk.label}
                </div>
              </div>
              <div className="reader-entry-analysis-text">
                {chunk.text}
              </div>
            </motion.div>
          ))}
        </motion.div>
      </motion.div>
    </div>
  );
}

function TranslationLayerGraphic({ mouseX, mouseY }: BentoGraphicProps) {
  const pageX = useTransform(mouseX, [0, 760], [-6, 6]);
  const pageY = useTransform(mouseY, [0, 520], [-6, 6]);

  return (
    <div className="absolute inset-0 flex items-start justify-center px-6 pt-8 md:px-8">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top_right,rgba(165,208,239,0.13),transparent_48%)]" />

      <motion.div
        className="relative w-full max-w-[36rem] rounded-[18px] border border-hairline/80 bg-white/92 px-5 py-4 shadow-sm backdrop-blur-md"
        style={{ x: pageX, y: pageY }}
      >
        <div className="space-y-4">
          {translationRows.map((row, index) => (
            <motion.div
              key={row.id}
              className="border-b border-hairline/55 pb-4 last:border-b-0 last:pb-0"
              initial={{ opacity: 0, y: 8 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-80px" }}
              transition={{ duration: 0.48, ease: [0.22, 1, 0.36, 1], delay: 0.15 + index * 0.12 }}
            >
              <p className="reader-serif text-[0.96rem] leading-[1.72] text-ink/82">
                {row.source}
              </p>
              <div className="reader-translation-layer reader-translation--muted">
                <div className="reader-translation-shell">
                  <p className="reader-translation-copy">
                    {row.translation}
                  </p>
                </div>
              </div>
            </motion.div>
          ))}
        </div>
      </motion.div>
    </div>
  );
}
