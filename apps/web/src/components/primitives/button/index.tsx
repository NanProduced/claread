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
          "rounded-[0.82rem] border-[rgba(46,89,219,0.72)] bg-[linear-gradient(180deg,rgba(72,117,255,0.98),rgba(47,92,232,0.98))] text-white shadow-[0_12px_28px_rgba(47,92,232,0.2),inset_0_1px_0_rgba(255,255,255,0.26)] hover:-translate-y-[0.5px] hover:border-[rgba(39,80,207,0.78)] hover:bg-[linear-gradient(180deg,rgba(67,111,244,0.98),rgba(42,84,214,0.98))] hover:shadow-[0_16px_32px_rgba(47,92,232,0.24),inset_0_1px_0_rgba(255,255,255,0.22)]",
        secondary:
          "rounded-[0.82rem] border-[rgba(30,31,37,0.82)] bg-[linear-gradient(180deg,rgba(34,35,41,0.98),rgba(21,22,28,0.98))] text-white shadow-[0_10px_24px_rgba(17,17,17,0.14),inset_0_1px_0_rgba(255,255,255,0.1)] hover:-translate-y-[0.5px] hover:bg-[linear-gradient(180deg,rgba(40,42,49,0.98),rgba(24,25,30,0.98))] hover:shadow-[0_14px_28px_rgba(17,17,17,0.18)]",
        outline:
          "rounded-[0.82rem] border-hairline bg-[linear-gradient(180deg,rgba(255,255,255,0.96),rgba(249,247,241,0.98))] text-ink shadow-[0_8px_20px_rgba(17,17,17,0.04),inset_0_1px_0_rgba(255,255,255,0.72)] hover:border-[rgba(113,116,124,0.34)] hover:bg-[linear-gradient(180deg,rgba(255,255,255,0.98),rgba(247,244,236,1))] hover:text-ink-soft",
        subtle:
          "rounded-[0.82rem] border-transparent bg-reader-paper text-ink shadow-none hover:bg-surface-warm",
        quiet:
          "rounded-[0.82rem] border-hairline bg-[linear-gradient(180deg,rgba(255,255,255,0.82),rgba(248,245,238,0.92))] text-ink-soft shadow-[inset_0_1px_0_rgba(255,255,255,0.7)] hover:border-[rgba(113,116,124,0.28)] hover:text-ink hover:shadow-[0_8px_18px_rgba(17,17,17,0.04)]",
        danger:
          "rounded-[0.82rem] border-[rgba(190,18,60,0.18)] bg-[linear-gradient(180deg,rgba(255,255,255,0.96),rgba(255,247,249,0.98))] text-error-red shadow-[0_8px_18px_rgba(190,18,60,0.05)] hover:bg-[linear-gradient(180deg,rgba(255,249,251,1),rgba(255,241,245,1))]",
        ghost:
          "rounded-[0.82rem] border-transparent bg-transparent text-muted shadow-none hover:bg-reader-paper/92 hover:text-ink",
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
  { asChild = false, className, type = "button", variant, size, density, ...props },
  ref,
) {
  const Comp = asChild ? Slot : "button";
  return (
    <Comp
      ref={ref}
      type={asChild ? undefined : type}
      className={cn(buttonVariants({ variant, size, density }), className)}
      {...props}
    />
  );
});

export { Button, buttonVariants };
