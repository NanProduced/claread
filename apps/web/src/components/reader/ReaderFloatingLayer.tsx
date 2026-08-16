"use client";

import type { ComponentPropsWithoutRef, CSSProperties, ReactNode } from "react";
import { autoUpdate, FloatingPortal } from "@floating-ui/react";
import {
  flip,
  offset,
  shift,
  useVirtualFloating,
  type Placement,
  type Strategy,
} from "@platejs/floating";
import { ReaderFloatingPanel } from "./plate-ui-adapter";

/**
 * 浮层定位首帧门控：useVirtualFloating 首帧 floatingStyles 尚未计算时，
 * 元素会以 fixed 定位落在视口 (0,0)——用户感知为"从左上角飞入/闪烁"。
 * 定位完成（isPositioned）前强制隐藏，配合 fade 入场。
 */
export function readerFloatingStyles(floating: {
  floatingStyles: unknown;
  isPositioned?: boolean;
}): CSSProperties {
  return {
    ...(floating.floatingStyles as CSSProperties),
    visibility: floating.isPositioned === false ? "hidden" : undefined,
  };
}

interface ReaderFloatingLayerOptions {
  open: boolean;
  placement?: Placement;
  offsetPx?: number;
  crossAxisOffsetPx?: number;
  collisionPadding?: number;
  strategy?: Strategy;
  /**
   * false = 锚定后不跟随滚动（autoUpdate 关闭）。用于笔记面板这类
   * 需要用户自由滚动查看上下文的浮层。
   */
  follow?: boolean;
}

export function useReaderFloatingLayer({
  open,
  placement = "bottom-start",
  offsetPx = 8,
  crossAxisOffsetPx = 0,
  collisionPadding = 16,
  strategy = "absolute",
  follow = true,
}: ReaderFloatingLayerOptions) {
  return useVirtualFloating({
    open,
    placement,
    strategy,
    whileElementsMounted: follow ? autoUpdate : undefined,
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
  chrome?: "lookup" | "bare" | "selection-toolbar";
}

type ReaderFloatingSurfaceDivProps = ReaderFloatingSurfaceProps &
  Omit<ComponentPropsWithoutRef<"div">, "children" | "className" | "chrome">;

export function ReaderFloatingSurface({
  children,
  className,
  floatingRef,
  chrome = "lookup",
  ...props
}: ReaderFloatingSurfaceDivProps) {
  return (
    <FloatingPortal>
      <ReaderFloatingPanel
        floatingRef={floatingRef}
        className={className}
        chrome={chrome}
        {...props}
      >
        {children}
      </ReaderFloatingPanel>
    </FloatingPortal>
  );
}
