import { cva } from "class-variance-authority"
import { cn } from "@/lib/cn"

export const primitiveFocusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lens-blue/20 focus-visible:ring-offset-2 focus-visible:ring-offset-reader-paper"

export const primitiveSurface =
  "border border-hairline bg-surface text-ink shadow-[var(--cl-shadow-2)]"

export const primitiveOverlay =
  "fixed inset-0 bg-[rgba(28,24,18,0.22)] backdrop-blur-[2px]"

export const controlVariants = cva(
  cn(
    "inline-flex items-center gap-2 rounded-[var(--cl-radius-control-md)] border border-hairline bg-reader-paper text-sm text-ink shadow-[var(--cl-shadow-1)] transition-[border-color,box-shadow,background-color,color,transform] duration-[var(--cl-duration-fast)] ease-[var(--cl-ease-standard)]",
    "hover:border-[rgba(98,101,109,0.35)]",
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
        default: "bg-reader-paper",
        panel: "bg-surface",
        quiet: "bg-[rgba(250,249,246,0.82)] shadow-none",
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
    "bg-[linear-gradient(180deg,rgba(255,255,255,0.96),rgba(251,250,246,0.98))]",
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
