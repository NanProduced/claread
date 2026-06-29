"use client";

import type { ComponentPropsWithoutRef, ReactNode } from "react";
import { cn } from "../../../lib/cn";

interface ReaderFloatingPanelProps
  extends Omit<ComponentPropsWithoutRef<"div">, "className" | "children"> {
  children: ReactNode;
  className?: string;
  floatingRef?: (node: HTMLDivElement | null) => void;
  chrome?: "lookup" | "bare";
}

const READER_FLOATING_PANEL_CHROME: Record<
  NonNullable<ReaderFloatingPanelProps["chrome"]>,
  string
> = {
  lookup:
    "reader-lookup-preview z-50 rounded-xl border border-border/75 bg-popover/98 text-popover-foreground shadow-lg shadow-black/5 backdrop-blur-md supports-[backdrop-filter]:bg-popover/95 animate-in fade-in zoom-in-95 duration-200 ease-out",
  bare: "z-50",
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
