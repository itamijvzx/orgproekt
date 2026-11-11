param(
    [Parameter(Mandatory=$true)][string]$Archive,
    [Parameter(Mandatory=$true)][string]$KeyFile,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-fA-F0-9]{64}$')][string]$ExpectedSha256,
    [switch]$HttpsTraining
)
. (Join-Path $PSScriptRoot 'backup-common.ps1')
$archivePath = Get-BackupAbsolutePath $Archive
$keyPath = Get-BackupAbsolutePath $KeyFile
if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf) -or -not (Test-Path -LiteralPath $keyPath -PathType Leaf)) { throw 'Не найден архив или отдельный ключ.' }
if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash -ne $ExpectedSha256) { throw 'SHA-256 архива не совпадает с доверенной квитанцией.' }
Initialize-BackupTools
$project = 'orgproekt-restore-' + [DateTime]::UtcNow.ToString('yyyyMMddHHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$privateParent = Join-Path $BackupRoot '.local/restore-labs'
if ($HttpsTraining) {
    $privateParent = Join-Path $BackupRoot '.local/https-training'
    if ((Test-Path -LiteralPath $privateParent) -and @(Get-ChildItem -LiteralPath $privateParent -Directory -Filter 'orgproekt-restore-*').Count) {
        throw 'Учебная копия уже создана. Используйте restore-lab-control.ps1 -HttpsTraining -Action Start.'
    }
}
Set-BackupPrivateDirectory $privateParent
$privatePath = Join-Path $privateParent $project
$archiveParent = Split-Path $archivePath -Parent
$keyParent = Split-Path $keyPath -Parent
$mounts = @('-v', "${archiveParent}:/backup:ro", '-v', "${keyParent}:/keys:ro")
$limits = @('--cap-drop','ALL','--security-opt','no-new-privileges','--read-only','--tmpfs','/work:rw,noexec,nosuid,size=1g','--tmpfs','/tmp:rw,noexec,nosuid,size=32m')
$archiveArg = '/backup/' + [IO.Path]::GetFileName($archivePath)
$keyArg = '/keys/' + [IO.Path]::GetFileName($keyPath)
Invoke-BackupDocker run --rm --network none @limits @mounts -v "${privateParent}:/labs" orgproekt-backup:local prepare --archive $archiveArg --key $keyArg --sha256 $ExpectedSha256 --project $project --private "/labs/$project"
Set-BackupPrivateDirectory $privatePath
$envFile = Join-Path $privatePath 'restore.env'
$manifest = Get-Content -LiteralPath (Join-Path $privatePath 'manifest.json') -Raw | ConvertFrom-Json
$caddyConfig = (Invoke-BackupDocker run --rm --network none --entrypoint cat $manifest.web_image /etc/caddy/Caddyfile) -join "`n"
$newSite = 'http://orgproekt.localhost:8080 {'
if ($caddyConfig.Contains($newSite)) {
    # Remove only the legacy localhost redirect block from the restored site.
    $legacy = [regex]::new('(?ms)^http://localhost:8080, http://127\.0\.0\.1:8080 \{\r?\n.*?^\}\r?\n')
    $cleaned = $legacy.Replace($caddyConfig, '', 1)
    if ($cleaned -eq $caddyConfig) { throw 'Неизвестная конфигурация перенаправления Caddy.' }
    $caddyConfig = $cleaned
    $sourceListener = $newSite
} elseif ($caddyConfig.Contains(':8080 {')) {
    $sourceListener = ':8080 {'
} else {
    throw 'Неизвестная конфигурация Caddy; автоматическая смена порта запрещена.'
}
$listener = ':8082 {'
if ($HttpsTraining) {
    $issuerScript = Join-Path $BackupRoot 'tools/https-lab/certificate.py'
    Invoke-BackupDocker run --rm --network none @limits --entrypoint python -v "${issuerScript}:/issue.py:ro" -v "${privatePath}:/private" orgproekt-backup:local /issue.py /private/tls
    $certPath = Join-Path $privatePath 'tls/issuer.cer'
    $cert = [Security.Cryptography.X509Certificates.X509Certificate2]::new($certPath)
    [ordered]@{ project=$project; issuer_sha256=(Get-FileHash -LiteralPath $certPath).Hash; thumbprint=$cert.Thumbprint } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $privatePath 'tls-identity.json') -Encoding ascii
    $listener = "https://orgproekt-lab.localhost:8443 {`n    tls /run/lab/server.pem /run/lab/server.key"
}
[IO.File]::WriteAllText((Join-Path $privatePath 'Caddyfile'), $caddyConfig.Replace($sourceListener, $listener), [Text.UTF8Encoding]::new($false))
$env:RESTORE_PRIVATE_DIR = $privatePath.Replace('\','/')
$composeArgs = @('compose','--env-file',$envFile,'-p',$project,'-f',(Join-Path $BackupRoot 'compose.restore.yml'))
if ($HttpsTraining) { $composeArgs += @('-f',(Join-Path $BackupRoot 'compose.https-training.yml')) }
$parserEnabled = $HttpsTraining -and (Test-ParserImage $manifest.app_image)
if ($parserEnabled) { $composeArgs += @('-f',(Join-Path $BackupRoot 'compose.https-training-parser.yml')) }
try {
    Invoke-BackupDocker @composeArgs up -d --build --wait db
    Invoke-BackupDocker run --rm --network "${project}_isolated" @limits @mounts -v "${privatePath}:/private" orgproekt-backup:local restore --archive $archiveArg --key $keyArg --sha256 $ExpectedSha256
    if ($HttpsTraining) {
        if ($parserEnabled) { Invoke-BackupDocker @composeArgs up -d --wait --wait-timeout 120 parser }
        Invoke-BackupDocker @composeArgs up -d --wait --wait-timeout 120 api caddy worker scheduler
        if ($parserEnabled) { [IO.File]::WriteAllText((Join-Path $privatePath 'parser-enabled'), '1') }
        Write-Output 'Учебная копия: https://orgproekt-lab.localhost:8443/. Для доверия Windows выполните restore-lab-control.ps1 -HttpsTraining -Action Trust.'
    } else {
        Invoke-BackupDocker @composeArgs up -d --wait api caddy
        Write-Output 'Восстановление проверено. Изолированный стенд: http://localhost:8082/'
        Write-Output 'Открывайте в отдельном профиле/приватном окне. Worker/scheduler и внешние вызовы выключены.'
    }
    Write-Output "Приватная папка стенда: $privatePath"
} catch {
    & $BackupDocker @composeArgs stop | Out-Null
    throw 'Восстановление не завершено; изолированные сервисы остановлены. Основной стенд не изменялся.'
} finally {
    Remove-Item Env:RESTORE_PRIVATE_DIR -ErrorAction SilentlyContinue
}
