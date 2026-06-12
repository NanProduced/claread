"use client";
import React, { useRef, useState, type CSSProperties, type ReactNode } from "react";
import {
  MotionValue,
  motion,
  useMotionValueEvent,
  useReducedMotion,
  useScroll,
  useTransform,
} from "motion/react";
import { cn } from "@/lib/cn";
import {
  IconBrightnessDown,
  IconBrightnessUp,
  IconCaretRightFilled,
  IconCaretUpFilled,
  IconChevronUp,
  IconMicrophone,
  IconMoon,
  IconPlayerSkipForward,
  IconPlayerTrackNext,
  IconPlayerTrackPrev,
  IconTable,
  IconVolume,
  IconVolume2,
  IconVolume3,
} from "@tabler/icons-react";
import { IconSearch } from "@tabler/icons-react";
import { IconWorld } from "@tabler/icons-react";
import { IconCommand } from "@tabler/icons-react";
import { IconCaretLeftFilled } from "@tabler/icons-react";
import { IconCaretDownFilled } from "@tabler/icons-react";


export const MacbookScroll = ({
  children,
  className,
  baseHeight = "22rem",
  deviceWidth = "32rem",
  finalScaleX = 1.5,
  finalScaleY = 1.5,
  finalTranslateY = 1500,
  interactionProgress = 0.82,
  lidHeight = "12rem",
  pinned = false,
  sceneClassName,
  screenHeight = "24rem",
  screenClassName,
  scrollYProgress: controlledScrollYProgress,
  src,
  showGradient,
  title,
  badge,
}: {
  children?:
    | ReactNode
    | ((state: {
        isInteractive: boolean;
        scrollYProgress: MotionValue<number>;
      }) => ReactNode);
  baseHeight?: string;
  className?: string;
  deviceWidth?: string;
  finalScaleX?: number;
  finalScaleY?: number;
  finalTranslateY?: number;
  interactionProgress?: number;
  lidHeight?: string;
  pinned?: boolean;
  sceneClassName?: string;
  screenHeight?: string;
  screenClassName?: string;
  scrollYProgress?: MotionValue<number>;
  src?: string;
  showGradient?: boolean;
  title?: string | ReactNode | null;
  badge?: ReactNode;
}) => {
  const ref = useRef<HTMLDivElement>(null);
  const shouldReduceMotion = useReducedMotion();
  const internalScroll = useScroll({
    target: ref,
    offset: ["start start", "end start"],
  });
  const scrollYProgress = controlledScrollYProgress ?? internalScroll.scrollYProgress;

  const [isMobile] = useState(
    () => typeof window !== "undefined" && window.innerWidth < 768,
  );
  const [isInteractive, setIsInteractive] = useState(false);

  const scaleX = useTransform(
    scrollYProgress,
    [0, 0.3],
    shouldReduceMotion ? [1, 1] : [1.2, isMobile ? 1 : finalScaleX],
  );
  const scaleY = useTransform(
    scrollYProgress,
    [0, 0.3],
    shouldReduceMotion ? [1, 1] : [0.6, isMobile ? 1 : finalScaleY],
  );
  const translate = useTransform(
    scrollYProgress,
    [0, 1],
    shouldReduceMotion ? [0, 0] : [0, finalTranslateY],
  );
  const rotate = useTransform(
    scrollYProgress,
    [0.1, 0.12, 0.3],
    shouldReduceMotion ? [0, 0, 0] : [-28, -28, 0],
  );

  const bezelOpacity = useTransform(
    scrollYProgress,
    [0.15, 0.28],
    shouldReduceMotion ? [0, 0] : [1, 0]
  );
  const bezelPadding = useTransform(
    scrollYProgress,
    [0.15, 0.28],
    shouldReduceMotion ? ["0px", "0px"] : ["2px", "0px"]
  );
  const bezelInset = useTransform(
    scrollYProgress,
    [0.15, 0.28],
    shouldReduceMotion ? ["0px", "0px"] : ["2px", "0px"]
  );
  const bezelBorderRadius = useTransform(
    scrollYProgress,
    [0.15, 0.28],
    shouldReduceMotion ? ["0px", "0px"] : ["4px", "0px"]
  );
  const screenOuterBg = useTransform(
    scrollYProgress,
    [0.15, 0.28],
    shouldReduceMotion
      ? ["rgba(1, 1, 1, 0)", "rgba(1, 1, 1, 0)"]
      : ["rgba(1, 1, 1, 1)", "rgba(1, 1, 1, 0)"]
  );
  const screenInnerBg = useTransform(
    scrollYProgress,
    [0.15, 0.28],
    shouldReduceMotion
      ? ["rgba(39, 39, 41, 0)", "rgba(39, 39, 41, 0)"]
      : ["rgba(39, 39, 41, 1)", "rgba(39, 39, 41, 0)"]
  );
  const bezelShadow = useTransform(
    scrollYProgress,
    [0.15, 0.28],
    shouldReduceMotion
      ? ["none", "none"]
      : ["none", "0px 25px 80px -12px rgba(0,0,0,0.3)"]
  );

  useMotionValueEvent(scrollYProgress, "change", (latest) => {
    const nextInteractive = Boolean(shouldReduceMotion) || latest >= interactionProgress;
    setIsInteractive((current) => (current === nextInteractive ? current : nextInteractive));
  });

  const effectiveInteractive = Boolean(shouldReduceMotion) || isInteractive;
  const screenContent: ReactNode =
    typeof children === "function"
      ? children({ isInteractive: effectiveInteractive, scrollYProgress })
      : children;
  const titleContent: ReactNode =
    title ?? (
      <span>
        This Macbook is built with Tailwindcss. <br /> No kidding.
      </span>
    );
  const deviceStyle = {
    "--macbook-base-height": baseHeight,
    "--macbook-lid-height": lidHeight,
    "--macbook-screen-height": screenHeight,
    "--macbook-width": deviceWidth,
  } as CSSProperties;

  return (
    <div
      ref={ref}
      className={cn(
        "relative flex min-h-[160vh] shrink-0 scale-[0.35] transform flex-col items-center justify-start py-0 [perspective:800px] sm:scale-50 md:scale-100 md:transform-none md:pt-16 md:pb-32",
        className,
      )}
    >
      {title !== null ? (
        <h2 className="mb-20 text-center text-3xl font-bold text-neutral-800 dark:text-white">
          {titleContent}
        </h2>
      ) : null}
      <div
        style={deviceStyle}
        className={cn(
          "flex flex-col items-center",
          pinned &&
            "sticky top-0 h-screen justify-start [perspective:800px]",
          sceneClassName,
        )}
      >
        {/* Lid */}
        <Lid
          screenClassName={screenClassName}
          src={src}
          scaleX={scaleX}
          scaleY={scaleY}
          rotate={rotate}
          translate={translate}
          isInteractive={effectiveInteractive}
          screenContent={screenContent}
          bezelOpacity={bezelOpacity}
          bezelPadding={bezelPadding}
          bezelInset={bezelInset}
          bezelBorderRadius={bezelBorderRadius}
          screenOuterBg={screenOuterBg}
          screenInnerBg={screenInnerBg}
          bezelShadow={bezelShadow}
        />
        {/* Base area */}
        <motion.div
          style={{ opacity: bezelOpacity }}
          className="relative -z-10 h-[var(--macbook-base-height)] w-[var(--macbook-width)] overflow-hidden rounded-b-[2rem] rounded-t-xl bg-[#d5d5d5] shadow-[0_-4px_12px_rgba(0,0,0,0.1)_inset,0_1px_1px_rgba(255,255,255,0.8)_inset] dark:bg-[#1a1a1c] dark:shadow-[0_-4px_12px_rgba(0,0,0,0.4)_inset,0_1px_1px_rgba(255,255,255,0.1)_inset]"
        >
          {/* above keyboard bar */}
          <div className="relative h-10 w-full pt-2">
            <div className="absolute inset-x-0 bottom-0 mx-auto h-[16px] w-[80%] rounded-t-[2px] bg-[#050505]" />
          </div>
          <div className="relative flex justify-center pb-4">
            <div className="h-full w-[10%] overflow-hidden pt-1">
              <SpeakerGrid />
            </div>
            <div className="h-full w-[80%] px-[2px]">
              <Keypad />
            </div>
            <div className="h-full w-[10%] overflow-hidden pt-1">
              <SpeakerGrid />
            </div>
          </div>
          <Trackpad />
          <div className="absolute inset-x-0 bottom-0 mx-auto h-2 w-20 rounded-tl-3xl rounded-tr-3xl bg-gradient-to-t from-[#272729] to-[#050505]" />
          {showGradient && (
            <div className="absolute inset-x-0 bottom-0 z-50 h-40 w-full bg-gradient-to-t from-white via-white to-transparent dark:from-black dark:via-black"></div>
          )}
          {badge && <div className="absolute bottom-4 left-4">{badge}</div>}
        </motion.div>
      </div>
    </div>
  );
};

