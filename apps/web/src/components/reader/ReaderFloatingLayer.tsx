"use client";

import type { AriaRole, CSSProperties, MouseEvent, PointerEvent, ReactNode } from "react";
import {
  flip,
  offset,
  shift,
  useVirtualFloating,
  type Placement,
  type Strategy,
} from "@platejs/floating";
import { ReaderFloatingPanel } from "./plate-ui-adapter";

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
  return useVirtualFloating({
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
  });
}

interface ReaderFloatingSurfaceProps {
  children: ReactNode;
  className: string;
  floatingRef?: (node: HTMLDivElement | null) => void;
  style?: CSSProperties;
  role?: AriaRole;
  onClick?: (event: MouseEvent<HTMLDivElement>) => void;
  onPointerDown?: (event: PointerEvent<HTMLDivElement>) => void;
}

export function ReaderFloatingSurface({
  children,
  className,
  floatingRef,
  style,
  role,
  onClick,
  onPointerDown,
}: ReaderFloatingSurfaceProps) {
  return (
    <ReaderFloatingPanel
      floatingRef={floatingRef}
      className={className}
      role={role}
      style={style}
      onClick={onClick}
      onPointerDown={onPointerDown}
    >
      {children}
    </ReaderFloatingPanel>
  );
}
