$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (Test-Path -LiteralPath '.env') {
    Write-Output '.env уже существует: секреты сохранены, файл не изменён.'
    exit 0
}
function New-RandomSecret {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return [Convert]::ToBase64String($bytes).Replace('+','-').Replace('/','_')
}
$dbAdmin = (New-RandomSecret).TrimEnd('=')
$dbApp = (New-RandomSecret).TrimEnd('=')
$rabbit = (New-RandomSecret).TrimEnd('=')
$mfaKey = New-RandomSecret
$auditKey = New-RandomSecret
$content = @"
APP_MODE=development
LOCAL_DEMO_MODE=true
APP_ORIGIN=http://orgproekt.localhost:8080
DISPLAY_TIMEZONE=Asia/Almaty
DB_ADMIN_PASSWORD=$dbAdmin
DB_APP_PASSWORD=$dbApp
RABBITMQ_PASSWORD=$rabbit
MFA_ENCRYPTION_KEY=$mfaKey
AUDIT_HMAC_KEY=$auditKey
GEMINI_API_KEY=
GEMINI_MODEL=
GEMINI_ENABLED=false
"@
[IO.File]::WriteAllText((Join-Path (Get-Location) '.env'), $content, [Text.UTF8Encoding]::new($false))
New-Item -ItemType Directory -Force -Path '.local' | Out-Null
Write-Output 'Созданы локальные секреты .env. Не публикуйте .env и .local. Следующий шаг: docker compose up -d --build.'
