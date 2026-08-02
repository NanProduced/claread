#!/usr/bin/env node
// CUTOVER-CONTROL-EVAL-LONG Logical gate:
// Old Parse Run Observability / analysis_* metadata sync is retired.
// This script must NOT recreate dashboards, panels, or analysis collections.
// Physical stage will delete this file. Do not re-enable without Data owner review.

console.error(
  "[retired] sync-parse-run-observability-metadata.mjs is disabled after Logical cutover. " +
    "Old Parse dashboard / quality panels / analysis_* metadata must not auto-revive. " +
    "Reader observability lives at endpoint /reader-orch/* only.",
);
process.exit(1);
