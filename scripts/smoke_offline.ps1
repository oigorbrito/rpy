$ErrorActionPreference = "Stop"
$project = "rpy-offline-smoke-$PID"
$compose = @("-f", "compose.yaml", "-f", "scripts/compose.offline.yaml", "-p", $project)
try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "Docker is required" }
    docker compose @compose up -d postgres
    docker compose @compose run --rm --no-deps -e ANTHROPIC_API_KEY=offline-disabled -e OPENAI_API_KEY= -e JUDIT_API_KEY= -e GEMINI_API_KEY= -e DATABASE_URL=postgresql://rpy:rpy@postgres:5432/rpy api python scripts/smoke_offline.py
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} catch { Write-Error "offline smoke failed: $($_.Exception.Message)"; exit 1 }
finally { docker compose @compose down -v --remove-orphans 2>$null | Out-Null }
