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
          "rounded-[0.82rem] border-hairline bg-[linear-gradient(180deg,rgba(255,255,255,0.96),rgba(249,247,241,0.98))] text-ink-soft shadow-[0_8px_18px_rgba(17,17,17,0.04),inset_0_1px_0_rgba(255,255,255,0.72)] hover:-translate-y-[0.5px] hover:border-[rgba(113,116,124,0.32)] hover:text-ink",
        quiet:
          "rounded-[0.82rem] border-transparent bg-transparent text-muted hover:bg-reader-paper/92 hover:text-ink",
        danger:
          "rounded-[0.82rem] border-[rgba(190,18,60,0.18)] bg-[linear-gradient(180deg,rgba(255,255,255,0.96),rgba(255,247,249,0.98))] text-error-red hover:bg-[linear-gradient(180deg,rgba(255,249,251,1),rgba(255,241,245,1))]",
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
