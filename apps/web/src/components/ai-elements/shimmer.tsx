"use client";

import { cn } from "@/lib/cn";
import type { ElementType } from "react";
import { memo, useMemo } from "react";
import { motion, type HTMLMotionProps } from "motion/react";

export interface TextShimmerProps {
  children: string;
  as?: ElementType;
  className?: string;
  duration?: number;
  spread?: number;
}

const ShimmerComponent = ({
  children,
  as: Component = "p",
  className,
  duration = 2,
  spread = 2,
}: TextShimmerProps) => {
  const dynamicSpread = useMemo(
    () => (children?.length ?? 0) * spread,
    [children, spread]
  );

  const shimmerClassName = cn(
    "relative inline-block bg-[length:250%_100%,auto] bg-clip-text text-transparent motion-reduce:bg-none motion-reduce:text-muted-foreground",
    "[--bg:linear-gradient(90deg,transparent_calc(50%-var(--spread)),var(--color-background),transparent_calc(50%+var(--spread)))] [background-repeat:no-repeat,padding-box]",
    className
  );

  const shimmerStyle = {
    "--spread": `${dynamicSpread}px`,
    backgroundImage:
      "var(--bg), linear-gradient(var(--color-muted-foreground), var(--color-muted-foreground))",
  } as HTMLMotionProps<"span">["style"];

  const shimmerProps = {
    animate: { backgroundPosition: "0% center" },
    className: shimmerClassName,
    initial: { backgroundPosition: "100% center" },
    style: shimmerStyle,
    transition: {
      duration,
      ease: "linear" as const,
      repeat: Number.POSITIVE_INFINITY,
    },
  };

  // Use pre-created motion components (motion.p, motion.span) instead of
  // dynamically creating components during render. Only "p" and "span" are
  // used in practice; other element types fall back to "p".
  if (Component === "span") {
    return <motion.span {...shimmerProps}>{children}</motion.span>;
  }

  return <motion.p {...shimmerProps}>{children}</motion.p>;
};

export const Shimmer = memo(ShimmerComponent);
