"use client"

import * as React from "react"
import { Command as CommandPrimitive } from "cmdk"
import { Search } from "lucide-react"
import { cn } from "@/lib/cn"
import { Dialog, DialogContent, DialogTitle } from "../dialog"

const CommandMenu = React.forwardRef<
  React.ElementRef<typeof CommandPrimitive>,
  React.ComponentPropsWithoutRef<typeof CommandPrimitive>
>(function CommandMenu({ className, ...props }, ref) {
  return (
    <CommandPrimitive
      ref={ref}
      className={cn("flex h-full w-full flex-col overflow-hidden rounded-[inherit] bg-transparent text-ink", className)}
      {...props}
    />
  )
})

interface CommandMenuDialogProps {
  open?: boolean
  defaultOpen?: boolean
  onOpenChange?: (open: boolean) => void
  modal?: boolean
  title?: string
  children?: React.ComponentPropsWithoutRef<typeof CommandPrimitive>["children"]
}

function CommandMenuDialog({
  title = "命令面板",
  children,
  ...props
}: CommandMenuDialogProps) {
  return (
    <Dialog {...props}>
      <DialogContent className="overflow-hidden p-0">
        <DialogTitle className="sr-only">{title}</DialogTitle>
        <CommandMenu>{children}</CommandMenu>
      </DialogContent>
    </Dialog>
  )
}

const CommandMenuInput = React.forwardRef<
  React.ElementRef<typeof CommandPrimitive.Input>,
  React.ComponentPropsWithoutRef<typeof CommandPrimitive.Input>
>(function CommandMenuInput({ className, ...props }, ref) {
  return (
    <div className="flex items-center gap-3 border-b border-hairline px-4 py-3">
      <Search className="size-4 text-muted" />
      <CommandPrimitive.Input
        ref={ref}
        className={cn("flex h-11 w-full bg-transparent text-sm text-ink outline-none placeholder:text-muted", className)}
        {...props}
      />
    </div>
  )
})

const CommandMenuList = React.forwardRef<
  React.ElementRef<typeof CommandPrimitive.List>,
  React.ComponentPropsWithoutRef<typeof CommandPrimitive.List>
>(function CommandMenuList({ className, ...props }, ref) {
  return <CommandPrimitive.List ref={ref} className={cn("max-h-80 overflow-y-auto p-2", className)} {...props} />
})

const CommandMenuEmpty = React.forwardRef<
  React.ElementRef<typeof CommandPrimitive.Empty>,
  React.ComponentPropsWithoutRef<typeof CommandPrimitive.Empty>
>(function CommandMenuEmpty({ className, ...props }, ref) {
  return <CommandPrimitive.Empty ref={ref} className={cn("px-4 py-8 text-sm text-muted", className)} {...props} />
})

const CommandMenuGroup = React.forwardRef<
  React.ElementRef<typeof CommandPrimitive.Group>,
  React.ComponentPropsWithoutRef<typeof CommandPrimitive.Group>
>(function CommandMenuGroup({ className, ...props }, ref) {
  return (
    <CommandPrimitive.Group
      ref={ref}
      className={cn("[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:pb-2 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-[0.14em] [&_[cmdk-group-heading]]:text-muted", className)}
      {...props}
    />
  )
})

const CommandMenuSeparator = React.forwardRef<
  React.ElementRef<typeof CommandPrimitive.Separator>,
  React.ComponentPropsWithoutRef<typeof CommandPrimitive.Separator>
>(function CommandMenuSeparator({ className, ...props }, ref) {
  return <CommandPrimitive.Separator ref={ref} className={cn("my-1 h-px bg-hairline", className)} {...props} />
})

const CommandMenuItem = React.forwardRef<
  React.ElementRef<typeof CommandPrimitive.Item>,
  React.ComponentPropsWithoutRef<typeof CommandPrimitive.Item>
>(function CommandMenuItem({ className, ...props }, ref) {
  return (
    <CommandPrimitive.Item
      ref={ref}
      className={cn(
        "flex min-h-10 cursor-default items-center gap-2 rounded-[var(--cl-radius-control-sm)] px-3 text-sm text-ink transition-colors data-[disabled=true]:pointer-events-none data-[disabled=true]:opacity-45 data-[selected=true]:bg-lens-blue-soft",
        className,
      )}
      {...props}
    />
  )
})

function CommandMenuShortcut({ className, ...props }: React.HTMLAttributes<HTMLSpanElement>) {
  return <span className={cn("ml-auto text-xs tracking-[0.08em] text-subtle", className)} {...props} />
}

export {
  CommandMenu,
  CommandMenuDialog,
  CommandMenuInput,
  CommandMenuList,
  CommandMenuEmpty,
  CommandMenuGroup,
  CommandMenuItem,
  CommandMenuSeparator,
  CommandMenuShortcut,
}
