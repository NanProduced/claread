param(
  [switch]$IncludeStaticRuns
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\\..")
$PostgresContainer = if ($env:DIRECTUS_POSTGRES_CONTAINER) { $env:DIRECTUS_POSTGRES_CONTAINER } else { "claread-postgres" }
$PostgresDb = if ($env:DIRECTUS_POSTGRES_DB) { $env:DIRECTUS_POSTGRES_DB } else { "claread" }
$PostgresUser = if ($env:DIRECTUS_POSTGRES_USER) { $env:DIRECTUS_POSTGRES_USER } else { "claread" }

$migrationPath = Join-Path $RepoRoot "infra\\migrations\\eval-center\\0001_eval_center_control_plane.sql"
$dropSqlPath = Join-Path $PSScriptRoot "drop_eval_center_tables.sql"
$resetScript = Join-Path $PSScriptRoot "reset-eval-center-data.ps1"
$directusEnvPath = Join-Path $RepoRoot "apps\\directus\\.env"
$nodeLabRuntime = Join-Path $RepoRoot "apps\\directus\\.runtime\\evals\\node-lab"
$workflowRuntime = Join-Path $RepoRoot "apps\\directus\\.runtime\\evals\\workflow-runs"
$workflowCompareRuntime = Join-Path $RepoRoot "apps\\directus\\.runtime\\evals\\workflow-compares"
$staticRunsRoot = Join-Path $RepoRoot "evals\\runs"
$datasetRoot = Join-Path $RepoRoot "evals\\datasets"
$rubricRoot = Join-Path $RepoRoot "evals\\rubrics"
$containerMigrationPath = "/tmp/eval_center_control_plane.sql"
$containerDropSqlPath = "/tmp/drop_eval_center_tables.sql"

function Invoke-PostgresScalar {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Sql
  )

  $result = docker exec $PostgresContainer psql -t -A -U $PostgresUser -d $PostgresDb -c $Sql
  if ($LASTEXITCODE -ne 0) {
    throw "[eval-center] postgres check failed: $Sql"
  }
  return [string]::Join("`n", $result).Trim()
}

function Get-PathEntryCount {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path
  )

  if (-not (Test-Path $Path)) {
    return 0
  }
  return @(Get-ChildItem -Force $Path).Count
}

if (Test-Path $directusEnvPath) {
  foreach ($line in Get-Content $directusEnvPath) {
    if ([string]::IsNullOrWhiteSpace($line) -or $line.TrimStart().StartsWith("#")) {
      continue
    }
    $pair = $line -split "=", 2
    if ($pair.Length -ne 2) {
      continue
    }
    $key = $pair[0].Trim()
    $value = $pair[1].Trim()
    if (-not [string]::IsNullOrWhiteSpace($key) -and -not (Test-Path "env:$key")) {
      Set-Item -Path "env:$key" -Value $value
    }
  }
}

Write-Host "[eval-center] dropping existing eval-center tables ..."
docker cp $dropSqlPath "${PostgresContainer}:$containerDropSqlPath"
if ($LASTEXITCODE -ne 0) {
  throw "[eval-center] failed to copy drop SQL into postgres container."
}
docker exec $PostgresContainer psql -v ON_ERROR_STOP=1 -U $PostgresUser -d $PostgresDb -f $containerDropSqlPath
if ($LASTEXITCODE -ne 0) {
  throw "[eval-center] failed to drop existing eval-center tables."
}

Write-Host "[eval-center] applying consolidated control-plane migration ..."
docker cp $migrationPath "${PostgresContainer}:$containerMigrationPath"
if ($LASTEXITCODE -ne 0) {
  throw "[eval-center] failed to copy consolidated control-plane migration into postgres container."
}
docker exec $PostgresContainer psql -v ON_ERROR_STOP=1 -U $PostgresUser -d $PostgresDb -f $containerMigrationPath
if ($LASTEXITCODE -ne 0) {
  throw "[eval-center] failed to apply consolidated control-plane migration."
}

Write-Host "[eval-center] resetting eval-only data ..."
& $resetScript @PSBoundParameters

Push-Location $RepoRoot
try {
  Write-Host "[eval-center] syncing Directus eval-center metadata ..."
  pnpm directus:eval-center:sync-metadata
  if ($LASTEXITCODE -ne 0) {
    throw "[eval-center] Directus eval-center metadata sync failed."
  }
}
finally {
  Pop-Location
}

