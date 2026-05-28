"use client";

import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/cn";
import { primitiveFocusRing } from "../shared";

const buttonVariants = cva(
  cn(
    "inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap border text-sm font-semibold tracking-[0.01em] transition-[background-color,border-color,color,box-shadow,transform,opacity] duration-[var(--cl-duration-fast)] ease-[var(--cl-ease-standard)] disabled:pointer-events-none disabled:opacity-50",
    primitiveFocusRing,
  ),
  {
    variants: {
      variant: {
        primary:
          "rounded-[0.82rem] border-[var(--app-primary-border)] [background-image:var(--app-primary-gradient)] text-primary-foreground shadow-[var(--app-primary-shadow)] hover:-translate-y-[0.5px] hover:border-[var(--app-primary-border-hover)] hover:[background-image:var(--app-primary-gradient-hover)] hover:shadow-[var(--app-primary-shadow-hover)]",
        "primary-ink":
          "rounded-full bg-ink text-[#FFFFFF] shadow-[0_4px_18px_rgba(17,17,17,0.08)] hover:-translate-y-[1px] hover:bg-ink-soft hover:shadow-[0_6px_24px_rgba(17,17,17,0.15)]",
        secondary:
          "rounded-[0.82rem] border-[var(--app-secondary-border)] [background-image:var(--app-secondary-gradient)] text-white shadow-[var(--app-secondary-shadow)] hover:-translate-y-[0.5px] hover:[background-image:var(--app-secondary-gradient-hover)] hover:shadow-[var(--app-secondary-shadow-hover)] dark:text-foreground",
        outline:
          "app-control-surface rounded-[0.82rem] border-hairline text-ink hover:border-[var(--app-control-border-hover)] hover:text-ink-soft",
        subtle:
          "rounded-[0.82rem] border-transparent bg-secondary text-ink shadow-none hover:bg-surface-warm",
        quiet:
          "rounded-[0.82rem] border-hairline bg-[var(--app-control-quiet)] text-ink-soft shadow-[inset_0_1px_0_rgba(255,255,255,0.08)] hover:border-[var(--app-control-border-hover)] hover:text-ink hover:shadow-[var(--app-panel-shadow-quiet)]",
        danger:
          "rounded-[0.82rem] border-[var(--app-danger-border)] [background-image:var(--app-danger-gradient)] text-error-red shadow-[0_8px_18px_rgba(190,18,60,0.08)] hover:[background-image:var(--app-danger-gradient-hover)]",
        ghost:
          "rounded-[0.82rem] border-transparent bg-transparent text-muted shadow-none hover:bg-[var(--app-control-quiet)] hover:text-ink",
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
