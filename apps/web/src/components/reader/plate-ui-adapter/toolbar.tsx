"use client";

import * as React from "react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../../ui/dropdown-menu";
import {
  Toolbar,
  ToolbarButton,
  ToolbarGroup,
  ToolbarSeparator,
} from "../../ui/toolbar";
import { cn } from "../../../lib/cn";

export function ReaderToolbarRoot({
  className,
  ...props
}: React.ComponentPropsWithoutRef<typeof Toolbar>) {
  return (
    <Toolbar
      className={cn(
        "flex max-w-[min(38rem,calc(100vw-1rem))] flex-wrap items-center rounded-xl border border-border/80 bg-popover/98 p-1.5 text-popover-foreground shadow-md backdrop-blur-sm",
        className,
      )}
      {...props}
    />
  );
}

export function ReaderToolbarGroup({
  className,
  ...props
}: React.ComponentPropsWithoutRef<"div">) {
  return (
    <div
      className={cn("flex items-center gap-1", className)}
      {...props}
    />
  );
}

export function ReaderToolbarSeparator({
  className,
  ...props
}: React.ComponentPropsWithoutRef<typeof ToolbarSeparator>) {
  return (
    <ToolbarSeparator
      className={cn("mx-1 hidden h-7 shrink-0 sm:inline-flex", className)}
      {...props}
    />
  );
}

export interface ReaderToolbarButtonProps
  extends React.ComponentPropsWithoutRef<typeof ToolbarButton> {
  active?: boolean;
}

export function ReaderToolbarButton({ active = false, className, ...props }: ReaderToolbarButtonProps) {
  return (
    <ToolbarButton
      className={cn(
        "focus-ring min-h-9 min-w-9 shrink-0 rounded-lg border border-transparent px-2.5 text-foreground/72 hover:border-border/70 hover:bg-muted/65 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40",
        active && "border-border/80 bg-accent text-accent-foreground shadow-xs",
        className,
      )}
      size="default"
      {...props}
    />
  );
}

export function ReaderToolbarMenu({
  modal = false,
  ...props
}: React.ComponentPropsWithoutRef<typeof DropdownMenu>) {
  return <DropdownMenu modal={modal} {...props} />;
}

export {
  DropdownMenuContent as ReaderToolbarMenuContent,
  DropdownMenuItem as ReaderToolbarMenuItem,
  DropdownMenuLabel as ReaderToolbarMenuLabel,
  DropdownMenuRadioGroup as ReaderToolbarMenuRadioGroup,
  DropdownMenuRadioItem as ReaderToolbarMenuRadioItem,
  DropdownMenuSeparator as ReaderToolbarMenuSeparator,
  DropdownMenuTrigger as ReaderToolbarMenuTrigger,
  ToolbarGroup as ReaderToolbarPrimitiveGroup,
};
