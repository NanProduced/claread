"use client"

import * as React from "react"
import { Command as CommandPrimitive } from "cmdk"
import { Search } from "lucide-react"
import { cn } from "@/lib/cn"
import { Dialog, DialogContent, DialogTitle } from "../dialog"
import { ScrollArea } from "../scroll-area"

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
      <DialogContent
        size="md"
        showCloseButton={false}
        className="app-panel-surface overflow-hidden border-hairline/90 p-0 shadow-[0_24px_80px_rgba(28,24,18,0.16)]"
      >
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
    <div className="px-4 pt-4 pb-2 shrink-0">
      <div className="flex items-center gap-2.5 rounded-lg border border-hairline bg-[color-mix(in_srgb,var(--surface)_40%,transparent)] px-3 py-2.5 shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] transition-colors focus-within:border-lens-blue/30 focus-within:ring-2 focus-within:ring-lens-blue/10">
        <Search className="size-4 text-muted-foreground/75 shrink-0" />
        <CommandPrimitive.Input
          ref={ref}
          className={cn(
            "flex h-5 w-full bg-transparent text-[0.88rem] text-ink outline-none placeholder:text-muted-foreground/60",
            className,
          )}
          {...props}
        />
      </div>
    </div>
  )
})

const CommandMenuList = React.forwardRef<
  React.ElementRef<typeof CommandPrimitive.List>,
  React.ComponentPropsWithoutRef<typeof CommandPrimitive.List>
>(function CommandMenuList({ className, ...props }, ref) {
  return (
    <ScrollArea className="max-h-[25rem]">
      <CommandPrimitive.List
        ref={ref}
        className={cn("p-2.5", className)}
        {...props}
      />
    </ScrollArea>
  )
})

const CommandMenuEmpty = React.forwardRef<
  React.ElementRef<typeof CommandPrimitive.Empty>,
  React.ComponentPropsWithoutRef<typeof CommandPrimitive.Empty>
>(function CommandMenuEmpty({ className, ...props }, ref) {
  return (
    <CommandPrimitive.Empty
      ref={ref}
      className={cn("px-5 py-10 text-sm text-muted-foreground", className)}
      {...props}
    />
  )
})

const CommandMenuGroup = React.forwardRef<
  React.ElementRef<typeof CommandPrimitive.Group>,
  React.ComponentPropsWithoutRef<typeof CommandPrimitive.Group>
>(function CommandMenuGroup({ className, ...props }, ref) {
  return (
    <CommandPrimitive.Group
      ref={ref}
      className={cn(
        "[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:pb-1.5 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:text-[0.68rem] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:tracking-[0.05em] [&_[cmdk-group-heading]]:text-muted-foreground/80",
        className,
      )}
      {...props}
    />
  )
})

const CommandMenuSeparator = React.forwardRef<
  React.ElementRef<typeof CommandPrimitive.Separator>,
  React.ComponentPropsWithoutRef<typeof CommandPrimitive.Separator>
>(function CommandMenuSeparator({ className, ...props }, ref) {
  return <CommandPrimitive.Separator ref={ref} className={cn("my-2 h-px bg-hairline/90", className)} {...props} />
})

const CommandMenuItem = React.forwardRef<
  React.ElementRef<typeof CommandPrimitive.Item>,
  React.ComponentPropsWithoutRef<typeof CommandPrimitive.Item>
>(function CommandMenuItem({ className, ...props }, ref) {
  return (
    <CommandPrimitive.Item
      ref={ref}
      className={cn(
        "flex min-h-10 cursor-default items-center gap-2.5 rounded-lg px-3 py-2 text-[0.88rem] text-ink transition-colors data-[disabled=true]:pointer-events-none data-[disabled=true]:opacity-45 data-[selected=true]:bg-lens-blue-soft/50 dark:data-[selected=true]:bg-zinc-800/80 data-[selected=true]:text-ink data-[selected=true]:shadow-[inset_0_0_0_1px_rgba(21,92,255,0.06)]",
        className,
      )}
      {...props}
    />
  )
})

function CommandMenuShortcut({ className, ...props }: React.HTMLAttributes<HTMLSpanElement>) {
  return <span className={cn("ml-auto text-[0.72rem] font-medium tracking-[0.04em] text-subtle", className)} {...props} />
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
