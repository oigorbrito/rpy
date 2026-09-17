from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

EXPECTED_SERVICES = {
    "postgres",
    "migrate",
    "api",
    "worker-1",
    "worker-2",
    "scheduler",
}
APPLICATION_SERVICES = ("migrate", "api", "worker-1", "worker-2", "scheduler")
IMMUTABLE_IMAGE_RE = re.compile(r"^.+@sha256:[0-9a-fA-F]{64}$")
DURATION_RE = re.compile(r"^(?P<value>[0-9]+(?:\.[0-9]+)?)(?P<unit>ms|s|m|h)$")
API_REQUIRED_ENV = {
    "DATABASE_URL",
    "JUDIT_WEBHOOK_TOKEN",
    "JUDIT_WEBHOOK_MAX_BODY_BYTES",
    "RPY_BEARER_TOKENS",
    "RPY_OPS_TOKEN",
    "OPS_BACKUP_MAX_AGE_SECONDS",
    "OPS_QUEUE_WARN_SECONDS",
    "OPS_QUEUE_CRITICAL_SECONDS",
    "OPS_STALE_PROCESSING_SECONDS",
    "OPS_DEAD_JOBS_WARN_24H",
    "OPS_DEAD_JOBS_CRITICAL_24H",
    "OPS_GENERATION_P95_WARN_MS",
    "OPS_GENERATION_P95_CRITICAL_MS",
}
WORKER_REQUIRED_ENV = {
    "DATABASE_URL",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "EMBEDDING_MODEL",
    "EMBEDDING_SPACE_RUNTIME_ENABLED",
    "EMBEDDING_PROVIDER",
    "BGE_EMBEDDING_MODEL",
    "BGE_EMBEDDING_PATH",
    "BGE_EMBEDDING_DEVICE",
    "BGE_EMBEDDING_USE_FP16",
    "ANTHROPIC_TIMEOUT_SECONDS",
    "EMBEDDING_TIMEOUT_SECONDS",
    "PROVIDER_MAX_ATTEMPTS",
    "PROVIDER_RETRY_BACKOFF_SECONDS",
    "PROVIDER_PROMPT_MAX_CHARS",
    "PROVIDER_STEP_TEXT_MAX_CHARS",
    "PROVIDER_STEPS_TEXT_MAX_CHARS",
    "WORKER_TASK_TIMEOUT_SECONDS",
    "WORKER_SHUTDOWN_GRACE_SECONDS",
}
SCHEDULER_REQUIRED_ENV = {
    "DATABASE_URL",
    "RETENTION_DAYS",
    "JOB_RETENTION_DAYS",
    "EXPUNGE_INTERVAL_SECONDS",
}
MIGRATE_REQUIRED_ENV = {
    "MIGRATION_DATABASE_URL",
    "API_DATABASE_URL",
    "WORKER_DATABASE_URL",
    "SCHEDULER_DATABASE_URL",
    "BACKUP_DATABASE_URL",
}
HTTP_SECRETS = {"JUDIT_WEBHOOK_TOKEN", "RPY_BEARER_TOKENS", "RPY_OPS_TOKEN"}
PROVIDER_SECRETS = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY"}
DB_ROLE_USERS = {
    "api": "rpy_api",
    "worker-1": "rpy_worker",
    "worker-2": "rpy_worker",
    "scheduler": "rpy_scheduler",
}


def _fail(message: str) -> None:
    raise SystemExit(f"production compose contract failed: {message}")


def _environment(services: dict[str, Any], service_name: str) -> dict[str, Any]:
    environment = services[service_name].get("environment") or {}
    if not isinstance(environment, dict):
        _fail(f"{service_name} environment must be an object")
    return environment


def _require_env(
    services: dict[str, Any], service_name: str, required: set[str]
) -> dict[str, Any]:
    environment = _environment(services, service_name)
    missing = sorted(required - set(environment))
    if missing:
        _fail(f"{service_name} environment is missing required settings: {missing}")
    return environment


def _forbid_env(
    services: dict[str, Any], service_name: str, forbidden: set[str]
) -> None:
    environment = _environment(services, service_name)
    leaked = sorted(forbidden & set(environment))
    if leaked:
        _fail(f"{service_name} must not receive unrelated secrets: {leaked}")


