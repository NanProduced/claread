"use client";

import {
  motion,
  type MotionValue,
  useMotionValueEvent,
  useReducedMotion,
  useScroll,
  useTransform,
} from "framer-motion";
import { useRef, useState } from "react";
import { GoalReaderCropPreview } from "@/components/product-page/GoalReaderCropPreview";
import { cn } from "@/lib/cn";
import {
  readerGoalDemoSections,
  type ReaderGoalDemoSection,
  type ReaderGoalDemoVariant,
} from "@/lib/product-page/reader-goal-demo";

const STEP_ITEM_HEIGHT_REM = 16.25;

type ReaderGoalDemoStep = {
  id: string;
  section: ReaderGoalDemoSection;
  variant: ReaderGoalDemoVariant;
};

const readerGoalDemoSteps: ReaderGoalDemoStep[] = readerGoalDemoSections.flatMap((section) =>
  section.variants.map((variant) => ({
    id: `${section.id}-${variant.id}`,
    section,
    variant,
  })),
);

const totalStepCount = readerGoalDemoSteps.length;
const totalStepLabel = String(totalStepCount).padStart(2, "0");

function formatStepNumber(index: number) {
  return String(index + 1).padStart(2, "0");
}

function activeStepForProgress(progress: number) {
  if (totalStepCount <= 1) return 0;

  const clamped = Math.max(0, Math.min(progress, 1));
  return Math.min(totalStepCount - 1, Math.round(clamped * (totalStepCount - 1)));
}

function SequenceProgress({
  activeIndex,
  progress,
  reducedMotion,
}: {
  activeIndex: number;
  progress: MotionValue<number>;
  reducedMotion: boolean;
}) {
  const staticProgress = totalStepCount > 1 ? activeIndex / (totalStepCount - 1) : 1;

  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute bottom-8 left-8 top-8 hidden w-px bg-[#F4EEDF]/10 lg:block"
    >
      <motion.div
        className="h-full w-px origin-top bg-[#8CAEFF]"
        initial={false}
        style={reducedMotion ? { scaleY: staticProgress } : { scaleY: progress }}
      />
    </div>
  );
}

function StepMarkers({ activeIndex }: { activeIndex: number }) {
  return (
    <div className="mt-7 flex items-center gap-2" aria-hidden="true">
      {readerGoalDemoSteps.map((step, index) => (
        <span
          key={step.id}
          className={cn(
            "h-1.5 rounded-full transition-[width,background-color,opacity] duration-300",
            index === activeIndex ? "w-8 bg-[#F4EEDF]" : "w-3 bg-[#F4EEDF]/22",
          )}
        />
      ))}
    </div>
  );
}

function GoalStepListItem({
  activeIndex,
  index,
  step,
}: {
  activeIndex: number;
  index: number;
  step: ReaderGoalDemoStep;
}) {
  const active = activeIndex === index;
  const distance = Math.abs(activeIndex - index);

  return (
    <div
      className={cn(
        "flex flex-col justify-start pr-8 transition-[opacity,filter,transform] duration-300",
        active ? "opacity-100 blur-0" : distance === 1 ? "opacity-22 blur-[0.75px]" : "opacity-8 blur-[1.25px]",
      )}
      style={{ height: `${STEP_ITEM_HEIGHT_REM}rem` }}
    >
      <div
        className={cn(
          "flex items-center gap-3 font-sans text-[0.82rem] font-semibold",
          active ? "text-[#8CAEFF]" : "text-[#8CAEFF]/55",
        )}
      >
        <span className="tabular-nums">
          {formatStepNumber(index)} / {totalStepLabel}
        </span>
        <span className="h-px w-9 bg-current opacity-45" />
        <span>
          {step.section.title} · {step.variant.label}
        </span>
        {step.section.beta ? (
          <span className="rounded-full bg-[#8CAEFF]/12 px-2 py-0.5 text-[0.68rem] text-[#B8CAFF] ring-1 ring-[#8CAEFF]/24">
            Beta
          </span>
        ) : null}
      </div>

      <h3
        className={cn(
          "mt-5 max-w-[34rem] origin-left font-headline text-[clamp(2rem,3vw,3.35rem)] font-semibold leading-[1.04] tracking-normal transition-transform duration-300 [text-wrap:balance]",
          active ? "text-[#F4EEDF]" : "text-[#F4EEDF]/42",
          active ? "scale-100" : "scale-[0.92]",
        )}
      >
        {step.variant.headline}
      </h3>
      <p
        className={cn(
          "mt-5 max-w-[31rem] font-sans text-[1rem] leading-8",
          active ? "text-[#D6CCBA]" : "text-[#D6CCBA]/52",
        )}
      >
        {step.section.description}
      </p>
      <p
        className={cn(
          "mt-4 max-w-[30rem] border-l border-[#F4EEDF]/18 pl-4 font-sans text-[0.92rem] leading-7",
          active ? "text-[#F4EEDF]/74" : "text-[#F4EEDF]/38",
        )}
      >
        {step.variant.description}
      </p>
    </div>
  );
}

function GoalStepTrack({
  activeIndex,
  reducedMotion,
  trackY,
}: {
  activeIndex: number;
  reducedMotion: boolean;
  trackY: MotionValue<string>;
}) {
  const railMask =
    "linear-gradient(to bottom, transparent 0%, black 10%, black 78%, transparent 100%)";

  return (
    <div
      className="relative h-[28rem] overflow-hidden"
      style={{
        WebkitMaskImage: railMask,
        maskImage: railMask,
      }}
    >
      <motion.div
        className="will-change-transform"
        initial={false}
        style={{ y: reducedMotion ? `${activeIndex * -STEP_ITEM_HEIGHT_REM}rem` : trackY }}
      >
        {readerGoalDemoSteps.map((step, index) => (
          <GoalStepListItem key={step.id} activeIndex={activeIndex} index={index} step={step} />
        ))}
      </motion.div>
    </div>
  );
}

