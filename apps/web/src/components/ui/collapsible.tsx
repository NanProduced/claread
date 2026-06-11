"use client"

import * as React from "react"
import { Collapsible as CollapsiblePrimitive } from "radix-ui"

type CollapsibleProps = React.HTMLAttributes<HTMLDivElement> & {
  asChild?: boolean
  defaultOpen?: boolean
  disabled?: boolean
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

function Collapsible({
  ...props
}: CollapsibleProps) {
  return <CollapsiblePrimitive.Root data-slot="collapsible" {...(props as any)} />
}

type CollapsibleTriggerProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  asChild?: boolean
}

function CollapsibleTrigger({
  ...props
}: CollapsibleTriggerProps) {
  return (
    <CollapsiblePrimitive.CollapsibleTrigger
      data-slot="collapsible-trigger"
      {...(props as any)}
    />
  )
}

type CollapsibleContentProps = React.HTMLAttributes<HTMLDivElement> & {
  asChild?: boolean
  forceMount?: boolean
}

function CollapsibleContent({
  ...props
}: CollapsibleContentProps) {
  return (
    <CollapsiblePrimitive.CollapsibleContent
      data-slot="collapsible-content"
      {...(props as any)}
    />
  )
}

export { Collapsible, CollapsibleTrigger, CollapsibleContent }
