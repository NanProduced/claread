"use client";

import { useEffect, useRef, useState } from "react";
import { HeroAppStage } from "@/components/product-page/hero/HeroAppStage";
import { Safari } from "@/components/ui/safari";

const HERO_DEMO_WIDTH = 1200;
const HERO_DEMO_HEIGHT = 700;

export function HeroSafariDemoFrame({
  interactive,
}: {
  interactive: boolean;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(0.519); // Default baseline fallback

  useEffect(() => {
    if (!containerRef.current) return;

    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const width = entry.contentRect.width;
        if (width > 0) {
          setScale(width / HERO_DEMO_WIDTH);
        }
      }
    });

    resizeObserver.observe(containerRef.current);
    return () => resizeObserver.disconnect();
  }, []);

  return (
    <Safari
      mode="simple"
      url="claread.app/read"
      className="!h-auto w-full"
      screenClassName="bg-reader-paper"
    >
      <div ref={containerRef} className="w-full h-full overflow-hidden">
        <div
          className="origin-top-left"
          style={{
            height: HERO_DEMO_HEIGHT,
            transform: `scale(${scale})`,
            width: HERO_DEMO_WIDTH,
          }}
        >
          <HeroAppStage
            className="bg-reader-paper"
            interactive={interactive}
            variant="device"
          />
        </div>
      </div>
    </Safari>
  );
}
