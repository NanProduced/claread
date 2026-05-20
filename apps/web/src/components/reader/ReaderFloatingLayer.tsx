"use client";

import type { ComponentPropsWithoutRef, ReactNode } from "react";
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
  className?: string;
  floatingRef?: (node: HTMLDivElement | null) => void;
}

type ReaderFloatingSurfaceDivProps = ReaderFloatingSurfaceProps &
  Omit<ComponentPropsWithoutRef<"div">, "children" | "className">;

export function ReaderFloatingSurface({
  children,
  className,
  floatingRef,
  ...props
}: ReaderFloatingSurfaceDivProps) {
  return (
    <ReaderFloatingPanel
      floatingRef={floatingRef}
      className={className}
      {...props}
    >
      {children}
    </ReaderFloatingPanel>
  );
}
