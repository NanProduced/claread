import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface TopActionBarProps {
  children: ReactNode;
  className?: string;
}

export function TopActionBar({ children, className }: TopActionBarProps) {
  return <div className={cn("flex flex-wrap gap-2", className)}>{children}</div>;
}
