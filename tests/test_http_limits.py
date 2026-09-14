from __future__ import annotations

import pytest

from app.http_limits import (
    DEFAULT_JUDIT_WEBHOOK_MAX_BODY_BYTES,
    JuditWebhookBodyLimitMiddleware,
    judit_webhook_max_body_bytes,
)


def _scope(*, content_length: int | None = None) -> dict:
    headers = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    return {
        "type": "http",
        "method": "POST",
        "path": "/webhooks/judit/token",
        "headers": headers,
        "http_version": "1.1",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1234),
        "query_string": b"",
    }


async def _run(middleware, scope: dict, messages: list[dict]) -> list[dict]:
    queue = list(messages)
    sent: list[dict] = []

    async def receive():
        if queue:
            return queue.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return sent


def test_webhook_body_limit_has_bounded_default(monkeypatch) -> None:
    monkeypatch.delenv("JUDIT_WEBHOOK_MAX_BODY_BYTES", raising=False)
    assert judit_webhook_max_body_bytes() == DEFAULT_JUDIT_WEBHOOK_MAX_BODY_BYTES
    assert DEFAULT_JUDIT_WEBHOOK_MAX_BODY_BYTES == 5 * 1024 * 1024


def test_webhook_body_limit_rejects_invalid_configuration(monkeypatch) -> None:
    monkeypatch.setenv("JUDIT_WEBHOOK_MAX_BODY_BYTES", "0")
    with pytest.raises(RuntimeError, match="greater than zero"):
        judit_webhook_max_body_bytes()


@pytest.mark.asyncio
async def test_content_length_over_limit_is_rejected_before_app(monkeypatch) -> None:
    monkeypatch.setenv("JUDIT_WEBHOOK_MAX_BODY_BYTES", "10")
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    middleware = JuditWebhookBodyLimitMiddleware(app)
    sent = await _run(
        middleware,
        _scope(content_length=11),
        [{"type": "http.request", "body": b"", "more_body": False}],
    )

    assert called is False
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_chunked_body_over_limit_is_rejected_without_content_length(monkeypatch) -> None:
    monkeypatch.setenv("JUDIT_WEBHOOK_MAX_BODY_BYTES", "10")

    async def app(scope, receive, send):
        while True:
            message = await receive()
            if message["type"] != "http.request" or not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = JuditWebhookBodyLimitMiddleware(app)
    sent = await _run(
        middleware,
        _scope(),
        [
            {"type": "http.request", "body": b"123456", "more_body": True},
            {"type": "http.request", "body": b"78901", "more_body": False},
        ],
    )

    starts = [message for message in sent if message["type"] == "http.response.start"]
    assert len(starts) == 1
    assert starts[0]["status"] == 413


@pytest.mark.asyncio
async def test_body_at_limit_reaches_app(monkeypatch) -> None:
    monkeypatch.setenv("JUDIT_WEBHOOK_MAX_BODY_BYTES", "10")

    async def app(scope, receive, send):
        message = await receive()
        assert message["body"] == b"1234567890"
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = JuditWebhookBodyLimitMiddleware(app)
    sent = await _run(
        middleware,
        _scope(content_length=10),
        [{"type": "http.request", "body": b"1234567890", "more_body": False}],
    )

    assert sent[0]["status"] == 204
