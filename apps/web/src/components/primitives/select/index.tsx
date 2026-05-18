"use client"

import * as React from "react"
import { Check, ChevronDown } from "lucide-react"
import { Select as BaseSelect } from "@base-ui/react/select"
import { cn } from "@/lib/cn"
import { controlVariants, panelSurface, primitiveDescriptionClass, primitiveLabelClass } from "../shared"

export interface SelectOption {
  label: string
  value: string | null
  description?: string
  disabled?: boolean
}

export interface SelectProps {
  label?: string
  items: SelectOption[]
  value?: string | null
  defaultValue?: string | null
  onValueChange?: (value: string | null) => void
  placeholder?: string
  disabled?: boolean
  name?: string
  size?: "sm" | "md" | "lg"
  tone?: "default" | "panel" | "quiet"
  className?: string
  description?: string
  "aria-label"?: string
}

function Select({
  label,
  items,
  value,
  defaultValue,
  onValueChange,
  placeholder = "请选择",
  disabled,
  name,
  size = "md",
  tone = "default",
  className,
  description,
  "aria-label": ariaLabel,
}: SelectProps) {
  return (
    <BaseSelect.Root<string | null>
      items={items}
      value={value}
      defaultValue={defaultValue}
      onValueChange={onValueChange}
      disabled={disabled}
      name={name}
    >
      <div className="grid gap-2">
        {label ? <BaseSelect.Label className={primitiveLabelClass}>{label}</BaseSelect.Label> : null}
        {description ? <p className={primitiveDescriptionClass}>{description}</p> : null}
        <BaseSelect.Trigger aria-label={ariaLabel ?? label ?? "选择"} className={cn(controlVariants({ size, tone }), "w-full justify-between", className)}>
          <BaseSelect.Value className="truncate text-left" placeholder={placeholder} />
          <BaseSelect.Icon className="text-muted">
            <ChevronDown className="size-4" />
          </BaseSelect.Icon>
        </BaseSelect.Trigger>
      </div>

      <BaseSelect.Portal>
        <BaseSelect.Positioner sideOffset={8} className="z-50">
          <BaseSelect.Popup className={cn("min-w-[var(--anchor-width)]", panelSurface({ padding: "sm" }))}>
            <BaseSelect.List className="max-h-72 space-y-1 overflow-y-auto">
              {items.map((item) => (
                <BaseSelect.Item
                  key={`${item.label}-${item.value ?? "null"}`}
                  value={item.value}
                  disabled={item.disabled}
                  className="relative flex min-h-10 cursor-default items-start gap-3 rounded-[var(--cl-radius-control-sm)] px-3 py-2 text-sm text-ink transition-colors data-[disabled]:pointer-events-none data-[disabled]:opacity-45 data-[highlighted]:bg-lens-blue-soft"
                >
                  <span className="inline-flex size-4 items-center justify-center pt-0.5 text-lens-blue">
                    <BaseSelect.ItemIndicator>
                      <Check className="size-4" />
                    </BaseSelect.ItemIndicator>
                  </span>
                  <div className="min-w-0">
                    <BaseSelect.ItemText className="block truncate">{item.label}</BaseSelect.ItemText>
                    {item.description ? <p className="mt-0.5 text-xs leading-5 text-muted">{item.description}</p> : null}
                  </div>
                </BaseSelect.Item>
              ))}
            </BaseSelect.List>
          </BaseSelect.Popup>
        </BaseSelect.Positioner>
      </BaseSelect.Portal>
    </BaseSelect.Root>
  )
}

export { Select }
