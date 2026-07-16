import { notFound } from "next/navigation";

import E2EAskActivityHarness from "./E2EAskActivityHarness";

/**
 * R2.5 — Server-side gate for the Agentic Ask Activity E2E harness.
 *
 * Renders a REAL AiWorkspacePanel with synthetic record context.
 * The harness exposes `window.__spikeAskActivity` to drive a gated SSE
 * stream: `setScript`, `releaseNext`, `releaseAll`, `reset`.
 *
 * Gate: ONLY rendered when CLAREAD_ENABLE_E2E_SPIKE === "1". Production
 * builds never set this flag, so the harness is unreachable.
 */

const ENABLE_E2E_SPIKE = process.env.CLAREAD_ENABLE_E2E_SPIKE;

export default function E2EAskActivitySpikePage() {
  if (ENABLE_E2E_SPIKE !== "1") {
    notFound();
  }

  return <E2EAskActivityHarness />;
}
