import { notFound } from "next/navigation";

import E2EAskFloatingOverlayHarness from "./E2EAskFloatingOverlayHarness";

/**
 * ASK-UX-MOBILE-R3 — Server-side gate for the floating-overlay E2E harness.
 *
 * Renders a REAL AiWorkspacePanel with:
 *   - layout="overlay"
 *   - surface="floating"
 *   - onChangeSurface (wired to internal state)
 *   - hasSidecarCapacity (toggleable via window.__spikeAskFloatingOverlay)
 *   - A scrollable background so body-lock verification has a real
 *     overflow condition to test against.
 *
 * Gate: ONLY rendered when CLAREAD_ENABLE_E2E_SPIKE === "1". Production
 * builds never set this flag, so the harness is unreachable.
 */

const ENABLE_E2E_SPIKE = process.env.CLAREAD_ENABLE_E2E_SPIKE;

export default function E2EAskFloatingOverlaySpikePage() {
  if (ENABLE_E2E_SPIKE !== "1") {
    notFound();
  }

  return <E2EAskFloatingOverlayHarness />;
}
