"use client";

import { useEffect, useState, type FocusEvent, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Languages } from "lucide-react";
import { Highlighter } from "@/components/ui/highlighter";
import { cn } from "@/lib/cn";

type FeatureId = "vocabulary" | "grammar" | "sentence" | "translation";
type FeatureTone = "amber" | "violet" | "green" | "blue";
type VocabularyStageId = "word" | "phrase" | "context";
type VocabularyTone = "vocab" | "phrase" | "context";
type GrammarPointId = "contrast" | "modifier";
type SentencePhase = "reading" | "falling" | "analysis";
type TranslationPhase = "source" | "aligned";

interface AnnotationFeature {
  id: FeatureId;
  label: string;
  title: string;
  body: string;
  badge: string;
  tone: FeatureTone;
  side: "copy-left" | "copy-right";
}

const features: AnnotationFeature[] = [
  {
    id: "vocabulary",
    label: "Vocabulary Layers",
    title: "词都查了，句子还是散的。",
    body: "Claread 不把词汇当成一张孤立词表。生词、短语和语境义会被分开标注，让你知道每个词在这句话里承担什么作用。",
    badge: "vocab_highlight · phrase_gloss · context_gloss",
    tone: "amber",
    side: "copy-left",
  },
  {
    id: "grammar",
    label: "Grammar Note",
    title: "看懂大意，但不知道为什么。",
    body: "很多句子不是译文看不懂，而是语法关系没有真的接上。grammar_note 只解释当前句子里影响理解的语法点，帮助你下次遇到同类结构时读出来。",
    badge: "grammar_note",
    tone: "violet",
    side: "copy-right",
  },
  {
    id: "sentence",
    label: "Sentence Analysis",
    title: "长句不是更长的词表。",
    body: "考试阅读里的长难句，难点通常是主干被从句、非谓语和修饰关系盖住。sentence_analysis 先露出骨架，再把枝叶按层级展开。",
    badge: "sentence_analysis",
    tone: "green",
    side: "copy-left",
  },
  {
    id: "translation",
    label: "Translation Rail",
    title: "译文只负责校准理解。",
    body: "直接看中文会跳过英文阅读过程。Claread 的译文按句贴在原文下方，低权重出现，用来确认理解，而不是替你读完原句。",
    badge: "sentence translation · reading_variant",
    tone: "blue",
    side: "copy-right",
  },
];

interface VocabularyStage {
  id: VocabularyStageId;
  tone: VocabularyTone;
  title: string;
  description: string;
  cardLabel: string;
  focusText: string;
  resultTitle: string;
  result: string;
  helper?: string;
}

const vocabularyStages: VocabularyStage[] = [
  {
    id: "word",
    tone: "vocab",
    title: "单词高亮",
    description: "先标出阻断阅读的词，给出当前需要的简明释义。",
    cardLabel: "词典",
    focusText: "Nationally",
    resultTitle: "释义",
    result: "在全国范围内",
  },
  {
    id: "phrase",
    tone: "phrase",
    title: "短语搭配",
    description: "把几个词看成一个语义单位，避免逐词硬拆。",
    cardLabel: "短语搭配",
    focusText: "miss out on",
    resultTitle: "短语义",
    result: "错过，失去获得某事的机会",
    helper: "这是一组动词短语，不能只按单词顺序拼意思。",
  },
  {
    id: "context",
    tone: "context",
    title: "语境释义",
    description: "只解释这个词组在当前句子里的意思。",
    cardLabel: "语境释义",
    focusText: "excused or unexcused",
    resultTitle: "当前句义",
    result: "无论是否有正当理由",
    helper: "这里强调缺勤本身会影响学习结果，不先区分缺勤原因。",
  },
];

const vocabularyToneMap = {
  vocab: {
    dot: "bg-[#d49a18]",
    text: "text-[#8a6a0f]",
    quietText: "text-[#6e5410]",
    mark: "reader-mark--vocab",
    card: "border-[#d49a18]/22 bg-[#f6d67a]/16",
    progress: "bg-[#d49a18]",
  },
  phrase: {
    dot: "bg-[#8e779f]",
    text: "text-[#675079]",
    quietText: "text-[#5f4e8a]",
    mark: "reader-mark--phrase",
    card: "border-[#8e779f]/20 bg-[#d0bff4]/16",
    progress: "bg-[#8e779f]",
  },
  context: {
    dot: "bg-[#4f89b3]",
    text: "text-[#2f6f9e]",
    quietText: "text-[#265f8f]",
    mark: "reader-mark--context",
    card: "border-[#4f89b3]/20 bg-[#a5d0ef]/16",
    progress: "bg-[#4f89b3]",
  },
} satisfies Record<VocabularyTone, Record<string, string>>;

interface GrammarPoint {
  id: GrammarPointId;
  title: string;
  label: string;
  content: string;
}

