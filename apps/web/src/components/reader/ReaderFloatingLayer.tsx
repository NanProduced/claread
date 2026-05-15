"use client";

import type { AriaRole, CSSProperties, MouseEvent, PointerEvent, ReactNode } from "react";
import {
  autoUpdate,
  flip,
  offset,
  shift,
  useFloating,
  type Placement,
  type Strategy,
} from "@floating-ui/react";

interface ReaderFloatingLayerOptions {
  open: boolean;
  placement?: Placement;
  offsetPx?: number;
  crossAxisOffsetPx?: number;
  collisionPadding?: number;
  strategy?: Strategy;
}

export function useReaderFloatingLayer({
  open,
  placement = "bottom-start",
  offsetPx = 8,
  crossAxisOffsetPx = 0,
  collisionPadding = 16,
  strategy = "absolute",
}: ReaderFloatingLayerOptions) {
  return useFloating({
    open,
    placement,
    strategy,
    middleware: [
      offset({
        mainAxis: offsetPx,
        crossAxis: crossAxisOffsetPx,
      }),
      flip({ padding: collisionPadding }),
      shift({ padding: collisionPadding }),
    ],
    whileElementsMounted: autoUpdate,
  });
}

interface ReaderFloatingSurfaceProps {
  children: ReactNode;
  className: string;
  floatingRef?: (node: HTMLSpanElement | null) => void;
  style?: CSSProperties;
  role?: AriaRole;
  "aria-live"?: "off" | "polite" | "assertive";
  onClick?: (event: MouseEvent<HTMLSpanElement>) => void;
  onPointerDown?: (event: PointerEvent<HTMLSpanElement>) => void;
}

export function ReaderFloatingSurface({
  children,
  className,
  floatingRef,
  style,
  role,
  "aria-live": ariaLive,
  onClick,
  onPointerDown,
}: ReaderFloatingSurfaceProps) {
  return (
    <span
      ref={floatingRef}
      className={className}
      role={role}
      aria-live={ariaLive}
      style={style}
      onClick={onClick}
      onPointerDown={onPointerDown}
    >
      {children}
    </span>
  );
}
