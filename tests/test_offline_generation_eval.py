from __future__ import annotations

import asyncio
import copy
import socket

import pytest

from scripts import evaluate_offline_generation as evaluator


def test_offline_generation_matches_versioned_baseline() -> None:
    report = asyncio.run(evaluator.evaluate_generation(evaluator.load_dataset()))
    baseline = evaluator.load_baseline()
    assert evaluator.compare_to_baseline(report, baseline) == []
    assert report["counts"]["forced_retries"] >= 1
    assert report["counts"]["forced_retries"] == report["counts"]["recovered_retries"]
    assert report["counts"]["secret_provider_calls"] == 0


def test_generation_regression_is_reported() -> None:
    report = asyncio.run(evaluator.evaluate_generation(evaluator.load_dataset()))
    baseline = copy.deepcopy(evaluator.load_baseline())
    report["metrics"]["final_validation_pass_rate"] = 0.5
    failures = evaluator.compare_to_baseline(report, baseline)
    assert failures == [
        {
            "metric": "final_validation_pass_rate",
            "direction": "min",
            "threshold": 1.0,
            "actual": 0.5,
        }
    ]


def test_offline_generation_performs_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked_connect(*args, **kwargs):
        raise AssertionError("offline generation evaluation attempted network access")

    monkeypatch.setattr(socket.socket, "connect", blocked_connect)
    report = asyncio.run(evaluator.evaluate_generation(evaluator.load_dataset()))
    assert report["metrics"]["final_validation_pass_rate"] == 1.0
    assert report["metrics"]["secret_provider_call_rate"] == 0.0
