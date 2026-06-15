"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import {
  type MotionValue,
  motion,
  useReducedMotion,
  useScroll,
  useTransform,
} from "motion/react";

import { HeroSafariDemoFrame } from "@/components/product-page/hero/HeroSafariDemoFrame";
import { MacbookScroll } from "@/components/ui/macbook-scroll";

export function HeroScrollScene() {
  const sceneRef = useRef<HTMLDivElement | null>(null);
  const shouldReduceMotion = useReducedMotion();
  const [viewportWidth, setViewportWidth] = useState<number | null>(null);
  const { scrollYProgress } = useScroll({
    target: sceneRef,
    offset: ["start start", "end start"],
  });
  const baseDeviceWidthPx = 512; // 32rem
  const finalScale =
    viewportWidth === null
      ? { x: 2.45, y: 2.48 }
      : {
          x: Math.min((viewportWidth * 0.88) / baseDeviceWidthPx, 2.8),
          y: Math.min((viewportWidth * 0.88) / baseDeviceWidthPx, 2.8),
        };
  const deviceOpacity = useTransform(
    scrollYProgress,
    [0, 0.76, 0.92, 1],
    Boolean(shouldReduceMotion) ? [1, 1, 1, 1] : [1, 1, 0.86, 0.7],
  );
  const deviceSettleScale = useTransform(
    scrollYProgress,
    [0, 0.78, 1],
    Boolean(shouldReduceMotion) ? [1, 1, 1] : [1.4, 1.05, 0.985],
  );

  useEffect(() => {
    const updateViewportWidth = () => setViewportWidth(window.innerWidth);

    updateViewportWidth();
    window.addEventListener("resize", updateViewportWidth);

    return () => window.removeEventListener("resize", updateViewportWidth);
  }, []);

  return (
    <div
      ref={sceneRef}
      data-hero-scroll-scene
      className="relative z-20 mt-10 hidden min-h-[226vh] w-full lg:block xl:mt-12"
    >
      <div className="sticky top-14 h-[calc(100vh-3.5rem)] overflow-visible">
        <HeroSceneBackdrop
          reducedMotion={Boolean(shouldReduceMotion)}
          scrollYProgress={scrollYProgress}
        />

        <div className="relative z-10 flex h-full w-full items-start justify-center pt-16 md:pt-24 lg:pt-32">
          <motion.div
            className="origin-top"
            style={{ opacity: deviceOpacity, scale: deviceSettleScale }}
          >
            <MacbookScroll
              baseHeight="19.6rem"
              className="!min-h-0 !py-0"
              deviceWidth="32rem"
              finalScaleX={finalScale.x}
              finalScaleY={finalScale.y}
              finalTranslateY={0}
              interactionProgress={0.38}
              lidHeight="11.2rem"
              sceneClassName="pt-0"
              screenClassName="bg-reader-paper"
              screenHeight="20.8rem"
              scrollYProgress={scrollYProgress}
              showGradient={false}
              title={null}
            >
              {({ isInteractive }) => (
                <HeroSafariDemoFrame interactive={isInteractive} />
              )}
            </MacbookScroll>
          </motion.div>
        </div>

        <HeroSceneBridge
          reducedMotion={Boolean(shouldReduceMotion)}
          scrollYProgress={scrollYProgress}
        />
      </div>
    </div>
  );
}

function HeroSceneBackdrop({
  reducedMotion,
  scrollYProgress,
}: {
  reducedMotion: boolean;
  scrollYProgress: MotionValue<number>;
}) {
  const rightOpacity = useTransform(
    scrollYProgress,
    [0, 0.18, 0.76, 1],
    reducedMotion ? [0.26, 0.26, 0.26, 0.26] : [0.14, 0.32, 0.2, 0.04],
  );
  const rightY = useTransform(
    scrollYProgress,
    [0, 1],
    reducedMotion ? ["0rem", "0rem"] : ["2rem", "-3rem"],
  );
  const leftOpacity = useTransform(
    scrollYProgress,
    [0, 0.24, 0.66, 0.9],
    reducedMotion ? [0.12, 0.12, 0.12, 0.12] : [0, 0.16, 0.08, 0],
  );
  const leftY = useTransform(
    scrollYProgress,
    [0, 1],
    reducedMotion ? ["0rem", "0rem"] : ["4rem", "-1.5rem"],
  );

  return (
    <div
      className="pointer-events-none absolute inset-x-[calc(50%_-_50vw)] bottom-[-12vh] top-[-10vh] -z-10 overflow-hidden"
      aria-hidden="true"
      style={{
        maskImage:
          "linear-gradient(to bottom, transparent 0%, black 12%, black 82%, transparent 100%)",
        WebkitMaskImage:
          "linear-gradient(to bottom, transparent 0%, black 12%, black 82%, transparent 100%)",
      }}
    >
      <motion.div
        className="absolute -right-[32rem] bottom-[-20rem] h-[70rem] w-[84rem] xl:-right-[27rem] xl:h-[76rem] xl:w-[92rem]"
        style={{ opacity: rightOpacity, y: rightY }}
      >
        <Image
          src="/brand/landing/hero-aperture-corner-v2.png"
          alt=""
          fill
          sizes="(max-width: 1279px) 84rem, 92rem"
          className="select-none object-contain object-right-bottom"
          priority
        />
      </motion.div>

      <motion.div
        className="absolute -bottom-[18rem] -left-[24rem] h-[48rem] w-[62rem] xl:-left-[20rem] xl:h-[54rem] xl:w-[70rem]"
        style={{ opacity: leftOpacity, y: leftY }}
      >
        <Image
          src="/brand/landing/hero-aperture-foreground-v2.png"
          alt=""
          fill
          sizes="(max-width: 1279px) 62rem, 70rem"
          className="select-none object-contain object-left-bottom"
        />
      </motion.div>
    </div>
  );
}

function HeroSceneBridge({
  reducedMotion,
  scrollYProgress,
}: {
  reducedMotion: boolean;
  scrollYProgress: MotionValue<number>;
}) {
  const opacity = useTransform(
    scrollYProgress,
    [0, 0.58, 0.82, 1],
    reducedMotion ? [1, 1, 1, 1] : [0, 0, 0.88, 1],
  );

  return (
    <motion.div
      className="pointer-events-none absolute inset-x-[calc(50%_-_50vw)] bottom-[-1px] z-30 h-[clamp(10rem,22vh,16rem)]"
      aria-hidden="true"
      style={{ opacity }}
    >
      <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(250,249,246,0)_0%,rgba(250,249,246,0.7)_48%,#F7F5F0_74%,#F7F5F0_100%)]" />
      <div className="absolute inset-x-0 bottom-0 h-16 bg-[#F7F5F0]" />
      <div className="absolute inset-x-[8vw] top-[42%] h-px bg-gradient-to-r from-transparent via-hairline/70 to-transparent" />
    </motion.div>
  );
}
