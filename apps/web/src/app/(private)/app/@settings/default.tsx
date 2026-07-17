/**
 * Default export for the `@settings` parallel slot.
 *
 * Returns null when no intercepted settings route is active, so the
 * underlying page (e.g. Reader) renders without any modal overlay.
 * When the user navigates to `/app/settings` from within the app,
 * the intercepting route `@settings/(.)settings/page.tsx` replaces
 * this slot with the Settings Dialog.
 */
export default function SettingsSlotDefault() {
  return null;
}
