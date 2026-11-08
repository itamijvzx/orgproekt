param([switch]$Build)
. (Join-Path $PSScriptRoot 'backup-common.ps1')
$labs = @(Get-ChildItem -LiteralPath (Join-Path $BackupRoot '.local/https-training') -Directory -Filter 'orgproekt-restore-*')
if ($labs.Count -ne 1) { throw 'Expected one HTTPS training lab.' }
$lab = $labs[0].FullName
$project = $labs[0].Name
$guard = Get-Content -LiteralPath (Join-Path $lab 'restore-result.json') -Raw | ConvertFrom-Json
if ($project -notmatch '^orgproekt-restore-[0-9]{14}-[a-f0-9]{8}$' -or $guard.project -ne $project -or $guard.result -ne 'verified') { throw 'Invalid training guard' }
if ($Build) {
    Invoke-BackupDocker build -t orgproekt-training-api:local -f (Join-Path $BackupRoot 'backend/Dockerfile') $BackupRoot
    Invoke-BackupDocker build -t orgproekt-training-web:local -f (Join-Path $BackupRoot 'frontend/Dockerfile') $BackupRoot
}
$api = (Invoke-BackupDocker image inspect orgproekt-training-api:local --format '{{.Id}}').Trim()
$web = (Invoke-BackupDocker image inspect orgproekt-training-web:local --format '{{.Id}}').Trim()
$envFile = Join-Path $lab 'restore.env'
$env:RESTORE_PRIVATE_DIR = $lab.Replace('\','/')
$base = @('compose','--env-file',$envFile,'-p',$project,'-f',(Join-Path $BackupRoot 'compose.restore.yml'),'-f',(Join-Path $BackupRoot 'compose.https-training.yml'))
$release = Join-Path $lab 'training-release.env'
$previous = if (Test-Path -LiteralPath $release) { [IO.File]::ReadAllText($release) }
$oldArgs = $base
if ($previous) { $oldArgs += @('--env-file',$release) }
$network = "${project}_isolated"
$keyDir = Join-Path $BackupRoot '.local/backup-keys'
$backupDir = Join-Path $lab 'upgrade-backups'
Set-BackupPrivateDirectory $backupDir
$limits = @('--rm','--network',$network,'--cap-drop','ALL','--security-opt','no-new-privileges','--read-only','--tmpfs','/work:rw,noexec,nosuid,size=1g','--tmpfs','/tmp:rw,noexec,nosuid,size=32m')
$sourceMounts = @('-v',"${envFile}:/source/.env:ro")
foreach ($entry in @{'demo-accounts.json'='demo-accounts.json';'workflow-accounts.json'='workflow-accounts.json';'report-reviewer.json'='report-reviewer/workflow-accounts.json';'audit-seal.json'='audit-seal.json'}.GetEnumerator()) {
    $file = Join-Path $lab $entry.Key
    if (Test-Path -LiteralPath $file -PathType Leaf) { $sourceMounts += @('-v',"${file}:/source/.local/$($entry.Value):ro") }
}
try {
    $oldApi = (Invoke-BackupDocker inspect --format '{{.Image}}' "${project}-api-1").Trim()
    $oldWeb = (Invoke-BackupDocker inspect --format '{{.Image}}' "${project}-caddy-1").Trim()
    if ($previous -and $oldApi -eq $api -and $oldWeb -eq $web) {
        Write-Output 'Selected passkey release is already installed.'
        return
    }
    try {
        Invoke-BackupDocker @oldArgs stop caddy api scheduler worker
        Invoke-BackupDocker run @limits @sourceMounts -v "${backupDir}:/backup" -v "${keyDir}:/keys:ro" orgproekt-backup:local create --key /keys/orgproekt.agekey --app-image $oldApi --web-image $oldWeb
        $migration = Join-Path $BackupRoot 'tools/https-lab/migrate_passkeys.py'
        Invoke-BackupDocker run @limits --entrypoint python -e PYTHONPATH=/app -v "${lab}:/private:ro" -v "${migration}:/upgrade.py:ro" $api /upgrade.py
        $tempRelease = Join-Path $lab 'training-release.next'
        [IO.File]::WriteAllLines($tempRelease, [string[]]@("RESTORE_APP_IMAGE=$api","RESTORE_WEB_IMAGE=$web"), [Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $tempRelease -Destination $release -Force
        Invoke-BackupDocker @base --env-file $release up -d --no-build --pull never --wait --wait-timeout 120 api caddy scheduler worker
    } catch {
        if ($previous) { [IO.File]::WriteAllText($release,$previous) }
        elseif (Test-Path -LiteralPath $release) { Remove-Item -LiteralPath $release }
        Invoke-BackupDocker @oldArgs up -d --no-build --pull never --wait --wait-timeout 120 api caddy scheduler worker
        throw 'Training upgrade failed. Previous images selected.'
    }
} finally { Remove-Item Env:RESTORE_PRIVATE_DIR -ErrorAction SilentlyContinue }
