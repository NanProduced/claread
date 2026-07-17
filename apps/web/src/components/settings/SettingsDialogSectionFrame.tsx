import * as React from "react"
import { cn } from "@/lib/cn"

export type SettingsDialogSectionWidth = "standard" | "wide"

export interface SettingsDialogSectionFrameProps {
  /** Visible section title (UI sans, not font-headline). */
  title: string
  /** Optional one-line description below the title. */
  description?: string
  /**
   * Content column width.
   * - "standard": constrains content to a readable column (default).
   * - "wide": full width for tables, ledgers, filterable lists.
   */
  width?: SettingsDialogSectionWidth
  children: React.ReactNode
}

/**
 * Unified frame for Settings Dialog section content.
 *
 * Renders a fixed header (title + optional description) and an independently
 * scrolling body. The header does not scroll away — it stays pinned so the
 * user always knows which section they are in while scrolling long content.
 *
 * The body provides:
 * - `min-h-0` + `overflow-y-auto` for independent scroll within the dialog.
 * - Content column width constraint (standard vs wide).
 * - `aria-labelledby` linking the body region to the header title.
 *
 * This frame is ONLY used in Dialog mode. The fallback `/app/settings` page
 * uses `SettingsSectionLayout` with its own `md:grid` structure.
 */
export function SettingsDialogSectionFrame({
  title,
  description,
  width = "standard",
  children,
}: SettingsDialogSectionFrameProps) {
  // Stable id for aria-labelledby relationship.
  const titleId = React.useId()

  return (
    <div className="grid h-full min-h-0 grid-rows-[auto_1fr]">
      <div
        className={cn(
          "shrink-0 border-b border-hairline px-5 py-5 md:px-8 md:py-6",
          // Right padding on desktop clears the absolute close button.
          "md:pr-16",
        )}
      >
        <h2
          id={titleId}
          className="text-lg font-semibold leading-tight text-ink md:text-xl"
        >
          {title}
        </h2>
        {description ? (
          <p className="mt-1.5 max-w-[42rem] text-sm leading-relaxed text-muted-foreground">
            {description}
          </p>
        ) : null}
      </div>
      <div
        className="min-h-0 overflow-y-auto"
        aria-labelledby={titleId}
      >
        <div
          className={cn(
            "px-5 py-7 md:px-8 md:py-8",
            width === "standard" && "max-w-[34rem]",
          )}
        >
          {children}
        </div>
      </div>
    </div>
  )
}
