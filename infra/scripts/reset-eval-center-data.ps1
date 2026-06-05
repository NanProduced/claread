$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\\..")
$PostgresContainer = if ($env:DIRECTUS_POSTGRES_CONTAINER) { $env:DIRECTUS_POSTGRES_CONTAINER } else { "claread-postgres" }
$PostgresDb = if ($env:DIRECTUS_POSTGRES_DB) { $env:DIRECTUS_POSTGRES_DB } else { "claread" }
$PostgresUser = if ($env:DIRECTUS_POSTGRES_USER) { $env:DIRECTUS_POSTGRES_USER } else { "claread" }

$resetSqlPath = Join-Path $PSScriptRoot "reset_eval_center_tables.sql"
$containerResetSqlPath = "/tmp/reset_eval_center_tables.sql"
$nodeLabRuntime = Join-Path $RepoRoot "apps\\directus\\.runtime\\evals\\node-lab"
$workflowRuntime = Join-Path $RepoRoot "apps\\directus\\.runtime\\evals\\workflow-runs"
$workflowCompareRuntime = Join-Path $RepoRoot "apps\\directus\\.runtime\\evals\\workflow-compares"

Write-Host "[eval-center] truncating eval-only tables in $PostgresContainer ..."
docker cp $resetSqlPath "${PostgresContainer}:$containerResetSqlPath"
if ($LASTEXITCODE -ne 0) {
  throw "[eval-center] failed to copy reset SQL into postgres container."
}
docker exec $PostgresContainer psql -v ON_ERROR_STOP=1 -U $PostgresUser -d $PostgresDb -f $containerResetSqlPath
if ($LASTEXITCODE -ne 0) {
  throw "[eval-center] failed to truncate eval-only tables."
}

foreach ($path in @($nodeLabRuntime, $workflowRuntime, $workflowCompareRuntime)) {
  if (Test-Path $path) {
    Write-Host "[eval-center] removing runtime artifacts: $path"
    Remove-Item -LiteralPath $path -Recurse -Force
  }
}

Write-Host "[eval-center] reset complete."
