param(
  [Parameter(Mandatory = $true)]
  [string]$DatabaseUrl,

  [Parameter(Mandatory = $true)]
  [string]$DumpPath,

  [string]$ExpectedSha256 = 'fbaf2455738d4084b7bf4faf52e56d7f0e4deb38735228e0522f674d6bc28316'
)

$ErrorActionPreference = 'Stop'

# Dictionary restore stages (every native command's exit code is checked):
#   0. dump exists + SHA256 matches the canonical D2 backup
#   1. pg_restore --list: archive readable, covers the three dict tables
#      (all validation happens BEFORE any destructive statement)
#   2. render the archive's data SQL to a temporary file
#   3. TRUNCATE + restore SQL + sequence repair in one transaction
#   4. check_dict_integrity.sql
# A failure during stage 3 rolls the whole transaction back to the pre-restore
# data. The canonical dump remains immutable and is never used as scratch space.

if (-not (Test-Path -LiteralPath $DumpPath)) {
  throw "Dump file not found: $DumpPath"
}

# --- Stage 0: SHA256 of the dump must match the canonical backup ---
$actualSha256 = (Get-FileHash -LiteralPath $DumpPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualSha256 -ne $ExpectedSha256.ToLowerInvariant()) {
  throw "Dump SHA256 mismatch: expected $ExpectedSha256, got $actualSha256 (refusing to touch dict tables)"
}

# --- Stage 1: pg_restore --list BEFORE anything destructive ---
$toc = & pg_restore --list $DumpPath 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "pg_restore --list failed (exit $LASTEXITCODE); archive unreadable: $toc"
}
foreach ($table in @('dict_entries', 'dict_lookup_targets', 'dict_redirects')) {
  if (-not ($toc | Where-Object { $_ -match "TABLE DATA\s+\S+\s+$table\s*$" -or $_ -match "TABLE DATA\s+.*\b$table\b" })) {
    throw "pg_restore --list: archive does not contain TABLE DATA for $table"
  }
}
Write-Host "pg_restore --list OK: archive covers dict_entries, dict_lookup_targets, dict_redirects"

$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$restoreTempDir = [System.IO.Path]::GetFullPath(
  (Join-Path $tempRoot "claread-dict-restore-$([guid]::NewGuid().ToString('N'))")
)
if (-not $restoreTempDir.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "Refusing unsafe restore temp path: $restoreTempDir"
}

New-Item -ItemType Directory -Path $restoreTempDir | Out-Null
$dataSqlPath = Join-Path $restoreTempDir 'dictionary-data.sql'
$transactionSqlPath = Join-Path $restoreTempDir 'restore-transaction.sql'

try {
  # --- Stage 2: render data SQL without connecting to the target database ---
  pg_restore `
    --data-only `
    --no-owner `
    --no-privileges `
    --table=dict_entries `
    --table=dict_lookup_targets `
    --table=dict_redirects `
    --file=$dataSqlPath `
    $DumpPath
  if ($LASTEXITCODE -ne 0) {
    throw "pg_restore SQL generation failed (exit $LASTEXITCODE); dict tables untouched"
  }

  # --- Stage 3: all destructive work shares one PostgreSQL transaction ---
  $psqlIncludePath = $dataSqlPath.Replace('\', '/').Replace("'", "''")
  $transactionSql = @"
\set ON_ERROR_STOP on
BEGIN;
TRUNCATE dict_lookup_targets, dict_redirects, dict_entries RESTART IDENTITY CASCADE;
\ir '$psqlIncludePath'
SELECT setval('dict_entries_id_seq', COALESCE((SELECT MAX(id) FROM dict_entries), 1));
SELECT setval('dict_lookup_targets_id_seq', COALESCE((SELECT MAX(id) FROM dict_lookup_targets), 1));
SELECT setval('dict_redirects_id_seq', COALESCE((SELECT MAX(id) FROM dict_redirects), 1));
COMMIT;
"@
  Set-Content -LiteralPath $transactionSqlPath -Value $transactionSql -Encoding UTF8

  psql $DatabaseUrl -v ON_ERROR_STOP=1 -f $transactionSqlPath
  if ($LASTEXITCODE -ne 0) {
    throw "dictionary restore transaction failed (exit $LASTEXITCODE); pre-restore data retained"
  }
}
finally {
  if (Test-Path -LiteralPath $restoreTempDir) {
    Remove-Item -LiteralPath $restoreTempDir -Recurse -Force
  }
}

# --- Stage 4: integrity check ---
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
psql $DatabaseUrl -v ON_ERROR_STOP=1 -f (Join-Path $scriptDir 'check_dict_integrity.sql')
if ($LASTEXITCODE -ne 0) {
  throw "check_dict_integrity.sql failed (exit $LASTEXITCODE)"
}

Write-Host "dictionary restore complete and integrity check passed"
