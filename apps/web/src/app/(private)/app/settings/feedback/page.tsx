import { redirect } from "next/navigation";

import { appReadRoute } from "@/lib/routes";

/**
 * `/app/settings/feedback` is no longer a routable page — the feedback
 * form now lives inside the Settings Dialog (Support section). Any direct
 * navigation lands here and is redirected to `/app/read`.
 *
 * To submit feedback, open Settings via `useSettingsDialog().openSettings`
 * and navigate to the Support section.
 */
export default function FeedbackRedirectPage(): never {
  redirect(appReadRoute);
}
