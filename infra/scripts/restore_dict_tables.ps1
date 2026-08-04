param(
  [Parameter(Mandatory = $true)]
  [string]$DatabaseUrl,

  [Parameter(Mandatory = $true)]
  [string]$DumpPath,

  [string]$ExpectedSha256 = 'fbaf2455738d4084b7bf4faf52e56d7f0e4deb38735228e0522f674d6bc28316'
)

$ErrorActionPreference = 'Stop'

# DATA-D2-CLOSEOUT-R1 hardened dictionary restore.
# Stages (every native command's exit code is checked explicitly):
#   0. dump exists + SHA256 matches the canonical D2 backup
#   1. pg_restore --list: archive readable, covers the three dict tables
#      (all validation happens BEFORE any destructive statement)
#   2. TRUNCATE (atomic statement)
#   3. pg_restore --single-transaction (atomic: commits or rolls back as one)
#   4. sequence repair (atomic statement)
#   5. check_dict_integrity.sql
# Each stage is individually atomic and the script is idempotent: a failed
# run leaves either the pre-restore data or empty dict tables, and re-running
# the script from the top is the rollback path (never delete the dump).

if (-not (Test-Path $DumpPath)) {
  throw "Dump file not found: $DumpPath"
}

# --- Stage 0: SHA256 of the dump must match the canonical backup ---
$actualSha256 = (Get-FileHash -Path $DumpPath -Algorithm SHA256).Hash.ToLowerInvariant()
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

# --- Stage 2: TRUNCATE ---
psql $DatabaseUrl -v ON_ERROR_STOP=1 -c "TRUNCATE dict_lookup_targets, dict_redirects, dict_entries RESTART IDENTITY CASCADE;"
if ($LASTEXITCODE -ne 0) {
  throw "TRUNCATE failed (exit $LASTEXITCODE); dict tables untouched or partially truncated - fix and re-run"
}

# --- Stage 3: data-only restore in a single transaction ---
pg_restore `
  --dbname=$DatabaseUrl `
  --data-only `
  --no-owner `
  --no-privileges `
  --single-transaction `
  --table=dict_entries `
  --table=dict_lookup_targets `
  --table=dict_redirects `
  $DumpPath
if ($LASTEXITCODE -ne 0) {
  throw "pg_restore failed (exit $LASTEXITCODE); single transaction rolled back - re-run this script to restore"
}

# --- Stage 4: sequence repair ---
psql $DatabaseUrl -v ON_ERROR_STOP=1 -c "SELECT setval('dict_entries_id_seq', COALESCE((SELECT MAX(id) FROM dict_entries), 1)); SELECT setval('dict_lookup_targets_id_seq', COALESCE((SELECT MAX(id) FROM dict_lookup_targets), 1)); SELECT setval('dict_redirects_id_seq', COALESCE((SELECT MAX(id) FROM dict_redirects), 1));"
if ($LASTEXITCODE -ne 0) {
  throw "sequence repair failed (exit $LASTEXITCODE); re-run this script to restore"
}

# --- Stage 5: integrity check ---
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
psql $DatabaseUrl -v ON_ERROR_STOP=1 -f (Join-Path $scriptDir 'check_dict_integrity.sql')
if ($LASTEXITCODE -ne 0) {
  throw "check_dict_integrity.sql failed (exit $LASTEXITCODE)"
}

Write-Host "dictionary restore complete and integrity check passed"