export const Lid = ({
  isInteractive,
  scaleX,
  scaleY,
  rotate,
  screenClassName,
  screenContent,
  translate,
  src,
  bezelOpacity,
  bezelPadding,
  bezelInset,
  bezelBorderRadius,
  screenOuterBg,
  screenInnerBg,
  bezelShadow,
}: {
  isInteractive: boolean;
  scaleX: MotionValue<number>;
  scaleY: MotionValue<number>;
  rotate: MotionValue<number>;
  screenClassName?: string;
  screenContent?: ReactNode;
  translate: MotionValue<number>;
  src?: string;
  bezelOpacity: MotionValue<number>;
  bezelPadding: MotionValue<string>;
  bezelInset: MotionValue<string>;
  bezelBorderRadius: MotionValue<string>;
  screenOuterBg: MotionValue<string>;
  screenInnerBg: MotionValue<string>;
  bezelShadow: MotionValue<string>;
}) => {
  return (
    <div className="relative [perspective:800px]">
      <motion.div
        style={{
          transform: "perspective(800px) rotateX(-25deg) translateZ(0px)",
          transformOrigin: "bottom",
          transformStyle: "preserve-3d",
          opacity: bezelOpacity,
        }}
        className="pointer-events-none relative h-[var(--macbook-lid-height)] w-[var(--macbook-width)] rounded-2xl bg-[#010101] p-2"
      >
        <div
          style={{
            boxShadow: "0px 2px 0px 2px #171717 inset",
          }}
          className="absolute inset-0 flex items-center justify-center rounded-lg bg-[#010101]"
        >
          <img
            src="/brand/claread-icon-fullcolor.png"
            alt="Claread"
            className="h-5 w-5 opacity-60 filter grayscale brightness-[2]"
          />
        </div>
      </motion.div>
      <motion.div
        style={{
          scaleX: scaleX,
          scaleY: scaleY,
          rotateX: rotate,
          translateY: translate,
          transformStyle: "preserve-3d",
          transformOrigin: "top",
          padding: bezelPadding,
          borderRadius: bezelBorderRadius,
          backgroundColor: screenOuterBg,
          boxShadow: bezelShadow,
        }}
        className="pointer-events-none absolute inset-0 h-[var(--macbook-screen-height)] w-[var(--macbook-width)]"
      >
        <motion.div 
          style={{
            backgroundColor: screenInnerBg,
            borderRadius: bezelBorderRadius,
          }}
          className="pointer-events-none absolute inset-0"
        />
        <motion.div
          style={{
            top: bezelInset,
            bottom: bezelInset,
            left: bezelInset,
            right: bezelInset,
            borderRadius: bezelBorderRadius,
          }}
          className={cn(
            "absolute z-10 overflow-hidden",
            isInteractive ? "pointer-events-auto" : "pointer-events-none",
            screenClassName,
          )}
          inert={isInteractive ? undefined : true}
        >
          {(screenContent as any) ??
            (src ? (
              <img
                src={src}
                alt=""
                className="absolute inset-0 h-full w-full object-cover object-left-top"
              />
            ) : null)}
        </motion.div>
      </motion.div>
    </div>
  );
};

