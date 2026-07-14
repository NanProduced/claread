import { notFound } from "next/navigation";

import E2EPlateSpikeHarness from "./E2EPlateSpikeHarness";

/**
 * T4.2a-PUX-R4-R2-S2 — Server-side gate for the E2E spike harness.
 *
 * The client harness (which exposes `window.__spikeEditor` and fixture
 * helpers) is ONLY rendered when the private environment variable
 * `CLAREAD_ENABLE_E2E_SPIKE === "1"`. In all other cases — dev without
 * the flag, test runner without the flag, production — this route
 * returns 404 via `notFound()` and the harness/fixture/window globals
 * are never mounted.
 *
 * This gate is server-side so the decision is made before any client
 * code is shipped. Production builds never set the flag, so the harness
 * is unreachable in production.
 *
 * Boundary: does NOT change Reader production pages, polling, backend,
 * or API.
 */

const ENABLE_E2E_SPIKE = process.env.CLAREAD_ENABLE_E2E_SPIKE;

export default function E2EPlateSpikePage() {
  if (ENABLE_E2E_SPIKE !== "1") {
    notFound();
  }

  return <E2EPlateSpikeHarness />;
}
