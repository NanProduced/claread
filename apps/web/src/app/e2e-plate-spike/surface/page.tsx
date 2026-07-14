import { notFound } from "next/navigation";

import E2ESurfaceHarness from "./E2ESurfaceHarness";

/**
 * T4.2a-PUX-R4-R2.1D — Server-side gate for the Real Surface E2E harness.
 *
 * Renders a REAL ReaderRecordPlateSurface (the same production component)
 * with a synthetic initial snapshot. The harness exposes
 * `window.__spikeSurface` with methods to drive the REAL Surface reload
 * path: `reloadWith(nextSnapshot, events, fence)`,
 * `reloadFallback(nextSnapshot)`, `changeGeneration(generation)`.
 *
 * The harness does NOT call editor.tf.replaceNodes / setValue / removeNodes
 * directly. All mutations flow through the Surface's props
 * (snapshot + pendingReloadContext + onReloadContextConsumed), exercising
 * the real mergeIncrementalProjection → targeted_apply / fallback_full_reload
 * pipeline.
 *
 * Gate: ONLY rendered when CLAREAD_ENABLE_E2E_SPIKE === "1". Production
 * builds never set this flag, so the harness is unreachable.
 */

const ENABLE_E2E_SPIKE = process.env.CLAREAD_ENABLE_E2E_SPIKE;

export default function E2ESurfaceSpikePage() {
  if (ENABLE_E2E_SPIKE !== "1") {
    notFound();
  }

  return <E2ESurfaceHarness />;
}
