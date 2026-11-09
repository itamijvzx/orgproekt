param([switch]$FullApi)
. (Join-Path $PSScriptRoot 'backup-common.ps1')
$labs = @(Get-ChildItem -LiteralPath (Join-Path $BackupRoot '.local/https-training') -Directory -Filter 'orgproekt-restore-*')
if ($labs.Count -ne 1) { throw 'Configure the trusted HTTPS training lab first.' }
$project = 'orgproekt-passkeys-qa-' + [Guid]::NewGuid().ToString('N').Substring(0,12)
$parent = Join-Path $BackupRoot '.local'
$qa = Join-Path $parent $project
Set-BackupPrivateDirectory $qa
$env:PASSKEY_QA_DIR = $qa.Replace('\','/')
$env:TRAINING_TLS_DIR = (Join-Path $labs[0].FullName 'tls').Replace('\','/')
$files = @('compose','--env-file',(Join-Path $qa '.env'),'-p',$project,'-f',(Join-Path $BackupRoot 'compose.yml'),
    '-f',(Join-Path $BackupRoot 'compose.test.yml'),'-f',(Join-Path $BackupRoot 'compose.passkeys-qa.yml'))
try {
    New-Item -ItemType Directory -Path (Join-Path $qa 'accounts') | Out-Null
    $caddy = [IO.File]::ReadAllText((Join-Path $labs[0].FullName 'Caddyfile'))
    if (-not $caddy.Contains('https://orgproekt-lab.localhost:8443 {')) {throw 'Unexpected training origin'}
    [IO.File]::WriteAllText((Join-Path $qa 'Caddyfile'), $caddy.Replace('orgproekt-lab.localhost:8443','orgproekt-lab.localhost:8444'))
    $setup = Join-Path $PSScriptRoot 'setup_env.py'
    Invoke-BackupDocker run --rm --network none -v "${qa}:/fixture" -v "${setup}:/fixture/scripts/setup_env.py:ro" python:3.13-slim@sha256:b3575dcf816cdacc5292df77a6e3e59b8941631078373a045efa2ac1e9c5c3b2 python /fixture/scripts/setup_env.py
    Invoke-BackupDocker @files up -d --no-build --pull never --wait --wait-timeout 120 api caddy
    $pytest = @('pytest', '-q', '--tb=short', '-p', 'no:cacheprovider')
    if (-not $FullApi) {
        $pytest += @('tests/test_auth.py', 'tests/test_passkeys.py', 'tests/test_api_contracts.py')
    }
    Invoke-BackupDocker @files --profile tests run --rm --no-deps tests @pytest
    Invoke-BackupDocker @files --profile tools run --rm --no-deps tools python -m app.cli seed
    $env:PLAYWRIGHT_BASE_URL='https://orgproekt-lab.localhost:8444'
    $env:PASSKEY_QA_PROJECT=$project
    Push-Location (Join-Path $BackupRoot 'frontend')
    try {
        & node node_modules/@playwright/test/cli.js test e2e/passkeys.spec.ts
        if ($LASTEXITCODE -ne 0) {throw 'Browser passkey check failed'}
    } finally { Pop-Location }
} finally {
    if ($project -notmatch '^orgproekt-passkeys-qa-[a-f0-9]{12}$' -or (Split-Path $qa -Parent) -ne $parent) {throw 'Invalid temporary project'}
    if (Test-Path -LiteralPath (Join-Path $qa '.env')) { Invoke-BackupDocker @files down --volumes }
    $absolute = (Get-Item -LiteralPath $qa).FullName
    if ($absolute -ne (Join-Path $parent $project) -or ((Get-Item -LiteralPath $absolute).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Unsafe QA directory' }
    Remove-Item -LiteralPath $absolute -Recurse -Force
    Remove-Item Env:PASSKEY_QA_DIR, Env:TRAINING_TLS_DIR, Env:PASSKEY_QA_PROJECT, Env:PLAYWRIGHT_BASE_URL -ErrorAction SilentlyContinue
}
