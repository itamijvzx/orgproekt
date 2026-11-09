param(
    [ValidateSet('Install', 'Update', 'Repair', 'Status', 'Remove')][string]$Action = 'Status',
    [ValidatePattern('^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')][string]$At = '03:00',
    [ValidateRange(1, 30)][int]$DaysInterval = 1
)
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'backup-common.ps1')

$taskName = 'OrgProekt-Backup'
$legacyTaskName = 'OrgProekt-Daily-Backup'
$runner = Join-Path $PSScriptRoot 'backup-scheduled.ps1'
$stablePowerShell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$taskArguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File "' + $runner + '"'
$legacyArguments = '-NoLogo -NoProfile -NonInteractive -File "' + $runner + '"'
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$legacy = Get-ScheduledTask -TaskName $legacyTaskName -ErrorAction SilentlyContinue
function New-StableBackupAction {
    if (-not (Test-Path -LiteralPath $stablePowerShell -PathType Leaf)) {
        throw 'Стандартный Windows PowerShell не найден; расписание не изменено.'
    }
    return New-ScheduledTaskAction -Execute $stablePowerShell -Argument $taskArguments -WorkingDirectory $BackupRoot
}

function Assert-OwnedBackupTask($Task) {
    if (@($Task.Actions).Count -ne 1) { throw 'Задача резервного копирования содержит посторонние действия.' }
    $actualAction = @($Task.Actions)[0]
    if ([IO.Path]::GetFileName($actualAction.Execute) -notin @('pwsh.exe', 'powershell.exe') -or
        $actualAction.Arguments -notin @($taskArguments, $legacyArguments) -or
        $actualAction.WorkingDirectory -ne $BackupRoot -or
        ($Task.Principal.UserId -ine [Security.Principal.WindowsIdentity]::GetCurrent().Name -and
         $Task.Principal.UserId -ine ([Security.Principal.WindowsIdentity]::GetCurrent().Name.Split('\')[-1]))) {
        throw 'Задача с таким именем уже существует и указывает на другую команду.'
    }
}
if ($null -ne $existing) { Assert-OwnedBackupTask $existing }

switch ($Action) {
    'Install' {
        if ($null -ne $existing) { throw 'Расписание уже существует. Для просмотра используйте -Action Status.' }
        if (-not (Test-Path -LiteralPath (Join-Path $BackupRoot '.local\backup-keys\orgproekt.agekey') -PathType Leaf)) {
            throw 'Ключ резервного копирования не найден.'
        }
        $taskAction = New-StableBackupAction
        $time = [DateTime]::Today.Add([TimeSpan]::ParseExact($At, 'hh\:mm', [Globalization.CultureInfo]::InvariantCulture))
        $trigger = New-ScheduledTaskTrigger -Daily -DaysInterval $DaysInterval -At $time
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit (New-TimeSpan -Hours 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
        if ($null -ne $legacy) { Assert-OwnedBackupTask $legacy }
        Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $trigger `
            -Settings $settings -Principal $principal -Description 'Локальная зашифрованная копия OrgProekt; запуск при входе пользователя.' | Out-Null
        if ($null -ne $legacy) {
            try { Unregister-ScheduledTask -TaskName $legacyTaskName -Confirm:$false }
            catch {
                Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
                throw 'Не удалось заменить старое расписание; оно сохранено.'
            }
        }
        Write-Output "Задание установлено: $taskName; интервал $DaysInterval дн. в $At по местному времени, когда пользователь вошёл в Windows."
    }
    'Update' {
        if ($null -eq $existing) { throw 'Расписание не установлено. Используйте -Action Install.' }
        $time = [DateTime]::Today.Add([TimeSpan]::ParseExact($At, 'hh\:mm', [Globalization.CultureInfo]::InvariantCulture))
        $trigger = New-ScheduledTaskTrigger -Daily -DaysInterval $DaysInterval -At $time
        Set-ScheduledTask -TaskName $taskName -Action (New-StableBackupAction) -Trigger $trigger | Out-Null
        Write-Output "Расписание обновлено: $taskName; интервал $DaysInterval дн. в $At по местному времени."
    }
    'Repair' {
        if ($null -eq $existing) { throw 'Расписание не установлено. Используйте -Action Install.' }
        Set-ScheduledTask -TaskName $taskName -Action (New-StableBackupAction) | Out-Null
        Write-Output "Команда запуска восстановлена: $taskName; время и интервал сохранены."
    }
    'Status' {
        if ($null -eq $existing) { Write-Output 'Расписание не установлено.'; break }
        $info = Get-ScheduledTaskInfo -TaskName $taskName
        $launcher = @($existing.Actions)[0].Execute
        $statusPath = Join-Path $BackupRoot '.local\backup-operations\daily-status.json'
        $backupStatus = $null
        $statusFile = $null
        if (Test-Path -LiteralPath $statusPath -PathType Leaf) {
            $statusFile = Get-Item -LiteralPath $statusPath
            $backupStatus = Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
        }
        $interval = [int]@($existing.Triggers)[0].DaysInterval
        $launcherExists = Test-Path -LiteralPath $launcher -PathType Leaf
        $healthy = $launcherExists -and $info.LastTaskResult -eq 0 -and
            $backupStatus -and $backupStatus.result -eq 'verified' -and
            $statusFile.LastWriteTime -ge $info.LastRunTime -and
            $statusFile.LastWriteTime -ge (Get-Date).AddDays(-($interval + 1))
        [pscustomobject]@{
            TaskName = $taskName
            State = $existing.State.ToString()
            User = $existing.Principal.UserId
            DaysInterval = $interval
            NextRunTime = $info.NextRunTime
            LastRunTime = $info.LastRunTime
            LastTaskResult = $info.LastTaskResult
            LauncherExists = $launcherExists
            LastVerifiedBackup = if ($backupStatus -and $backupStatus.result -eq 'verified') { $backupStatus.finished_at_utc } else { $null }
            Health = if ($healthy) { 'verified' } else { 'needs_attention' }
        } | Format-List
        if (-not $healthy) { Write-Warning 'Резервное копирование требует проверки: смотрите LastTaskResult и дату последней копии.' }
    }
    'Remove' {
        if ($null -eq $existing) { Write-Output 'Расписание уже отсутствует.'; break }
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Output 'Расписание удалено. Созданные архивы и ключ сохранены.'
    }
}
