"use client";

import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useId, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import type {
  ReaderGoalAnnotationLayout,
  ReaderGoalGrammarNote,
  ReaderDemoTextSegment,
  ReaderGoalDemoPreview,
} from "@/lib/product-page/reader-goal-demo";

const easeOutQuint = [0.22, 1, 0.36, 1] as const;

const notePlacements: Record<
  ReaderGoalAnnotationLayout,
  {
    className: string;
    enterX: number;
    rotate: number;
  }
> = {
  "upper-margin": {
    className: "sm:right-[1.4rem] sm:top-[6.2rem] sm:w-[19.8rem]",
    enterX: 18,
    rotate: 1.6,
  },
  "middle-margin": {
    className: "sm:right-[1.2rem] sm:top-[10.4rem] sm:w-[20.4rem]",
    enterX: 22,
    rotate: -1.2,
  },
  "lower-margin": {
    className: "sm:right-[1.55rem] sm:bottom-[2.4rem] sm:w-[20rem]",
    enterX: 16,
    rotate: 1,
  },
};

function SourceLine({
  className,
  highlightLayoutId,
  reducedMotion,
  segments,
}: {
  className?: string;
  highlightLayoutId: string;
  reducedMotion: boolean;
  segments: ReaderDemoTextSegment[];
}) {
  return (
    <p className={cn("reader-font-reading text-[#171511]", className)}>
      {segments.map((segment, index) => {
        if (!segment.highlight) {
          return <span key={`${segment.text}-${index}`}>{segment.text}</span>;
        }

        return (
          <motion.span
            key={`${segment.text}-${index}`}
            className={cn(
              "relative box-decoration-clone rounded-[0.12rem] px-[0.06em] text-[#171511]",
              "bg-[#FFF0A8]/72 shadow-[inset_0_-0.34em_0_rgba(212,154,24,0.3)]",
              "decoration-[#7B6332]/55 decoration-[1.5px] underline underline-offset-[0.17em]",
            )}
            layoutId={reducedMotion ? undefined : highlightLayoutId}
            initial={reducedMotion ? false : { backgroundColor: "rgba(255,240,168,0)" }}
            animate={{ backgroundColor: "rgba(255,240,168,0.72)" }}
            transition={{ duration: reducedMotion ? 0.01 : 0.34, ease: easeOutQuint }}
          >
            {segment.text}
          </motion.span>
        );
      })}
    </p>
  );
}

function ReadingBase({
  highlightLayoutId,
  preview,
  reducedMotion,
}: {
  highlightLayoutId: string;
  preview: ReaderGoalDemoPreview;
  reducedMotion: boolean;
}) {
  return (
    <div className="relative z-20 max-w-[41.5rem]">
      <AnimatePresence initial={false} mode="popLayout">
        <motion.div
          key={preview.sourceKey}
          animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
          exit={reducedMotion ? { opacity: 0 } : { opacity: 0, y: -10, filter: "blur(5px)" }}
          initial={reducedMotion ? false : { opacity: 0, y: 12, filter: "blur(6px)" }}
          transition={{ duration: reducedMotion ? 0.01 : 0.28, ease: easeOutQuint }}
        >
          <SourceLine
            className="max-w-[58ch] text-[1.24rem] leading-[1.82] tracking-normal sm:text-[1.38rem]"
            highlightLayoutId={highlightLayoutId}
            reducedMotion={reducedMotion}
            segments={preview.source}
          />
          <p className="mt-5 max-w-[48ch] font-sans text-[0.86rem] leading-[1.85] text-[#6E685E]">
            {preview.translation}
          </p>
        </motion.div>
      </AnimatePresence>
    </div>
  );
}

