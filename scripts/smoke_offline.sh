#!/usr/bin/env bash
set -euo pipefail
command -v docker >/dev/null || { echo "offline smoke failed: Docker is required" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "offline smoke failed: Docker daemon is not running" >&2; exit 1; }
project="rpy-offline-smoke-$$"
compose=(docker compose -f compose.yaml -f scripts/compose.offline.yaml -p "$project")
cleanup() { "${compose[@]}" down -v --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT
"${compose[@]}" up -d postgres
"${compose[@]}" run --rm --no-deps -e ANTHROPIC_API_KEY=offline-disabled -e OPENAI_API_KEY= -e JUDIT_API_KEY= -e GEMINI_API_KEY= -e DATABASE_URL=postgresql://rpy:rpy@postgres:5432/rpy api python scripts/smoke_offline.py
