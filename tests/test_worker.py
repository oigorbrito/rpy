from app.worker import WorkerSettings


def test_worker_settings_defaults(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/rpy")
    settings = WorkerSettings.from_env()
    assert settings.concurrency == 2
    assert settings.stale_after_seconds > settings.heartbeat_interval_seconds
    assert settings.task_timeout_seconds > 0
