import { notFound } from "next/navigation";

import E2EPlatePasteSpikeHarness from "./E2EPlatePasteSpikeHarness";

/**
 * L0 paste spike — Server-side gate, same pattern as /e2e-plate-spike.
 *
 * Only rendered when `CLAREAD_ENABLE_E2E_SPIKE === "1"`. Otherwise 404.
 * Test-only; never reachable in production builds.
 */

const ENABLE_E2E_SPIKE = process.env.CLAREAD_ENABLE_E2E_SPIKE;

export default function E2EPlatePasteSpikePage() {
  if (ENABLE_E2E_SPIKE !== "1") {
    notFound();
  }

  return <E2EPlatePasteSpikeHarness />;
}
