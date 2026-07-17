"use client"

import * as React from "react"
import { X } from "lucide-react"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/primitives/dialog"
import { primitiveFocusRing } from "@/components/primitives/shared"
import { cn } from "@/lib/cn"

export type SettingsSection = "account" | "preferences" | "usage" | "support"

export interface SettingsDialogShellProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  activeSection: SettingsSection
  onSectionChange: (section: SettingsSection) => void
  children: React.ReactNode
  overlayClassName?: string
}

const SETTINGS_SECTIONS: ReadonlyArray<{ id: SettingsSection; label: string }> = [
  { id: "account", label: "账户" },
  { id: "preferences", label: "偏好" },
  { id: "usage", label: "用量与积分" },
  { id: "support", label: "支持" },
]

const SETTINGS_NAV_MOTION =
  "transition-colors duration-[var(--cl-duration-base)] ease-[var(--cl-ease-standard)] motion-reduce:transition-none motion-reduce:duration-0"

export function SettingsDialogShell({
  open,
  onOpenChange,
  activeSection,
  onSectionChange,
  children,
  overlayClassName,
}: SettingsDialogShellProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        size="xl"
        className={cn(
          // Important: override Dialog primitive defaults (grid / gap-4 / padding)
          // so the desktop rail/content layout and long-content scrolling stay stable
          // regardless of Tailwind utility generation order.
          "relative !flex flex-col overflow-hidden !p-0 !gap-0",
          "motion-reduce:transition-none motion-reduce:duration-0",
          // Static surface: override panelSurface recipe with static surface.
          "!bg-none !bg-surface !shadow-none",
          // Mobile: full-screen sheet, no rounded corners, anchored top-left.
          "max-md:w-screen max-md:h-dvh max-md:max-w-none max-md:max-h-none",
          "max-md:rounded-none max-md:left-0 max-md:top-0",
          "max-md:translate-x-0 max-md:translate-y-0",
        )}
        overlayClassName={cn("backdrop-blur-none", overlayClassName)}
        showCloseButton={false}
      >
        <DialogTitle className="sr-only">设置</DialogTitle>
        <DialogDescription className="sr-only">
          管理账户、偏好、用量与积分与支持选项。
        </DialogDescription>

        <DialogClose
          aria-label="关闭设置"
          className={cn(
            "absolute right-4 top-4 z-10 inline-flex size-9 items-center justify-center",
            "max-md:size-11",
            "rounded-[var(--cl-radius-control-sm)] border border-hairline bg-transparent",
            "text-muted-foreground",
            SETTINGS_NAV_MOTION,
            "hover:bg-[var(--interactive-quiet-hover)] hover:text-ink",
            primitiveFocusRing,
          )}
        >
          <X className="size-4" aria-hidden="true" />
        </DialogClose>

        <div className="flex min-h-0 flex-1 flex-col md:flex-row">
          <nav
            aria-label="设置分区"
            className={cn(
              "flex shrink-0 gap-1 overflow-x-auto bg-surface-raised p-2",
              "md:w-[13.5rem] md:flex-col md:overflow-y-auto md:border-r md:border-hairline md:p-4",
            )}
          >
            {SETTINGS_SECTIONS.map((section) => {
              const selected = activeSection === section.id
              return (
                <button
                  key={section.id}
                  type="button"
                  aria-current={selected ? "page" : undefined}
                  onClick={() => onSectionChange(section.id)}
                  className={cn(
                    "whitespace-nowrap rounded-[var(--cl-radius-control-sm)] px-3 py-2 text-left text-sm",
                    "max-md:min-h-11",
                    SETTINGS_NAV_MOTION,
                    primitiveFocusRing,
                    selected
                      ? "bg-[var(--app-control-current)] text-ink"
                      : "text-muted-foreground hover:bg-[var(--interactive-quiet-hover)] hover:text-ink",
                  )}
                >
                  {section.label}
                </button>
              )
            })}
          </nav>

          <div
            className="flex-1 overflow-y-auto bg-surface p-4 md:p-6"
          >
            {children}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
