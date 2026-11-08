param([switch]$LocalDemo)
# Explicitly disposable E2E stack. Never touches the orgproekt project or its volumes.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$projectRoot = (Get-Location).Path
$composeFiles = @('-f','compose.yml','-f','compose.e2e.yml')
if ($LocalDemo) { $composeFiles += @('-f','compose.demo-e2e.yml') }
$specs = @('e2e/access.spec.ts','e2e/changes.spec.ts','e2e/reports.spec.ts','e2e/workflow.spec.ts')
if ($LocalDemo) { $specs += 'e2e/local-demo.spec.ts' }
$testPrivate = [IO.Path]::GetFullPath((Join-Path $projectRoot '.local\e2e'))
if (-not $testPrivate.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Путь тестовых данных выходит за пределы проекта'
}
& "$PSScriptRoot/docker.ps1" compose -p orgproekt-e2e @composeFiles down --volumes --remove-orphans
if ($LASTEXITCODE -ne 0) { throw 'Не удалось остановить тестовый стек' }
New-Item -ItemType Directory -Force -Path $testPrivate,'docs/screenshots' | Out-Null
foreach ($name in @('demo-accounts.json','test-factors.json')) {
    $target = Join-Path $testPrivate $name
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target }
}
& "$PSScriptRoot/docker.ps1" compose -p orgproekt-e2e @composeFiles up -d --build api worker scheduler caddy
if ($LASTEXITCODE -ne 0) { throw 'Не удалось запустить тестовый стек' }
& "$PSScriptRoot/docker.ps1" compose -p orgproekt-e2e @composeFiles --profile tools run --rm tools python -m app.cli seed
if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать тестовые данные' }
& "$PSScriptRoot/docker.ps1" compose -p orgproekt-e2e @composeFiles --profile tools run --rm tools python -m app.cli expand-demo
if ($LASTEXITCODE -ne 0) { throw 'Не удалось расширить синтетический тестовый состав' }
$fixtureMarker = Join-Path $testPrivate 'e2e-only.guard'
[IO.File]::WriteAllText($fixtureMarker, 'disposable-orgproekt-e2e')
& "$PSScriptRoot/docker.ps1" compose -p orgproekt-e2e @composeFiles --profile tools run --rm -e ORGPROEKT_E2E_FIXTURE=disposable-orgproekt-e2e tools python -m tests.setup_change_e2e
if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать изолированные учетные записи согласования' }
$testExit = 0
foreach ($spec in $specs) {
    & "$PSScriptRoot/docker.ps1" compose -p orgproekt-e2e @composeFiles --profile tools run --rm --no-deps -e ORGPROEKT_E2E_FIXTURE=disposable-orgproekt-e2e tools python -m tests.reset_e2e_limits
    if ($LASTEXITCODE -ne 0) { $testExit = $LASTEXITCODE; break }
    & "$PSScriptRoot/docker.ps1" compose -p orgproekt-e2e @composeFiles --profile e2e run --rm --no-deps e2e npm run test:e2e -- $spec
    if ($LASTEXITCODE -ne 0) { $testExit = $LASTEXITCODE; break }
}
& "$PSScriptRoot/docker.ps1" compose -p orgproekt-e2e @composeFiles stop
exit $testExit
