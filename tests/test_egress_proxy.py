from __future__ import annotations

import asyncio

import pytest

from app import egress_proxy


def test_allowlist_requires_dns_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "EGRESS_PROXY_ALLOWED_HOSTS",
        "api.anthropic.com, requests.production.judit.io",
    )
    assert egress_proxy._allowed_hosts() == frozenset(
        {"api.anthropic.com", "requests.production.judit.io"}
    )

    monkeypatch.setenv("EGRESS_PROXY_ALLOWED_HOSTS", "127.0.0.1")
    with pytest.raises(RuntimeError, match="DNS hostnames"):
        egress_proxy._allowed_hosts()


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("api.anthropic.com:443", ("api.anthropic.com", 443)),
        ("API.ANTHROPIC.COM.:443", ("api.anthropic.com", 443)),
    ],
)
def test_connect_target_is_exact_tls_hostname(target: str, expected: tuple[str, int]) -> None:
    assert egress_proxy._parse_connect_target(target) == expected


@pytest.mark.parametrize(
    "target",
    [
        "api.anthropic.com:80",
        "127.0.0.1:443",
        "[::1]:443",
        "api.anthropic.com:443:extra",
        "bad host:443",
    ],
)
def test_connect_target_rejects_bypass_shapes(target: str) -> None:
    with pytest.raises(ValueError):
        egress_proxy._parse_connect_target(target)


class _Writer:
    def __init__(self) -> None:
        self.data = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


@pytest.mark.asyncio
async def test_disallowed_connect_is_rejected_before_dns_or_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = (
        b"CONNECT metadata.google.internal:443 HTTP/1.1\r\n"
        b"Host: metadata.google.internal:443\r\n\r\n"
    )
    reader = asyncio.StreamReader()
    reader.feed_data(request)
    reader.feed_eof()
    writer = _Writer()

    async def must_not_connect(*_args, **_kwargs):
        raise AssertionError("disallowed destination must not reach network")

    monkeypatch.setattr(asyncio, "open_connection", must_not_connect)
    await egress_proxy._handle_client(
        reader,
        writer,  # type: ignore[arg-type]
        allowed_hosts=frozenset({"api.anthropic.com"}),
        timeout_seconds=1.0,
    )

    assert b"403 Forbidden" in bytes(writer.data)
    assert writer.closed is True


@pytest.mark.asyncio
async def test_plain_http_proxying_is_not_supported() -> None:
    request = b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n"
    reader = asyncio.StreamReader()
    reader.feed_data(request)
    reader.feed_eof()
    writer = _Writer()

    await egress_proxy._handle_client(
        reader,
        writer,  # type: ignore[arg-type]
        allowed_hosts=frozenset({"example.com"}),
        timeout_seconds=1.0,
    )

    assert b"405 Method Not Allowed" in bytes(writer.data)
