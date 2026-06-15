"use client";

import { AnimatePresence, motion, useInView } from "framer-motion";
import { useEffect, useRef, useState, useMemo, type ReactNode } from "react";
import { Sparkles, Flag, ChevronDown } from "lucide-react";
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
  const [containerOpacity, setContainerOpacity] = useState(1);
  const isPausedRef = useRef(false);
  const reducedMotion = useReducedMotion();
  const isReduced = reducedMotion === true;

  useEffect(() => {
    if (!isInView) return;
    if (isReduced) {
      setRevealedCount(VOCAB_CARDS.length);
      return;
    }

    let cancelled = false;
    const runCycle = async () => {
      while (!cancelled) {
        // Step 1: Reset state and fade in the container
        setContainerOpacity(1);
        setRevealedCount(0);
        await waitWithPause(500, isPausedRef, reducedMotion);
        if (cancelled) break;

        // Step 2: Reveal cards one by one
        for (let i = 0; i < VOCAB_CARDS.length; i++) {
          if (cancelled) break;
          setRevealedCount(i + 1);
          await waitWithPause(900, isPausedRef, reducedMotion);
        }
        if (cancelled) break;

        // Step 3: Wait at the final stack for the user to read
        await waitWithPause(2500, isPausedRef, reducedMotion);
        if (cancelled) break;

        // Step 4: Fade out the container
        setContainerOpacity(0);
        await waitWithPause(600, isPausedRef, reducedMotion);
      }
    };

    runCycle();

    return () => {
      cancelled = true;
    };
  }, [isInView, reducedMotion, isReduced]);

  const stackSize = Math.min(revealedCount, VOCAB_CARD_STACK_LIMIT);
  const stackHeight = stackSize * VOCAB_CARD_HEIGHT_PX + Math.max(stackSize - 1, 0) * VOCAB_CARD_GAP_PX;
  const stagedCards = VOCAB_CARDS.slice(Math.max(0, revealedCount - VOCAB_CARD_WINDOW_SIZE), revealedCount)
    .reverse();

  return (
    <div
      ref={ref}
      className="relative h-full w-full max-w-[28rem] overflow-hidden cursor-help"
      style={{
        WebkitMaskImage:
          "linear-gradient(to bottom, transparent 0, black 8%, black 84%, transparent 100%)",
        maskImage:
          "linear-gradient(to bottom, transparent 0, black 8%, black 84%, transparent 100%)",
      }}
      aria-live="polite"
      aria-label="词汇标注通知栏演示"
      onMouseEnter={() => { isPausedRef.current = true; }}
      onMouseLeave={() => { isPausedRef.current = false; }}
    >
      <div className="flex h-full w-full flex-col items-center justify-center overflow-hidden p-1">
        <motion.div
          className="relative h-full w-full max-w-[24rem]"
          animate={isReduced ? { opacity: 1 } : { opacity: containerOpacity }}
          transition={{ duration: 0.4, ease: "easeInOut" }}
        >
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
                initial={isReduced ? false : { opacity: 0, scale: 0.985, y: targetY - 14 }}
                animate={{
                  opacity: isExiting ? 0 : 1,
                  scale: isExiting ? 0.985 : 1,
                  y: targetY,
                }}
                transition={
                  isReduced
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
        </motion.div>
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

interface TypewriterSegment {
  text: string;
  highlight?: boolean;
}

function TypewriterText({
  segments,
  active,
  speed = 25,
  onComplete,
}: {
  segments: TypewriterSegment[];
  active: boolean;
  speed?: number;
  onComplete?: () => void;
}) {
  const [charCount, setCharCount] = useState(0);
  const onCompleteRef = useRef(onComplete);

  useEffect(() => {
    onCompleteRef.current = onComplete;
  }, [onComplete]);

  const totalChars = useMemo(() => {
    return segments.reduce((acc, seg) => acc + seg.text.length, 0);
  }, [segments]);

  useEffect(() => {
    if (!active) {
      setCharCount(0);
      return;
    }

    let currentCount = 0;
    const interval = setInterval(() => {
      currentCount++;
      setCharCount(currentCount);
      if (currentCount >= totalChars) {
        clearInterval(interval);
        onCompleteRef.current?.();
      }
    }, speed);

    return () => clearInterval(interval);
  }, [active, totalChars, speed]);

  let renderedChars = 0;
  return (
    <>
      {segments.map((seg, sIdx) => {
        if (renderedChars >= charCount) return null;

        const charsToShow = Math.min(seg.text.length, charCount - renderedChars);
        renderedChars += seg.text.length;

        const displayText = seg.text.slice(0, charsToShow);

        return (
          <span
            key={sIdx}
            className={seg.highlight ? "font-semibold text-grammar-violet font-reading" : ""}
          >
            {displayText}
          </span>
        );
      })}
    </>
  );
}

const PARAGRAPH_1_SEGMENTS = [
  { text: "这是 wants to be able to 后面的三个并列不定式短语（to 被省略）。前两个完整写出 " },
  { text: "take you to...", highlight: true },
  { text: "，第三个 " },
  { text: "beyond", highlight: true },
  { text: " 是副词，省略了动词 take you to，意为“最终去往更远的地方（如火星之外）”。" }
];

const PARAGRAPH_2_SEGMENTS = [
  { text: "阅读时需识别这种并列结构的省略，避免误解 " },
  { text: "beyond", highlight: true },
  { text: " 的词性。" }
];

function GrammarNoteGraphic() {
  const ref = useRef<HTMLDivElement | null>(null);
  const isInView = useInView(ref, { once: true, margin: "-80px" });
  const [step, setStep] = useState<0 | 1 | 2 | 3>(0);
  const [hoverHold, setHoverHold] = useState(false);
  const [p1Active, setP1Active] = useState(false);
  const [p2Active, setP2Active] = useState(false);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    if (!isInView || hoverHold) return;

    let cancelled = false;
    const cycle = async () => {
      while (!cancelled) {
        await wait(1200, reducedMotion);
        if (cancelled) return;
        setStep(1); // Start underline
        await wait(600, reducedMotion);
        if (cancelled) return;
        setStep(2); // Start highlight
        await wait(500, reducedMotion);
        if (cancelled) return;
        setStep(3); // Drop note
        await wait(8000, reducedMotion);
        if (cancelled) return;
        setStep(0); // Reset
        await wait(800, reducedMotion);
      }
    };
    void cycle();
    return () => {
      cancelled = true;
    };
  }, [isInView, reducedMotion, hoverHold]);

  useEffect(() => {
    if (step < 3 && !hoverHold) {
      setP1Active(false);
      setP2Active(false);
    }
  }, [step, hoverHold]);

  const showUnderline = step >= 1 || hoverHold;
  const showHighlight = step >= 2 || hoverHold;
  const showNote = step >= 3 || hoverHold;

  return (
    <div
      ref={ref}
      className="relative flex h-full w-full max-w-[28rem] flex-col justify-center"
      aria-label="情境语法旁注演示"
      onMouseEnter={() => {
        setHoverHold(true);
        setStep(3); // Instantly drop note on hover
      }}
      onMouseLeave={() => {
        setHoverHold(false);
        setStep(0); // Reset on hover exit
      }}
    >
      <p className="reader-serif text-[1.05rem] leading-[1.8] text-ink/85 text-left">
        <span className="block">At the IPO, he said: "Whoever you are watching this,</span>
        <span className="block">SpaceX wants to be able to</span>
        <span className="block">
          <Highlighter
            active={showUnderline}
            action="underline"
            color="var(--ink)"
            delay={0}
            animationDuration={400}
            strokeWidth={1}
            padding={2}
          >
            take you to the Moon, take you to Mars, and
          </Highlighter>
        </span>
        <span className="block mt-1">
          <Highlighter
            active={showHighlight}
            action="highlight"
            color="rgba(95, 78, 138, 0.18)"
            delay={0}
            animationDuration={300}
            padding={3}
            className="font-medium text-ink"
          >
            ultimately beyond
          </Highlighter>
          ."
        </span>
      </p>

      {/* Note Drop Container */}
      <div className="relative mt-5 min-h-[13rem] w-full">
        <AnimatePresence initial={false}>
          {showNote && (
            <motion.div
              key="note-drop"
              className="absolute left-0 top-0 w-full"
              initial={{
                opacity: 0,
                y: 12,
              }}
              animate={{
                opacity: 1,
                y: 0,
              }}
              exit={{
                opacity: 0,
                y: 6,
              }}
              transition={{
                duration: 0.4,
                ease: [0.22, 1, 0.36, 1], // ease-out-quint
              }}
              onAnimationComplete={() => {
                if (showNote) {
                  setP1Active(true);
                }
              }}
            >
              <div
                className="relative overflow-hidden rounded-[12px] bg-[#FDFCFB] border border-hairline/70 shadow-[0_12px_32px_rgba(28,24,18,0.08),0_1px_2px_rgba(17,17,17,0.04)] before:absolute before:inset-0 before:pointer-events-none before:rounded-[12px] before:border before:border-white/60 before:shadow-[inset_0_1px_0_rgba(255,255,255,0.8)]"
                aria-label="语法旁注 · 宾语从句中的省略与并列"
              >
                <header className="bg-grammar-violet/[0.04] px-5 py-3 border-b border-hairline/50 flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2.5">
                    <span className="inline-flex items-center justify-center rounded-full bg-grammar-violet/10 px-2.5 py-0.5 text-[0.68rem] font-bold text-grammar-violet tracking-wide">
                      语法旁注
                    </span>
                    <span className="text-[0.72rem] font-semibold text-ink-soft/90">
                      宾语从句中的省略与并列
                    </span>
                  </div>
                  <span className="flex h-4 w-4 items-center justify-center text-grammar-violet/70">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                      <path d="M12 2C12 7.5 16.5 12 22 12C16.5 12 12 16.5 12 22C12 16.5 7.5 12 2 12C7.5 12 12 7.5 12 2Z" />
                    </svg>
                  </span>
                </header>

                <div className="px-5 py-4 flex flex-col gap-3.5 text-[0.82rem] leading-[1.65] text-ink-soft/95 font-sans">
                  <p className="min-h-[3rem]">
                    <TypewriterText
                      segments={PARAGRAPH_1_SEGMENTS}
                      active={p1Active}
                      speed={20}
                      onComplete={() => setP2Active(true)}
                    />
                  </p>

                  <AnimatePresence>
                    {p2Active && (
                      <motion.div
                        initial={{ opacity: 0, y: 4 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
                        className="rounded-[8px] border border-hairline/45 bg-grammar-violet/[0.02] p-3 text-[0.78rem] leading-[1.6] text-ink-soft/85 flex gap-2 items-start"
                      >
                        <svg className="w-3.5 h-3.5 text-grammar-violet/85 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                        </svg>
                        <p className="flex-1">
                          <TypewriterText
                            segments={PARAGRAPH_2_SEGMENTS}
                            active={p2Active}
                            speed={20}
                          />
                        </p>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
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
  tone: 1 | 2 | 3;
}

const STRUCTURE_CHUNKS: StructureChunk[] = [
  { id: 1, text: "This design reduces the risk", label: "主干", tone: 1 },
  { id: 2, text: "that an explanation becomes detached", label: "同位语从句", tone: 2 },
  { id: 3, text: "from the passage while keeping the original sentence in view.", label: "伴随结构", tone: 3 },
];

const SENTENCE_SUMMARY = "本句的主干是 This design reduces the risk（主谓宾结构），宾语 risk 后面接了一个由 that 引导的同位语从句进行解释说明，最后使用 while 引导的现在分词短语作伴随状语，补充说明主干动作发生时的状态。";

function SentenceStructureGraphic() {
  const ref = useRef<HTMLDivElement | null>(null);
  const isInView = useInView(ref, { once: true, margin: "-80px" });
  const [step, setStep] = useState<0 | 1 | 2 | 3>(0);
  const [hoverId, setHoverId] = useState<number | null>(null);
  const [hoverHold, setHoverHold] = useState(false);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    if (!isInView || hoverHold || hoverId !== null) return;

    let cancelled = false;
    const t = setTimeout(() => {
      if (!cancelled) {
        setStep((prev) => (prev === 3 ? 0 : prev + 1) as 0 | 1 | 2 | 3);
      }
    }, reducedMotion ? 0 : 2000);

    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [isInView, reducedMotion, hoverHold, hoverId, step]);

  const activeId = hoverId !== null ? hoverId : step;

  const isReduced = reducedMotion === true;
  const cubicBezier: [number, number, number, number] = [0.22, 1, 0.36, 1];

  const verticalAnim = isReduced ? {
    initial: { scaleY: 1 },
    animate: { scaleY: 1 },
    transition: { duration: 0 }
  } : {
    initial: { scaleY: 0 },
    animate: { scaleY: 1 },
    transition: { duration: 0.2, ease: cubicBezier }
  };

  const horizontalAnim = isReduced ? {
    initial: { pathLength: 1 },
    animate: { pathLength: 1 },
    transition: { duration: 0 }
  } : {
    initial: { pathLength: 0 },
    animate: { pathLength: 1 },
    transition: { duration: 0.15, delay: 0.2, ease: "easeInOut" as const }
  };

  const topDotAnim = isReduced ? {
    initial: { scale: 1, opacity: 1 },
    animate: { scale: 1, opacity: 1 },
    transition: { duration: 0 }
  } : {
    initial: { scale: 0, opacity: 0 },
    animate: { scale: 1, opacity: 1 },
    transition: { duration: 0.15, ease: cubicBezier }
  };

  const bottomDotAnim = isReduced ? {
    initial: { scale: 1, opacity: 1 },
    animate: { scale: 1, opacity: 1 },
    transition: { duration: 0 }
  } : {
    initial: { scale: 0, opacity: 0 },
    animate: { scale: 1, opacity: 1 },
    transition: { duration: 0.1, delay: 0.35, ease: cubicBezier }
  };

  return (
    <div
      ref={ref}
      className="flex h-full w-full max-w-[28rem] flex-col justify-center"
      aria-label="句级透视拆解演示"
      onMouseEnter={() => setHoverHold(true)}
      onMouseLeave={() => {
        setHoverHold(false);
        setHoverId(null);
      }}
    >
      <p className="reader-serif text-[1.02rem] leading-[1.85] text-ink/85 text-left mb-6">
        {STRUCTURE_CHUNKS.map((chunk) => (
          <StructureAtom
            key={chunk.id}
            chunk={chunk}
            isActive={activeId === chunk.id}
            onMouseEnter={() => setHoverId(chunk.id)}
            onMouseLeave={() => setHoverId(null)}
          />
        ))}
      </p>

      {/* Upgraded Typographic Waterfall Tree */}
      <div className="relative mt-2 min-h-[12.5rem] w-full flex flex-col gap-1.5 pl-2">
        <AnimatePresence initial={false}>
          {STRUCTURE_CHUNKS.filter(
            (chunk) => hoverHold || hoverId !== null || step >= chunk.id
          ).map((chunk) => {
            const isItemActive = activeId === chunk.id;
            
            // Calculate indentation styles
            const indentClass = 
              chunk.id === 1 ? "pl-0" :
              chunk.id === 2 ? "pl-9" :
              "pl-18";

            // Accent tone color mix
            const toneAccent = `var(--reader-analysis-tone-${chunk.tone})`;
            const circleNumbers = ["①", "②", "③"];
            const labelWithCircle = `${circleNumbers[chunk.id - 1]} ${chunk.label}`;

            const isRow2Visible = hoverHold || hoverId !== null || step >= 2;
            const isRow3Visible = hoverHold || hoverId !== null || step >= 3;

            return (
              <motion.div
                key={chunk.id}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: isReduced ? 0 : 0.2, ease: [0.22, 1, 0.36, 1] }}
                className={cn(
                  "relative flex items-start pr-3.5 py-2.5 rounded-[8px] transition-all duration-300 cursor-pointer select-none",
                  indentClass
                )}
                onMouseEnter={() => setHoverId(chunk.id)}
                onMouseLeave={() => setHoverId(null)}
              >
                {/* L-Shape Guide Lines rendered inside the parent rows to be layout-safe */}
                {chunk.id === 1 && (
                  <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: isRow2Visible ? 1 : 0 }}
                    transition={{ duration: isRow2Visible ? 0 : 0.2 }}
                    className="absolute inset-0 pointer-events-none"
                  >
                    {/* Vertical line container */}
                    <div className="absolute left-[10px] top-[42px] bottom-[-15px] w-[1px] pointer-events-none">
                      {/* Inactive line (base) */}
                      <div className="absolute inset-0 bg-hairline transition-opacity duration-300" style={{ opacity: activeId >= 2 ? 0 : 1 }} />
                      
                      {/* Active glowing line */}
                      <motion.div 
                        initial={{ scaleY: 0, opacity: 0 }}
                        animate={{ 
                          scaleY: activeId >= 2 ? 1 : 0,
                          opacity: activeId >= 2 ? 1 : 0 
                        }}
                        transition={verticalAnim.transition}
                        className="absolute inset-0 pointer-events-none"
                        style={{ 
                          backgroundColor: "var(--reader-analysis-tone-2)", 
                          originY: 0,
                          boxShadow: "0 0 4px var(--reader-analysis-tone-2)" 
                        }}
                      />

                      {/* Light particle sliding down (transient, only runs on activation) */}
                      {activeId >= 2 && (
                        <motion.div
                          key={`v-particle-1-${activeId}`}
                          initial={{ top: "-8px", opacity: 0 }}
                          animate={{
                            top: ["-8px", "100%"],
                            opacity: [0, 1, 1, 0]
                          }}
                          transition={{
                            duration: 0.2,
                            ease: "linear"
                          }}
                          className="absolute left-[-0.5px] w-[2px] h-[8px] rounded-full pointer-events-none"
                          style={{
                            background: `linear-gradient(to bottom, transparent, var(--reader-analysis-tone-2), transparent)`
                          }}
                        />
                      )}
                    </div>

                    {/* Horizontal box (SVG) */}
                    <div className="absolute left-[10px] bottom-[-21px] w-[18px] h-[6px] pointer-events-none">
                      <svg width="18" height="6" viewBox="0 0 18 6" className="absolute inset-0 overflow-visible">
                        {/* Inactive bend path */}
                        <path 
                          d="M 0.5,0 L 0.5,0.5 A 5,5 0 0,0 5.5,5.5 L 18,5.5" 
                          fill="none" 
                          stroke="var(--hairline)" 
                          strokeWidth={1} 
                          strokeLinecap="round"
                          className="transition-opacity duration-300"
                          style={{ opacity: activeId >= 2 ? 0 : 1 }}
                        />
                        {/* Active bend paths */}
                        {/* Glow layer */}
                        <motion.path 
                          initial={{ pathLength: 0, opacity: 0 }}
                          animate={{ 
                            pathLength: activeId >= 2 ? 1 : 0,
                            opacity: activeId >= 2 ? 1 : 0
                          }}
                          transition={{
                            duration: 0.15,
                            delay: activeId >= 2 ? 0.2 : 0,
                            ease: "easeInOut" as const
                          }}
                          d="M 0.5,0 L 0.5,0.5 A 5,5 0 0,0 5.5,5.5 L 18,5.5" 
                          fill="none" 
                          stroke="var(--reader-analysis-tone-2)" 
                          strokeWidth={3} 
                          strokeLinecap="round"
                          className="opacity-20 blur-[1px]"
                        />
                        {/* Main stroke layer */}
                        <motion.path 
                          initial={{ pathLength: 0, opacity: 0 }}
                          animate={{ 
                            pathLength: activeId >= 2 ? 1 : 0,
                            opacity: activeId >= 2 ? 1 : 0
                          }}
                          transition={{
                            duration: 0.15,
                            delay: activeId >= 2 ? 0.2 : 0,
                            ease: "easeInOut" as const
                          }}
                          d="M 0.5,0 L 0.5,0.5 A 5,5 0 0,0 5.5,5.5 L 18,5.5" 
                          fill="none" 
                          stroke="var(--reader-analysis-tone-2)" 
                          strokeWidth={1} 
                          strokeLinecap="round"
                        />
                        {/* Light particle flowing horizontally */}
                        {activeId >= 2 && (
                          <motion.circle
                            key={`h-particle-1-${activeId}`}
                            r={1.5}
                            fill="var(--reader-analysis-tone-2)"
                            className="pointer-events-none"
                            style={{ filter: "drop-shadow(0 0 2px var(--reader-analysis-tone-2))" }}
                            initial={{ cx: 0.5, cy: 0, opacity: 0 }}
                            animate={{
                              cx: [0.5, 0.5, 5.5, 18],
                              cy: [0, 0.5, 5.5, 5.5],
                              opacity: [0, 1, 1, 0]
                            }}
                            transition={{
                              duration: 0.15,
                              delay: 0.2,
                              times: [0, 0.18, 0.46, 1],
                              ease: "linear"
                            }}
                          />
                        )}
                      </svg>
                    </div>

                    {/* Top Dot */}
                    <motion.div 
                      initial={{ scale: 0, opacity: 0 }}
                      animate={{ 
                        scale: activeId >= 2 ? 1 : 0,
                        opacity: activeId >= 2 ? 1 : 0
                      }}
                      transition={topDotAnim.transition}
                      className="absolute left-[8px] top-[40px] w-1.5 h-1.5 rounded-full pointer-events-none flex items-center justify-center"
                      style={{ color: activeId >= 2 ? "var(--reader-analysis-tone-2)" : "var(--hairline)" }}
                    >
                      <div className="w-full h-full rounded-full bg-current" />
                      {activeId >= 2 && !isReduced && (
                        <motion.span 
                          animate={{ scale: [1, 2.2], opacity: [0.5, 0] }} 
                          transition={{ duration: 1.6, repeat: Infinity, ease: "easeOut" }} 
                          className="absolute inset-0 rounded-full bg-current pointer-events-none" 
                        />
                      )}
                    </motion.div>

                    {/* Bottom Dot */}
                    <motion.div 
                      initial={{ scale: 0, opacity: 0 }}
                      animate={{ 
                        scale: activeId >= 2 ? 1 : 0,
                        opacity: activeId >= 2 ? 1 : 0
                      }}
                      transition={{
                        duration: 0.15,
                        delay: activeId >= 2 ? 0.35 : 0,
                        ease: cubicBezier
                      }}
                      className="absolute left-[25px] bottom-[-24px] w-1.5 h-1.5 rounded-full pointer-events-none flex items-center justify-center"
                      style={{ color: activeId >= 2 ? "var(--reader-analysis-tone-2)" : "var(--hairline)" }}
                    >
                      <div className="w-full h-full rounded-full bg-current" />
                      {activeId >= 2 && !isReduced && (
                        <motion.span 
                          animate={{ scale: [1, 2.2], opacity: [0.5, 0] }} 
                          transition={{ duration: 1.6, repeat: Infinity, ease: "easeOut", delay: 0.5 }} 
                          className="absolute inset-0 rounded-full bg-current pointer-events-none" 
                        />
                      )}
                    </motion.div>
                  </motion.div>
                )}
                {chunk.id === 2 && (
                  <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: isRow3Visible ? 1 : 0 }}
                    transition={{ duration: isRow3Visible ? 0 : 0.2 }}
                    className="absolute inset-0 pointer-events-none"
                  >
                    {/* Vertical line container */}
                    <div className="absolute left-[46px] top-[42px] bottom-[-15px] w-[1px] pointer-events-none">
                      {/* Inactive line (base) */}
                      <div className="absolute inset-0 bg-hairline transition-opacity duration-300" style={{ opacity: activeId >= 3 ? 0 : 1 }} />
                      
                      {/* Active glowing line */}
                      <motion.div 
                        initial={{ scaleY: 0, opacity: 0 }}
                        animate={{ 
                          scaleY: activeId >= 3 ? 1 : 0,
                          opacity: activeId >= 3 ? 1 : 0 
                        }}
                        transition={verticalAnim.transition}
                        className="absolute inset-0 pointer-events-none"
                        style={{ 
                          backgroundColor: "var(--reader-analysis-tone-3)", 
                          originY: 0,
                          boxShadow: "0 0 4px var(--reader-analysis-tone-3)" 
                        }}
                      />

                      {/* Light particle sliding down */}
                      {activeId >= 3 && (
                        <motion.div
                          key={`v-particle-2-${activeId}`}
                          initial={{ top: "-8px", opacity: 0 }}
                          animate={{
                            top: ["-8px", "100%"],
                            opacity: [0, 1, 1, 0]
                          }}
                          transition={{
                            duration: 0.2,
                            ease: "linear"
                          }}
                          className="absolute left-[-0.5px] w-[2px] h-[8px] rounded-full pointer-events-none"
                          style={{
                            background: `linear-gradient(to bottom, transparent, var(--reader-analysis-tone-3), transparent)`
                          }}
                        />
                      )}
                    </div>

                    {/* Horizontal box (SVG) */}
                    <div className="absolute left-[46px] bottom-[-21px] w-[18px] h-[6px] pointer-events-none">
                      <svg width="18" height="6" viewBox="0 0 18 6" className="absolute inset-0 overflow-visible">
                        {/* Inactive bend path */}
                        <path 
                          d="M 0.5,0 L 0.5,0.5 A 5,5 0 0,0 5.5,5.5 L 18,5.5" 
                          fill="none" 
                          stroke="var(--hairline)" 
                          strokeWidth={1} 
                          strokeLinecap="round"
                          className="transition-opacity duration-300"
                          style={{ opacity: activeId >= 3 ? 0 : 1 }}
                        />
                        {/* Active bend paths */}
                        {/* Glow layer */}
                        <motion.path 
                          initial={{ pathLength: 0, opacity: 0 }}
                          animate={{ 
                            pathLength: activeId >= 3 ? 1 : 0,
                            opacity: activeId >= 3 ? 1 : 0
                          }}
                          transition={{
                            duration: 0.15,
                            delay: activeId >= 3 ? 0.2 : 0,
                            ease: "easeInOut" as const
                          }}
                          d="M 0.5,0 L 0.5,0.5 A 5,5 0 0,0 5.5,5.5 L 18,5.5" 
                          fill="none" 
                          stroke="var(--reader-analysis-tone-3)" 
                          strokeWidth={3} 
                          strokeLinecap="round"
                          className="opacity-20 blur-[1px]"
                        />
                        {/* Main stroke layer */}
                        <motion.path 
                          initial={{ pathLength: 0, opacity: 0 }}
                          animate={{ 
                            pathLength: activeId >= 3 ? 1 : 0,
                            opacity: activeId >= 3 ? 1 : 0
                          }}
                          transition={{
                            duration: 0.15,
                            delay: activeId >= 3 ? 0.2 : 0,
                            ease: "easeInOut" as const
                          }}
                          d="M 0.5,0 L 0.5,0.5 A 5,5 0 0,0 5.5,5.5 L 18,5.5" 
                          fill="none" 
                          stroke="var(--reader-analysis-tone-3)" 
                          strokeWidth={1} 
                          strokeLinecap="round"
                        />
                        {/* Light particle flowing horizontally */}
                        {activeId >= 3 && (
                          <motion.circle
                            key={`h-particle-2-${activeId}`}
                            r={1.5}
                            fill="var(--reader-analysis-tone-3)"
                            className="pointer-events-none"
                            style={{ filter: "drop-shadow(0 0 2px var(--reader-analysis-tone-3))" }}
                            initial={{ cx: 0.5, cy: 0, opacity: 0 }}
                            animate={{
                              cx: [0.5, 0.5, 5.5, 18],
                              cy: [0, 0.5, 5.5, 5.5],
                              opacity: [0, 1, 1, 0]
                            }}
                            transition={{
                              duration: 0.15,
                              delay: 0.2,
                              times: [0, 0.18, 0.46, 1],
                              ease: "linear"
                            }}
                          />
                        )}
                      </svg>
                    </div>

                    {/* Top Dot */}
                    <motion.div 
                      initial={{ scale: 0, opacity: 0 }}
                      animate={{ 
                        scale: activeId >= 3 ? 1 : 0,
                        opacity: activeId >= 3 ? 1 : 0
                      }}
                      transition={topDotAnim.transition}
                      className="absolute left-[44px] top-[40px] w-1.5 h-1.5 rounded-full pointer-events-none flex items-center justify-center"
                      style={{ color: activeId >= 3 ? "var(--reader-analysis-tone-3)" : "var(--hairline)" }}
                    >
                      <div className="w-full h-full rounded-full bg-current" />
                      {activeId >= 3 && !isReduced && (
                        <motion.span 
                          animate={{ scale: [1, 2.2], opacity: [0.5, 0] }} 
                          transition={{ duration: 1.6, repeat: Infinity, ease: "easeOut" }} 
                          className="absolute inset-0 rounded-full bg-current pointer-events-none" 
                        />
                      )}
                    </motion.div>

                    {/* Bottom Dot */}
                    <motion.div 
                      initial={{ scale: 0, opacity: 0 }}
                      animate={{ 
                        scale: activeId >= 3 ? 1 : 0,
                        opacity: activeId >= 3 ? 1 : 0
                      }}
                      transition={{
                        duration: 0.1,
                        delay: activeId >= 3 ? 0.35 : 0,
                        ease: cubicBezier
                      }}
                      className="absolute left-[61px] bottom-[-24px] w-1.5 h-1.5 rounded-full pointer-events-none flex items-center justify-center"
                      style={{ color: activeId >= 3 ? "var(--reader-analysis-tone-3)" : "var(--hairline)" }}
                    >
                      <div className="w-full h-full rounded-full bg-current" />
                      {activeId >= 3 && !isReduced && (
                        <motion.span 
                          animate={{ scale: [1, 2.2], opacity: [0.5, 0] }} 
                          transition={{ duration: 1.6, repeat: Infinity, ease: "easeOut", delay: 0.35 }} 
                          className="absolute inset-0 rounded-full bg-current pointer-events-none" 
                        />
                      )}
                    </motion.div>
                  </motion.div>
                )}

                {/* Row content container without opacity dimming */}
                <div className="flex items-start gap-3.5 flex-1 transition-opacity duration-300">
                  {/* Tag pill capsule */}
                  <span 
                    className="inline-flex items-center justify-center rounded-full px-2.5 py-0.5 text-[0.68rem] font-bold tracking-wide flex-shrink-0 mt-0.5 select-none transition-all duration-300"
                    style={{
                      color: toneAccent,
                      backgroundColor: `color-mix(in srgb, ${toneAccent} 10%, transparent)`,
                      boxShadow: isItemActive ? `0 0 0 1px color-mix(in srgb, ${toneAccent} 20%, transparent)` : "none"
                    }}
                  >
                    {labelWithCircle}
                  </span>

                  {/* Clause English text - purely typographic highlight */}
                  <span 
                    className={cn(
                      "font-reading text-[0.96rem] leading-[1.48] transition-colors duration-300 py-0.5 px-1 font-medium",
                      isItemActive ? "text-ink" : "text-ink-soft/90"
                    )}
                  >
                    {chunk.text}
                  </span>
                </div>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </div>
  );
}

function StructureAtom({
  chunk,
  isActive,
  onMouseEnter,
  onMouseLeave,
}: {
  chunk: StructureChunk;
  isActive: boolean;
  onMouseEnter: () => void;
  onMouseLeave: () => void;
}) {
  return (
    <span
      className="inline cursor-help"
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      <span
        className={cn(
          "reader-analysis-atom transition-all duration-300",
          `reader-analysis-atom--${chunk.tone}`,
          isActive && "reader-analysis-atom--active"
        )}
        style={{
          ["--analysis-accent" as string]: `var(--reader-analysis-tone-${chunk.tone})`
        }}
      >
        {chunk.text}
      </span>
      {" "}
    </span>
  );
}

interface TranslationSentence {
  english: string;
  chinese: string;
}

const TRANSLATION_SENTENCES: TranslationSentence[] = [
  {
    english: "Elon Musk is a super-entrepreneur.",
    chinese: "埃隆·马斯克是一位超级企业家。",
  },
  {
    english: "He made his initial fortune by helping to create PayPal.",
    chinese: "他通过帮助创建 PayPal 赚得了第一桶金。",
  },
  {
    english: "That netted him about $176 million, which he used to start SpaceX in 2002, and invest in Tesla.",
    chinese: "这让他净赚了约 1.76 亿美元，他用这笔钱在 2002 年创办了 SpaceX，并投资了特斯拉。",
  },
  {
    english: "SpaceX pioneered the reuse of rocket boosters and now dominates the space travel industry.",
    chinese: "SpaceX 开创了火箭助推器重复使用的先河，如今主导着太空旅行行业。",
  },
];

function TranslationReadAlongGraphic() {
  const ref = useRef<HTMLDivElement | null>(null);
  const isInView = useInView(ref, { once: true, margin: "-80px" });
  const [stage, setStage] = useState<0 | 1 | 2 | 3>(0);
  const isPausedRef = useRef(false);
  const reducedMotion = useReducedMotion();
  const isReduced = reducedMotion === true;

  useEffect(() => {
    if (!isInView) return;

    let cancelled = false;

    const runCycle = async () => {
      while (!cancelled) {
        // Stage 0: Show undivided original text (Editorial reader style)
        setStage(0);
        await waitWithPause(isReduced ? 1500 : 2500, isPausedRef, reducedMotion);
        if (cancelled) break;

        // Stage 1: Scanning transition (Physical clip-path wipe)
        setStage(1);
        await waitWithPause(isReduced ? 300 : 1500, isPausedRef, reducedMotion);
        if (cancelled) break;

        // Stage 2: Show deconstructed translated sentences
        setStage(2);
        await waitWithPause(isReduced ? 2000 : 3500, isPausedRef, reducedMotion);
        if (cancelled) break;

        // Stage 3: Fade out / reset
        setStage(3);
        await waitWithPause(isReduced ? 0 : 500, isPausedRef, reducedMotion);
      }
    };

    runCycle();

    return () => {
      cancelled = true;
    };
  }, [isInView, reducedMotion, isReduced]);

  return (
    <div
      ref={ref}
      className="absolute inset-0 mx-auto w-full max-w-[28rem] text-left select-none flex flex-col justify-center cursor-help"
      aria-label="句间双语对照演示"
      onMouseEnter={() => { isPausedRef.current = true; }}
      onMouseLeave={() => { isPausedRef.current = false; }}
    >
      {/* 1. Undivided Paragraph Layer (Original) */}
      <motion.div
        className="absolute inset-y-2 left-0 right-0 px-4 sm:px-6 flex flex-col justify-center"
        animate={
          isReduced
            ? { opacity: stage <= 1 ? 1 : 0 }
            : {
                clipPath:
                  stage === 0
                    ? "inset(0% 0px 0px 0px)"
                    : stage === 1
                    ? "inset(100% 0px 0px 0px)"
                    : "inset(100% 0px 0px 0px)",
                opacity: stage === 3 ? 0 : 1,
              }
        }
        transition={{
          duration: stage === 1 ? (isReduced ? 0.3 : 1.5) : stage === 3 ? 0.5 : 0,
          ease: stage === 1 ? "linear" : [0.22, 1, 0.36, 1],
        }}
      >
        <div className="relative pl-9">
          <span
            className="reader-immersive-paragraph-cue absolute left-0 top-1 flex h-6 w-6 items-center justify-center rounded-full border border-hairline/70 bg-paper text-[0.6rem] font-bold text-ink-soft/70"
            aria-hidden="true"
          >
            01
          </span>
          <p className="reader-serif text-[1.02rem] leading-[1.78] text-ink/85 text-left tracking-normal">
            {TRANSLATION_SENTENCES.map((s) => s.english).join(" ")}
          </p>
        </div>
      </motion.div>

      {/* 2. Deconstructed Translated Sentences Layer (Bilingual Read-Along) */}
      <motion.div
        className="absolute inset-y-2 left-0 right-0 px-4 sm:px-6 flex flex-col justify-center"
        animate={
          isReduced
            ? { opacity: stage === 2 ? 1 : 0 }
            : {
                clipPath:
                  stage === 0
                    ? "inset(0% 0px 100% 0px)"
                    : stage === 1
                    ? "inset(0% 0px 0% 0px)"
                    : "inset(0% 0px 0% 0px)",
                opacity: stage === 3 ? 0 : 1,
              }
        }
        transition={{
          duration: stage === 1 ? (isReduced ? 0.3 : 1.5) : stage === 3 ? 0.5 : 0,
          ease: stage === 1 ? "linear" : [0.22, 1, 0.36, 1],
        }}
      >
        <div className="relative pl-9 flex flex-col gap-2.5">
          <span
            className="reader-immersive-paragraph-cue absolute left-0 top-1 flex h-6 w-6 items-center justify-center rounded-full border border-hairline/70 bg-paper text-[0.6rem] font-bold text-ink-soft/70"
            aria-hidden="true"
          >
            01
          </span>
          {TRANSLATION_SENTENCES.map((sentence) => (
            <div key={sentence.english} className="flex flex-col">
              <p className="reader-serif text-[0.92rem] leading-[1.4] text-ink font-medium tracking-normal">
                {sentence.english}
              </p>
              <p className="reader-translation-copy pl-0 text-[0.8rem] leading-[1.38] text-ink-soft/80 mt-0.5 font-sans">
                {sentence.chinese}
              </p>
            </div>
          ))}
        </div>
      </motion.div>

      {/* 3. Laser Scanline */}
      {!isReduced && (stage === 1 || stage === 2) && (
        <motion.div
          className="absolute left-4 sm:left-6 right-4 sm:right-6 h-[2px] bg-gradient-to-r from-transparent via-reader-analysis-tone-2 to-transparent z-10 pointer-events-none"
          style={{
            boxShadow: "0 0 6px var(--reader-analysis-tone-2), 0 0 12px var(--reader-analysis-tone-2)",
          }}
          animate={{
            top: stage === 1 ? ["8%", "92%"] : "92%",
            opacity: stage === 1 ? [0, 1, 1, 0] : 0,
          }}
          transition={{
            duration: stage === 1 ? 1.5 : 0.2,
            times: [0, 0.05, 0.95, 1],
            ease: "linear",
          }}
        />
      )}
    </div>
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

function waitWithPause(
  duration: number,
  isPausedRef: { current: boolean },
  reducedMotion: boolean | null
) {
  if (reducedMotion) {
    return new Promise<void>((resolve) => window.setTimeout(resolve, 0));
  }
  return new Promise<void>(async (resolve) => {
    let elapsed = 0;
    const chunk = 50;
    while (elapsed < duration) {
      await new Promise((r) => window.setTimeout(r, chunk));
      if (!isPausedRef.current) {
        elapsed += chunk;
      }
    }
    resolve();
  });
}
