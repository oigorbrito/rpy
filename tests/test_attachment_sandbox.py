from __future__ import annotations

import asyncio
import pytest

from app.attachment_processing import AttachmentProcessingError
from app.attachment_sandbox import (
    _handle_client,
    _parse_request,
    parse_attachment_sandboxed,
)


@pytest.mark.asyncio
async def test_sandbox_classifies_corrupt_pdf_without_crashing_server() -> None:
    import base64

    response = await _parse_request(
        {
            "version": 1,
            "content_type": "application/pdf",
            "max_bytes": 50_000,
            "chunk_chars": 256,
            "data_b64": base64.b64encode(b"not a pdf").decode("ascii"),
        }
    )
    assert response == {
        "version": 1,
        "ok": False,
        "status": "corrupt",
        "error_code": "invalid_pdf_header",
    }


@pytest.mark.asyncio
async def test_sandbox_client_round_trips_over_unix_socket(tmp_path) -> None:
    socket_path = tmp_path / "parser.sock"
    server = await asyncio.start_unix_server(_handle_client, path=str(socket_path))
    try:
        with pytest.raises(AttachmentProcessingError) as exc_info:
            await parse_attachment_sandboxed(
                b"not a pdf",
                content_type="application/pdf",
                max_bytes=50_000,
                chunk_chars=256,
                socket_path=str(socket_path),
                timeout_seconds=1,
            )
        assert exc_info.value.status == "corrupt"
        assert exc_info.value.error_code == "invalid_pdf_header"
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_sandbox_timeout_is_attachment_local(monkeypatch) -> None:
    async def blocked_open(*args, **kwargs):
        await asyncio.sleep(1)
        raise AssertionError("unreachable")

    monkeypatch.setattr(asyncio, "open_unix_connection", blocked_open)
    with pytest.raises(AttachmentProcessingError) as exc_info:
        await parse_attachment_sandboxed(
            b"%PDF-1.4\n",
            content_type="application/pdf",
            max_bytes=50_000,
            chunk_chars=256,
            socket_path="/run/rpy-parser/parser.sock",
            timeout_seconds=0.01,
        )
    assert exc_info.value.status == "unreadable"
    assert exc_info.value.error_code == "parser_timeout"


@pytest.mark.asyncio
async def test_sandbox_missing_socket_is_attachment_local(tmp_path) -> None:
    with pytest.raises(AttachmentProcessingError) as exc_info:
        await parse_attachment_sandboxed(
            b"%PDF-1.4\n",
            content_type="application/pdf",
            max_bytes=50_000,
            chunk_chars=256,
            socket_path=str(tmp_path / "missing.sock"),
            timeout_seconds=1,
        )
    assert exc_info.value.status == "unreadable"
    assert exc_info.value.error_code == "parser_unavailable"
