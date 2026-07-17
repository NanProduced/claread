import * as React from "react";
import { loadSettingsData } from "@/app/(private)/app/settings/lib/loadSettingsData";
import { SettingsDialogRouteClient } from "@/components/settings/SettingsDialogRouteClient";

/**
 * Intercepted settings route.
 *
 * When a user navigates to `/app/settings` from within the app (e.g. from
 * the Reader), Next.js intercepts the navigation and renders this page in
 * the `@settings` parallel slot instead of the full `/app/settings` page.
 * The underlying page (Reader) stays mounted — the dialog overlays it.
 *
 * Direct navigation or a page refresh bypasses the interception and falls
 * through to `settings/page.tsx`, which renders the complete fallback page.
 *
 * Data loading uses the same shared `loadSettingsData()` loader as the
 * fallback page — no API contract is duplicated or altered.
 */
export default async function InterceptedSettingsPage() {
  const { accountData, preferencesData, usageData } = await loadSettingsData();

  return (
    <React.Suspense fallback={null}>
      <SettingsDialogRouteClient
        accountData={accountData}
        preferencesData={preferencesData}
        usageData={usageData}
      />
    </React.Suspense>
  );
}
