param([Parameter(Mandatory=$true)][string]$PrivateDirectory)
. (Join-Path $PSScriptRoot 'backup-common.ps1')
$privatePath = Get-BackupAbsolutePath $PrivateDirectory
$labParent = Join-Path $BackupRoot '.local/restore-labs'
if (-not $privatePath.StartsWith($labParent + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Разрешена только приватная папка изолированного стенда.' }
$guard = Get-Content -LiteralPath (Join-Path $privatePath 'restore.guard.json') -Raw | ConvertFrom-Json
$project = $guard.project
if ($project -notmatch '^orgproekt-restore-[0-9]{14}-[a-f0-9]{8}$' -or (Split-Path $privatePath -Leaf) -ne $project) { throw 'Некорректная метка изолированного стенда.' }
$qaPath = Join-Path $privatePath 'qa-accounts.json'
if (Test-Path -LiteralPath $qaPath) { throw 'Остался файл прежнего QA. Удалите только qa-accounts.json этой тестовой папки перед новым запуском.' }
$env:RESTORE_PRIVATE_DIR = $privatePath.Replace('\','/')
try {
    Invoke-BackupDocker run --rm --network "${project}_isolated" --cap-drop ALL --security-opt no-new-privileges `
        --entrypoint python -v "${privatePath}:/private" orgproekt-backup:local -m ops.restore_check
    Invoke-BackupDocker compose --env-file (Join-Path $privatePath 'restore.env') -p $project -f (Join-Path $BackupRoot 'compose.restore.yml') --profile qa run --rm --no-deps browser
} finally {
    # Exact fixed filename within the validated test folder; never touch source credentials.
    if (Test-Path -LiteralPath $qaPath) { Remove-Item -LiteralPath $qaPath -Force }
    Remove-Item Env:RESTORE_PRIVATE_DIR -ErrorAction SilentlyContinue
}