export const Trackpad = () => {
  return (
    <div
      className="mx-auto mt-2 h-32 w-[40%] rounded-xl bg-[#c5c5c5] shadow-[0_1px_1px_rgba(0,0,0,0.1)_inset,0_1px_1px_rgba(255,255,255,0.4)] dark:bg-[#1f1f21] dark:shadow-[0_1px_1px_rgba(0,0,0,0.4)_inset,0_1px_1px_rgba(255,255,255,0.05)]"
    ></div>
  );
};

export const Keypad = () => {
  return (
    <div className="h-full rounded-md bg-[#050505] p-1 shadow-[0_1px_2px_rgba(0,0,0,0.5)_inset,0_1px_0_rgba(255,255,255,0.2)]">
      {/* First Row */}
      <div className="mb-[2px] flex w-full shrink-0 gap-[2px]">
        <KBtn
          className="w-10 items-end justify-start pb-[2px] pl-[4px]"
          childrenClassName="items-start"
        >
          esc
        </KBtn>
        <KBtn>
          <IconBrightnessDown className="h-[6px] w-[6px]" />
          <span className="mt-1 inline-block">F1</span>
        </KBtn>
        <KBtn>
          <IconBrightnessUp className="h-[6px] w-[6px]" />
          <span className="mt-1 inline-block">F2</span>
        </KBtn>
        <KBtn>
          <IconTable className="h-[6px] w-[6px]" />
          <span className="mt-1 inline-block">F3</span>
        </KBtn>
        <KBtn>
          <IconSearch className="h-[6px] w-[6px]" />
          <span className="mt-1 inline-block">F4</span>
        </KBtn>
        <KBtn>
          <IconMicrophone className="h-[6px] w-[6px]" />
          <span className="mt-1 inline-block">F5</span>
        </KBtn>
        <KBtn>
          <IconMoon className="h-[6px] w-[6px]" />
          <span className="mt-1 inline-block">F6</span>
        </KBtn>
        <KBtn>
          <IconPlayerTrackPrev className="h-[6px] w-[6px]" />
          <span className="mt-1 inline-block">F7</span>
        </KBtn>
        <KBtn>
          <IconPlayerSkipForward className="h-[6px] w-[6px]" />
          <span className="mt-1 inline-block">F8</span>
        </KBtn>
        <KBtn>
          <IconPlayerTrackNext className="h-[6px] w-[6px]" />
          <span className="mt-1 inline-block">F9</span>
        </KBtn>
        <KBtn>
          <IconVolume3 className="h-[6px] w-[6px]" />
          <span className="mt-1 inline-block">F10</span>
        </KBtn>
        <KBtn>
          <IconVolume2 className="h-[6px] w-[6px]" />
          <span className="mt-1 inline-block">F11</span>
        </KBtn>
        <KBtn>
          <IconVolume className="h-[6px] w-[6px]" />
          <span className="mt-1 inline-block">F12</span>
        </KBtn>
        <KBtn>
          <div className="h-4 w-4 rounded-full bg-gradient-to-b from-neutral-900 from-20% via-black via-50% to-neutral-900 to-95% p-px">
            <div className="h-full w-full rounded-full bg-black" />
          </div>
        </KBtn>
      </div>

      {/* Second row */}
      <div className="mb-[2px] flex w-full shrink-0 gap-[2px]">
        <KBtn>
          <span className="block">~</span>
          <span className="mt-1 block">`</span>
        </KBtn>
        <KBtn>
          <span className="block">!</span>
          <span className="block">1</span>
        </KBtn>
        <KBtn>
          <span className="block">@</span>
          <span className="block">2</span>
        </KBtn>
        <KBtn>
          <span className="block">#</span>
          <span className="block">3</span>
        </KBtn>
        <KBtn>
          <span className="block">$</span>
          <span className="block">4</span>
        </KBtn>
        <KBtn>
          <span className="block">%</span>
          <span className="block">5</span>
        </KBtn>
        <KBtn>
          <span className="block">^</span>
          <span className="block">6</span>
        </KBtn>
        <KBtn>
          <span className="block">&</span>
          <span className="block">7</span>
        </KBtn>
        <KBtn>
          <span className="block">*</span>
          <span className="block">8</span>
        </KBtn>
        <KBtn>
          <span className="block">(</span>
          <span className="block">9</span>
        </KBtn>
        <KBtn>
          <span className="block">)</span>
          <span className="block">0</span>
        </KBtn>
        <KBtn>
          <span className="block">_</span>
          <span className="block">-</span>
        </KBtn>
        <KBtn>
          <span className="block">+</span>
          <span className="block"> = </span>
        </KBtn>
        <KBtn
          className="w-10 items-end justify-end pr-[4px] pb-[2px]"
          childrenClassName="items-end"
        >
          delete
        </KBtn>
      </div>

      {/* Third row */}
      <div className="mb-[2px] flex w-full shrink-0 gap-[2px]">
        <KBtn
          className="w-10 items-end justify-start pb-[2px] pl-[4px]"
          childrenClassName="items-start"
        >
          tab
        </KBtn>
        <KBtn>
          <span className="block">Q</span>
        </KBtn>
        <KBtn>
          <span className="block">W</span>
        </KBtn>
        <KBtn>
          <span className="block">E</span>
        </KBtn>
        <KBtn>
          <span className="block">R</span>
        </KBtn>
        <KBtn>
          <span className="block">T</span>
        </KBtn>
        <KBtn>
          <span className="block">Y</span>
        </KBtn>
        <KBtn>
          <span className="block">U</span>
        </KBtn>
        <KBtn>
          <span className="block">I</span>
        </KBtn>
        <KBtn>
          <span className="block">O</span>
        </KBtn>
        <KBtn>
          <span className="block">P</span>
        </KBtn>
        <KBtn>
          <span className="block">{`{`}</span>
          <span className="block">{`[`}</span>
        </KBtn>
        <KBtn>
          <span className="block">{`}`}</span>
          <span className="block">{`]`}</span>
        </KBtn>
        <KBtn>
          <span className="block">{`|`}</span>
          <span className="block">{`\\`}</span>
        </KBtn>
      </div>

      {/* Fourth Row */}
      <div className="mb-[2px] flex w-full shrink-0 gap-[2px]">
        <KBtn
          className="w-[2.8rem] items-end justify-start pb-[2px] pl-[4px]"
          childrenClassName="items-start"
        >
          caps lock
        </KBtn>
        <KBtn>
          <span className="block">A</span>
        </KBtn>
        <KBtn>
          <span className="block">S</span>
        </KBtn>
        <KBtn>
          <span className="block">D</span>
        </KBtn>
        <KBtn>
          <span className="block">F</span>
        </KBtn>
        <KBtn>
          <span className="block">G</span>
        </KBtn>
        <KBtn>
          <span className="block">H</span>
        </KBtn>
        <KBtn>
          <span className="block">J</span>
        </KBtn>
        <KBtn>
          <span className="block">K</span>
        </KBtn>
        <KBtn>
          <span className="block">L</span>
        </KBtn>
        <KBtn>
          <span className="block">{`:`}</span>
          <span className="block">{`;`}</span>
        </KBtn>
        <KBtn>
          <span className="block">{`"`}</span>
          <span className="block">{`'`}</span>
        </KBtn>
        <KBtn
          className="w-[2.85rem] items-end justify-end pr-[4px] pb-[2px]"
          childrenClassName="items-end"
        >
          return
        </KBtn>
      </div>

      {/* Fifth Row */}
      <div className="mb-[2px] flex w-full shrink-0 gap-[2px]">
        <KBtn
          className="w-[3.65rem] items-end justify-start pb-[2px] pl-[4px]"
          childrenClassName="items-start"
        >
          shift
        </KBtn>
        <KBtn>
          <span className="block">Z</span>
        </KBtn>
        <KBtn>
          <span className="block">X</span>
        </KBtn>
        <KBtn>
          <span className="block">C</span>
        </KBtn>
        <KBtn>
          <span className="block">V</span>
        </KBtn>
        <KBtn>
          <span className="block">B</span>
        </KBtn>
        <KBtn>
          <span className="block">N</span>
        </KBtn>
        <KBtn>
          <span className="block">M</span>
        </KBtn>
        <KBtn>
          <span className="block">{`<`}</span>
          <span className="block">{`,`}</span>
        </KBtn>
        <KBtn>
          <span className="block">{`>`}</span>
          <span className="block">{`.`}</span>
        </KBtn>
        <KBtn>
          <span className="block">{`?`}</span>
          <span className="block">{`/`}</span>
        </KBtn>
        <KBtn
          className="w-[3.65rem] items-end justify-end pr-[4px] pb-[2px]"
          childrenClassName="items-end"
        >
          shift
        </KBtn>
      </div>

      {/* sixth Row */}
      <div className="mb-[2px] flex w-full shrink-0 gap-[2px]">
        <KBtn className="" childrenClassName="h-full justify-between py-[4px]">
          <div className="flex w-full justify-end pr-1">
            <span className="block">fn</span>
          </div>
          <div className="flex w-full justify-start pl-1">
            <IconWorld className="h-[6px] w-[6px]" />
          </div>
        </KBtn>
        <KBtn className="" childrenClassName="h-full justify-between py-[4px]">
          <div className="flex w-full justify-end pr-1">
            <IconChevronUp className="h-[6px] w-[6px]" />
          </div>
          <div className="flex w-full justify-start pl-1">
            <span className="block">control</span>
          </div>
        </KBtn>
        <KBtn className="" childrenClassName="h-full justify-between py-[4px]">
          <div className="flex w-full justify-end pr-1">
            <OptionKey className="h-[6px] w-[6px]" />
          </div>
          <div className="flex w-full justify-start pl-1">
            <span className="block">option</span>
          </div>
        </KBtn>
        <KBtn
          className="w-8"
          childrenClassName="h-full justify-between py-[4px]"
        >
          <div className="flex w-full justify-end pr-1">
            <IconCommand className="h-[6px] w-[6px]" />
          </div>
          <div className="flex w-full justify-start pl-1">
            <span className="block">command</span>
          </div>
        </KBtn>
        <KBtn className="w-[8.2rem]"></KBtn>
        <KBtn
          className="w-8"
          childrenClassName="h-full justify-between py-[4px]"
        >
          <div className="flex w-full justify-start pl-1">
            <IconCommand className="h-[6px] w-[6px]" />
          </div>
          <div className="flex w-full justify-end pr-1">
            <span className="block">command</span>
          </div>
        </KBtn>
        <KBtn className="" childrenClassName="h-full justify-between py-[4px]">
          <div className="flex w-full justify-start pl-1">
            <OptionKey className="h-[6px] w-[6px]" />
          </div>
          <div className="flex w-full justify-start pl-1">
            <span className="block">option</span>
          </div>
        </KBtn>
        <div className="mt-[2px] flex h-6 w-[4.9rem] flex-col items-center justify-end rounded-[4px] p-[0.5px]">
          <KBtn className="h-3 w-6">
            <IconCaretUpFilled className="h-[6px] w-[6px]" />
          </KBtn>
          <div className="flex">
            <KBtn className="h-3 w-6">
              <IconCaretLeftFilled className="h-[6px] w-[6px]" />
            </KBtn>
            <KBtn className="h-3 w-6">
              <IconCaretDownFilled className="h-[6px] w-[6px]" />
            </KBtn>
            <KBtn className="h-3 w-6">
              <IconCaretRightFilled className="h-[6px] w-[6px]" />
            </KBtn>
          </div>
        </div>
      </div>
    </div>
  );
};

