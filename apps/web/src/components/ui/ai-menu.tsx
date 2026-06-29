"use client";

import * as React from "react";
import * as PopoverPrimitive from "@radix-ui/react-popover";

import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { cn } from "@/lib/cn";

export function AIMenu({
  open,
  onOpenChange,
  children,
}: React.ComponentProps<typeof PopoverPrimitive.Root>) {
  return (
    <PopoverPrimitive.Root open={open} onOpenChange={onOpenChange}>
      {children}
    </PopoverPrimitive.Root>
  );
}

export function AIMenuAnchor({
  children,
}: {
  children: React.ReactNode;
}) {
  return <PopoverPrimitive.Anchor asChild>{children}</PopoverPrimitive.Anchor>;
}

export function AIMenuContent({
  className,
  children,
  ...props
}: React.ComponentPropsWithoutRef<typeof PopoverPrimitive.Content>) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Content
        align="start"
        side="bottom"
        sideOffset={8}
        collisionPadding={12}
        data-plate-focus="true"
        className={cn(
          "z-50 w-[min(44rem,calc(100vw-2rem))] overflow-hidden rounded-lg border bg-popover text-popover-foreground shadow-md outline-hidden data-[side=bottom]:slide-in-from-top-2 data-[side=top]:slide-in-from-bottom-2 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
          className,
        )}
        onOpenAutoFocus={(event) => event.preventDefault()}
        {...props}
      >
        {children}
      </PopoverPrimitive.Content>
    </PopoverPrimitive.Portal>
  );
}

export function AIMenuCommand({
  className,
  ...props
}: React.ComponentPropsWithoutRef<typeof Command>) {
  return (
    <Command
      className={cn(
        "rounded-lg bg-popover text-popover-foreground [&_[data-slot=command-input-wrapper]]:h-11 [&_[data-slot=command-input-wrapper]]:border-border/70 [&_[data-slot=command-input-wrapper]]:px-3 [&_[data-slot=command-input]]:h-11 [&_[data-slot=command-input]]:text-sm [&_[data-slot=command-item]]:rounded-md [&_[data-slot=command-item]]:px-3 [&_[data-slot=command-item]]:py-2.5",
        className,
      )}
      {...props}
    />
  );
}

export function AIMenuInput(props: React.ComponentPropsWithoutRef<typeof CommandInput>) {
  return <CommandInput {...props} />;
}

export function AIMenuList(props: React.ComponentPropsWithoutRef<typeof CommandList>) {
  return <CommandList {...props} />;
}

export function AIMenuEmpty(props: React.ComponentPropsWithoutRef<typeof CommandEmpty>) {
  return <CommandEmpty {...props} />;
}

export function AIMenuItem({
  className,
  ...props
}: React.ComponentPropsWithoutRef<typeof CommandItem>) {
  return (
    <CommandItem
      className={cn(
        "cursor-pointer gap-3 aria-selected:bg-muted data-[selected=true]:bg-muted",
        className,
      )}
      {...props}
    />
  );
}
