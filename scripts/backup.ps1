param(
    [Parameter(Mandatory=$true)][string]$KeyFile,
    [string]$OutputDirectory = 'backups',
    [switch]$CreateKey,
    [switch]$BuildTools
)
. (Join-Path $PSScriptRoot 'backup-common.ps1')
$keyPath = Get-BackupAbsolutePath $KeyFile
$outputPath = Get-BackupAbsolutePath $OutputDirectory
$keyParent = Split-Path $keyPath -Parent
if ([IO.Path]::GetExtension($keyPath) -ne '.agekey' -or $keyParent -eq $BackupRoot -or
    $keyPath.StartsWith($outputPath.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or
    $outputPath.StartsWith($keyParent.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or $keyParent -eq $outputPath) {
    throw 'Ключ .agekey и архивы должны находиться в разных отдельных папках.'
}
Set-BackupPrivateDirectory $outputPath
Set-BackupPrivateDirectory $keyParent
Initialize-BackupTools -Rebuild:$BuildTools
if ($CreateKey) {
    if (Test-Path -LiteralPath $keyPath) { throw 'Ключ уже существует. Уберите -CreateKey; существующий ключ не заменяется.' }
    Invoke-BackupDocker run --rm --network none --cap-drop ALL --security-opt no-new-privileges -v "${keyParent}:/keys" orgproekt-backup:local keygen --key "/keys/$([IO.Path]::GetFileName($keyPath))"
}
if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf)) { throw 'Файл ключа не найден. Для первого запуска используйте -CreateKey.' }
$appImage = Get-BackupContainerImage 'api'
$webImage = Get-BackupContainerImage 'caddy'
$result = Invoke-BackupDocker run --rm --network orgproekt_internal --cap-drop ALL --security-opt no-new-privileges --read-only `
    --tmpfs '/work:rw,noexec,nosuid,size=1g' --tmpfs '/tmp:rw,noexec,nosuid,size=32m' `
    -v "${BackupRoot}:/source:ro" -v "${outputPath}:/backup" -v "${keyParent}:/keys:ro" `
    orgproekt-backup:local create --key "/keys/$([IO.Path]::GetFileName($keyPath))" --app-image $appImage --web-image $webImage
$receipt = $result | ConvertFrom-Json
Write-Output $result
Write-Output "Зашифрованная копия: $(Join-Path $outputPath $receipt.file)"
Write-Output 'Сохраните архив и квитанцию отдельно от recovery-копии ключа. Основная база не изменялась.'
