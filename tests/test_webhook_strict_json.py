from __future__ import annotations

import httpx
import pytest

from app.api import app


@pytest.mark.asyncio
async def test_judit_webhook_rejects_duplicate_json_keys_before_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "strict-json-webhook-token"
    monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN", token)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/webhooks/judit/{token}",
            headers={"Content-Type": "application/json"},
            content=(
                '{"event_type":"request_completed",'
                '"event_type":"response_created",'
                '"reference_type":"request","reference_id":"req-1",'
                '"payload":{"status":"completed"}}'
            ),
        )

    if response.status_code != 400:
        raise AssertionError(
            f"expected duplicate webhook JSON to return 400, got {response.status_code}"
        )
    if response.json().get("detail") != "invalid payload":
        raise AssertionError(f"unexpected webhook error response: {response.json()!r}")