Write-Host "[eval-center] readiness checks"
$exampleCollectionCount = Invoke-PostgresScalar "SELECT COUNT(*) FROM directus_collections WHERE collection = 'eval_example_lab_entries';"
$ragEligibleFieldCount = Invoke-PostgresScalar "SELECT COUNT(*) FROM directus_fields WHERE collection = 'eval_example_lab_entries' AND field = 'rag_eligible';"
$nodeWorkspaceDefault = Invoke-PostgresScalar "SELECT column_default FROM information_schema.columns WHERE table_name = 'eval_node_lab_sessions' AND column_name = 'allowed_workspace_types_json';"
$nodeWorkspaceConstraint = Invoke-PostgresScalar "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'eval_node_lab_trials_workspace_type_check';"
$nodeResultKindConstraint = Invoke-PostgresScalar "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'eval_node_lab_trials_result_kind_check';"
$promptVariantDraftCount = Invoke-PostgresScalar "SELECT COUNT(*) FROM eval_prompt_variant_drafts;"
$workflowRequestCount = Invoke-PostgresScalar "SELECT COUNT(*) FROM eval_workflow_run_requests;"
$workflowCompareCount = Invoke-PostgresScalar "SELECT COUNT(*) FROM eval_workflow_compares;"
$workflowCompareJudgeCount = Invoke-PostgresScalar "SELECT COUNT(*) FROM eval_workflow_compare_judge_requests;"
$reviewNoteCount = Invoke-PostgresScalar "SELECT COUNT(*) FROM eval_review_notes;"
$nodeSessionCount = Invoke-PostgresScalar "SELECT COUNT(*) FROM eval_node_lab_sessions;"
$nodeTrialCount = Invoke-PostgresScalar "SELECT COUNT(*) FROM eval_node_lab_trials;"
$exampleEntryCount = Invoke-PostgresScalar "SELECT COUNT(*) FROM eval_example_lab_entries;"
$nodeLabRuntimeEntries = Get-PathEntryCount $nodeLabRuntime
$workflowRuntimeEntries = Get-PathEntryCount $workflowRuntime
$workflowCompareRuntimeEntries = Get-PathEntryCount $workflowCompareRuntime
$staticRunEntries = Get-PathEntryCount $staticRunsRoot
$resetCountChecks = @(
  @{ Name = "prompt_variant_drafts"; Value = $promptVariantDraftCount }
  @{ Name = "workflow_requests"; Value = $workflowRequestCount }
  @{ Name = "workflow_compares"; Value = $workflowCompareCount }
  @{ Name = "workflow_compare_judges"; Value = $workflowCompareJudgeCount }
  @{ Name = "review_notes"; Value = $reviewNoteCount }
  @{ Name = "node_sessions"; Value = $nodeSessionCount }
  @{ Name = "node_trials"; Value = $nodeTrialCount }
  @{ Name = "example_entries"; Value = $exampleEntryCount }
)

if ($exampleCollectionCount -ne "1") {
  throw "[eval-center] Example Lab collection metadata is missing."
}
if ($ragEligibleFieldCount -ne "0") {
  throw "[eval-center] stale Example Lab field directus_fields.eval_example_lab_entries.rag_eligible still exists."
}
if ($nodeWorkspaceDefault -match "judge_compare") {
  throw "[eval-center] eval_node_lab_sessions.allowed_workspace_types_json still contains judge_compare."
}
if ($nodeWorkspaceConstraint -match "judge_compare") {
  throw "[eval-center] eval_node_lab_trials workspace_type check still contains judge_compare."
}
if ($nodeResultKindConstraint -match "judge_compare_result") {
  throw "[eval-center] eval_node_lab_trials result_kind check still contains judge_compare_result."
}
$nonEmptyResetCounts = @($resetCountChecks | Where-Object { $_.Value -ne "0" })
if ($nonEmptyResetCounts.Count -gt 0) {
  $summary = ($nonEmptyResetCounts | ForEach-Object { "$($_.Name)=$($_.Value)" }) -join " "
  throw "[eval-center] eval-only tables are not empty after init: $summary"
}

Write-Host ("  datasets: " + (Test-Path $datasetRoot))
Write-Host ("  rubrics: " + (Test-Path $rubricRoot))
Write-Host ("  metadata: eval_example_lab_entries collection present")
Write-Host ("  metadata: rag_eligible removed from directus_fields")
Write-Host ("  node-lab default: " + $nodeWorkspaceDefault)
Write-Host ("  node-lab workspace_type check: " + $nodeWorkspaceConstraint)
Write-Host ("  node-lab result_kind check: " + $nodeResultKindConstraint)
Write-Host ("  reset counts: prompt_variant_drafts=0 workflow_requests=0 workflow_compares=0 workflow_compare_judges=0 review_notes=0 node_sessions=0 node_trials=0 example_entries=0")
Write-Host ("  node-lab runtime entries: " + $nodeLabRuntimeEntries)
Write-Host ("  workflow runtime entries: " + $workflowRuntimeEntries)
Write-Host ("  workflow compare runtime entries: " + $workflowCompareRuntimeEntries)
Write-Host ("  static evals/runs entries: " + $staticRunEntries)
Write-Host "[eval-center] init complete."
