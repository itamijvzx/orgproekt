$ErrorActionPreference = 'Stop'
$BackupRoot = Split-Path $PSScriptRoot -Parent
$BackupDocker = Join-Path $PSScriptRoot 'docker.ps1'

function Invoke-BackupDocker {
    & $BackupDocker @args
    if ($LASTEXITCODE -ne 0) { throw 'Команда Docker не завершена. Закрытые диагностические данные не выводятся.' }
}

function Get-BackupAbsolutePath([string]$Path) {
    if ([IO.Path]::IsPathRooted($Path)) { return [IO.Path]::GetFullPath($Path) }
    return [IO.Path]::GetFullPath((Join-Path $BackupRoot $Path))
}

function Set-BackupPrivateDirectory([string]$Path) {
    $fullPath = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $projectPath = $BackupRoot.TrimEnd('\')
    if ($fullPath -eq [IO.Path]::GetPathRoot($Path).TrimEnd('\') -or $fullPath -eq $projectPath -or
        $projectPath.StartsWith($fullPath + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $fullPath -eq (Join-Path $BackupRoot '.local')) {
        throw 'Для ключей/копий нужна отдельная папка; ACL диска, профиля, проекта и общей .local менять запрещено.'
    }
    if (-not (Test-Path -LiteralPath $Path)) { New-Item -ItemType Directory -Path $Path | Out-Null }
    $directory = Get-Item -LiteralPath $Path
    if ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Приватная папка не может быть ссылкой.' }
    $current = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $allowed = @($current.Value, 'S-1-5-18', 'S-1-5-32-544')
    # icacls changes only the DACL; Set-Acl can unnecessarily request SeSecurityPrivilege.
    & icacls.exe $Path '/inheritance:r' '/grant:r' "*$($current.Value):(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось ограничить Windows ACL приватной папки.' }
    $acl = Get-Acl -LiteralPath $Path
    foreach ($rule in $acl.Access) {
        $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        if ($sid -notin $allowed) {
            & icacls.exe $Path '/remove' "*$sid" | Out-Null
            if ($LASTEXITCODE -ne 0) { throw 'Не удалось убрать посторонний доступ к приватной папке.' }
        }
    }
}

function Initialize-BackupTools([switch]$Rebuild) {
    $appImage = (Invoke-BackupDocker image inspect orgproekt-backend:local --format '{{.Id}}').Trim()
    $labels = & $BackupDocker image inspect orgproekt-backup:local --format '{{json .Config.Labels}}' 2>$null
    $builtFrom = if ($LASTEXITCODE -eq 0 -and $labels) { ($labels | ConvertFrom-Json).'orgproekt.base-image-id' }
    if ($Rebuild -or $LASTEXITCODE -ne 0 -or $builtFrom -ne $appImage) {
        Invoke-BackupDocker build --build-arg "APP_IMAGE_ID=$appImage" -f (Join-Path $BackupRoot 'tools/backup/Dockerfile') -t orgproekt-backup:local $BackupRoot
    }
}

function Get-BackupContainerImage([string]$Service) {
    $id = Invoke-BackupDocker compose -p orgproekt -f (Join-Path $BackupRoot 'compose.yml') ps -q $Service
    if (-not $id) { throw "Основной сервис $Service не запущен." }
    return (Invoke-BackupDocker inspect --format '{{.Image}}' $id).Trim()
}

function Test-ParserImage([string]$Image) {
    & $BackupDocker run --rm --network none --entrypoint /usr/bin/test $Image -f /app/app/parser_service.py 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}