function FloatingGrammarNote({
  layout,
  note,
  reducedMotion,
}: {
  layout: ReaderGoalAnnotationLayout;
  note: ReaderGoalGrammarNote;
  reducedMotion: boolean;
}) {
  const noteKey = `${note.label}-${note.note}`;
  const placement = notePlacements[layout];

  return (
    <AnimatePresence initial={false} mode="popLayout">
      <motion.aside
        key={noteKey}
        className={cn(
          "relative z-40 mt-[-1.35rem] w-[calc(100%-1.2rem)] sm:absolute sm:mt-0",
          "origin-top-right",
          "sm:max-w-[calc(100%-2rem)]",
          placement.className,
        )}
        layout={!reducedMotion}
        animate={{
          filter: "blur(0px)",
          opacity: 1,
          rotate: reducedMotion ? 0 : placement.rotate,
          scale: 1,
          x: 0,
          y: 0,
        }}
        exit={
          reducedMotion
            ? { opacity: 0 }
            : { opacity: 0, rotate: placement.rotate + 1.8, scale: 0.985, x: 10, y: 8, filter: "blur(3px)" }
        }
        initial={
          reducedMotion
            ? false
            : {
                opacity: 0,
                rotate: placement.rotate - 2.2,
                scale: 0.985,
                x: placement.enterX,
                y: 14,
                filter: "blur(5px)",
              }
        }
        transition={{ duration: reducedMotion ? 0.01 : 0.38, ease: easeOutQuint }}
      >
        <div className="relative overflow-hidden rounded-[0.32rem] bg-[#FFE59A] px-5 py-4 shadow-[0_18px_28px_rgba(23,21,17,0.18),0_2px_0_rgba(128,89,19,0.08)] ring-1 ring-[#D6AA45]/32">
          <div className="pointer-events-none absolute inset-x-0 top-0 h-2 bg-[#FFF1BE]/80" />
          <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(135deg,rgba(255,255,255,0.42),transparent_40%),radial-gradient(circle_at_88%_18%,rgba(255,255,255,0.32),transparent_24%)]" />
          <div className="pointer-events-none absolute right-0 top-0 h-5 w-5 bg-[linear-gradient(135deg,rgba(255,255,255,0.74)_0%,rgba(255,255,255,0.74)_49%,rgba(217,176,83,0.28)_50%,rgba(217,176,83,0.28)_100%)]" />
          <div className="relative min-h-[6.15rem]">
            <p className="font-sans text-[0.82rem] font-semibold leading-5 text-[#2A251B]">{note.label}</p>
            <p className="mt-3 font-sans text-[0.84rem] leading-6 text-[#3D3423]">
              {note.note}
            </p>
          </div>
        </div>
      </motion.aside>
    </AnimatePresence>
  );
}

function PaperCard({ children }: { children: ReactNode }) {
  return (
    <div
      className={cn(
        "relative z-10 min-h-[22.7rem] overflow-visible rounded-[0.92rem] bg-[#FBF7EE] text-[#171511]",
        "px-5 pt-7 pb-24 shadow-[0_24px_60px_rgba(0,0,0,0.34)] sm:px-8 sm:pt-8 sm:pb-28",
      )}
    >
      <div className="pointer-events-none absolute inset-0 overflow-hidden rounded-[inherit] bg-[radial-gradient(circle_at_88%_20%,rgba(255,232,168,0.16),transparent_30%),linear-gradient(135deg,rgba(255,255,255,0.46),transparent_42%)]" />
      <div className="pointer-events-none absolute inset-x-5 top-5 h-px bg-[#D9D1C3]/86 sm:inset-x-8" />
      <div className="relative z-10">{children}</div>
    </div>
  );
}

export function GoalReaderCropPreview({
  className,
  goalTitle,
  preview,
  variantLabel,
}: {
  className?: string;
  goalTitle: string;
  preview: ReaderGoalDemoPreview;
  variantLabel: string;
}) {
  const highlightLayoutId = `${useId()}-reader-highlight`;
  const reducedMotion = Boolean(useReducedMotion());

  return (
    <article
      aria-label={`${goalTitle} ${variantLabel} grammar note preview`}
      className={cn(
        "relative mx-auto h-auto min-h-[30rem] w-full overflow-visible text-[#171511]",
        "lg:h-[30rem] lg:max-w-[50rem]",
        className,
      )}
    >
      <div className="pointer-events-none absolute -inset-x-4 top-[48%] h-48 -translate-y-1/2 rounded-full bg-[#F4EEDF]/5 blur-3xl" />
      <div className="relative h-full min-h-[30rem] lg:min-h-0">
        <PaperCard>
          <ReadingBase highlightLayoutId={highlightLayoutId} preview={preview} reducedMotion={reducedMotion} />
        </PaperCard>
        <FloatingGrammarNote layout={preview.annotationLayout} note={preview.note} reducedMotion={reducedMotion} />
      </div>
    </article>
  );
}
