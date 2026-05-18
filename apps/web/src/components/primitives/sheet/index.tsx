"use client"

import * as React from "react"
import * as DialogPrimitive from "@radix-ui/react-dialog"
import { X } from "lucide-react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/cn"
import { primitiveDescriptionClass, primitiveFocusRing, primitiveOverlay, primitiveSurface } from "../shared"

const sheetContentVariants = cva(
  cn(
    "fixed z-50 flex flex-col gap-4 bg-[linear-gradient(180deg,rgba(255,255,255,0.98),rgba(251,250,246,0.98))] p-6 text-ink shadow-[var(--cl-shadow-3)] transition-transform duration-[var(--cl-duration-base)] ease-[var(--cl-ease-standard)]",
    primitiveSurface,
  ),
  {
    variants: {
      side: {
        right: "inset-y-0 right-0 h-full w-[min(28rem,100vw)] border-l",
        left: "inset-y-0 left-0 h-full w-[min(28rem,100vw)] border-r",
      },
    },
    defaultVariants: {
      side: "right",
    },
  },
)

function Sheet(props: React.ComponentProps<typeof DialogPrimitive.Root>) {
  return <DialogPrimitive.Root {...props} />
}

function SheetTrigger(props: React.ComponentProps<typeof DialogPrimitive.Trigger>) {
  return <DialogPrimitive.Trigger {...props} />
}

function SheetClose(props: React.ComponentProps<typeof DialogPrimitive.Close>) {
  return <DialogPrimitive.Close {...props} />
}

const SheetContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content> & VariantProps<typeof sheetContentVariants>
>(function SheetContent({ className, side, children, ...props }, ref) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className={primitiveOverlay} />
      <DialogPrimitive.Content ref={ref} className={cn(sheetContentVariants({ side }), className)} {...props}>
        {children}
        <DialogPrimitive.Close
          className={cn(
            "absolute right-4 top-4 inline-flex size-9 items-center justify-center rounded-[var(--cl-radius-control-sm)] border border-hairline bg-reader-paper text-muted transition-colors hover:text-ink",
            primitiveFocusRing,
          )}
          aria-label="关闭侧边面板"
        >
          <X className="size-4" />
        </DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  )
})

function SheetHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("grid gap-2", className)} {...props} />
}

function SheetFooter({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("mt-auto flex flex-col gap-3", className)} {...props} />
}

const SheetTitle = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(function SheetTitle({ className, ...props }, ref) {
  return <DialogPrimitive.Title ref={ref} className={cn("font-headline text-[1.4rem] font-semibold", className)} {...props} />
})

const SheetDescription = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(function SheetDescription({ className, ...props }, ref) {
  return <DialogPrimitive.Description ref={ref} className={cn(primitiveDescriptionClass, className)} {...props} />
})

export { Sheet, SheetTrigger, SheetClose, SheetContent, SheetHeader, SheetFooter, SheetTitle, SheetDescription }
