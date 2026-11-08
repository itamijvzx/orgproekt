Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'backup-common.ps1')

$statusDirectory = Join-Path $BackupRoot '.local\backup-operations'
$statusPath = Join-Path $statusDirectory 'daily-status.json'
$keyPath = Join-Path $BackupRoot '.local\backup-keys\orgproekt.agekey'
$archiveDirectory = Join-Path $BackupRoot 'backups\scheduled'
$startedAt = [DateTimeOffset]::UtcNow.ToString('o')
$exitCode = 0

Set-BackupPrivateDirectory $statusDirectory
try {
    $lines = @(& (Join-Path $PSScriptRoot 'backup.ps1') -KeyFile $keyPath -OutputDirectory $archiveDirectory)
    $receipt = $null
    foreach ($line in $lines) {
        if ($line -is [string] -and $line.TrimStart().StartsWith('{')) {
            try {
                $candidate = $line | ConvertFrom-Json -ErrorAction Stop
                if ($candidate.operation -eq 'backup' -and $candidate.result -eq 'verified') { $receipt = $candidate }
            } catch { }
        }
    }
    if ($null -eq $receipt -or $receipt.file -notmatch '^orgproekt-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\.tar\.age$' -or
        $receipt.sha256 -notmatch '^[0-9a-f]{64}$') {
        throw 'Результат резервного копирования не подтверждён.'
    }
    $archivePath = Join-Path $archiveDirectory $receipt.file
    $receiptPath = "$archivePath.receipt.json"
    if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $receiptPath -PathType Leaf) -or
        (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash -ine $receipt.sha256) {
        throw 'Созданная копия или квитанция не прошла проверку.'
    }
    # Retention affects only complete archives created in this separate scheduled directory.
    $completeCopies = @(foreach ($file in (Get-ChildItem -LiteralPath $archiveDirectory -File -Filter '*.tar.age')) {
        if ($file.Name -notmatch '^orgproekt-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\.tar\.age$' -or
            ($file.Attributes -band [IO.FileAttributes]::ReparsePoint)) { continue }
        $pairedReceipt = "$($file.FullName).receipt.json"
        if (Test-Path -LiteralPath $pairedReceipt -PathType Leaf) { $file }
    })
    foreach ($oldCopy in ($completeCopies | Sort-Object Name -Descending | Select-Object -Skip 3)) {
        Remove-Item -LiteralPath $oldCopy.FullName -Force
        Remove-Item -LiteralPath "$($oldCopy.FullName).receipt.json" -Force
    }
    $status = [ordered]@{
        result = 'verified'
        started_at_utc = $startedAt
        finished_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
        archive = $receipt.file
        sha256 = $receipt.sha256
    }
} catch {
    $exitCode = 1
    $status = [ordered]@{
        result = 'failed'
        started_at_utc = $startedAt
        finished_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
        error_code = 'backup_failed'
    }
}

$temporaryPath = Join-Path $statusDirectory ('.daily-status-' + [Guid]::NewGuid().ToString('N') + '.tmp')
try {
    [IO.File]::WriteAllText($temporaryPath, ($status | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporaryPath -Destination $statusPath -Force
} finally {
    if (Test-Path -LiteralPath $temporaryPath) { Remove-Item -LiteralPath $temporaryPath -Force }
}
if ($exitCode -ne 0) { throw 'Резервное копирование не удалось. Проверьте .local\backup-operations\daily-status.json.' }
Write-Output "Резервная копия проверена: $($status.archive)"
