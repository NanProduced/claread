import { cva } from "class-variance-authority";
import { primitiveFocusRing } from "@/components/primitives/shared";
import { cn } from "@/lib/cn";

export const readerTransitionFast =
  "transition-[background-color,border-color,color,box-shadow,transform,opacity] duration-[var(--cl-duration-fast)] ease-[var(--cl-ease-standard)]";

export const readerTransitionStandard =
  "transition-[background-color,border-color,color,box-shadow,transform,opacity] duration-[180ms] ease-[var(--cl-ease-standard)]";

export const readerInlineFocusRing =
  "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-lens-blue/30 focus-visible:ring-offset-2 focus-visible:ring-offset-background";

export const readerCommandControl = cn(
  "inline-flex items-center justify-center gap-2 border border-transparent text-sm font-semibold tracking-[0.01em]",
  primitiveFocusRing,
  readerTransitionFast,
  "disabled:pointer-events-none disabled:opacity-50",
  "hover:bg-ink/[0.02] hover:text-ink-soft active:bg-ink/[0.04]",
);

export const readerFloatingAction = cn(
  "inline-flex items-center justify-center border border-transparent text-foreground/75",
  primitiveFocusRing,
  readerTransitionFast,
  "disabled:pointer-events-none disabled:opacity-40",
  "hover:bg-muted/80 hover:text-foreground active:scale-[0.96]",
);

export const readerPanelItem = cn(
  "items-center gap-2.5 border border-transparent text-sm font-medium text-foreground",
  primitiveFocusRing,
  readerTransitionFast,
  "disabled:pointer-events-none disabled:opacity-50",
  "hover:bg-muted/80 hover:text-foreground active:bg-muted/60",
);

export const readerSegmentedOption = cva(
  cn(
    "inline-flex items-center justify-center border border-transparent bg-transparent font-semibold select-none",
    primitiveFocusRing,
    readerTransitionFast,
    "disabled:pointer-events-none disabled:opacity-50",
    "hover:bg-ink/[0.01] hover:border-muted hover:text-ink active:bg-ink/[0.04]",
  ),
  {
    variants: {
      selected: {
        true: "bg-background text-vocab-amber shadow-[0_2px_6px_rgba(0,0,0,0.04)]",
        false: "text-muted",
      },
    },
    defaultVariants: {
      selected: false,
    },
  },
);