const grammarPoints: GrammarPoint[] = [
  {
    id: "contrast",
    title: "not that... but that...",
    label: "提速结构：not A but B",
    content: "先排除 A，再把真正原因放在 B。快速阅读时先抓 but that 后面的句子主干。",
  },
  {
    id: "modifier",
    title: "inside + 并列名词",
    label: "信息隐藏位置",
    content: "inside 后面的并列名词说明信息藏在从句、修饰语和改写表达里，不是另一个主干动作。",
  },
];

interface SentencePiece {
  label: string;
  text: string;
  explanation: string;
  indent: number;
}

const sentenceText =
  "This design reduces the risk that an explanation becomes detached from the passage while keeping the original sentence in view.";

const sentenceWords = sentenceText.replace(".", "").split(" ");

const sentencePieces: SentencePiece[] = [
  {
    label: "主干",
    text: "This design reduces the risk",
    explanation: "先抓主语和谓语，知道这句话真正说的动作。",
    indent: 0,
  },
  {
    label: "定语从句",
    text: "that an explanation becomes detached from the passage",
    explanation: "说明 risk 的具体内容，不要把它当成第二个主句。",
    indent: 1,
  },
  {
    label: "伴随结构",
    text: "while keeping the original sentence in view",
    explanation: "补充说明 Claread 如何降低风险：原句仍留在视野里。",
    indent: 2,
  },
];

interface TranslationSentence {
  source: string;
  translation: string;
}

const translationSourceText =
  "Claread keeps each sentence visible. It opens the vocabulary, grammar, and meaning only where they help. The translation stays below the original, so you can check understanding without leaving English.";

const translationSentences: TranslationSentence[] = [
  {
    source: "Claread keeps each sentence visible.",
    translation: "Claread 让每个句子仍然留在视野里。",
  },
  {
    source: "It opens the vocabulary, grammar, and meaning only where they help.",
    translation: "它只在真正有帮助的地方展开词汇、语法和含义。",
  },
  {
    source: "The translation stays below the original, so you can check understanding without leaving English.",
    translation: "译文停留在原句下方，用来校准理解，而不是把你带离英文。",
  },
];

export function ProductAnnotations() {
  return (
    <section className="relative isolate overflow-hidden border-b border-hairline/80 bg-surface-warm px-5 py-20 text-ink sm:px-6 sm:py-28 lg:px-8 lg:py-32">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-28 bg-gradient-to-b from-web-canvas to-transparent" />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-28 bg-gradient-to-t from-web-canvas to-transparent" />
      <div className="pointer-events-none absolute left-[-12rem] top-24 h-[32rem] w-[32rem] rounded-full border border-ink/5" />
      <div className="pointer-events-none absolute right-[-14rem] top-[36rem] h-[38rem] w-[38rem] rounded-full border border-ink/5" />

      <div className="relative mx-auto max-w-[76rem]">
        <div className="mb-20 max-w-3xl">
          <p className="text-sm font-semibold text-lens-blue">从卡住的地方开始</p>
          <h2 className="mt-4 max-w-3xl font-headline text-4xl font-semibold leading-[1.08] text-ink sm:text-5xl md:text-6xl">
            四种标注，按阅读卡点逐个出现。
          </h2>
          <p className="mt-6 max-w-2xl text-base leading-8 text-muted">
            不是功能清单，也不是一张复杂截图。每一段先说一个真实痛点，再展示 Claread 用哪一类标注把它解开。
          </p>
        </div>

        <div className="flex flex-col gap-20 lg:gap-28">
          {features.map((feature) => (
            <FeatureScene key={feature.id} feature={feature} />
          ))}
        </div>
      </div>
    </section>
  );
}

function FeatureScene({ feature }: { feature: AnnotationFeature }) {
  if (feature.id === "vocabulary") {
    return (
      <motion.article
        initial={{ opacity: 0, y: 42 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, amount: 0.28 }}
        transition={{ duration: 0.72, ease: [0.22, 1, 0.36, 1] }}
        className="grid items-center gap-10 lg:grid-cols-[0.72fr_1.28fr] lg:gap-16"
      >
        <VocabularyFocusScene />
      </motion.article>
    );
  }

  if (feature.id === "grammar") {
    return (
      <motion.article
        initial={{ opacity: 0, y: 42 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, amount: 0.28 }}
        transition={{ duration: 0.72, ease: [0.22, 1, 0.36, 1] }}
        className="grid items-center gap-10 lg:grid-cols-[1.16fr_0.84fr] lg:gap-16"
      >
        <GrammarHighlighterDemo />
        <GrammarFeatureCopy />
      </motion.article>
    );
  }

  if (feature.id === "sentence") {
    return (
      <motion.article
        initial={{ opacity: 0, y: 42 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, amount: 0.28 }}
        transition={{ duration: 0.72, ease: [0.22, 1, 0.36, 1] }}
        className="grid items-center gap-10 lg:grid-cols-[0.76fr_1.24fr] lg:gap-16"
      >
        <SentenceAnalysisCopy />
        <SentenceAnalysisDemo />
      </motion.article>
    );
  }

  return (
    <motion.article
      initial={{ opacity: 0, y: 42 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.28 }}
      transition={{ duration: 0.72, ease: [0.22, 1, 0.36, 1] }}
      className="grid items-center gap-10 lg:grid-cols-[1.12fr_0.88fr] lg:gap-16"
    >
      <TranslationSwapDemo />
      <TranslationFeatureCopy />
    </motion.article>
  );
}

