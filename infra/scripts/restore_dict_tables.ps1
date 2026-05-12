param(
  [Parameter(Mandatory = $true)]
  [string]$DatabaseUrl,

  [Parameter(Mandatory = $true)]
  [string]$DumpPath
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $DumpPath)) {
  throw "Dump file not found: $DumpPath"
}

psql $DatabaseUrl -c "TRUNCATE dict_lookup_targets, dict_redirects, dict_entries RESTART IDENTITY CASCADE;"

pg_restore `
  --dbname=$DatabaseUrl `
  --data-only `
  --no-owner `
  --no-privileges `
  --table=dict_entries `
  --table=dict_lookup_targets `
  --table=dict_redirects `
  $DumpPath

psql $DatabaseUrl -c "SELECT setval('dict_entries_id_seq', COALESCE((SELECT MAX(id) FROM dict_entries), 1)); SELECT setval('dict_lookup_targets_id_seq', COALESCE((SELECT MAX(id) FROM dict_lookup_targets), 1)); SELECT setval('dict_redirects_id_seq', COALESCE((SELECT MAX(id) FROM dict_redirects), 1));"
