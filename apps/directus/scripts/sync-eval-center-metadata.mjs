#!/usr/bin/env node
// CUTOVER-CONTROL-EVAL-LONG Logical gate:
// Old Eval Center / Example Lab / Workflow Lab metadata sync is retired.
// This script must NOT re-enable module bar items or eval_* control-plane collections.
// Physical stage will delete this file and list tables for Data owner DDL.

console.error(
  "[retired] sync-eval-center-metadata.mjs is disabled after Logical cutover. " +
    "Old Eval Center module bar / Example Lab / workflow-lab metadata must not auto-revive. " +
    "New Console/Eval will be rebuilt post architectural cutover from Reader/Ask durable facts only.",
);
process.exit(1);
