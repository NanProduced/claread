"use client"

import * as React from "react"
import {
  BarChart3,
  LifeBuoy,
  SlidersHorizontal,
  User,
  X,
} from "lucide-react"
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
  /**
   * Forwarded to Radix DialogContent's `onCloseAutoFocus`.
   *
   * Radix fires this when the dialog closes and focus is about to return.
   * By default Radix focuses `triggerRef`, but the route-based dialog has
   * no DialogTrigger, so consumers must handle focus restoration here.
   */
  onCloseAutoFocus?: (event: Event) => void
}

type SectionItem = {
  id: SettingsSection
  label: string
  icon: React.ComponentType<{ className?: string }>
}

type SectionGroup = {
  label: string
  items: ReadonlyArray<SectionItem>
}

const SECTION_GROUPS: ReadonlyArray<SectionGroup> = [
  {
    label: "账户",
    items: [
      { id: "account", label: "个人资料", icon: User },
      { id: "preferences", label: "偏好", icon: SlidersHorizontal },
    ],
  },
  {
    label: "Claread",
    items: [
      { id: "usage", label: "用量与积分", icon: BarChart3 },
      { id: "support", label: "支持", icon: LifeBuoy },
    ],
  },
]

const SETTINGS_NAV_MOTION =
  "transition-colors duration-[var(--cl-duration-base)] ease-[var(--cl-ease-standard)] motion-reduce:transition-none motion-reduce:duration-0"

const CLOSE_BTN_BASE = cn(
  "inline-flex items-center justify-center",
  "rounded-[var(--cl-radius-control-sm)] border border-hairline bg-transparent",
  "text-muted-foreground",
  SETTINGS_NAV_MOTION,
  "hover:bg-[var(--interactive-quiet-hover)] hover:text-ink",
  primitiveFocusRing,
)

const RAIL_BTN_BASE = cn(
  "inline-flex items-center gap-2 whitespace-nowrap rounded-[var(--cl-radius-control-sm)] px-3 py-2 text-left text-sm",
  "max-md:min-h-11",
  SETTINGS_NAV_MOTION,
  primitiveFocusRing,
)

function RailButton({
  selected,
  onClick,
  children,
  className,
}: {
  selected: boolean
  onClick: () => void
  children: React.ReactNode
  className?: string
}) {
  return (
    <button
      type="button"
      aria-current={selected ? "page" : undefined}
      onClick={onClick}
      className={cn(
        RAIL_BTN_BASE,
        selected
          ? "bg-[var(--app-control-current)] text-ink"
          : "text-muted-foreground hover:bg-[var(--interactive-quiet-hover)] hover:text-ink",
        className,
      )}
    >
      {children}
    </button>
  )
}

export function SettingsDialogShell({
  open,
  onOpenChange,
  activeSection,
  onSectionChange,
  children,
  overlayClassName,
  onCloseAutoFocus,
}: SettingsDialogShellProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        size="xl"
        onCloseAutoFocus={onCloseAutoFocus}
        className={cn(
          // Important: override Dialog primitive defaults (grid / gap-4 / padding)
          // so the desktop rail/content layout and long-content scrolling stay stable
          // regardless of Tailwind utility generation order.
          "!fixed !flex flex-col overflow-hidden !p-0 !gap-0",
          // Centered workspace: rely on the primitive's left-1/2 top-1/2
          // -translate-x-1/2 -translate-y-1/2 for desktop.
          // Mobile overrides below anchor the sheet to the viewport edges.
          "!w-[min(76rem,calc(100vw-4rem))] !max-w-[min(76rem,calc(100vw-4rem))]",
          "h-[min(60rem,calc(100dvh-4rem))] !max-h-[min(60rem,calc(100dvh-4rem))]",
          "motion-reduce:transition-none motion-reduce:duration-0",
          // Static surface: override panelSurface recipe with static surface.
          "!bg-none !bg-surface !shadow-none",
          // Mobile: full-screen sheet, no rounded corners, anchored top-left.
          "max-md:!w-screen max-md:h-dvh max-md:!max-w-none max-md:!max-h-none",
          "max-md:rounded-none max-md:!left-0 max-md:!top-0",
          "max-md:!translate-x-0 max-md:!translate-y-0",
        )}
        overlayClassName={cn("backdrop-blur-none", overlayClassName)}
        showCloseButton={false}
      >
        <DialogTitle className="sr-only">设置</DialogTitle>
        <DialogDescription className="sr-only">
          管理账户、偏好、用量与积分与支持选项。
        </DialogDescription>

        {/*
         * Mobile close bar — fixed chrome above nav and content.
         * 44px touch target, right-aligned, shrink-0 so it never scrolls away.
         * Desktop hides this and uses the absolute close button in the right panel.
         */}
        <div className="flex shrink-0 justify-end p-2 md:hidden">
          <DialogClose
            aria-label="关闭设置"
            className={cn(CLOSE_BTN_BASE, "size-11")}
          >
            <X className="size-4" aria-hidden="true" />
          </DialogClose>
        </div>

        <div className="flex min-h-0 flex-1 flex-col md:flex-row">
          <nav
            aria-label="设置分区"
            className={cn(
              "flex shrink-0 gap-1 overflow-x-auto bg-surface-raised p-2",
              "md:w-[12rem] md:flex-col md:overflow-visible md:border-r md:border-hairline md:p-3",
            )}
          >
            {SECTION_GROUPS.map((group) => (
              <div
                key={group.label}
                className="flex flex-col gap-1 max-md:flex-row max-md:gap-1"
              >
                <div className="px-3 py-1.5 text-xs text-muted-foreground max-md:hidden">
                  {group.label}
                </div>
                {group.items.map((section) => {
                  const Icon = section.icon
                  return (
                    <RailButton
                      key={section.id}
                      selected={activeSection === section.id}
                      onClick={() => onSectionChange(section.id)}
                      className="md:w-full"
                    >
                      <Icon className="size-4 shrink-0" aria-hidden="true" />
                      <span className="truncate">{section.label}</span>
                    </RailButton>
                  )
                })}
              </div>
            ))}
          </nav>

          {/*
           * Right panel: contains the section frame (which provides its own
           * fixed header + scrollable body). The desktop close button is
           * absolutely positioned in this panel's top-right corner, over
           * the frame's fixed header — never over scroll content.
           */}
          <div className="relative flex min-h-0 flex-1 flex-col">
            <DialogClose
              aria-label="关闭设置"
              className={cn(
                CLOSE_BTN_BASE,
                "absolute right-4 top-4 z-10 hidden size-9 md:inline-flex",
              )}
            >
              <X className="size-4" aria-hidden="true" />
            </DialogClose>

            {children}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
