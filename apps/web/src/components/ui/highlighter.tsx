"use client";

import { useEffect, useRef, useState, type CSSProperties, type HTMLAttributes, type ReactNode } from "react";
import { useReducedMotion } from "motion/react";
import { cn } from "@/lib/cn";

type HighlighterAction =
  | "highlight"
  | "circle"
  | "box"
  | "bracket"
  | "crossed-off"
  | "strike-through"
  | "underline";

interface HighlighterProps extends Omit<HTMLAttributes<HTMLSpanElement>, "color"> {
  children: ReactNode;
  color?: string;
  action?: HighlighterAction;
  active?: boolean;
  delay?: number;
  strokeWidth?: number;
  animationDuration?: number;
  iterations?: number;
  padding?: number;
  multiline?: boolean;
  isView?: boolean;
}

export function Highlighter({
  action = "highlight",
  active = true,
  animationDuration = 500,
  children,
  className,
  color = "#ffd1dc",
  delay = 0,
  isView = false,
  iterations = 1,
  multiline = true,
  padding = 2,
  strokeWidth = 1.5,
  style,
  ...props
}: HighlighterProps) {
  const shouldReduceMotion = useReducedMotion();
  const highlighterRef = useRef<HTMLSpanElement>(null);
  const [isVisible, setIsVisible] = useState(!isView);
  const isUnderline = action === "underline" || action === "strike-through" || action === "crossed-off";
  const isStrike = action === "strike-through" || action === "crossed-off";
  const effectiveStrokeWidth = strokeWidth * Math.min(1.4, 0.9 + iterations * 0.1);
  const highlightStop = Math.min(78, 60 + iterations * 3);
  const backgroundImage = isUnderline
    ? `linear-gradient(to right, ${color}, ${color})`
    : `linear-gradient(to top, ${color} 0 ${highlightStop}%, transparent ${highlightStop}%)`;
  const initialBackgroundSize = isUnderline ? `0% ${effectiveStrokeWidth}px` : "0% 88%";
  const finalBackgroundSize = isUnderline ? `100% ${effectiveStrokeWidth}px` : "100% 88%";
  const backgroundPosition = isStrike ? "0 58%" : "0 100%";
  const shouldShow = Boolean(shouldReduceMotion || (isVisible && active));
  const activeBackgroundSize = shouldShow ? finalBackgroundSize : initialBackgroundSize;

  useEffect(() => {
    if (!isView || shouldReduceMotion || isVisible) {
      return;
    }

    const element = highlighterRef.current;

    if (!element) {
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsVisible(true);
          observer.disconnect();
        }
      },
      { threshold: 0.72 },
    );

    observer.observe(element);

    return () => observer.disconnect();
  }, [isView, isVisible, shouldReduceMotion]);

  const highlighterStyle = {
    backgroundImage,
    backgroundPosition,
    backgroundRepeat: "no-repeat",
    backgroundSize: activeBackgroundSize,
    paddingInline: `${padding}px`,
    paddingBlock: isUnderline ? 0 : `${Math.max(1, padding / 2)}px`,
    transition: shouldReduceMotion ? "none" : `background-size ${animationDuration}ms cubic-bezier(0.22, 1, 0.36, 1)`,
    transitionDelay: shouldReduceMotion || !shouldShow ? "0ms" : `${delay}ms`,
    ...style,
  } satisfies CSSProperties;

  return (
    <span
      ref={highlighterRef}
      className={cn(
        "relative rounded-[0.16em] [-webkit-box-decoration-break:clone] [box-decoration-break:clone]",
        !multiline && "whitespace-nowrap",
        className,
      )}
      style={highlighterStyle}
      {...props}
    >
      {children}
    </span>
  );
}
