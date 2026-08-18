"use client";

import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/cn";
import { primitiveFocusRing } from "../shared";

const buttonVariants = cva(
  cn(
    // Base shape + motion. Surface and recipe classes stay stable so the
    // existing Paper visual recipe (gradients, shadows, hover lift) does
    // not regress; only static text/border colors are routed through the
    // semantic token layer.
    "inline-flex shrink-0 cursor-pointer items-center justify-center gap-2 whitespace-nowrap border text-sm font-semibold tracking-[0.01em] transition-[background-color,border-color,color,box-shadow,transform,opacity] duration-[var(--cl-duration-fast)] ease-[var(--cl-ease-standard)] disabled:cursor-not-allowed disabled:pointer-events-none disabled:opacity-50",
    primitiveFocusRing,
  ),
  {
    variants: {
      variant: {
        primary:
          // Gradient recipe is the visual identity of the CTA. action-primary
          // provides the base hue but cannot express the gradient by itself,
          // so we keep --app-primary-gradient as background-image.
          "rounded-[0.82rem] border-[var(--app-primary-border)] [background-image:var(--app-primary-gradient)] text-action-primary-foreground shadow-[var(--app-primary-shadow)] hover:-translate-y-[0.5px] hover:border-[var(--app-primary-border-hover)] hover:[background-image:var(--app-primary-gradient-hover)] hover:shadow-[var(--app-primary-shadow-hover)]",
        "primary-ink":
          // Solid ink surface, distinct from the gradient CTA. Kept as a
          // raw recipe because it has no semantic equivalent; if the design
          // system grows an action-primary-solid token, swap here.
          "rounded-full bg-ink text-[#FFFFFF] shadow-[0_4px_18px_rgba(17,17,17,0.08)] hover:-translate-y-[1px] hover:bg-ink-soft hover:shadow-[0_6px_24px_rgba(17,17,17,0.15)]",
        secondary:
          // Neutral chip routed through the --action-secondary token pair
          // (light: paper surface + ink text; dark: raised surface + soft
          // white text). The legacy near-black gradient shell is removed:
          // it paired a dark background with the light-theme ink foreground
          // (#151515 on #161616), rendering label text invisible in light
          // theme, and it read as a primary action, which the design system
          // forbids for secondary behavior.
          "rounded-[0.82rem] border-border-subtle bg-action-secondary text-action-secondary-foreground shadow-none hover:-translate-y-[0.5px] hover:border-border-strong hover:shadow-[var(--app-panel-shadow-quiet)]",
        outline:
          "app-control-surface rounded-[0.82rem] border-border-subtle text-text-primary hover:border-border-strong hover:text-text-secondary",
        subtle:
          "rounded-[0.82rem] border-transparent bg-secondary text-text-primary shadow-none hover:bg-surface-warm",
        quiet:
          "rounded-[0.82rem] border-border-subtle bg-[var(--app-control-quiet)] text-text-secondary shadow-[inset_0_1px_0_rgba(255,255,255,0.08)] hover:border-border-strong hover:text-text-primary hover:shadow-[var(--app-panel-shadow-quiet)]",
        danger:
          // Danger gradient recipe is the visual identity of destructive
          // intent. feedback-error provides the base hue but cannot express
          // the gradient, so we keep --app-danger-gradient as background.
          "rounded-[0.82rem] border-[var(--app-danger-border)] [background-image:var(--app-danger-gradient)] text-feedback-error shadow-[0_8px_18px_rgba(190,18,60,0.08)] hover:[background-image:var(--app-danger-gradient-hover)]",
        ghost:
          "rounded-[0.82rem] border-transparent bg-transparent text-text-secondary shadow-none hover:bg-[var(--app-control-quiet)] hover:text-text-primary",
      },
      size: {
        sm: "min-h-9 px-3.5 text-[0.82rem]",
        md: "min-h-10 px-4.5 text-[0.92rem]",
        lg: "min-h-[2.9rem] px-5 text-[0.95rem]",
      },
      density: {
        default: "",
        compact: "min-h-8 px-3 text-xs",
      },
    },
    defaultVariants: {
      variant: "outline",
      size: "md",
      density: "default",
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { asChild = false, className, type = "button", variant, size, density, style, ...props },
  ref,
) {
  const Comp = asChild ? Slot : "button";

  const mergedStyle = variant === "primary-ink"
    ? { color: "var(--surface)", ...style }
    : style;

  return (
    <Comp
      ref={ref}
      type={asChild ? undefined : type}
      className={cn(buttonVariants({ variant, size, density }), className)}
      style={mergedStyle}
      {...props}
    />
  );
});

export { Button, buttonVariants };