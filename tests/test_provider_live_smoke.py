from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "provider_live_smoke.py"
SPEC = importlib.util.spec_from_file_location("provider_live_smoke", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("failed to load provider live smoke module")
smoke = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = smoke
SPEC.loader.exec_module(smoke)


def test_judit_missing_credentials_skips_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def fake_create(code: str):
        nonlocal called
        called = True
        return SimpleNamespace(request_id="should-not-run")

    monkeypatch.delenv("JUDIT_API_KEY", raising=False)
    monkeypatch.setenv("PROVIDER_ACCEPTANCE_AUTHORIZED", "true")
    monkeypatch.setattr(smoke, "create_lawsuit_request", fake_create)

    report = asyncio.run(smoke.run_smoke("judit", "0000000-00.2026.8.21.0001"))

    if report["status"] != "skipped_missing_credentials":
        raise AssertionError(f"unexpected report: {report!r}")
    if report["network_calls_performed"] is not False or called:
        raise AssertionError("missing credentials must not perform Judit network work")


def test_judit_live_path_uses_existing_adapter_and_hashes_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []

    async def fake_create(code: str):
        observed.append(code)
        return SimpleNamespace(request_id="provider-request-123")

    monkeypatch.setenv("JUDIT_API_KEY", "test-key")
    monkeypatch.setenv("PROVIDER_ACCEPTANCE_AUTHORIZED", "true")
    monkeypatch.setenv("JUDIT_ATTACHMENTS_ENABLED", "false")
    monkeypatch.setattr(smoke, "create_lawsuit_request", fake_create)

    report = asyncio.run(smoke.run_smoke("judit", "0000000-00.2026.8.21.0001"))

    if report["status"] != "request_created":
        raise AssertionError(f"unexpected report: {report!r}")
    if observed != ["0000000-00.2026.8.21.0001"]:
        raise AssertionError(f"unexpected adapter input: {observed!r}")
    if "request_id" in report:
        raise AssertionError("raw provider request id must not be emitted")
    if len(report["request_id_sha256"]) != 64:
        raise AssertionError(f"request id hash is invalid: {report!r}")


def test_judit_live_path_rejects_paid_attachments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "test-key")
    monkeypatch.setenv("PROVIDER_ACCEPTANCE_AUTHORIZED", "true")
    monkeypatch.setenv("JUDIT_ATTACHMENTS_ENABLED", "true")
    monkeypatch.setenv("JUDIT_ATTACHMENT_DOWNLOAD_MODE", "direct_api_key")

    with pytest.raises(RuntimeError, match="JUDIT_ATTACHMENTS_ENABLED=false"):
        asyncio.run(smoke.run_smoke("judit", "0000000-00.2026.8.21.0001"))


def test_datajud_requires_explicit_authorization_before_adapter_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def fake_lookup(**kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(status="ok", metadata=object(), error_code=None)

    monkeypatch.setenv("DATAJUD_ENABLED", "true")
    monkeypatch.setenv("DATAJUD_AUTHORIZED_USE", "false")
    monkeypatch.setenv("DATAJUD_API_KEY", "test-key")
    monkeypatch.setenv("PROVIDER_ACCEPTANCE_AUTHORIZED", "true")
    monkeypatch.setattr(smoke, "lookup_datajud_metadata", fake_lookup)

    report = asyncio.run(smoke.run_smoke("datajud", "0000000-00.2026.8.21.0001"))

    if report["status"] != "skipped_missing_authorization":
        raise AssertionError(f"unexpected report: {report!r}")
    if called:
        raise AssertionError("unauthorized DataJud smoke must not call the adapter")


def test_datajud_live_path_uses_existing_adapter_and_sanitized_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[dict[str, object]] = []

    async def fake_lookup(**kwargs):
        observed.append(kwargs)
        return SimpleNamespace(status="ok", metadata=object(), error_code=None)

    monkeypatch.setenv("DATAJUD_ENABLED", "true")
    monkeypatch.setenv("DATAJUD_AUTHORIZED_USE", "true")
    monkeypatch.setenv("DATAJUD_API_KEY", "test-key")
    monkeypatch.setenv("PROVIDER_ACCEPTANCE_AUTHORIZED", "true")
    monkeypatch.setattr(smoke, "lookup_datajud_metadata", fake_lookup)

    report = asyncio.run(smoke.run_smoke("datajud", "0000000-00.2026.8.21.0001"))

    if report["status"] != "ok" or report["metadata_present"] is not True:
        raise AssertionError(f"unexpected report: {report!r}")
    expected = {"code": "0000000-00.2026.8.21.0001", "secrecy_level": 0}
    if observed != [expected]:
        raise AssertionError(f"unexpected adapter invocation: {observed!r}")
