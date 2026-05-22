"use client";

import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/cn";
import { primitiveFocusRing } from "../shared";

const iconButtonVariants = cva(
  cn(
    "inline-flex shrink-0 items-center justify-center border transition-[background-color,border-color,color,box-shadow,transform,opacity] duration-[var(--cl-duration-fast)] ease-[var(--cl-ease-standard)] disabled:pointer-events-none disabled:opacity-50",
    primitiveFocusRing,
  ),
  {
    variants: {
      variant: {
        outline:
          "app-control-surface rounded-[0.82rem] border-hairline text-ink-soft hover:-translate-y-[0.5px] hover:border-[var(--app-control-border-hover)] hover:text-ink",
        quiet:
          "rounded-[0.82rem] border-transparent bg-transparent text-muted hover:bg-[var(--app-control-quiet)] hover:text-ink",
        danger:
          "rounded-[0.82rem] border-[var(--app-danger-border)] [background-image:var(--app-danger-gradient)] text-error-red hover:[background-image:var(--app-danger-gradient-hover)]",
      },
      size: {
        sm: "h-8 w-8",
        md: "h-9 w-9",
        lg: "h-10 w-10",
      },
    },
    defaultVariants: {
      variant: "outline",
      size: "md",
    },
  },
);

export interface IconButtonProps
  extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "aria-label">,
    VariantProps<typeof iconButtonVariants> {
  "aria-label": string;
  asChild?: boolean;
}

const IconButton = React.forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { asChild = false, className, type = "button", variant, size, ...props },
  ref,
) {
  const Comp = asChild ? Slot : "button";
  return <Comp ref={ref} type={asChild ? undefined : type} className={cn(iconButtonVariants({ variant, size }), className)} {...props} />;
});

export { IconButton, iconButtonVariants };
