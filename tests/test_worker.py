import asyncio
import logging
from uuid import uuid4

import pytest

import app.worker as worker_module
from app.tasks import PermanentTaskError
from app.worker import Worker, WorkerSettings, _decode_payload


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


class _Acquire:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def acquire(self):
        return _Acquire()


def _settings(*, task_timeout_seconds: float = 1) -> WorkerSettings:
    return WorkerSettings(
        database_url="postgresql://unused/rpy",
        concurrency=1,
        heartbeat_interval_seconds=1,
        stale_after_seconds=2,
        task_timeout_seconds=task_timeout_seconds,
        reclaim_interval_seconds=1,
    )


@pytest.mark.asyncio
async def test_worker_task_timeout_remains_retryable(monkeypatch) -> None:
    failed: list[tuple[str, bool]] = []

    async def hung_handler(payload):
        await asyncio.sleep(60)

    async def fake_fail(conn, job_id, worker_id, *, attempts, error, permanent=False):
        failed.append((error, permanent))
        return True

    monkeypatch.setattr(worker_module, "resolve_task", lambda name: hung_handler)
    monkeypatch.setattr(worker_module, "fail", fake_fail)

    worker = Worker(_Pool(), _settings(task_timeout_seconds=0.01), worker_id=uuid4())
    row = {
        "id": uuid4(),
        "task_name": "hung-provider-call",
        "payload": {},
        "attempts": 1,
    }

    await worker._run_job(row)

    assert len(failed) == 1
    assert failed[0][0].startswith("TimeoutError:")
    assert failed[0][1] is False


@pytest.mark.asyncio
async def test_worker_marks_explicit_permanent_task_error(monkeypatch) -> None:
    failed: list[tuple[str, bool]] = []

    async def permanent_handler(payload):
        raise PermanentTaskError("deterministic contract failure")

    async def fake_fail(conn, job_id, worker_id, *, attempts, error, permanent=False):
        failed.append((error, permanent))
        return True

    monkeypatch.setattr(worker_module, "resolve_task", lambda name: permanent_handler)
    monkeypatch.setattr(worker_module, "fail", fake_fail)

    worker = Worker(_Pool(), _settings(), worker_id=uuid4())
    row = {
        "id": uuid4(),
        "task_name": "permanent",
        "payload": {},
        "attempts": 1,
    }

    await worker._run_job(row)

    assert failed == [("PermanentTaskError: deterministic contract failure", True)]


@pytest.mark.asyncio
async def test_worker_redacts_secret_from_error_log_and_runtime_log(
    monkeypatch, caplog
) -> None:
    failed: list[str] = []
    secret = "sk-provider-secret-value"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)

    async def leaking_handler(payload):
        raise RuntimeError(
            f"provider failed with {secret} at "
            "postgresql://rpy_worker:db-password@postgres:5432/rpy"
        )

    async def fake_fail(conn, job_id, worker_id, *, attempts, error, permanent=False):
        failed.append(error)
        return True

    monkeypatch.setattr(worker_module, "resolve_task", lambda name: leaking_handler)
    monkeypatch.setattr(worker_module, "fail", fake_fail)
    caplog.set_level(logging.ERROR, logger="app.worker")

    worker = Worker(_Pool(), _settings(), worker_id=uuid4())
    row = {
        "id": uuid4(),
        "task_name": "leaking-provider",
        "payload": {},
        "attempts": 1,
    }

    await worker._run_job(row)

    assert len(failed) == 1
    assert secret not in failed[0]
    assert "db-password" not in failed[0]
    assert "[REDACTED]" in failed[0]
    rendered_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert secret not in rendered_logs
    assert "db-password" not in rendered_logs
    assert "[REDACTED]" in rendered_logs
    assert all(record.exc_info is None for record in caplog.records)
