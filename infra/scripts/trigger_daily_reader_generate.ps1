param(
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$baseUrl = $env:CLAREAD_API_BASE_URL
if ([string]::IsNullOrWhiteSpace($baseUrl)) {
    $baseUrl = 'http://127.0.0.1:8000'
}
$baseUrl = $baseUrl.TrimEnd('/')
$adminKey = $env:DAILY_READER_ADMIN_API_KEY
$url = "$baseUrl/daily-reader/admin/generate"
$body = '{"force":false,"max_count":3}'

if ($DryRun) {
    $keyState = if ([string]::IsNullOrWhiteSpace($adminKey)) { '<missing>' } else { '<set>' }
    Write-Output "DRY-RUN POST $url"
    Write-Output "Header x-admin-api-key: $keyState"
    Write-Output "Body: $body"
    exit 0
}

if ([string]::IsNullOrWhiteSpace($adminKey)) {
    throw 'DAILY_READER_ADMIN_API_KEY is not set'
}

$response = Invoke-RestMethod -Method Post -Uri $url -Headers @{
    'x-admin-api-key' = $adminKey
    'Content-Type'    = 'application/json'
} -Body $body
$response | ConvertTo-Json -Compress
