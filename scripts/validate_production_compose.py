from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

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
API_REQUIRED_ENV = {
    "DATABASE_URL",
    "JUDIT_WEBHOOK_TOKEN",
    "JUDIT_WEBHOOK_MAX_BODY_BYTES",
    "RPY_BEARER_TOKENS",
    "RPY_OPS_TOKEN",
}
WORKER_REQUIRED_ENV = {
    "DATABASE_URL",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "EMBEDDING_MODEL",
    "ANTHROPIC_TIMEOUT_SECONDS",
    "EMBEDDING_TIMEOUT_SECONDS",
    "PROVIDER_MAX_ATTEMPTS",
    "PROVIDER_RETRY_BACKOFF_SECONDS",
    "WORKER_TASK_TIMEOUT_SECONDS",
}
SCHEDULER_REQUIRED_ENV = {
    "DATABASE_URL",
    "RETENTION_DAYS",
    "JOB_RETENTION_DAYS",
    "EXPUNGE_INTERVAL_SECONDS",
}
HTTP_SECRETS = {"JUDIT_WEBHOOK_TOKEN", "RPY_BEARER_TOKENS", "RPY_OPS_TOKEN"}
PROVIDER_SECRETS = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY"}


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


def validate(config: dict[str, Any]) -> None:
    services = config.get("services")
    if not isinstance(services, dict):
        _fail("services must be an object")

    if set(services) != EXPECTED_SERVICES:
        _fail(f"unexpected services: {sorted(services)}")

    _validate_application_image(services)

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

    migrate_env = _environment(services, "migrate")
    if set(migrate_env) != {"DATABASE_URL"}:
        _fail("migrate must receive only DATABASE_URL")

    _forbid_env(services, "api", PROVIDER_SECRETS)
    for service_name in ("worker-1", "worker-2"):
        _forbid_env(services, service_name, HTTP_SECRETS)
    _forbid_env(services, "scheduler", HTTP_SECRETS | PROVIDER_SECRETS)
    _forbid_env(services, "migrate", HTTP_SECRETS | PROVIDER_SECRETS)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: validate_production_compose.py <compose-config.json>")
    path = Path(sys.argv[1])
    validate(json.loads(path.read_text(encoding="utf-8")))
    print("production compose contract: ok")


if __name__ == "__main__":
    main()