function VocabularyFocusScene() {
  const shouldReduceMotion = useReducedMotion();
  const [activeId, setActiveId] = useState<VocabularyStageId>("word");
  const [isPaused, setIsPaused] = useState(false);
  const activeStage = vocabularyStages.find((stage) => stage.id === activeId) ?? vocabularyStages[0];

  useEffect(() => {
    if (shouldReduceMotion || isPaused) {
      return;
    }

    const intervalId = window.setInterval(() => {
      setActiveId((currentId) => {
        const currentIndex = vocabularyStages.findIndex((stage) => stage.id === currentId);
        const nextIndex = currentIndex === -1 ? 0 : (currentIndex + 1) % vocabularyStages.length;

        return vocabularyStages[nextIndex].id;
      });
    }, 2600);

    return () => window.clearInterval(intervalId);
  }, [isPaused, shouldReduceMotion]);

  const activateStage = (stageId: VocabularyStageId) => {
    setActiveId(stageId);
    setIsPaused(true);
  };

  const resumeLoop = () => {
    setIsPaused(false);
  };

  const handleSelectorBlur = (event: FocusEvent<HTMLDivElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
      resumeLoop();
    }
  };

  return (
    <>
      <div className="max-w-xl">
        <p className="text-sm font-semibold text-lens-blue">词汇标注</p>
        <h3 className="mt-4 max-w-[32rem] font-headline text-4xl font-semibold leading-[1.08] text-ink sm:text-5xl">
          查了单词，还是不清楚意思。
        </h3>
        <p className="mt-6 max-w-[31rem] text-base leading-8 text-muted">
          Claread 把生词、短语和语境义分开标注，让解释回到正在读的这一句。
        </p>

        <div
          className="mt-7 flex max-w-[31rem] flex-col gap-2"
          onMouseLeave={resumeLoop}
          onBlur={handleSelectorBlur}
        >
          {vocabularyStages.map((stage) => (
            <VocabularyStageButton
              key={stage.id}
              stage={stage}
              active={stage.id === activeStage.id}
              onActivate={() => activateStage(stage.id)}
            />
          ))}
        </div>
      </div>

      <VocabularyReadingDemo activeStage={activeStage} shouldReduceMotion={Boolean(shouldReduceMotion)} />
    </>
  );
}

function VocabularyStageButton({
  stage,
  active,
  onActivate,
}: {
  stage: VocabularyStage;
  active: boolean;
  onActivate: () => void;
}) {
  const tone = vocabularyToneMap[stage.tone];

  return (
    <button
      type="button"
      aria-pressed={active}
      onMouseEnter={onActivate}
      onFocus={onActivate}
      onClick={onActivate}
      className={cn(
        "group rounded-[0.85rem] border px-4 py-3 text-left transition duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lens-blue/22",
        active
          ? "border-hairline bg-web-canvas/72 shadow-[0_1px_2px_rgba(23,21,17,0.04)]"
          : "border-transparent hover:border-hairline/70 hover:bg-web-canvas/45",
      )}
    >
      <span className="flex items-center gap-2">
        <span className={cn("size-2 rounded-full", tone.dot)} />
        <span className={cn("text-[0.95rem] font-semibold", active ? tone.text : "text-ink")}>{stage.title}</span>
      </span>
      <span className="mt-1 block text-sm leading-6 text-muted">{stage.description}</span>
    </button>
  );
}

