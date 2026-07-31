"use client";

import type { ComponentPropsWithoutRef, ReactNode } from "react";
import { cn } from "../../../lib/cn";

interface ReaderFloatingPanelProps
  extends Omit<ComponentPropsWithoutRef<"div">, "className" | "children"> {
  children: ReactNode;
  className?: string;
  floatingRef?: (node: HTMLDivElement | null) => void;
  chrome?: "lookup" | "bare" | "selection-toolbar";
}

const READER_FLOATING_PANEL_CHROME: Record<
  NonNullable<ReaderFloatingPanelProps["chrome"]>,
  string
> = {
  lookup:
    "reader-lookup-preview z-50 rounded-xl border border-border/75 bg-popover/98 text-popover-foreground shadow-lg shadow-black/5 backdrop-blur-md supports-[backdrop-filter]:bg-popover/95",
  bare: "z-50",
  // Reader 选区工具栏专用 chrome：完全不透明背景 + 明确边框 + 前景色，
  // 不依赖 backdrop-blur 或半透明叠加。亮/暗模式均有可靠对比度。
  // 使用显式色值（而非 /98 /95 透明 token）保证工具栏在任何主题下都可读。
  "selection-toolbar":
    "reader-selection-toolbar z-50 rounded-[10px] border border-zinc-200 bg-white text-zinc-900 shadow-lg shadow-black/10 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:shadow-black/30",
};

export function ReaderFloatingPanel({
  children,
  className,
  floatingRef,
  chrome = "lookup",
  style,
  role = "dialog",
  onClick,
  onPointerDown,
  ...props
}: ReaderFloatingPanelProps) {
  return (
    <div
      ref={floatingRef}
      className={cn(READER_FLOATING_PANEL_CHROME[chrome], className)}
      role={role}
      tabIndex={-1}
      style={style}
      onClick={onClick}
      onPointerDown={onPointerDown}
      {...props}
    >
      {children}
    </div>
  );
}
