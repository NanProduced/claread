"use client";

import { LoaderCircle } from "lucide-react";
import { cn } from "@/lib/cn";

export type LoaderVariant =
  | "circular"
  | "classic"
  | "pulse"
  | "pulse-dot"
  | "dots"
  | "typing"
  | "wave"
  | "bars"
  | "terminal"
  | "text-blink"
  | "text-shimmer"
  | "loading-dots";

export type LoaderSize = "sm" | "md" | "lg";

export interface LoaderProps {
  variant?: LoaderVariant;
  size?: LoaderSize;
  text?: string;
  className?: string;
}

const SIZE_MAP: Record<LoaderSize, string> = {
  sm: "text-[11px]",
  md: "text-[12px]",
  lg: "text-[14px]",
};

const DOT_SIZE_MAP: Record<LoaderSize, string> = {
  sm: "h-1.5 w-1.5",
  md: "h-2 w-2",
  lg: "h-2.5 w-2.5",
};

const SPINNER_SIZE_MAP: Record<LoaderSize, string> = {
  sm: "h-3 w-3",
  md: "h-3.5 w-3.5",
  lg: "h-4 w-4",
};

function AnimatedDots({ size, className }: { size: LoaderSize; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1", className)} aria-hidden="true">
      {[0, 1, 2].map((index) => (
        <span
          key={index}
          className={cn(
            "prompt-kit-loader-dot rounded-full bg-current",
            DOT_SIZE_MAP[size],
          )}
          style={{ animationDelay: `${index * 120}ms` }}
        />
      ))}
    </span>
  );
}

export function Loader({
  variant = "circular",
  size = "md",
  text,
  className,
}: LoaderProps) {
  if (variant === "text-shimmer") {
    return (
      <span className={cn("inline-flex items-center", SIZE_MAP[size], className)}>
        <span className="prompt-kit-loader-shimmer bg-[linear-gradient(90deg,rgba(98,101,109,0.72)_0%,rgba(38,40,46,0.96)_50%,rgba(98,101,109,0.72)_100%)] bg-[length:200%_100%] bg-clip-text text-transparent">
          {text ?? "Thinking"}
        </span>
      </span>
    );
  }

  if (variant === "text-blink") {
    return (
      <span className={cn("prompt-kit-loader-blink inline-flex items-center", SIZE_MAP[size], className)}>
        {text ?? "Loading"}
      </span>
    );
  }

  if (variant === "loading-dots") {
    return (
      <span className={cn("inline-flex items-center gap-1.5 text-muted", SIZE_MAP[size], className)}>
        {text ? <span>{text}</span> : null}
        <AnimatedDots size={size} />
      </span>
    );
  }

  if (variant === "dots" || variant === "typing") {
    return (
      <span className={cn("inline-flex items-center gap-1 text-muted", className)}>
        <AnimatedDots size={size} />
      </span>
    );
  }

  return (
    <span className={cn("inline-flex items-center gap-2 text-muted", SIZE_MAP[size], className)}>
      <LoaderCircle className={cn("animate-spin", SPINNER_SIZE_MAP[size])} aria-hidden="true" />
      {text ? <span>{text}</span> : null}
    </span>
  );
}
