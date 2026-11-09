param(
    [ValidateSet('Install', 'Update', 'Status', 'Remove')][string]$Action = 'Status',
    [ValidatePattern('^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')][string]$At = '03:00',
    [ValidateRange(1, 30)][int]$DaysInterval = 1
)
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'backup-common.ps1')

$taskName = 'OrgProekt-Backup'
$legacyTaskName = 'OrgProekt-Daily-Backup'
$runner = Join-Path $PSScriptRoot 'backup-scheduled.ps1'
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$legacy = Get-ScheduledTask -TaskName $legacyTaskName -ErrorAction SilentlyContinue
function Assert-OwnedBackupTask($Task) {
    if (@($Task.Actions).Count -ne 1) { throw 'Задача резервного копирования содержит посторонние действия.' }
    $actualAction = @($Task.Actions)[0]
    $expectedArguments = '-NoLogo -NoProfile -NonInteractive -File "' + $runner + '"'
    if ([IO.Path]::GetFileName($actualAction.Execute) -notin @('pwsh.exe', 'powershell.exe') -or
        $actualAction.Arguments -ne $expectedArguments -or
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
        $powershell = (Get-Process -Id $PID).Path
        if ([IO.Path]::GetFileName($powershell) -notin @('pwsh.exe', 'powershell.exe')) {
            throw 'Установите расписание из Windows PowerShell или PowerShell 7.'
        }
        $taskAction = New-ScheduledTaskAction -Execute $powershell `
            -Argument ('-NoLogo -NoProfile -NonInteractive -File "' + $runner + '"') `
            -WorkingDirectory $BackupRoot
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
        Set-ScheduledTask -TaskName $taskName -Trigger $trigger | Out-Null
        Write-Output "Расписание обновлено: $taskName; интервал $DaysInterval дн. в $At по местному времени."
    }
    'Status' {
        if ($null -eq $existing) { Write-Output 'Расписание не установлено.'; break }
        $info = Get-ScheduledTaskInfo -TaskName $taskName
        [pscustomobject]@{
            TaskName = $taskName
            State = $existing.State.ToString()
            User = $existing.Principal.UserId
            DaysInterval = @($existing.Triggers)[0].DaysInterval
            NextRunTime = $info.NextRunTime
            LastRunTime = $info.LastRunTime
            LastTaskResult = $info.LastTaskResult
        } | Format-List
        $statusPath = Join-Path $BackupRoot '.local\backup-operations\daily-status.json'
        if (Test-Path -LiteralPath $statusPath -PathType Leaf) {
            Get-Content -LiteralPath $statusPath -Raw
        }
    }
    'Remove' {
        if ($null -eq $existing) { Write-Output 'Расписание уже отсутствует.'; break }
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Output 'Расписание удалено. Созданные архивы и ключ сохранены.'
    }
}
