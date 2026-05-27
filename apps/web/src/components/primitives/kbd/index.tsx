"use client"

import * as React from "react"
import { cn } from "@/lib/cn"

const Kbd = React.forwardRef<HTMLElement, React.HTMLAttributes<HTMLElement>>(function Kbd(
  { className, ...props },
  ref,
) {
  return (
    <kbd
      ref={ref}
      className={cn(
        "inline-flex min-h-5 min-w-5 items-center justify-center rounded-[6px] border border-hairline/90 bg-[color-mix(in_srgb,var(--surface)_78%,transparent)] px-1.5 font-sans text-[0.7rem] font-semibold leading-none text-ink-soft shadow-[inset_0_1px_0_rgba(255,255,255,0.65)]",
        className,
      )}
      {...props}
    />
  )
})

export { Kbd }
