import asyncio
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

    settings = WorkerSettings(
        database_url="postgresql://unused/rpy",
        concurrency=1,
        heartbeat_interval_seconds=1,
        stale_after_seconds=2,
        task_timeout_seconds=0.01,
        reclaim_interval_seconds=1,
    )
    worker = Worker(_Pool(), settings, worker_id=uuid4())
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

    settings = WorkerSettings(
        database_url="postgresql://unused/rpy",
        concurrency=1,
        heartbeat_interval_seconds=1,
        stale_after_seconds=2,
        task_timeout_seconds=1,
        reclaim_interval_seconds=1,
    )
    worker = Worker(_Pool(), settings, worker_id=uuid4())
    row = {
        "id": uuid4(),
        "task_name": "permanent",
        "payload": {},
        "attempts": 1,
    }

    await worker._run_job(row)

    assert failed == [("PermanentTaskError: deterministic contract failure", True)]
