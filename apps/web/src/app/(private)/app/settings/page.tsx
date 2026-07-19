import { redirect } from "next/navigation";

import { appReadRoute } from "@/lib/routes";

/**
 * `/app/settings` is no longer a routable page — Settings is a global
 * modal owned by `SettingsDialogProvider`. Any direct navigation
 * (typed URL, external link, refresh on a stale tab) lands here and
 * is redirected to `/app/read`, the default host page.
 *
 * To open Settings, callers should use `useSettingsDialog().openSettings`
 * (Sidebar user menu, Command Palette) so the address bar never changes.
 */
export default function SettingsRedirectPage(): never {
  redirect(appReadRoute);
}
