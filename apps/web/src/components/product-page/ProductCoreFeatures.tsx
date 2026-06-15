"use client";

import { AnimatePresence, motion, useInView } from "framer-motion";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Highlighter } from "@/components/ui/highlighter";
import { cn } from "@/lib/cn";

type DemoId = "vocabulary" | "grammar" | "structure" | "translation";

interface Quadrant {
  id: DemoId;
  index: string;
  label: string;
  title: string;
  body: string;
  children: ReactNode;
}

export function ProductCoreFeatures() {
  const quadrants: Quadrant[] = [
    {
      id: "vocabulary",
      index: "01",
      label: "Vocabulary",
      title: "高阶词汇标注",
      body: "在阅读上下文里点出生词、专名与高频短语，词卡跟随 marker 弹出，不打断阅读节奏。",
      children: <VocabularyNotificationGraphic />,
    },
    {
      id: "grammar",
      index: "02",
      label: "Grammar",
      title: "情境语法旁注",
      body: "只在长难句真正影响理解时插一条 grammar_note，解析写得像编辑批注，不罗列规则。",
      children: <GrammarNoteGraphic />,
    },
    {
      id: "structure",
      index: "03",
      label: "Structure",
      title: "句级透视拆解",
      body: "把主句、从句、伴随结构与插入语逐层点亮，下方编号卡同步呈现成分与定位。",
      children: <SentenceStructureGraphic />,
    },
    {
      id: "translation",
      index: "04",
      label: "Translation",
      title: "句间双语对照",
      body: "原文按句子拆开，每一句的译文直接落在段间，保留英文排版，只在需要时低声校准。",
      children: <TranslationReadAlongGraphic />,
    },
  ];

  return (
    <section className="mx-auto w-full max-w-[88rem] px-5 py-24 sm:px-6 lg:px-8">
      <header className="mb-14 flex flex-col items-center gap-5 text-center sm:mb-16 md:mb-20">
        <span className="font-sans text-[0.7rem] font-bold tracking-[0.22em] text-ink-soft/65 uppercase">
          The Reading Lens · Core Capabilities
        </span>
        <h2 className="reader-serif max-w-3xl text-[clamp(2rem,4.8vw,2.85rem)] font-semibold leading-[1.12] tracking-[-0.018em] text-ink text-wrap-balance">
          四类输出标注，贴着原文展开。
        </h2>
        <p className="max-w-xl text-[0.98rem] font-normal leading-[1.78] text-ink-soft">
          词汇、语法、整句拆解和句级译文都锚定在 Reader 的原文位置，按需出现，解释完把注意力还给英文句子。
        </p>
      </header>

      <div className="relative">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-0 left-0 hidden w-6 lg:block"
          style={{
            backgroundImage:
              "repeating-linear-gradient(135deg, transparent 0 7px, rgba(17,17,17,0.085) 7px 8px)",
          }}
        />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-0 right-0 hidden w-6 lg:block"
          style={{
            backgroundImage:
              "repeating-linear-gradient(135deg, transparent 0 7px, rgba(17,17,17,0.085) 7px 8px)",
          }}
        />

        <ol
          className="grid grid-cols-1 md:grid-cols-2 md:auto-rows-fr"
          aria-label="Claread 核心能力"
        >
          {quadrants.map((quadrant, index) => (
            <li
              key={quadrant.id}
              className={cn(
                "relative flex h-full flex-col px-6 py-12 sm:px-10 sm:py-14",
                index % 2 === 0 ? "md:border-l md:border-l-hairline/65" : "md:border-l-0",
                index < 2 ? "md:border-t md:border-t-hairline/65" : "md:border-t-0",
                "md:border-r md:border-r-hairline/65 md:border-b md:border-b-hairline/65",
              )}
            >
              <div className="mb-5 flex items-baseline gap-3">
                <span className="reader-serif text-[0.78rem] font-medium tracking-[0.08em] text-ink-soft/55">
                  № {quadrant.index}
                </span>
                <span className="h-px flex-1 bg-hairline/65" aria-hidden="true" />
                <span className="font-sans text-[0.7rem] font-bold tracking-[0.18em] text-ink-soft/55 uppercase">
                  {quadrant.label}
                </span>
              </div>

              <FeatureStage label={quadrant.label} index={quadrant.index}>
                {quadrant.children}
              </FeatureStage>

              <div className="mt-12 flex flex-col gap-3">
                <h3 className="text-[1.18rem] font-semibold leading-[1.3] tracking-[-0.005em] text-ink">
                  {quadrant.title}
                </h3>
                <p className="max-w-[28rem] text-[0.95rem] leading-[1.72] text-ink-soft">
                  {quadrant.body}
                </p>
              </div>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

function FeatureStage({
  index,
  label,
  children,
}: {
  index: string;
  label: string;
  children: ReactNode;
}) {
  return (
    <div
      className="relative isolate flex h-[22rem] max-h-[22rem] min-h-[22rem] flex-1 flex-col overflow-hidden"
      data-feature-stage={index}
      aria-label={`${label} 演示`}
    >
      <div className="relative flex h-full flex-col justify-center overflow-hidden px-2 py-5 sm:px-4 sm:py-6">
        {children}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// 01 · Vocabulary — Animated notification stack
// ----------------------------------------------------------------------

type VocabGlossType =
  | "phrasal_verb"
  | "idiom"
  | "collocation"
  | "proper_noun"
  | "compound"
  | "context";

interface VocabCard {
  id: string;
  type: VocabGlossType;
  label: string;
  accentVar: string;
  labelTokenClass: string;
  anchor: string;
  zh: string;
  idx?: number;
}

const VOCAB_CARDS_RAW: Omit<VocabCard, "idx">[] = [
  {
    id: "phrase-ipo",
    type: "compound",
    label: "compound",
    labelTokenClass: "text-phrase-lavender",
    accentVar: "var(--reader-mark-phrase-ink)",
    anchor: "initial public offering (IPO)",
    zh: "首次公开募股；上市",
  },
  {
    id: "phrase-tied-up",
    type: "phrasal_verb",
    label: "phrasal_verb",
    labelTokenClass: "text-phrase-lavender",
    accentVar: "var(--reader-mark-phrase-ink)",
    anchor: "tied up in",
    zh: "被占用；被困在（指资金无法自由流动）",
  },
  {
    id: "phrase-netted",
    type: "collocation",
    label: "collocation",
    labelTokenClass: "text-phrase-lavender",
    accentVar: "var(--reader-mark-phrase-ink)",
    anchor: "netted him",
    zh: "使他净赚；让他获得（净利润）",
  },
  {
    id: "context-literally-you",
    type: "context",
    label: "context",
    labelTokenClass: "text-context-blue",
    accentVar: "var(--reader-mark-context-ink)",
    anchor: "literally you",
    zh: "字面意义上的你；真正的普通人",
  },
  {
    id: "phrase-rocket",
    type: "compound",
    label: "compound",
    labelTokenClass: "text-phrase-lavender",
    accentVar: "var(--reader-mark-phrase-ink)",
    anchor: "rocket boosters",
    zh: "火箭助推器",
  },
  {
    id: "phrase-ultimately",
    type: "collocation",
    label: "collocation",
    labelTokenClass: "text-phrase-lavender",
    accentVar: "var(--reader-mark-phrase-ink)",
    anchor: "ultimately beyond",
    zh: "最终超越（指超越月球和火星，去往更远的深空）",
  },
];

const VOCAB_CARDS: VocabCard[] = VOCAB_CARDS_RAW.map((card, idx) => ({
  ...card,
  idx,
}));

const VOCAB_CARD_HEIGHT_PX = 94;
const VOCAB_CARD_GAP_PX = 6;
const VOCAB_CARD_SLOT_PX = VOCAB_CARD_HEIGHT_PX + VOCAB_CARD_GAP_PX;
const VOCAB_CARD_STACK_LIMIT = 3;
const VOCAB_CARD_WINDOW_SIZE = VOCAB_CARD_STACK_LIMIT + 1;

function VocabularyNotificationGraphic() {
  const ref = useRef<HTMLDivElement | null>(null);
  const isInView = useInView(ref, { once: true, margin: "-80px" });
  const [revealedCount, setRevealedCount] = useState(0);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    if (!isInView) return;
    if (reducedMotion) {
      queueMicrotask(() => setRevealedCount(VOCAB_CARDS.length));
      return;
    }
    let cancelled = false;
    const timers: ReturnType<typeof setTimeout>[] = [];
    for (let i = 0; i < VOCAB_CARDS.length; i++) {
      const t = setTimeout(() => {
        if (!cancelled) setRevealedCount((c) => Math.max(c, i + 1));
      }, 900 * i + 80);
      timers.push(t);
    }
    return () => {
      cancelled = true;
      timers.forEach(clearTimeout);
    };
  }, [isInView, reducedMotion]);

  const stackSize = Math.min(revealedCount, VOCAB_CARD_STACK_LIMIT);
  const stackHeight = stackSize * VOCAB_CARD_HEIGHT_PX + Math.max(stackSize - 1, 0) * VOCAB_CARD_GAP_PX;
  const stagedCards = VOCAB_CARDS.slice(Math.max(0, revealedCount - VOCAB_CARD_WINDOW_SIZE), revealedCount)
    .reverse();

  return (
    <div
      ref={ref}
      className="relative h-full w-full max-w-[28rem] overflow-hidden"
      style={{
        WebkitMaskImage:
          "linear-gradient(to bottom, transparent 0, black 8%, black 84%, transparent 100%)",
        maskImage:
          "linear-gradient(to bottom, transparent 0, black 8%, black 84%, transparent 100%)",
      }}
      aria-live="polite"
      aria-label="词汇标注通知栏演示"
    >
      <div className="flex h-full w-full flex-col items-center justify-center overflow-hidden p-1">
        <div className="relative h-full w-full max-w-[24rem]">
          {stagedCards.map((card) => {
            const slot = revealedCount - (card.idx ?? 0) - 1;
            const isExiting = slot >= VOCAB_CARD_STACK_LIMIT;
            const targetY = -stackHeight / 2 + Math.min(slot, VOCAB_CARD_STACK_LIMIT) * VOCAB_CARD_SLOT_PX;

            return (
              <motion.div
                key={card.id}
                aria-hidden={isExiting}
                className="absolute left-0 right-0 top-1/2 h-[5.875rem] will-change-transform"
                data-vocab-card-slot={slot}
                initial={reducedMotion ? false : { opacity: 0, scale: 0.985, y: targetY - 14 }}
                animate={{
                  opacity: isExiting ? 0 : 1,
                  scale: isExiting ? 0.985 : 1,
                  y: targetY,
                }}
                transition={
                  reducedMotion
                    ? { duration: 0 }
                    : {
                        duration: isExiting ? 0.24 : 0.32,
                        ease: [0.22, 1, 0.36, 1],
                      }
                }
                style={{ zIndex: VOCAB_CARD_WINDOW_SIZE - slot }}
              >
                <NotificationCard card={card} />
              </motion.div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function NotificationCard({ card }: { card: VocabCard }) {
  return (
    <div className="flex h-full flex-col justify-center rounded-[10px] border border-hairline/75 bg-paper-deep/55 px-3.5 py-2.5 shadow-[0_4px_14px_rgba(17,17,17,0.05)]">
      <div className="flex items-center gap-1.5">
        <span
          className={cn(
            "text-[0.66rem] font-bold tracking-[0.14em] uppercase truncate",
            card.labelTokenClass,
          )}
        >
          {card.label}
        </span>
        <span className="text-ink-soft/30">·</span>
        <span className="text-[0.62rem] font-mono tracking-[0.04em] text-ink-soft/65 truncate">
          {card.type}
        </span>
      </div>
      <span
        className="reader-serif mt-1 block text-[1.05rem] font-semibold leading-tight truncate"
        style={{ color: card.accentVar }}
      >
        {card.anchor}
      </span>
      <p className="mt-1 text-[0.78rem] leading-[1.5] text-ink truncate">
        {card.zh}
      </p>
    </div>
  );
}

// ----------------------------------------------------------------------
// 02 · Grammar — Inline highlight + grammar note drop
// ----------------------------------------------------------------------

function GrammarNoteGraphic() {
  const ref = useRef<HTMLDivElement | null>(null);
  const isInView = useInView(ref, { once: true, margin: "-80px" });
  const [noteOpen, setNoteOpen] = useState(false);
  const [hoverHold, setHoverHold] = useState(false);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    if (!isInView) return;

    let cancelled = false;
    const cycle = async () => {
      while (!cancelled) {
        await wait(1400, reducedMotion);
        if (cancelled) return;
        setNoteOpen(true);
        await wait(5500, reducedMotion);
        if (cancelled) return;
        setNoteOpen(false);
        await wait(800, reducedMotion);
      }
    };
    void cycle();
    return () => {
      cancelled = true;
    };
  }, [isInView, reducedMotion]);

  const showNote = noteOpen || hoverHold;

  return (
    <div
      ref={ref}
      className="relative flex h-full w-full max-w-[28rem] flex-col justify-center"
      aria-label="情境语法旁注演示"
      onMouseEnter={() => setHoverHold(true)}
      onMouseLeave={() => setHoverHold(false)}
    >
      <p className="reader-serif text-[0.98rem] leading-[1.85] text-ink/85 text-center">
        <span>Nationally, one in six children miss 15 or more days of school. </span>
        <span
          className={cn(
            "reader-mark--grammar-segment reader-mark--grammar-pinned",
            "transition-colors duration-300",
          )}
        >
          The problem is not that every word is unknown
        </span>
        <span>, </span>
        <Highlighter
          active
          color="rgba(95, 78, 138, 0.18)"
          padding={3}
          animationDuration={600}
          className="font-medium text-ink"
        >
          but that
        </Highlighter>
        <span> a sentence hides the tested information inside clauses.</span>
      </p>

      <AnimatePresence initial={false}>
        {showNote ? (
          <motion.section
            key="note"
            initial={{ opacity: 0, y: -6, height: 0 }}
            animate={{ opacity: 1, y: 0, height: "auto" }}
            exit={{ opacity: 0, y: -4, height: 0 }}
            transition={{ duration: 0.32, ease: [0.22, 1, 0.36, 1] }}
            className="mt-3 overflow-hidden"
          >
            <div
              className="reader-entry-note reader-entry-note--grammar-note rounded-[8px] border border-grammar-violet/[0.22] bg-grammar-violet/[0.06] px-4 py-3"
              aria-label="语法旁注 · not A, but B"
            >
              <header className="flex items-center justify-between gap-3">
                <span
                  className="text-[0.72rem] font-bold tracking-[0.08em] text-grammar-violet"
                  style={{ color: "var(--reader-entry-note-grammar-accent)" }}
                >
                  语法旁注 · not A, but B
                </span>
                <span
                  className="text-[0.72rem] font-bold text-grammar-violet"
                  style={{ color: "var(--reader-entry-note-grammar-accent)" }}
                >
                  ①
                </span>
              </header>
              <p className="mt-2 border-t border-hairline/60 pt-2 text-[0.82rem] leading-[1.62] text-ink-soft">
                先排除
                <span className="font-semibold text-grammar-violet"> not that every word is unknown </span>
                （不是每个词都不认识），真正原因落在
                <span className="font-semibold text-grammar-violet"> but that </span>
                之后：信息藏进了从句里。
              </p>
              <p className="mt-1.5 text-[0.78rem] leading-[1.58] text-ink-soft/85">
                <span className="font-semibold text-ink-soft">判别：</span>
                读懂 A 就以为读懂全文，说明 not A 是干扰项；真正卡住你的是 but that 引导的从句。
              </p>
            </div>
          </motion.section>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

// ----------------------------------------------------------------------
// 03 · Structure — Sentence X-Ray (analysis-tone cycle)
// ----------------------------------------------------------------------

interface StructureChunk {
  id: number;
  text: string;
  label: string;
  tone: 1 | 2 | 3 | 4 | 5 | 6;
}

function SentenceStructureGraphic() {
  const ref = useRef<HTMLDivElement | null>(null);
  const isInView = useInView(ref, { once: true, margin: "-80px" });
  const [activeId, setActiveId] = useState<number | null>(null);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    if (!isInView) return;
    if (reducedMotion) {
      queueMicrotask(() => setActiveId(1));
      return;
    }
    queueMicrotask(() => setActiveId(0));
    const timer = window.setInterval(() => {
      setActiveId((current) => {
        if (current === null) return 1;
        return current >= STRUCTURE_CHUNKS.length ? 1 : current + 1;
      });
    }, 2400);
    return () => window.clearInterval(timer);
  }, [isInView, reducedMotion]);

  return (
    <div ref={ref} className="flex h-full w-full max-w-[28rem] flex-col gap-3.5" aria-label="句级透视拆解演示">
      <p className="reader-serif text-[0.98rem] leading-[1.85] text-ink/85 text-center">
        {STRUCTURE_CHUNKS.map((chunk) => (
          <StructureAtom
            key={chunk.id}
            chunk={chunk}
            isActive={activeId === chunk.id}
            reducedMotion={reducedMotion ?? false}
          />
        ))}
      </p>

      <ol className="reader-entry-analysis-list flex-1">
        {STRUCTURE_CHUNKS.map((chunk, index) => {
          return (
            <li
              key={chunk.id}
              className="reader-entry-analysis-item reader-entry-analysis-item-tint"
              style={{ ["--analysis-accent" as string]: `var(--reader-analysis-tone-${chunk.tone})` }}
            >
              <span
                className={`reader-analysis-row-index reader-analysis-row-index--${chunk.tone}`}
                aria-hidden="true"
              >
                {index + 1}
              </span>
              <div className="flex flex-col gap-0.5">
                <span className="reader-entry-analysis-label">{chunk.label}</span>
                <span className="reader-entry-analysis-text text-ink-soft">
                  {chunk.text}
                </span>
                <span className="mt-0.5 text-[0.7rem] leading-[1.5] text-ink-soft/70">
                  {chunk.note}
                </span>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

interface StructureChunk {
  id: number;
  text: string;
  label: string;
  tone: 1 | 2 | 3 | 4 | 5 | 6;
  note: string;
}

const STRUCTURE_CHUNKS: StructureChunk[] = [
  { id: 1, text: "This design reduces the risk", label: "主干", tone: 1, note: "主谓宾：This design（主语）reduces（谓语）the risk（宾语）" },
  { id: 2, text: "that an explanation becomes detached", label: "定语从句", tone: 2, note: "that 引导的定语从句修饰 the risk，先行词在从句中作主语" },
  { id: 3, text: "from the passage while keeping the original sentence in view.", label: "伴随结构", tone: 3, note: "while + 现在分词构成伴随状语，与主句主语 this design 保持一致" },
];

function StructureAtom({
  chunk,
  isActive,
  reducedMotion,
}: {
  chunk: StructureChunk;
  isActive: boolean;
  reducedMotion: boolean;
}) {
  return (
    <span className="inline">
      <motion.span
        className={cn(
          "reader-analysis-atom",
          `reader-analysis-atom--${chunk.tone}`,
          isActive && "reader-analysis-atom--active",
        )}
        animate={
          reducedMotion || isActive
            ? { opacity: 1 }
            : { opacity: [0.55, 0.78, 0.55] }
        }
        transition={
          reducedMotion
            ? { duration: 0 }
            : { duration: 2.4, repeat: Infinity, ease: "easeInOut" }
        }
      >
        {chunk.text}
      </motion.span>
      {" "}
    </span>
  );
}

// ----------------------------------------------------------------------
// 04 · Translation — Sentence-by-sentence read-along
// ----------------------------------------------------------------------

interface TranslationSentence {
  english: string;
  chinese: string;
}

const TRANSLATION_SENTENCES: TranslationSentence[] = [
  {
    english: "Claread keeps each sentence visible.",
    chinese: "Claread 让每个句子留在视野里。",
  },
  {
    english: "It opens vocabulary, grammar, and meaning only where they help.",
    chinese: "它只在真正有帮助的地方展开词汇、语法和含义。",
  },
  {
    english: "The translation stays below the original, so you can check understanding without leaving English.",
    chinese: "译文落在原文下方，保留英文阅读视野。",
  },
  {
    english: "When you finish, the paragraph becomes a piece you actually read.",
    chinese: "读完之后，段落才真正被你读过一遍。",
  },
];

function TranslationReadAlongGraphic() {
  const ref = useRef<HTMLDivElement | null>(null);
  const isInView = useInView(ref, { once: true, margin: "-80px" });
  const reducedMotion = useReducedMotion();

  return (
    <div
      ref={ref}
      className="relative h-full w-full max-w-[28rem] pl-9 text-left"
      aria-label="句间双语对照演示"
    >
      <span
        className="reader-immersive-paragraph-cue absolute left-0 top-1 hidden h-6 w-6 items-center justify-center rounded-full border border-hairline/70 bg-paper text-[0.6rem] font-bold text-ink-soft/70 sm:inline-flex"
        aria-hidden="true"
      >
        01
      </span>
      <div className="flex h-full flex-col justify-center gap-3.5">
        {TRANSLATION_SENTENCES.map((sentence, index) => (
          <TranslationRow
            key={sentence.english}
            index={index}
            sentence={sentence}
            visible={isInView}
            reducedMotion={reducedMotion ?? false}
          />
        ))}
      </div>
    </div>
  );
}

function TranslationRow({
  index,
  sentence,
  visible,
  reducedMotion,
}: {
  index: number;
  sentence: TranslationSentence;
  visible: boolean;
  reducedMotion: boolean;
}) {
  return (
    <motion.div
      className="flex flex-col gap-2"
      initial={{ opacity: 0, y: 6 }}
      animate={visible ? { opacity: 1, y: 0 } : { opacity: 0, y: 6 }}
      transition={{
        duration: 0.45,
        delay: reducedMotion ? 0 : 0.12 * index,
        ease: [0.22, 1, 0.36, 1],
      }}
    >
      <div className="flex items-baseline gap-2">
        <span className="font-sans text-[0.7rem] font-bold tracking-[0.16em] text-ink-soft/55">
          0{index + 1}/0{TRANSLATION_SENTENCES.length}
        </span>
      </div>
      <p className="reader-serif text-[1.08rem] leading-[1.75] text-ink">
        {sentence.english}
      </p>
      <p className="reader-translation-copy pl-0 text-[0.94rem] leading-[1.7] text-ink-soft/85">
        {sentence.chinese}
      </p>
    </motion.div>
  );
}

// ----------------------------------------------------------------------
// Shared helpers
// ----------------------------------------------------------------------

function useReducedMotion() {
  const [reducedMotion, setReducedMotion] = useState<boolean | null>(null);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    queueMicrotask(() => setReducedMotion(media.matches));
    const handler = (event: MediaQueryListEvent) => setReducedMotion(event.matches);
    media.addEventListener("change", handler);
    return () => media.removeEventListener("change", handler);
  }, []);
  return reducedMotion;
}

function wait(duration: number, reducedMotion: boolean | null) {
  if (reducedMotion) {
    return new Promise<void>((resolve) => window.setTimeout(resolve, 0));
  }
  return new Promise<void>((resolve) => window.setTimeout(resolve, duration));
}
