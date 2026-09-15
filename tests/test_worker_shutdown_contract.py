from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_production_compose.py"
spec = importlib.util.spec_from_file_location("validate_production_compose_shutdown", MODULE_PATH)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def _services(*, app_grace: str = "30", container_grace: str = "40s") -> dict:
    return {
        "worker-1": {
            "environment": {"WORKER_SHUTDOWN_GRACE_SECONDS": app_grace},
            "stop_grace_period": container_grace,
        },
        "worker-2": {
            "environment": {"WORKER_SHUTDOWN_GRACE_SECONDS": app_grace},
            "stop_grace_period": container_grace,
        },
    }


def test_accepts_container_grace_longer_than_worker_drain() -> None:
    module._validate_worker_shutdown(_services())


def test_rejects_container_grace_equal_to_worker_drain() -> None:
    with pytest.raises(SystemExit, match="must exceed"):
        module._validate_worker_shutdown(
            _services(app_grace="30", container_grace="30s")
        )


def test_rejects_mismatched_worker_shutdown_contracts() -> None:
    services = _services()
    services["worker-2"]["stop_grace_period"] = "45s"

    with pytest.raises(SystemExit, match="same shutdown grace contract"):
        module._validate_worker_shutdown(services)


def test_duration_parser_accepts_compose_units() -> None:
    assert module._duration_seconds("250ms") == pytest.approx(0.25)
    assert module._duration_seconds("40s") == pytest.approx(40)
    assert module._duration_seconds("2m") == pytest.approx(120)
