import pytest

from app.worker import WorkerSettings, _decode_payload


def test_worker_settings_defaults(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/rpy")
    settings = WorkerSettings.from_env()
    assert settings.concurrency == 2
    assert settings.stale_after_seconds > settings.heartbeat_interval_seconds
    assert settings.task_timeout_seconds > 0


def test_decode_payload_accepts_mapping_and_json_string() -> None:
    assert _decode_payload({"request_id": "req-1"}) == {"request_id": "req-1"}
    assert _decode_payload('{"request_id":"req-2"}') == {"request_id": "req-2"}
    assert _decode_payload(None) == {}


def test_decode_payload_rejects_non_object_json() -> None:
    with pytest.raises(ValueError, match="object"):
        _decode_payload('["not", "an", "object"]')
