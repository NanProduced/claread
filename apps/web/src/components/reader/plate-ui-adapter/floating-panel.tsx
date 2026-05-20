"use client";

import type { AriaRole, CSSProperties, MouseEvent, PointerEvent, ReactNode } from "react";
import { cn } from "../../../lib/cn";

interface ReaderFloatingPanelProps {
  children: ReactNode;
  className?: string;
  floatingRef?: (node: HTMLDivElement | null) => void;
  style?: CSSProperties;
  role?: AriaRole;
  onClick?: (event: MouseEvent<HTMLDivElement>) => void;
  onPointerDown?: (event: PointerEvent<HTMLDivElement>) => void;
}

export function ReaderFloatingPanel({
  children,
  className,
  floatingRef,
  style,
  role = "dialog",
  onClick,
  onPointerDown,
}: ReaderFloatingPanelProps) {
  return (
    <div
      ref={floatingRef}
      className={cn(
        "reader-lookup-preview rounded-xl border border-border/80 bg-popover/98 text-popover-foreground shadow-md backdrop-blur-sm",
        className,
      )}
      role={role}
      style={style}
      onClick={onClick}
      onPointerDown={onPointerDown}
    >
      {children}
    </div>
  );
}
