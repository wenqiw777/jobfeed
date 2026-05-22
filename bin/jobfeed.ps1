$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path ".jobfeed-dev" | Out-Null
docker compose run --rm jobfeed-cli jobfeed @args
exit $LASTEXITCODE