function VocabularyReadingDemo({
  activeStage,
  shouldReduceMotion,
}: {
  activeStage: VocabularyStage;
  shouldReduceMotion: boolean;
}) {
  return (
    <div className="reader-shell--intensive relative min-h-[29rem] overflow-hidden rounded-[1.1rem] border border-hairline bg-reader-paper px-5 py-6 shadow-[0_12px_28px_rgba(23,21,17,0.08)] sm:px-8 sm:py-8 lg:min-h-[32rem]">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_78%_10%,rgba(31,94,255,0.07),transparent_32%),linear-gradient(180deg,rgba(255,255,255,0.5),transparent_38%)]" />
      <div className="absolute right-5 top-5 flex items-center gap-3" aria-hidden="true">
        {vocabularyStages.map((stage) => {
          const tone = vocabularyToneMap[stage.tone];

          return (
            <span
              key={stage.id}
              className={cn(
                "h-1.5 w-8 rounded-full transition-colors duration-300",
                activeStage.id === stage.id ? tone.progress : "bg-ink/10",
              )}
            />
          );
        })}
      </div>

      <div className="relative mx-auto flex min-h-[25rem] max-w-[39rem] flex-col justify-center pt-8 sm:pt-10">
        <p className="font-reading text-[1.45rem] leading-[2.1] text-ink sm:text-[1.85rem] lg:text-[2rem]">
          <VocabularyTextMark tone="vocab" active={activeStage.id === "word"} shouldReduceMotion={shouldReduceMotion}>
            Nationally
          </VocabularyTextMark>
          , students who{" "}
          <VocabularyTextMark
            tone="phrase"
            active={activeStage.id === "phrase"}
            shouldReduceMotion={shouldReduceMotion}
          >
            miss out on
          </VocabularyTextMark>{" "}
          school because of{" "}
          <VocabularyTextMark
            tone="context"
            active={activeStage.id === "context"}
            shouldReduceMotion={shouldReduceMotion}
          >
            excused or unexcused
          </VocabularyTextMark>{" "}
          absences lose time with the material.
        </p>

        <VocabularyLookupCard activeStage={activeStage} shouldReduceMotion={shouldReduceMotion} />
      </div>
    </div>
  );
}

function VocabularyTextMark({
  tone,
  active,
  shouldReduceMotion,
  children,
}: {
  tone: VocabularyTone;
  active: boolean;
  shouldReduceMotion: boolean;
  children: ReactNode;
}) {
  const toneStyle = vocabularyToneMap[tone];

  return (
    <span
      className={cn(
        "reader-mark relative inline-block whitespace-nowrap",
        toneStyle.mark,
        active && "reader-mark--group-active",
      )}
    >
      {children}
      {active && <VocabularyFocusCorners shouldReduceMotion={shouldReduceMotion} />}
    </span>
  );
}

