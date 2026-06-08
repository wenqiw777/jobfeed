$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Mounts = @()
foreach ($File in @("config.toml", "resume.md", "preamble_personal.md")) {
    $Path = Join-Path $RepoRoot $File
    if (Test-Path -Path $Path -PathType Leaf) {
        $Mounts += @("-v", "${Path}:/app/${File}:ro")
    }
}
docker compose run --rm @Mounts jobfeed-cli jobfeed @args
exit $LASTEXITCODE
