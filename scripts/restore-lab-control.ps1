param(
    [Parameter(Mandatory=$true,ParameterSetName='Restore')][string]$PrivateDirectory,
    [Parameter(Mandatory=$true,ParameterSetName='Training')][switch]$HttpsTraining,
    [ValidateSet('Status','Start','Stop','Trust','Untrust')][string]$Action = 'Status'
)
. (Join-Path $PSScriptRoot 'backup-common.ps1')
$labParent = Join-Path $BackupRoot '.local/restore-labs'
if ($HttpsTraining) {
    $labParent = Join-Path $BackupRoot '.local/https-training'
    $labs = @(Get-ChildItem -LiteralPath $labParent -Directory -Filter 'orgproekt-restore-*')
    if ($labs.Count -ne 1) { throw 'Требуется одна существующая учебная HTTPS-копия.' }
    $privatePath = $labs[0].FullName
} else {
    if ($Action -in 'Trust','Untrust') { throw 'Эта операция доступна только с -HttpsTraining.' }
    $privatePath = Get-BackupAbsolutePath $PrivateDirectory
}
if ((Split-Path $privatePath -Parent) -ne $labParent) { throw 'Папка вне каталога созданных стендов.' }
$guard = Get-Content -LiteralPath (Join-Path $privatePath 'restore.guard.json') -Raw | ConvertFrom-Json
$project = $guard.project
if ($project -notmatch '^orgproekt-restore-[0-9]{14}-[a-f0-9]{8}$' -or (Split-Path $privatePath -Leaf) -ne $project) { throw 'Некорректная метка изолированного стенда.' }
if ($Action -in 'Start','Trust') {
    $result = Get-Content -LiteralPath (Join-Path $privatePath 'restore-result.json') -Raw | ConvertFrom-Json
    if ($result.result -ne 'verified' -or $result.project -ne $project) { throw 'Стенд не прошёл проверку восстановления.' }
}
if ($Action -in 'Trust','Untrust') {
    $issuer = Join-Path $privatePath 'tls/issuer.cer'
    $cert = [Security.Cryptography.X509Certificates.X509Certificate2]::new($issuer)
    $identity = Get-Content -LiteralPath (Join-Path $privatePath 'tls-identity.json') -Raw | ConvertFrom-Json
    if ($identity.project -ne $project -or $identity.issuer_sha256 -ne (Get-FileHash -LiteralPath $issuer).Hash -or
        $identity.thumbprint -ne $cert.Thumbprint -or $cert.Subject -ne 'CN=OrgProekt HTTPS training CA' -or $cert.HasPrivateKey) {
        throw 'Публичный сертификат не совпал с паспортом этого стенда.'
    }
    $store = [Security.Cryptography.X509Certificates.X509Store]::new('Root','CurrentUser')
    try {
        $store.Open('ReadWrite')
        if ($Action -eq 'Trust' -and -not $store.Certificates.Contains($cert)) { $store.Add($cert) }
        if ($Action -eq 'Untrust' -and $store.Certificates.Contains($cert)) { $store.Remove($cert) }
        # Re-open the system store after the Windows confirmation, without provider path caching.
        $store.Close()
        $store.Open('ReadOnly')
        $trusted = $store.Certificates.Contains($cert)
        if ($trusted -ne ($Action -eq 'Trust')) { throw 'Windows не подтвердила изменение доверия.' }
        Write-Output "Доверие текущего пользователя: $trusted. Отпечаток $($cert.Thumbprint)"
    } finally { $store.Close() }
    return
}
$env:RESTORE_PRIVATE_DIR = $privatePath.Replace('\','/')
$composeArgs = @('compose','--env-file',(Join-Path $privatePath 'restore.env'),'-p',$project,'-f',(Join-Path $BackupRoot 'compose.restore.yml'))
if ($HttpsTraining) {
    $composeArgs += @('-f',(Join-Path $BackupRoot 'compose.https-training.yml'))
    $release = Join-Path $privatePath 'training-release.env'
    if (Test-Path -LiteralPath $release) { $composeArgs += @('--env-file',$release) }
}
try {
    switch ($Action) {
        'Start' {
            $services = @('db','api','caddy')
            if ($HttpsTraining) { $services += @('rabbitmq','worker','scheduler') }
            Invoke-BackupDocker @composeArgs up -d --wait --wait-timeout 120 @services
        }
        'Stop' { Invoke-BackupDocker @composeArgs stop }
        'Status' { Invoke-BackupDocker @composeArgs ps }
    }
} finally { Remove-Item Env:RESTORE_PRIVATE_DIR -ErrorAction SilentlyContinue }
