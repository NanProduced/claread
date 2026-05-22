import { cva } from "class-variance-authority"
import { cn } from "@/lib/cn"

export const primitiveFocusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lens-blue/20 focus-visible:ring-offset-2 focus-visible:ring-offset-background"

export const primitiveSurface =
  "app-panel-surface border border-hairline text-ink"

export const primitiveOverlay =
  "app-overlay fixed inset-0 backdrop-blur-[2px]"

export const controlVariants = cva(
  cn(
    "inline-flex items-center gap-2 rounded-[var(--cl-radius-control-md)] border border-hairline text-sm text-ink transition-[border-color,box-shadow,background-color,color,transform] duration-[var(--cl-duration-fast)] ease-[var(--cl-ease-standard)]",
    "app-control-surface hover:border-[var(--app-control-border-hover)]",
    primitiveFocusRing,
  ),
  {
    variants: {
      size: {
        sm: "min-h-9 px-3",
        md: "min-h-10 px-3.5",
        lg: "min-h-11 px-4",
      },
      tone: {
        default: "",
        panel: "bg-surface",
        quiet: "bg-[var(--app-control-quiet)] shadow-none",
      },
    },
    defaultVariants: {
      size: "md",
      tone: "default",
    },
  },
)

export const menuItemVariants = cva(
  cn(
    "relative flex cursor-default select-none items-center gap-2 rounded-[var(--cl-radius-control-sm)] px-3 py-2 text-sm text-ink transition-colors",
    "data-[disabled]:pointer-events-none data-[disabled]:opacity-45",
    "data-[highlighted]:bg-lens-blue-soft data-[highlighted]:text-ink",
    primitiveFocusRing,
  ),
)

export const panelSurface = cva(
  cn(
    "rounded-[var(--cl-radius-surface-md)]",
    primitiveSurface,
  ),
  {
    variants: {
      padding: {
        sm: "p-4",
        md: "p-5",
        lg: "p-6",
        none: "p-0",
      },
    },
    defaultVariants: {
      padding: "md",
    },
  },
)

export const primitiveLabelClass = "text-sm font-semibold text-ink"
export const primitiveDescriptionClass = "text-sm leading-6 text-muted"
