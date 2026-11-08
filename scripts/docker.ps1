# Обёртка для терминалов, открытых до установки Docker Desktop. PATH меняется только в процессе.
$ErrorActionPreference = 'Stop'
$dockerDir = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin'
if (-not (Test-Path -LiteralPath (Join-Path $dockerDir 'docker.exe'))) {
    $dockerDir = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin'
}
if (Test-Path -LiteralPath (Join-Path $dockerDir 'docker.exe')) {
    $env:PATH = "$dockerDir;$env:PATH"
}
& docker @args
exit $LASTEXITCODE
