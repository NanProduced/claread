"use client"

import * as React from "react"
import * as SwitchPrimitive from "@radix-ui/react-switch"
import { cn } from "@/lib/cn"
import { primitiveFocusRing } from "../shared"

const Switch = React.forwardRef<
  React.ElementRef<typeof SwitchPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof SwitchPrimitive.Root>
>(function Switch({ className, ...props }, ref) {
  return (
    <SwitchPrimitive.Root
      ref={ref}
      className={cn(
        "peer inline-flex h-6 w-11 shrink-0 items-center rounded-full border border-hairline bg-[rgba(232,228,218,0.9)] p-0.5 shadow-[var(--cl-shadow-1)] transition-colors data-[state=checked]:border-lens-blue/20 data-[state=checked]:bg-lens-blue-soft data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50",
        primitiveFocusRing,
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb className="pointer-events-none block size-5 rounded-full bg-surface shadow-[0_2px_10px_rgba(17,17,17,0.15)] transition-transform data-[state=checked]:translate-x-5" />
    </SwitchPrimitive.Root>
  )
})

export { Switch }
