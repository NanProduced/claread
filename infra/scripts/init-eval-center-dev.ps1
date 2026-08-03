param()

$ErrorActionPreference = "Stop"

# CUTOVER-CONTROL-EVAL-LONG Logical tombstone.
# Old Eval Center bootstrap is retired. This script MUST fail before any
# Docker / DDL / metadata sync side effect so it cannot re-create control-plane
# tables, drop data, or re-register retired Directus surfaces.
# Physical stage may delete this script; until then it is a hard no-op exit.

Write-Error (
  "[retired] infra/scripts/init-eval-center-dev.ps1 is disabled after Logical cutover. " +
  "It must not drop/recreate eval-center tables, reset eval data, or run " +
  "directus:eval-center:sync-metadata. New Console/Eval will be rebuilt post " +
  "architectural cutover from Reader/Ask durable facts only. " +
  "eval_example_lab_entries remains KEEP/REHOME and is not bootstrapped here."
)
exit 1
