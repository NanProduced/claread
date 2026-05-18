"use client"

import * as React from "react"
import { Slider as BaseSlider } from "@base-ui/react/slider"
import { cn } from "@/lib/cn"
import { primitiveDescriptionClass, primitiveLabelClass } from "../shared"

export interface SliderProps {
  label?: string
  description?: string
  value?: number
  defaultValue?: number
  onValueChange?: (value: number) => void
  min?: number
  max?: number
  step?: number
  disabled?: boolean
  name?: string
  "aria-label"?: string
  className?: string
  showValue?: boolean
}

function Slider({
  label,
  description,
  value,
  defaultValue,
  onValueChange,
  min = 0,
  max = 100,
  step = 1,
  disabled,
  name,
  "aria-label": ariaLabel,
  className,
  showValue = true,
}: SliderProps) {
  return (
    <BaseSlider.Root
      className={cn("grid gap-3", className)}
      value={value}
      defaultValue={defaultValue}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      name={name}
      onValueChange={(nextValue) => onValueChange?.(Array.isArray(nextValue) ? nextValue[0] ?? min : nextValue)}
    >
      {(label || showValue) && (
        <div className="flex items-center justify-between gap-3">
          {label ? <BaseSlider.Label className={primitiveLabelClass}>{label}</BaseSlider.Label> : <span />}
          {showValue ? <BaseSlider.Value className="text-sm font-medium text-muted" /> : null}
        </div>
      )}
      {description ? <p className={primitiveDescriptionClass}>{description}</p> : null}
      <BaseSlider.Control className="flex h-8 items-center">
        <BaseSlider.Track className="relative h-2 w-full rounded-full bg-[rgba(232,228,218,0.92)]">
          <BaseSlider.Indicator className="absolute h-full rounded-full bg-lens-blue" />
          <BaseSlider.Thumb
            aria-label={ariaLabel ?? label ?? "滑块"}
            className="block size-5 rounded-full border border-hairline bg-surface shadow-[var(--cl-shadow-2)] outline-none ring-offset-reader-paper transition-shadow focus-visible:ring-2 focus-visible:ring-lens-blue/20 focus-visible:ring-offset-2"
          />
        </BaseSlider.Track>
      </BaseSlider.Control>
    </BaseSlider.Root>
  )
}

export { Slider }
