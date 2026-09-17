from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

DIGEST_RE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
DB_URL_KEYS = (
    "MIGRATION_DATABASE_URL",
    "API_DATABASE_URL",
    "WORKER_DATABASE_URL",
    "SCHEDULER_DATABASE_URL",
    "BACKUP_DATABASE_URL",
)
REQUIRED_KEYS = (
    "RPY_IMAGE",
    "POSTGRES_PASSWORD",
    *DB_URL_KEYS,
    "ANTHROPIC_API_KEY",
    "JUDIT_API_KEY",
    "JUDIT_WEBHOOK_TOKEN",
    "RPY_BEARER_TOKENS",
    "RPY_OPS_TOKEN",
)
PLACEHOLDER_MARKERS = ("replace-with", "<64-hex-digest>", "example")
BGE_MODEL = "BAAI/bge-m3"


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"invalid environment line: {raw_line!r}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _database_identity(value: str, key: str) -> tuple[str, str, int | None, str]:
    parsed = urlsplit(value)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError(f"{key} must use postgres:// or postgresql://")
    if not parsed.username:
        raise ValueError(f"{key} must include a database username")
    if not parsed.hostname:
        raise ValueError(f"{key} must include a database hostname")
    database = parsed.path.lstrip("/")
    if not database:
        raise ValueError(f"{key} must include a database name")
    return parsed.username, parsed.hostname, parsed.port, database


def _bool_value(values: dict[str, str], key: str, default: bool = False) -> bool:
    raw = values.get(key)
    if raw is None or not str(raw).strip():
        return default
    normalized = str(raw).strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{key} must be a boolean")


def _validate_embedding_runtime(values: dict[str, str], errors: list[str]) -> None:
    try:
        runtime_enabled = _bool_value(values, "EMBEDDING_SPACE_RUNTIME_ENABLED", False)
    except ValueError as exc:
        errors.append(str(exc))
        return

    if not runtime_enabled:
        openai_key = values.get("OPENAI_API_KEY", "").strip()
        if not openai_key:
            errors.append("OPENAI_API_KEY is required when EMBEDDING_SPACE_RUNTIME_ENABLED is false")
        elif any(marker in openai_key.lower() for marker in PLACEHOLDER_MARKERS):
            errors.append("OPENAI_API_KEY still contains a placeholder value")
        return

    provider = str(values.get("EMBEDDING_PROVIDER") or "bge").strip().casefold()
    if provider != "bge":
        errors.append(
            "EMBEDDING_PROVIDER must be 'bge' when EMBEDDING_SPACE_RUNTIME_ENABLED is true; "
            "the Cohere runtime adapter is not implemented"
        )
        return

    model = str(values.get("BGE_EMBEDDING_MODEL") or BGE_MODEL).strip()
    if model != BGE_MODEL:
        errors.append(f"BGE_EMBEDDING_MODEL must be {BGE_MODEL!r}")

    artifact_path = str(values.get("BGE_EMBEDDING_PATH") or "").strip()
    if not artifact_path:
        errors.append(
            "BGE_EMBEDDING_PATH is required when EMBEDDING_SPACE_RUNTIME_ENABLED is true"
        )
    elif any(marker in artifact_path.lower() for marker in PLACEHOLDER_MARKERS):
        errors.append("BGE_EMBEDDING_PATH still contains a placeholder value")
    elif not Path(artifact_path).is_absolute():
        errors.append("BGE_EMBEDDING_PATH must be an absolute path inside the worker container")


def validate(values: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for key in REQUIRED_KEYS:
        value = values.get(key, "").strip()
        if not value:
            errors.append(f"{key} is required")
            continue
        if any(marker in value.lower() for marker in PLACEHOLDER_MARKERS):
            errors.append(f"{key} still contains a placeholder value")

    _validate_embedding_runtime(values, errors)

    image = values.get("RPY_IMAGE", "").strip()
    if image and not DIGEST_RE.fullmatch(image):
        errors.append("RPY_IMAGE must be an immutable sha256 registry digest")

    identities = {}
    for key in DB_URL_KEYS:
        value = values.get(key, "").strip()
        if not value:
            continue
        try:
            identities[key] = _database_identity(value, key)
        except ValueError as exc:
            errors.append(str(exc))
    if len(identities) == len(DB_URL_KEYS):
        usernames = {key: identity[0] for key, identity in identities.items()}
        if len(set(usernames.values())) != len(usernames):
            duplicates = {}
            for key, username in usernames.items():
                duplicates.setdefault(username, []).append(key)
            for username, keys in sorted(duplicates.items()):
                if len(keys) > 1:
                    errors.append(
                        f"database role {username!r} is reused by: {', '.join(sorted(keys))}"
                    )
        if len({(host, port, database) for _, host, port, database in identities.values()}) != 1:
            errors.append("all database URLs must target the same PostgreSQL database")

    bearer_raw = values.get("RPY_BEARER_TOKENS", "").strip()
    if bearer_raw:
        try:
            bearer_tokens = json.loads(bearer_raw)
            if not isinstance(bearer_tokens, dict) or not bearer_tokens:
                raise ValueError("must be a non-empty JSON object")
            for token, tenant_id in bearer_tokens.items():
                if not isinstance(token, str) or not token:
                    raise ValueError("bearer token keys must be non-empty strings")
                UUID(str(tenant_id))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"RPY_BEARER_TOKENS is invalid: {exc}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate production deploy environment invariants")
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Read values from a KEY=VALUE file instead of the current process environment",
    )
    args = parser.parse_args()
    values = _load_env_file(args.env_file) if args.env_file else dict(os.environ)
    errors = validate(values)
    if errors:
        for error in errors:
            print(f"deploy preflight: {error}")
        return 1
    print("deploy preflight: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
