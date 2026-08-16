"use client";

import { useLayoutEffect, useState } from "react";
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
 * 定位完成前强制隐藏；具体表面是否需要入场动效由调用方决定。
 */
export function readerFloatingStyles(floating: {
  floatingStyles: unknown;
  isPositioned?: boolean;
}): CSSProperties {
  const floatingStyles = floating.floatingStyles as CSSProperties;
  const isPositioned = floating.isPositioned !== false;

  return {
    ...floatingStyles,
    visibility: isPositioned ? floatingStyles.visibility : "hidden",
    animationPlayState: isPositioned ? undefined : "paused",
  };
}

interface ReaderFloatingLayerOptions {
  open: boolean;
  /** 参考锚点或内容切换时，重新等待一次浮层定位。 */
  positionKey?: string | number;
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
  positionKey,
  placement = "bottom-start",
  offsetPx = 8,
  crossAxisOffsetPx = 0,
  collisionPadding = 16,
  strategy = "absolute",
  follow = true,
}: ReaderFloatingLayerOptions) {
  const floating = useVirtualFloating({
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

  const [isPositioned, setIsPositioned] = useState(!open);

  useLayoutEffect(() => {
    if (!open) {
      return;
    }

    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- this layout gate must hide stale coordinates before the first paint.
    setIsPositioned(false);

    const update = floating.update;
    if (update) {
      update();
    }

    const timer = window.setTimeout(() => {
      if (!cancelled) {
        setIsPositioned(true);
      }
    }, 0);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [floating.update, open, positionKey]);

  return { ...floating, isPositioned };
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