export const KBtn = ({
  className,
  children,
  childrenClassName,
  backlit = true,
}: {
  className?: string;
  children?: ReactNode;
  childrenClassName?: string;
  backlit?: boolean;
}) => {
  return (
    <div
      className={cn(
        "[transform:translateZ(0)] rounded-[4px] p-[0.5px] [will-change:transform]",
        backlit && "bg-white/[0.15] shadow-[0_0_1.5px_0.5px_rgba(255,255,255,0.3),_0_0_3px_1px_rgba(255,255,255,0.25)]",
      )}
    >
      <div
        className={cn(
          "flex h-6 w-6 items-center justify-center rounded-[3.5px] bg-[#0A090D]",
          className,
        )}
        style={{
          boxShadow:
            "0px -0.5px 2px 0 #0D0D0F inset, -0.5px 0px 2px 0 #0D0D0F inset",
        }}
      >
        <div
          className={cn(
            "flex w-full flex-col items-center justify-center text-[5px] text-neutral-200",
            childrenClassName,
            backlit && "text-white",
          )}
        >
          {children}
        </div>
      </div>
    </div>
  );
};

export const SpeakerGrid = () => {
  return (
    <div
      className="mt-2 flex h-40 gap-[2px] px-[0.5px]"
      style={{
        backgroundImage:
          "radial-gradient(circle, #08080A 0.5px, transparent 0.5px)",
        backgroundSize: "3px 3px",
      }}
    ></div>
  );
};

export const OptionKey = ({ className }: { className: string }) => {
  return (
    <svg
      fill="none"
      version="1.1"
      id="icon"
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 32 32"
      className={className}
    >
      <rect
        stroke="currentColor"
        strokeWidth={2}
        x="18"
        y="5"
        width="10"
        height="2"
      />
      <polygon
        stroke="currentColor"
        strokeWidth={2}
        points="10.6,5 4,5 4,7 9.4,7 18.4,27 28,27 28,25 19.6,25 "
      />
      <rect
        id="_Transparent_Rectangle_"
        className="st0"
        width="32"
        height="32"
        stroke="none"
      />
    </svg>
  );
};

const AceternityLogo = () => {
  return (
    <svg
      width="66"
      height="65"
      viewBox="0 0 66 65"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className="h-3 w-3 text-white"
    >
      <path
        d="M8 8.05571C8 8.05571 54.9009 18.1782 57.8687 30.062C60.8365 41.9458 9.05432 57.4696 9.05432 57.4696"
        stroke="currentColor"
        strokeWidth="15"
        strokeMiterlimit="3.86874"
        strokeLinecap="round"
      />
    </svg>
  );
};
