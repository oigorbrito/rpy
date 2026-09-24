from __future__ import annotations

import httpx
import pytest

from app.api import app


@pytest.mark.asyncio
async def test_v1_summary_request_rejects_duplicate_json_keys_before_auth() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/resumos",
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": "strict-json-duplicate",
            },
            content=(
                '{"cnj":"0000000-00.2026.8.21.0001",'
                '"cnj":"0000000-00.2026.8.21.0002","format":"json"}'
            ),
        )

    if response.status_code != 400:
        raise AssertionError(
            f"expected duplicate request JSON to return 400, got {response.status_code}"
        )
    if response.json().get("detail") != "request body must contain strict JSON":
        raise AssertionError(f"unexpected duplicate JSON response: {response.json()!r}")


@pytest.mark.asyncio
async def test_v1_summary_request_preserves_malformed_json_error_shape() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/resumos",
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": "malformed-json",
            },
            content='{"cnj":',
        )

    if response.status_code != 400:
        raise AssertionError(
            f"expected malformed request JSON to return 400, got {response.status_code}"
        )
    if response.json().get("detail") != "invalid JSON payload":
        raise AssertionError(f"unexpected malformed JSON response: {response.json()!r}")


@pytest.mark.asyncio
@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
async def test_v1_summary_request_rejects_non_standard_numeric_constants_before_auth(
    constant: str,
) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/resumos",
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": "strict-json-non-finite",
            },
            content=(
                '{"cnj":"0000000-00.2026.8.21.0001","format":"json",'
                f'"unused":{{"score":{constant}}}}}'
            ),
        )

    if response.status_code != 400:
        raise AssertionError(
            f"expected non-standard JSON number to return 400, got {response.status_code}"
        )
    if response.json().get("detail") != "request body must contain strict JSON":
        raise AssertionError(f"unexpected non-standard JSON response: {response.json()!r}")


@pytest.mark.asyncio
async def test_v1_summary_request_rejects_duplicate_idempotency_keys_before_auth() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/resumos",
            headers=[
                ("Content-Type", "application/json"),
                ("Idempotency-Key", "first-key"),
                ("Idempotency-Key", "second-key"),
            ],
            content='{"cnj":"0000000-00.2026.8.21.0001","format":"json"}',
        )

    if response.status_code != 400:
        raise AssertionError(
            f"expected duplicate Idempotency-Key to return 400, got {response.status_code}"
        )
    if response.json().get("detail") != "Idempotency-Key must appear exactly once":
        raise AssertionError(f"unexpected duplicate idempotency response: {response.json()!r}")
