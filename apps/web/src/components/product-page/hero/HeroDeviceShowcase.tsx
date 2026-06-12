"use client";

import { MacbookScroll } from "@/components/ui/macbook-scroll";
import { Safari } from "@/components/ui/safari";
import { HeroAppStage } from "@/components/product-page/hero/HeroAppStage";

const HERO_DEMO_WIDTH = 1200;
const HERO_DEMO_HEIGHT = 700;
const MACBOOK_SCREEN_SCALE = 0.413;

export function HeroDeviceShowcase() {
  return (
    <div
      data-hero-device-showcase
      className="relative z-20 mt-0 hidden w-full justify-center lg:flex"
    >
      <MacbookScroll
        className="min-h-[226vh] py-4 md:py-6 xl:py-8"
        finalScaleX={2.45}
        finalScaleY={2.35}
        finalTranslateY={0}
        interactionProgress={0.3}
        pinned
        sceneClassName="pt-0"
        screenClassName="bg-reader-paper"
        showGradient
        title={null}
      >
        {({ isInteractive }) => (
          <Safari
            mode="simple"
            url="claread.app/read"
            className="!h-auto w-full"
            screenClassName="bg-reader-paper"
          >
            <div
              className="origin-top-left"
              style={{
                height: HERO_DEMO_HEIGHT,
                transform: `scale(${MACBOOK_SCREEN_SCALE})`,
                width: HERO_DEMO_WIDTH,
              }}
            >
              <HeroAppStage
                className="bg-reader-paper"
                interactive={isInteractive}
                variant="device"
              />
            </div>
          </Safari>
        )}
      </MacbookScroll>
    </div>
  );
}