def _validate_application_image(services: dict[str, Any]) -> None:
    images: dict[str, str] = {}
    for service_name in APPLICATION_SERVICES:
        service = services[service_name]
        if "build" in service:
            _fail(f"{service_name} must not build source on the production host")
        image = str(service.get("image") or "")
        if not IMMUTABLE_IMAGE_RE.fullmatch(image):
            _fail(
                f"{service_name} image must be pinned by sha256 digest, got {image!r}"
            )
        images[service_name] = image

    unique_images = set(images.values())
    if len(unique_images) != 1:
        rendered = ", ".join(f"{name}={image}" for name, image in images.items())
        _fail(f"all application services must use the same image digest: {rendered}")


def _database_user(url: str) -> str:
    return urlparse(url).username or ""


def _validate_database_isolation(services: dict[str, Any]) -> None:
    runtime_urls: dict[str, str] = {}
    for service_name, expected_user in DB_ROLE_USERS.items():
        environment = _environment(services, service_name)
        database_url = str(environment.get("DATABASE_URL") or "")
        actual_user = _database_user(database_url)
        if actual_user != expected_user:
            _fail(
                f"{service_name} DATABASE_URL must use role {expected_user!r}, got {actual_user!r}"
            )
        runtime_urls[service_name] = database_url

    if runtime_urls["api"] == runtime_urls["worker-1"]:
        _fail("api and worker database credentials must be distinct")
    if runtime_urls["api"] == runtime_urls["scheduler"]:
        _fail("api and scheduler database credentials must be distinct")
    if runtime_urls["worker-1"] == runtime_urls["scheduler"]:
        _fail("worker and scheduler database credentials must be distinct")
    if runtime_urls["worker-1"] != runtime_urls["worker-2"]:
        _fail("both workers must use the same worker database credential")

    migrate_env = _environment(services, "migrate")
    if set(migrate_env) != MIGRATE_REQUIRED_ENV:
        _fail("migrate must receive only migration/runtime database provisioning URLs")
    migration_url = str(migrate_env["MIGRATION_DATABASE_URL"])
    migration_user = _database_user(migration_url)
    if migration_user in set(DB_ROLE_USERS.values()) | {"rpy_backup"}:
        _fail("migration credential must be distinct from every runtime database role")

    expected_provisioning_users = {
        "API_DATABASE_URL": "rpy_api",
        "WORKER_DATABASE_URL": "rpy_worker",
        "SCHEDULER_DATABASE_URL": "rpy_scheduler",
        "BACKUP_DATABASE_URL": "rpy_backup",
    }
    for env_name, expected_user in expected_provisioning_users.items():
        actual_user = _database_user(str(migrate_env[env_name]))
        if actual_user != expected_user:
            _fail(f"migrate {env_name} must use role {expected_user!r}")