function VocabularyFocusCorners({ shouldReduceMotion }: { shouldReduceMotion: boolean }) {
  return (
    <motion.span
      layoutId="vocabulary-focus-frame"
      initial={shouldReduceMotion ? false : { opacity: 0, scale: 0.96 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
      className="pointer-events-none absolute -inset-1.5 rounded-[0.28em]"
      aria-hidden="true"
    >
      <span className="absolute left-0 top-0 size-2.5 border-l-2 border-t-2 border-lens-blue" />
      <span className="absolute right-0 top-0 size-2.5 border-r-2 border-t-2 border-lens-blue" />
      <span className="absolute bottom-0 left-0 size-2.5 border-b-2 border-l-2 border-lens-blue" />
      <span className="absolute bottom-0 right-0 size-2.5 border-b-2 border-r-2 border-lens-blue" />
    </motion.span>
  );
}

function VocabularyLookupCard({
  activeStage,
  shouldReduceMotion,
}: {
  activeStage: VocabularyStage;
  shouldReduceMotion: boolean;
}) {
  const tone = vocabularyToneMap[activeStage.tone];
  const alignment = {
    word: "self-start sm:ml-[5%]",
    phrase: "self-center",
    context: "self-end sm:mr-[5%]",
  }[activeStage.id];

  return (
    <div className="mt-8 flex min-h-[10rem]">
      <motion.div
        key={activeStage.id}
        initial={shouldReduceMotion ? false : { opacity: 0, y: 12, filter: "blur(6px)" }}
        animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
        transition={{ duration: 0.34, ease: [0.22, 1, 0.36, 1] }}
        className={cn(
          "w-full max-w-[22rem] rounded-[0.9rem] border border-hairline/80 bg-surface/92 p-4 shadow-[0_12px_28px_rgba(23,21,17,0.08)]",
          alignment,
        )}
      >
        <div className="flex items-center gap-2">
          <span className={cn("size-2 rounded-full", tone.dot)} />
          <span className={cn("text-xs font-semibold", tone.text)}>{activeStage.cardLabel}</span>
        </div>
        <p className="mt-2 font-reading text-[1.35rem] leading-snug text-ink">{activeStage.focusText}</p>
        <div className={cn("mt-3 rounded-[0.65rem] border px-3 py-2.5", tone.card)}>
          <p className={cn("text-xs font-semibold", tone.quietText)}>{activeStage.resultTitle}</p>
          <p className="mt-1 text-sm font-semibold leading-6 text-ink">{activeStage.result}</p>
        </div>
        {activeStage.helper ? <p className="mt-3 text-xs leading-5 text-muted">{activeStage.helper}</p> : null}
      </motion.div>
    </div>
  );
}

function GrammarFeatureCopy() {
  return (
    <div className="max-w-xl lg:justify-self-end">
      <p className="text-sm font-semibold text-[#5f4e8a]">语法旁注</p>
      <h3 className="mt-4 font-headline text-3xl font-semibold leading-tight text-ink sm:text-4xl">
        看懂大意，但不知道为什么。
      </h3>
      <p className="mt-4 max-w-[33rem] text-base leading-8 text-muted">
        Claread 不讲整章语法，只标出影响当前句子的结构关系。语法点留在原句旁边，下一次遇到同类结构时也能读出来。
      </p>
      <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
        <GrammarCopyPoint title="看关系" body="真正重点常在 but that 后面。" />
        <GrammarCopyPoint title="看边界" body="修饰成分只说明信息藏在哪里。" />
      </div>
    </div>
  );
}

function GrammarCopyPoint({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-[0.85rem] border border-[#746694]/18 bg-web-canvas/55 px-4 py-3">
      <p className="text-sm font-semibold text-[#5f4e8a]">{title}</p>
      <p className="mt-1 text-sm leading-6 text-muted">{body}</p>
    </div>
  );
}

function GrammarHighlighterDemo() {
  const shouldReduceMotion = useReducedMotion();
  const [activePointId, setActivePointId] = useState<GrammarPointId>("contrast");

  useEffect(() => {
    if (shouldReduceMotion) {
      return;
    }

    const intervalId = window.setInterval(() => {
      setActivePointId((currentId) => (currentId === "contrast" ? "modifier" : "contrast"));
    }, 2600);

    return () => window.clearInterval(intervalId);
  }, [shouldReduceMotion]);

  const contrastActive = activePointId === "contrast";
  const modifierActive = activePointId === "modifier";

  return (
    <div className="relative overflow-hidden rounded-[1.15rem] border border-hairline bg-surface p-2 shadow-[0_18px_48px_rgba(95,78,138,0.08)]">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_18%_10%,rgba(116,102,148,0.1),transparent_34%),linear-gradient(180deg,rgba(255,255,255,0.62),transparent)]" />
      <div className="relative rounded-[0.9rem] border border-hairline/80 bg-reader-paper">
        <div className="flex h-12 items-center justify-between border-b border-hairline/80 px-4">
          <div className="flex items-center gap-2">
            <span className="size-2 rounded-full bg-[#b9a8e6]" />
            <span className="text-xs font-semibold text-ink">语法旁注</span>
          </div>
          <div className="flex items-center gap-2" aria-hidden="true">
            {grammarPoints.map((point) => (
              <span
                key={point.id}
                className={cn(
                  "h-1.5 w-8 rounded-full transition-colors duration-300",
                  activePointId === point.id ? "bg-[#6e6389]" : "bg-[#6e6389]/18",
                )}
              />
            ))}
          </div>
        </div>

        <div className="p-5 sm:p-6">
          <div className="rounded-[0.85rem] border border-hairline/80 bg-surface/72 p-5 shadow-[0_1px_2px_rgba(23,21,17,0.03)]">
            <p className="font-reading text-[1.08rem] leading-[2] text-ink sm:text-[1.18rem]">
              For students preparing for exams, the difficulty is often{" "}
              <Highlighter
                action="underline"
                active={contrastActive}
                animationDuration={620}
                color="#6e6389"
                isView
                strokeWidth={2}
              >
                not that every word is unknown
              </Highlighter>
              ,{" "}
              <Highlighter
                action="highlight"
                active={contrastActive}
                animationDuration={760}
                color="rgba(110,99,137,0.18)"
                delay={140}
                isView
                iterations={2}
                padding={1}
              >
                but that a sentence hides the tested information
              </Highlighter>{" "}
              inside{" "}
              <Highlighter
                action="underline"
                active={modifierActive}
                animationDuration={680}
                color="#6e6389"
                isView
                strokeWidth={2}
              >
                clauses, modifiers, and rewritten expressions
              </Highlighter>
              .
            </p>
          </div>

          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {grammarPoints.map((point, index) => (
              <GrammarNoteCard key={point.id} point={point} index={index} active={activePointId === point.id} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function GrammarNoteCard({ point, index, active }: { point: GrammarPoint; index: number; active: boolean }) {
  const shouldReduceMotion = useReducedMotion();

  return (
    <motion.div
      initial={shouldReduceMotion ? false : { opacity: 0, y: 12 }}
      whileInView={{ opacity: 1, y: 0 }}
      animate={shouldReduceMotion ? undefined : { y: active ? -3 : 0 }}
      viewport={{ once: true, amount: 0.5 }}
      transition={{ delay: shouldReduceMotion ? 0 : 0.16 + index * 0.1, duration: 0.42, ease: [0.22, 1, 0.36, 1] }}
      className={cn(
        "rounded-[0.85rem] border p-4 transition-[background-color,border-color,box-shadow] duration-300",
        active
          ? "border-[#746694]/34 bg-[#746694]/[0.105] shadow-[0_8px_18px_rgba(95,78,138,0.1)]"
          : "border-[#746694]/18 bg-[#746694]/[0.055]",
      )}
    >
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "inline-flex size-6 shrink-0 items-center justify-center rounded-full border text-xs font-semibold transition-colors duration-300",
            active ? "border-[#746694]/40 bg-[#6e6389] text-white" : "border-[#746694]/24 bg-surface text-[#5f4e8a]",
          )}
        >
          {index + 1}
        </span>
        <span className="text-xs font-semibold text-[#5f4e8a]">语法旁注</span>
      </div>
      <p className="mt-3 text-sm font-semibold leading-5 text-ink">{point.title}</p>
      <p className="mt-1 text-xs leading-5 text-muted">{point.label}</p>
      <p className="mt-3 text-sm leading-6 text-ink-soft">{point.content}</p>
    </motion.div>
  );
}

function SentenceAnalysisCopy() {
  return (
    <div className="max-w-xl">
      <p className="text-sm font-semibold text-[#276c4d]">长难句拆解</p>
      <h3 className="mt-4 font-headline text-3xl font-semibold leading-tight text-ink sm:text-4xl">
        长句不是更长的词表。
      </h3>
      <p className="mt-4 max-w-[33rem] text-base leading-8 text-muted">
        Claread 先把长句打散，再按主干、从句和修饰关系重新排好。你看到的不是翻译结果，而是这句话怎样成立。
      </p>
      <div className="mt-6 flex flex-col gap-2">
        <SentenceCopyPoint title="先找主干" body="谁做了什么，先从长句里露出来。" />
        <SentenceCopyPoint title="再看枝叶" body="从句、伴随结构和修饰成分按层级归位。" />
      </div>
    </div>
  );
}

function SentenceCopyPoint({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-[0.85rem] border border-[#3c8c68]/18 bg-web-canvas/55 px-4 py-3">
      <p className="text-sm font-semibold text-[#276c4d]">{title}</p>
      <p className="mt-1 text-sm leading-6 text-muted">{body}</p>
    </div>
  );
}

function SentenceAnalysisDemo() {
  const shouldReduceMotion = useReducedMotion();
  const [phase, setPhase] = useState<SentencePhase>("reading");
  const visiblePhase = shouldReduceMotion ? "analysis" : phase;

  useEffect(() => {
    if (shouldReduceMotion) {
      return;
    }

    const phaseOrder: SentencePhase[] = ["reading", "falling", "analysis"];
    const phaseDurations = {
      reading: 1500,
      falling: 980,
      analysis: 2600,
    } satisfies Record<SentencePhase, number>;

    const timeoutId = window.setTimeout(() => {
      setPhase((currentPhase) => {
        const currentIndex = phaseOrder.indexOf(currentPhase);
        const nextIndex = currentIndex === -1 ? 0 : (currentIndex + 1) % phaseOrder.length;

        return phaseOrder[nextIndex];
      });
    }, phaseDurations[phase]);

    return () => window.clearTimeout(timeoutId);
  }, [phase, shouldReduceMotion]);

  return (
    <div className="relative overflow-hidden rounded-[1.15rem] border border-hairline bg-surface p-2 shadow-[0_18px_48px_rgba(39,108,77,0.08)]">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_80%_10%,rgba(60,140,104,0.1),transparent_34%),linear-gradient(180deg,rgba(255,255,255,0.62),transparent)]" />
      <div className="relative overflow-hidden rounded-[0.9rem] border border-hairline/80 bg-reader-paper">
        <div className="flex h-12 items-center justify-between border-b border-hairline/80 px-4">
          <div className="flex items-center gap-2">
            <span className="size-2 rounded-full bg-[#7bc09b]" />
            <span className="text-xs font-semibold text-ink">长难句拆解</span>
          </div>
          <div className="flex items-center gap-2" aria-hidden="true">
            {(["reading", "falling", "analysis"] satisfies SentencePhase[]).map((item) => (
              <span
                key={item}
                className={cn(
                  "h-1.5 w-7 rounded-full transition-colors duration-300",
                  visiblePhase === item ? "bg-[#3c8c68]" : "bg-[#3c8c68]/16",
                )}
              />
            ))}
          </div>
        </div>

        <div className="min-h-[32rem] p-5 sm:p-6">
          <div className="relative min-h-[13.5rem] overflow-hidden rounded-[0.85rem] border border-hairline/80 bg-surface/72 p-5">
            <motion.p
              animate={shouldReduceMotion ? { opacity: 0.14 } : { opacity: phase === "analysis" ? 0.18 : 1 }}
              transition={{ duration: 0.32, ease: [0.22, 1, 0.36, 1] }}
              className="font-reading text-[1.14rem] leading-[2.05] text-ink sm:text-[1.28rem]"
            >
              {sentenceWords.map((word, index) => (
                <SentenceFallingWord
                  key={`${word}-${index}`}
                  word={word}
                  index={index}
                  phase={visiblePhase}
                  shouldReduceMotion={Boolean(shouldReduceMotion)}
                />
              ))}
              <span>.</span>
            </motion.p>

            <motion.div
              animate={{
                opacity: visiblePhase === "falling" ? 1 : 0,
                y: visiblePhase === "falling" ? 0 : 10,
              }}
              transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
              className="pointer-events-none absolute bottom-4 left-5 right-5 flex items-center gap-2 text-xs font-semibold text-[#276c4d]"
              aria-hidden="true"
            >
              <span className="h-px flex-1 bg-[#3c8c68]/18" />
              句子正在拆成成分
              <span className="h-px flex-1 bg-[#3c8c68]/18" />
            </motion.div>
          </div>

          <motion.div
            initial={false}
            animate={visiblePhase === "analysis" ? { opacity: 1, y: 0 } : { opacity: 0.18, y: 14 }}
            transition={{ duration: 0.48, ease: [0.22, 1, 0.36, 1] }}
            className="mt-4 rounded-[0.85rem] border border-[#3c8c68]/18 bg-[#3c8c68]/[0.055] p-4"
          >
            <div className="mb-4 flex items-center justify-between gap-3">
              <span className="text-xs font-semibold text-[#276c4d]">句子成分划分</span>
              <span className="text-xs text-muted">主干先行，枝叶归位</span>
            </div>
            <div className="space-y-3">
              {sentencePieces.map((piece, index) => (
                <SentencePieceRow
                  key={piece.label}
                  piece={piece}
                  index={index}
                  active={visiblePhase === "analysis"}
                  shouldReduceMotion={Boolean(shouldReduceMotion)}
                />
              ))}
            </div>
          </motion.div>
        </div>
      </div>
    </div>
  );
}

function SentenceFallingWord({
  index,
  phase,
  shouldReduceMotion,
  word,
}: {
  index: number;
  phase: SentencePhase;
  shouldReduceMotion: boolean;
  word: string;
}) {
  const fallX = ((index % 7) - 3) * 7;
  const fallY = 44 + (index % 5) * 18;
  const rotate = ((index % 6) - 2.5) * 6;

  return (
    <motion.span
      className="mr-[0.32em] inline-block will-change-transform"
      animate={
        shouldReduceMotion || phase === "reading"
          ? { x: 0, y: 0, rotate: 0, opacity: 1, scale: 1 }
          : phase === "falling"
            ? { x: fallX, y: fallY, rotate, opacity: 0.58, scale: 0.98 }
            : { x: fallX * 0.35, y: fallY + 26, rotate: rotate * 0.4, opacity: 0, scale: 0.94 }
      }
      transition={{
        delay: shouldReduceMotion ? 0 : Math.min(index * 0.018, 0.42),
        duration: phase === "falling" ? 0.58 : 0.42,
        ease: [0.22, 1, 0.36, 1],
      }}
    >
      {word}
    </motion.span>
  );
}

function SentencePieceRow({
  active,
  index,
  piece,
  shouldReduceMotion,
}: {
  active: boolean;
  index: number;
  piece: SentencePiece;
  shouldReduceMotion: boolean;
}) {
  return (
    <motion.div
      animate={active ? { opacity: 1, x: 0 } : { opacity: 0.54, x: -8 }}
      transition={{
        delay: shouldReduceMotion ? 0 : index * 0.11,
        duration: 0.38,
        ease: [0.22, 1, 0.36, 1],
      }}
      className="flex items-start gap-3"
      style={{ marginLeft: `${piece.indent * 1.35}rem` }}
    >
      <span
        className={cn(
          "mt-0.5 inline-flex min-h-6 shrink-0 items-center rounded-full border px-2 text-[0.68rem] font-semibold",
          index === 0
            ? "border-[#3c8c68]/30 bg-[#3c8c68]/12 text-[#276c4d]"
            : "border-hairline/80 bg-surface/72 text-muted",
        )}
      >
        {piece.label}
      </span>
      <span className="min-w-0">
        <span className={cn("block text-sm leading-6", index === 0 ? "font-semibold text-ink" : "text-ink-soft")}>
          {piece.text}
        </span>
        <span className="mt-0.5 block text-xs leading-5 text-muted">{piece.explanation}</span>
      </span>
    </motion.div>
  );
}

function TranslationFeatureCopy() {
  return (
    <div className="max-w-xl lg:justify-self-end">
      <p className="text-sm font-semibold text-[#355f87]">句级对照</p>
      <h3 className="mt-4 font-headline text-3xl font-semibold leading-tight text-ink sm:text-4xl">
        译文只负责校准理解。
      </h3>
      <p className="mt-4 max-w-[33rem] text-base leading-8 text-muted">
        Claread 先把原文按句切开，再把中文放在每句下方。英文仍是主阅读层，译文只在需要确认时低声出现。
      </p>
      <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
        <TranslationCopyPoint title="按句切开" body="每个英文句子有稳定位置。" />
        <TranslationCopyPoint title="低声对照" body="中文在句下校准，不抢正文。" />
      </div>
    </div>
  );
}

function TranslationCopyPoint({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-[0.85rem] border border-[#4c91c2]/16 bg-web-canvas/55 px-4 py-3">
      <p className="text-sm font-semibold text-[#355f87]">{title}</p>
      <p className="mt-1 text-sm leading-6 text-muted">{body}</p>
    </div>
  );
}

function TranslationSwapDemo() {
  const shouldReduceMotion = useReducedMotion();
  const [phase, setPhase] = useState<TranslationPhase>("source");
  const visiblePhase = shouldReduceMotion ? "aligned" : phase;

  useEffect(() => {
    if (shouldReduceMotion) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      setPhase((currentPhase) => (currentPhase === "source" ? "aligned" : "source"));
    }, visiblePhase === "source" ? 1900 : 3200);

    return () => window.clearTimeout(timeoutId);
  }, [shouldReduceMotion, visiblePhase]);

  return (
    <div className="relative overflow-hidden rounded-[1.15rem] border border-hairline bg-surface p-2 shadow-[0_18px_48px_rgba(53,95,135,0.08)]">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_20%_12%,rgba(76,145,194,0.1),transparent_34%),linear-gradient(180deg,rgba(255,255,255,0.62),transparent)]" />
      <div className="relative rounded-[0.9rem] border border-hairline/80 bg-reader-paper">
        <div className="flex h-12 items-center justify-between border-b border-hairline/80 px-4">
          <div className="flex items-center gap-2">
            <span className="size-2 rounded-full bg-[#86bfe8]" />
            <span className="text-xs font-semibold text-ink">句级对照</span>
          </div>
          <div className="inline-flex rounded-full border border-hairline bg-surface/76 p-0.5 text-[0.68rem] font-semibold text-muted">
            <span
              className={cn(
                "rounded-full px-2.5 py-1 transition-colors duration-300",
                visiblePhase === "source" ? "bg-[#4c91c2]/12 text-[#355f87]" : "text-muted",
              )}
            >
              原文
            </span>
            <span
              className={cn(
                "rounded-full px-2.5 py-1 transition-colors duration-300",
                visiblePhase === "aligned" ? "bg-[#4c91c2]/12 text-[#355f87]" : "text-muted",
              )}
            >
              对照
            </span>
          </div>
        </div>

        <div className="min-h-[30rem] p-5 sm:p-6">
          <AnimatePresence mode="wait" initial={false}>
            {visiblePhase === "source" ? <TranslationSourcePanel key="source" /> : <TranslationAlignedPanel key="aligned" />}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}

function TranslationSourcePanel() {
  return (
    <motion.div
      initial={{ opacity: 0, y: 14, filter: "blur(6px)" }}
      animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
      exit={{ opacity: 0, y: -14, filter: "blur(6px)" }}
      transition={{ duration: 0.42, ease: [0.22, 1, 0.36, 1] }}
      className="rounded-[0.9rem] border border-hairline/80 bg-surface/72 p-5"
    >
      <div className="mb-4 flex items-center justify-between gap-3">
        <span className="text-xs font-semibold text-[#355f87]">原文输入</span>
        <span className="inline-flex items-center gap-1.5 rounded-full border border-hairline bg-reader-paper px-2.5 py-1 text-xs text-muted">
          <Languages className="size-3" aria-hidden="true" />
          ready
        </span>
      </div>
      <p className="font-reading text-[1.16rem] leading-[2.05] text-ink sm:text-[1.28rem]">{translationSourceText}</p>
      <div className="mt-5 flex items-center gap-2 text-xs font-semibold text-muted">
        <span className="h-px flex-1 bg-hairline" />
        swap to sentence view
        <span className="h-px flex-1 bg-hairline" />
      </div>
    </motion.div>
  );
}

function TranslationAlignedPanel() {
  return (
    <motion.div
      initial={{ opacity: 0, y: 16, filter: "blur(6px)" }}
      animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
      exit={{ opacity: 0, y: -12, filter: "blur(6px)" }}
      transition={{ duration: 0.42, ease: [0.22, 1, 0.36, 1] }}
      className="space-y-3"
    >
      {translationSentences.map((sentence, index) => (
        <motion.div
          key={sentence.source}
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: index * 0.1, duration: 0.36, ease: [0.22, 1, 0.36, 1] }}
          className="rounded-[0.85rem] border border-[#4c91c2]/16 bg-surface/82 p-4"
        >
          <div className="mb-2 flex items-center gap-2">
            <span className="inline-flex size-6 shrink-0 items-center justify-center rounded-full border border-[#4c91c2]/20 bg-[#a5d0ef]/16 text-xs font-semibold text-[#355f87]">
              {index + 1}
            </span>
            <span className="text-xs font-semibold text-[#355f87]">sentence</span>
          </div>
          <p className="font-reading text-[1.05rem] leading-7 text-ink">{sentence.source}</p>
          <p className="mt-2 border-t border-hairline/60 pt-2 text-sm leading-6 text-muted">{sentence.translation}</p>
        </motion.div>
      ))}
    </motion.div>
  );
}
