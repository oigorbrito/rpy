from __future__ import annotations

import json
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
REQUIRED_SECRET_ENV = {
    "DATABASE_URL",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "JUDIT_WEBHOOK_TOKEN",
    "RPY_BEARER_TOKENS",
    "RPY_OPS_TOKEN",
}


def _fail(message: str) -> None:
    raise SystemExit(f"production compose contract failed: {message}")


def validate(config: dict[str, Any]) -> None:
    services = config.get("services")
    if not isinstance(services, dict):
        _fail("services must be an object")

    if set(services) != EXPECTED_SERVICES:
        _fail(f"unexpected services: {sorted(services)}")

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

    environment = services["api"].get("environment") or {}
    missing = sorted(REQUIRED_SECRET_ENV - set(environment))
    if missing:
        _fail(f"api environment is missing required settings: {missing}")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: validate_production_compose.py <compose-config.json>")
    path = Path(sys.argv[1])
    validate(json.loads(path.read_text(encoding="utf-8")))
    print("production compose contract: ok")


if __name__ == "__main__":
    main()