def _duration_seconds(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
        if seconds <= 0:
            _fail("duration must be greater than zero")
        return seconds

    rendered = str(value or "").strip()
    match = DURATION_RE.fullmatch(rendered)
    if match is None:
        _fail(f"unsupported duration value: {rendered!r}")
    amount = float(match.group("value"))
    unit = match.group("unit")
    multiplier = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[unit]
    seconds = amount * multiplier
    if seconds <= 0:
        _fail("duration must be greater than zero")
    return seconds


def _validate_worker_shutdown(services: dict[str, Any]) -> None:
    rendered_pairs: list[tuple[float, float]] = []
    for service_name in ("worker-1", "worker-2"):
        environment = _environment(services, service_name)
        try:
            app_grace = float(environment["WORKER_SHUTDOWN_GRACE_SECONDS"])
        except (KeyError, TypeError, ValueError):
            _fail(f"{service_name} WORKER_SHUTDOWN_GRACE_SECONDS must be numeric")
        if app_grace <= 0:
            _fail(f"{service_name} WORKER_SHUTDOWN_GRACE_SECONDS must be positive")
        container_grace = _duration_seconds(
            services[service_name].get("stop_grace_period")
        )
        if container_grace <= app_grace:
            _fail(
                f"{service_name} stop_grace_period must exceed "
                "WORKER_SHUTDOWN_GRACE_SECONDS"
            )
        rendered_pairs.append((app_grace, container_grace))

    if len(set(rendered_pairs)) != 1:
        _fail("both workers must use the same shutdown grace contract")


def _validate_worker_embedding_contract(services: dict[str, Any]) -> None:
    fields = (
        "EMBEDDING_SPACE_RUNTIME_ENABLED",
        "EMBEDDING_PROVIDER",
        "BGE_EMBEDDING_MODEL",
        "BGE_EMBEDDING_PATH",
        "BGE_EMBEDDING_DEVICE",
        "BGE_EMBEDDING_USE_FP16",
        "EMBEDDING_MODEL",
    )
    left = _environment(services, "worker-1")
    right = _environment(services, "worker-2")
    for field in fields:
        if left.get(field) != right.get(field):
            _fail(f"both workers must use the same {field}")


def validate(config: dict[str, Any]) -> None:
    services = config.get("services")
    if not isinstance(services, dict):
        _fail("services must be an object")

    if set(services) != EXPECTED_SERVICES:
        _fail(f"unexpected services: {sorted(services)}")

    _validate_application_image(services)
    _validate_database_isolation(services)

    postgres_image = str(services["postgres"].get("image") or "")
    if not IMMUTABLE_IMAGE_RE.fullmatch(postgres_image):
        _fail("postgres image must be pinned by sha256 digest")

    if "ports" in services["postgres"]:
        _fail("postgres must not publish host ports")

    api_ports = services["api"].get("ports") or []
    if len(api_ports) != 1:
        _fail("api must publish exactly one host port")
    host_ip = str(api_ports[0].get("host_ip") or "")
    if host_ip != "127.0.0.1":
        _fail("api must bind to 127.0.0.1 by default")

    api_command = services["api"].get("command") or []
    if "--workers" not in api_command or api_command[api_command.index("--workers") + 1] != "1":
        _fail("api uvicorn worker count must be explicitly 1")

    worker_services = sorted(name for name in services if name.startswith("worker-"))
    if worker_services != ["worker-1", "worker-2"]:
        _fail("exactly two worker services are required")

    scheduler_services = [name for name in services if name == "scheduler"]
    if len(scheduler_services) != 1:
        _fail("exactly one scheduler service is required")

    networks = config.get("networks") or {}
    backend = networks.get("backend") or {}
    if backend.get("internal") is not True:
        _fail("backend network must be internal")

    postgres_networks = set((services["postgres"].get("networks") or {}).keys())
    if postgres_networks != {"backend"}:
        _fail("postgres must only join the backend network")

    for service_name in ("api", "worker-1", "worker-2"):
        service_networks = set((services[service_name].get("networks") or {}).keys())
        if not {"backend", "egress"}.issubset(service_networks):
            _fail(f"{service_name} must join backend and egress networks")

    scheduler_networks = set((services["scheduler"].get("networks") or {}).keys())
    if scheduler_networks != {"backend"}:
        _fail("scheduler must remain backend-only")

    _require_env(services, "api", API_REQUIRED_ENV)
    for service_name in ("worker-1", "worker-2"):
        _require_env(services, service_name, WORKER_REQUIRED_ENV)
    _require_env(services, "scheduler", SCHEDULER_REQUIRED_ENV)
    _validate_worker_shutdown(services)
    _validate_worker_embedding_contract(services)

    _forbid_env(services, "api", PROVIDER_SECRETS | MIGRATE_REQUIRED_ENV)
    for service_name in ("worker-1", "worker-2"):
        _forbid_env(services, service_name, HTTP_SECRETS | MIGRATE_REQUIRED_ENV)
    _forbid_env(
        services,
        "scheduler",
        HTTP_SECRETS | PROVIDER_SECRETS | MIGRATE_REQUIRED_ENV,
    )
    _forbid_env(services, "migrate", HTTP_SECRETS | PROVIDER_SECRETS)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: validate_production_compose.py <compose-config.json>")
    path = Path(sys.argv[1])
    validate(json.loads(path.read_text(encoding="utf-8")))
    print("production compose contract: ok")


if __name__ == "__main__":
    main()
