from __future__ import annotations

import pytest

from app.observability import (
    OperationalThresholds,
    assess_operational_health,
    operational_thresholds,
)


def _metrics() -> dict:
    return {
        "backup": {"age_seconds": 10.0},
        "queue": {
            "oldest_runnable_pending_seconds": 0.0,
            "stale_processing": 0,
            "dead_last_24h": 0,
        },
        "summaries": {"p95_generation_ms": 100.0},
    }


def test_operational_health_is_ok_without_threshold_breaches() -> None:
    result = assess_operational_health(_metrics(), OperationalThresholds())
    assert result == {"status": "ok", "alerts": []}


def test_missing_backup_is_critical() -> None:
    metrics = _metrics()
    metrics["backup"]["age_seconds"] = None
    result = assess_operational_health(metrics, OperationalThresholds())
    assert result["status"] == "critical"
    assert result["alerts"][0]["signal"] == "backup_missing"


def test_queue_lag_degrades_then_becomes_critical() -> None:
    metrics = _metrics()
    thresholds = OperationalThresholds(queue_warn_seconds=10, queue_critical_seconds=20)
    metrics["queue"]["oldest_runnable_pending_seconds"] = 15
    assert assess_operational_health(metrics, thresholds)["status"] == "degraded"
    metrics["queue"]["oldest_runnable_pending_seconds"] = 25
    assert assess_operational_health(metrics, thresholds)["status"] == "critical"


def test_threshold_configuration_rejects_inverted_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPS_QUEUE_WARN_SECONDS", "30")
    monkeypatch.setenv("OPS_QUEUE_CRITICAL_SECONDS", "10")
    with pytest.raises(RuntimeError, match="must exceed"):
        operational_thresholds()
