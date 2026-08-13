$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$HostJobfeed = Join-Path $RepoRoot ".venv\Scripts\jobfeed.exe"
if (-not (Test-Path -Path $HostJobfeed -PathType Leaf)) {
    Write-Error "host runtime is not installed; run ./setup"
    exit 2
}
Set-Location $RepoRoot
& $HostJobfeed @args
exit $LASTEXITCODE