function ReaderPreviewTransition({
  step,
}: {
  step: ReaderGoalDemoStep;
}) {
  return (
    <div className="relative w-full">
      <GoalReaderCropPreview
        className="lg:h-[30rem] lg:max-w-[50rem]"
        goalTitle={step.section.title}
        preview={step.variant.preview}
        variantLabel={step.variant.label}
      />
    </div>
  );
}

function MobileGoalStep({ index, step }: { index: number; step: ReaderGoalDemoStep }) {
  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center gap-3 font-sans text-[0.78rem] font-semibold text-[#8CAEFF]">
          <span className="tabular-nums">
            {formatStepNumber(index)} / {totalStepLabel}
          </span>
          <span className="h-px w-8 bg-[#8CAEFF]/42" />
          <span>
            {step.section.title} · {step.variant.label}
          </span>
        </div>
        <h3 className="mt-4 font-headline text-[2.35rem] font-semibold leading-[1.05] text-[#F4EEDF]">
          {step.variant.headline}
        </h3>
        <p className="mt-4 font-sans text-[0.98rem] leading-8 text-[#D6CCBA]">{step.variant.description}</p>
      </div>

      <GoalReaderCropPreview
        className="min-h-[30rem] lg:h-auto"
        goalTitle={step.section.title}
        preview={step.variant.preview}
        variantLabel={step.variant.label}
      />
    </div>
  );
}

export function ProductReaderDemo() {
  const reducedMotion = Boolean(useReducedMotion());
  const sequenceRef = useRef<HTMLDivElement>(null);
  const [activeStepIndex, setActiveStepIndex] = useState(0);
  const { scrollYProgress } = useScroll({
    target: sequenceRef,
    offset: ["start start", "end end"],
  });
  const trackY = useTransform(
    scrollYProgress,
    [0, 1],
    ["0rem", `-${(totalStepCount - 1) * STEP_ITEM_HEIGHT_REM}rem`],
  );

  useMotionValueEvent(scrollYProgress, "change", (latest) => {
    const nextIndex = activeStepForProgress(latest);
    setActiveStepIndex((current) => (current === nextIndex ? current : nextIndex));
  });

  const activeStep = readerGoalDemoSteps[activeStepIndex] ?? readerGoalDemoSteps[0];

  if (!activeStep) return null;

  return (
    <section id="reader-demo" className="relative isolate bg-[#161412] text-[#F4EEDF]">
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_72%_42%,rgba(244,238,223,0.08),transparent_34%)]" />
        <div className="absolute inset-x-0 top-0 h-40 bg-gradient-to-b from-[#161412] to-[#161412]/0" />
      </div>

      <div
        ref={sequenceRef}
        className="relative hidden lg:block"
        style={{ minHeight: `${totalStepCount * 86}vh` }}
      >
        <SequenceProgress activeIndex={activeStepIndex} progress={scrollYProgress} reducedMotion={reducedMotion} />

        <div className="sticky top-14 flex h-[calc(100vh-3.5rem)] items-center overflow-hidden px-8 py-8">
          <div className="mx-auto grid h-full w-full max-w-[92rem] grid-cols-[minmax(0,0.78fr)_minmax(38rem,1.2fr)] items-center gap-[clamp(3rem,6vw,7rem)]">
            <div className="flex h-full max-h-[42rem] flex-col justify-center">
              <div className="mb-[clamp(1.45rem,3vh,2.55rem)] max-w-[34rem]">
                <p className="font-sans text-[0.82rem] font-semibold text-[#8CAEFF]">Goal-Based Reader Demo</p>
                <h2 className="mt-3 max-w-[31rem] font-sans text-[clamp(1.35rem,1.6vw,1.7rem)] font-semibold leading-[1.45] text-[#F4EEDF] [text-wrap:balance]">
                  同一篇文章，不同目标，解释重点不同。
                </h2>
                <p className="mt-3 max-w-[31rem] font-sans text-[0.94rem] leading-7 text-[#D6CCBA]/72">
                  Claread 把原文、译文和语法旁注放在同一张样张里，滚动查看不同阅读目标下的讲解口径。
                </p>
              </div>

              <GoalStepTrack activeIndex={activeStepIndex} reducedMotion={reducedMotion} trackY={trackY} />
              <StepMarkers activeIndex={activeStepIndex} />
            </div>

            <div className="relative flex h-full max-h-[42rem] items-center">
              <div className="pointer-events-none absolute -inset-x-8 top-1/2 h-52 -translate-y-1/2 rounded-full bg-[#F4EEDF]/8 blur-3xl" />
              <ReaderPreviewTransition step={activeStep} />
            </div>
          </div>
        </div>
      </div>

      <div className="relative mx-auto space-y-14 px-5 py-20 sm:px-6 lg:hidden">
        <div className="max-w-3xl">
          <p className="font-sans text-sm font-semibold text-[#8CAEFF]">Goal-Based Reader Demo</p>
          <h2 className="mt-4 font-headline text-[clamp(2.35rem,11vw,4rem)] font-semibold leading-[1.04] text-[#F4EEDF]">
            同一段英文，按阅读目标展开不同解释。
          </h2>
          <p className="mt-5 font-sans text-base leading-8 text-[#D6CCBA]/78">
            Claread 把原文、译文和语法旁注放在同一张样张里，滚动查看每一种解析口径。
          </p>
        </div>

        <div className="space-y-16">
          {readerGoalDemoSteps.map((step, index) => (
            <MobileGoalStep key={step.id} index={index} step={step} />
          ))}
        </div>
      </div>
    </section>
  );
}
