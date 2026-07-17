"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import type { AccountData, PreferencesData, UsageData } from "@/app/(private)/app/settings/sections/SettingsSectionContent";
import type { SettingsSection } from "@/components/settings/SettingsDialogShell";
import { SettingsDialogShell } from "@/components/settings/SettingsDialogShell";
import { SettingsSectionContent } from "@/app/(private)/app/settings/sections/SettingsSectionContent";

const VALID_SECTIONS: ReadonlyArray<SettingsSection> = [
  "account",
  "preferences",
  "usage",
  "support",
];

/** Parse the `?section=` query param with whitelist validation. */
export function parseSettingsSection(
  value: string | null | undefined,
): SettingsSection {
  if (value && VALID_SECTIONS.includes(value as SettingsSection)) {
    return value as SettingsSection;
  }
  return "preferences";
}

export interface SettingsDialogRouteClientProps {
  accountData: AccountData;
  preferencesData: PreferencesData;
  usageData: UsageData;
}

/**
 * Client component rendered by the intercepted settings route
 * (`@settings/(.)settings/page.tsx`).
 *
 * - `open` is always `true` because the route's existence implies the
 *   dialog is mounted.
 * - Section is driven by the `?section=` query param (URL is the single
 *   source of truth). Switching sections uses `router.replace` so history
 *   does not accumulate per switch.
 * - Closing the dialog (close button, Esc, overlay click) calls
 *   `router.back()` to return to the originating page (e.g. Reader)
 *   without navigating to the standalone settings URL.
 */
export function SettingsDialogRouteClient({
  accountData,
  preferencesData,
  usageData,
}: SettingsDialogRouteClientProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const activeSection = parseSettingsSection(searchParams.get("section"));

  // Capture the element that had focus before the dialog opened.
  // useState with a lazy initializer runs during the first render only,
  // BEFORE any layout effects (including Radix Dialog's auto-focus).
  // This is the only safe way to read document.activeElement before Radix
  // moves focus into the dialog content. The value is immutable — we never
  // call setOpener, so Dialog-internal focus migrations cannot overwrite it.
  const [opener] = React.useState<HTMLElement | null>(() => {
    if (
      typeof document !== "undefined" &&
      document.activeElement instanceof HTMLElement &&
      document.activeElement !== document.body
    ) {
      return document.activeElement;
    }
    return null;
  });

  const handleSectionChange = React.useCallback(
    (section: SettingsSection) => {
      const params = new URLSearchParams(searchParams.toString());
      params.set("section", section);
      router.replace(`/app/settings?${params.toString()}`);
    },
    [router, searchParams],
  );

  const handleOpenChange = React.useCallback(
    (open: boolean) => {
      if (!open) {
        router.back();
      }
    },
    [router],
  );

  const handleCloseAutoFocus = React.useCallback((event: Event) => {
    // Restore focus to the element that opened the dialog.
    // Radix's default would focus triggerRef (null for route-based dialogs),
    // so we handle it here. Only focus if the element is still connected.
    if (opener && opener.isConnected) {
      opener.focus();
      event.preventDefault();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <SettingsDialogShell
      open
      onOpenChange={handleOpenChange}
      activeSection={activeSection}
      onSectionChange={handleSectionChange}
      onCloseAutoFocus={handleCloseAutoFocus}
    >
      <SettingsSectionContent
        section={activeSection}
        accountData={accountData}
        preferencesData={preferencesData}
        usageData={usageData}
        usageShowLedger
      />
    </SettingsDialogShell>
  );
}
