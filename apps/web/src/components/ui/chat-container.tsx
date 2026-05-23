"use client"

import * as React from "react"
import { cn } from "@/lib/cn"
import { useStickToBottom } from "use-stick-to-bottom"
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area"
import * as ScrollAreaPrimitive from "@radix-ui/react-scroll-area"

const ChatContainerContext = React.createContext<{
  contentRef: any;
} | null>(null)

export type ChatContainerRootProps = {
  children: React.ReactNode
  className?: string
} & React.HTMLAttributes<HTMLDivElement>

export type ChatContainerContentProps = {
  children: React.ReactNode
  className?: string
} & React.HTMLAttributes<HTMLDivElement>

export type ChatContainerScrollAnchorProps = {
  className?: string
  ref?: React.RefObject<HTMLDivElement>
} & React.HTMLAttributes<HTMLDivElement>

export function ChatContainerRoot({
  children,
  className,
  dir,
  ...props
}: ChatContainerRootProps) {
  const { scrollRef, contentRef } = useStickToBottom({
    resize: "smooth",
    initial: "instant",
  })

  return (
    <ChatContainerContext.Provider value={{ contentRef }}>
      <ScrollAreaPrimitive.Root
        className={cn("relative flex h-full w-full flex-col overflow-hidden", className)}
        dir={dir as "ltr" | "rtl" | undefined}
        {...props}
      >
        <ScrollAreaPrimitive.Viewport ref={scrollRef as any} className="h-full w-full rounded-[inherit] [&>div]:!block">
          {children}
        </ScrollAreaPrimitive.Viewport>
        <ScrollBar />
        <ScrollAreaPrimitive.Corner />
      </ScrollAreaPrimitive.Root>
    </ChatContainerContext.Provider>
  )
}

export function ChatContainerContent({
  children,
  className,
  ...props
}: ChatContainerContentProps) {
  const context = React.useContext(ChatContainerContext)
  return (
    <div
      ref={context?.contentRef}
      className={cn("flex w-full flex-col", className)}
      {...props}
    >
      {children}
    </div>
  )
}

export function ChatContainerScrollAnchor({
  className,
  ...props
}: ChatContainerScrollAnchorProps) {
  return (
    <div
      className={cn("h-px w-full shrink-0 scroll-mt-4", className)}
      {...props}
    />
  )
}

export default ChatContainerRoot
