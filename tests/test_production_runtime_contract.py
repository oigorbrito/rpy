from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_production_compose.py"
spec = importlib.util.spec_from_file_location("validate_production_compose", MODULE_PATH)
assert spec is not None and spec.loader is not None
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


def _runtime_service(*, cpus: float, mem_limit: str, pids_limit: int) -> dict:
    return {
        "read_only": True,
        "user": "10001:10001",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "tmpfs": ["/tmp:rw,noexec,nosuid,nodev,size=64m"],
        "cpus": cpus,
        "mem_limit": mem_limit,
        "pids_limit": pids_limit,
    }


def _runtime_services() -> dict:
    return {
        "migrate": _runtime_service(cpus=1.0, mem_limit="512m", pids_limit=128),
        "api": _runtime_service(cpus=1.0, mem_limit="512m", pids_limit=128),
        "egress-proxy": _runtime_service(cpus=0.5, mem_limit="256m", pids_limit=128),
        "worker-1": _runtime_service(cpus=2.0, mem_limit="8g", pids_limit=256),
        "worker-2": _runtime_service(cpus=2.0, mem_limit="8g", pids_limit=256),
        "scheduler": _runtime_service(cpus=0.5, mem_limit="256m", pids_limit=64),
    }


def test_runtime_confinement_accepts_expected_budget() -> None:
    contract._validate_runtime_confinement(_runtime_services())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("read_only", False, "root filesystem must be read-only"),
        ("cap_drop", [], "must drop all Linux capabilities"),
        ("security_opt", [], "must enable no-new-privileges"),
        ("tmpfs", ["/tmp:rw"], "/tmp tmpfs must include noexec"),
        ("pids_limit", 0, "pids_limit must be positive"),
    ],
)
def test_runtime_confinement_rejects_removed_controls(
    field: str, value: object, message: str
) -> None:
    services = _runtime_services()
    services["api"][field] = value
    with pytest.raises(SystemExit, match=message):
        contract._validate_runtime_confinement(services)


def test_runtime_confinement_rejects_seccomp_unconfined_and_added_caps() -> None:
    services = _runtime_services()
    services["worker-1"]["security_opt"].append("seccomp=unconfined")
    with pytest.raises(SystemExit, match="preserve Docker seccomp confinement"):
        contract._validate_runtime_confinement(services)

    services = _runtime_services()
    services["worker-1"]["cap_add"] = ["NET_ADMIN"]
    with pytest.raises(SystemExit, match="must not add Linux capabilities"):
        contract._validate_runtime_confinement(services)


def _networked_services() -> dict:
    worker_env = {
        "HTTPS_PROXY": "http://egress-proxy:3128",
        "https_proxy": "http://egress-proxy:3128",
        "NO_PROXY": "postgres,localhost,127.0.0.1",
        "no_proxy": "postgres,localhost,127.0.0.1",
    }
    return {
        "postgres": {"networks": {"backend": None}, "environment": {}},
        "migrate": {"networks": {"backend": None}, "environment": {}},
        "api": {"networks": {"backend": None}, "environment": {}},
        "worker-1": {
            "networks": {"backend": None, "provider-gateway": None},
            "environment": dict(worker_env),
        },
        "worker-2": {
            "networks": {"backend": None, "provider-gateway": None},
            "environment": dict(worker_env),
        },
        "scheduler": {"networks": {"backend": None}, "environment": {}},
        "egress-proxy": {
            "networks": {"provider-gateway": None, "egress": None},
            "environment": {
                "EGRESS_PROXY_ALLOWED_HOSTS": "api.anthropic.com",
                "EGRESS_PROXY_CONNECT_TIMEOUT_SECONDS": "10",
            },
        },
    }


def test_egress_topology_has_single_external_gateway() -> None:
    contract._validate_egress_topology(
        _networked_services(),
        {
            "backend": {"internal": True},
            "provider-gateway": {"internal": True},
            "egress": {},
        },
    )


def test_worker_direct_egress_route_is_rejected() -> None:
    services = _networked_services()
    services["worker-1"]["networks"]["egress"] = None
    with pytest.raises(SystemExit, match="worker-1 network set"):
        contract._validate_egress_topology(
            services,
            {
                "backend": {"internal": True},
                "provider-gateway": {"internal": True},
                "egress": {},
            },
        )


def test_proxy_cannot_receive_provider_secrets_by_contract() -> None:
    services = _networked_services()
    services["egress-proxy"]["environment"]["ANTHROPIC_API_KEY"] = "secret"
    with pytest.raises(SystemExit, match="must receive only its allowlist"):
        contract._validate_egress_topology(
            services,
            {
                "backend": {"internal": True},
                "provider-gateway": {"internal": True},
                "egress": {},
            },
        )
