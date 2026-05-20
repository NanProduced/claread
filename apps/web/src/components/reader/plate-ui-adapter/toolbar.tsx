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
  ToolbarSeparator,
  ToolbarSplitButton,
  ToolbarSplitButtonPrimary,
  ToolbarSplitButtonSecondary,
} from "../../ui/toolbar";
import { cn } from "../../../lib/cn";

export function ReaderToolbarRoot({
  className,
  ...props
}: React.ComponentPropsWithoutRef<typeof Toolbar>) {
  return (
    <Toolbar
      className={cn(
        "flex max-w-[min(38rem,calc(100vw-1rem))] flex-wrap items-center gap-1 rounded-xl border border-border/65 bg-background/94 p-1.5 text-foreground shadow-sm backdrop-blur-sm",
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
  return <div className={cn("flex items-center gap-1", className)} {...props} />;
}

export function ReaderToolbarSeparator({
  className,
  ...props
}: React.ComponentPropsWithoutRef<typeof ToolbarSeparator>) {
  return (
    <ToolbarSeparator
      className={cn("mx-0.5 hidden h-6 shrink-0 opacity-70 sm:inline-flex", className)}
      {...props}
    />
  );
}

const readerToolbarButtonBaseClassName =
  "focus-ring shrink-0 rounded-lg border border-transparent text-foreground/72 transition-[background-color,border-color,color,box-shadow] hover:border-border/55 hover:bg-muted/45 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40";

export interface ReaderToolbarButtonProps
  extends React.ComponentPropsWithoutRef<typeof ToolbarButton> {
  active?: boolean;
}

export function ReaderToolbarButton({
  active = false,
  className,
  ...props
}: ReaderToolbarButtonProps) {
  return (
    <ToolbarButton
      className={cn(
        readerToolbarButtonBaseClassName,
        "min-h-9 min-w-9 px-2.5",
        active && "border-border/65 bg-background text-foreground shadow-sm",
        className,
      )}
      size="default"
      {...props}
    />
  );
}

export function ReaderToolbarActionButton({
  className,
  ...props
}: ReaderToolbarButtonProps) {
  return <ReaderToolbarButton className={cn("gap-2 px-3 font-medium", className)} {...props} />;
}

export function ReaderToolbarIconButton({
  className,
  ...props
}: ReaderToolbarButtonProps) {
  return <ReaderToolbarButton className={cn("h-10 min-w-10 px-2.5", className)} {...props} />;
}

export interface ReaderToolbarSplitActionProps {
  label: string;
  icon?: React.ReactNode;
  disabled?: boolean;
  active?: boolean;
  onPrimaryClick?: () => void;
  children: React.ReactNode;
}

export function ReaderToolbarSplitAction({
  active = false,
  children,
  disabled = false,
  icon,
  label,
  onPrimaryClick,
}: ReaderToolbarSplitActionProps) {
  return (
    <ReaderToolbarMenu>
      <ToolbarSplitButton
        aria-label={label}
        className={cn(
          "rounded-lg border border-transparent",
          disabled && "pointer-events-none opacity-40",
          active && "border-border/65 bg-background shadow-sm",
        )}
        data-pressed={active ? "true" : undefined}
      >
        <ToolbarSplitButtonPrimary
          className={cn(
            readerToolbarButtonBaseClassName,
            "min-h-10 min-w-10 rounded-r-none border-r-0 px-2.5",
            active && "border-border/65 bg-background text-foreground",
          )}
          disabled={disabled}
          onClick={(event) => {
            event.stopPropagation();
            onPrimaryClick?.();
          }}
          title={label}
        >
          {icon}
          <span className="sr-only">{label}</span>
        </ToolbarSplitButtonPrimary>
        <DropdownMenuTrigger asChild disabled={disabled}>
          <ToolbarSplitButtonSecondary
            aria-label={`${label}更多选项`}
            className={cn(
              readerToolbarButtonBaseClassName,
              "h-10 w-8 rounded-l-none border-l border-border/45 px-0",
              active && "border-border/65 bg-background text-foreground",
            )}
          />
        </DropdownMenuTrigger>
      </ToolbarSplitButton>
      <DropdownMenuContent align="start" className="w-44">
        {children}
      </DropdownMenuContent>
    </ReaderToolbarMenu>
  );
}

export function ReaderToolbarPopoverCard({
  children,
  className,
  ...props
}: React.ComponentPropsWithoutRef<"div">) {
  return (
    <div
      className={cn(
        "mt-2 w-[min(20rem,calc(100vw-1rem))] rounded-xl border border-border/70 bg-background/95 p-3 text-foreground shadow-sm",
        className,
      )}
      {...props}
    >
      {children}
    </div>
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
};
