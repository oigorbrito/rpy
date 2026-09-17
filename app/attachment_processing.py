from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import asyncpg

from app.attachments import AttachmentChunkInput, replace_attachment_chunks, upsert_attachment_state

DEFAULT_ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_ATTACHMENT_CHUNK_CHARS = 4_000
MIN_ATTACHMENT_CHUNK_CHARS = 256

FailureStatus = Literal["corrupt", "unreadable"]


@dataclass(frozen=True, slots=True)
class AttachmentProcessingLimits:
    max_bytes: int = DEFAULT_ATTACHMENT_MAX_BYTES
    chunk_chars: int = DEFAULT_ATTACHMENT_CHUNK_CHARS


class AttachmentProcessingError(ValueError):
    def __init__(self, *, status: FailureStatus, error_code: str):
        super().__init__(error_code)
        self.status = status
        self.error_code = error_code


def _positive_env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero")
    return value


def attachment_processing_limits() -> AttachmentProcessingLimits:
    max_bytes = _positive_env_int("ATTACHMENT_MAX_BYTES", DEFAULT_ATTACHMENT_MAX_BYTES)
    chunk_chars = _positive_env_int("ATTACHMENT_CHUNK_CHARS", DEFAULT_ATTACHMENT_CHUNK_CHARS)
    if chunk_chars < MIN_ATTACHMENT_CHUNK_CHARS:
        raise RuntimeError(
            f"ATTACHMENT_CHUNK_CHARS must be at least {MIN_ATTACHMENT_CHUNK_CHARS}"
        )
    return AttachmentProcessingLimits(max_bytes=max_bytes, chunk_chars=chunk_chars)


def normalize_content_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def _validate_text_payload(
    data: bytes,
    *,
    content_type: str,
    limits: AttachmentProcessingLimits,
) -> str:
    normalized_type = normalize_content_type(content_type)
    if normalized_type != "text/plain":
        raise AttachmentProcessingError(
            status="unreadable",
            error_code="unsupported_content_type",
        )
    if len(data) > limits.max_bytes:
        raise AttachmentProcessingError(
            status="unreadable",
            error_code="attachment_too_large",
        )
    if not data:
        raise AttachmentProcessingError(
            status="unreadable",
            error_code="empty_attachment",
        )
    if b"\x00" in data:
        raise AttachmentProcessingError(
            status="corrupt",
            error_code="invalid_text_bytes",
        )
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AttachmentProcessingError(
            status="corrupt",
            error_code="invalid_utf8",
        ) from exc

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise AttachmentProcessingError(
            status="unreadable",
            error_code="empty_text",
        )
    return normalized


def chunk_attachment_text(text: str, *, chunk_chars: int) -> list[AttachmentChunkInput]:
    if chunk_chars < MIN_ATTACHMENT_CHUNK_CHARS:
        raise ValueError(f"chunk_chars must be at least {MIN_ATTACHMENT_CHUNK_CHARS}")
    if not text:
        return []

    chunks: list[AttachmentChunkInput] = []
    cursor = 0
    text_length = len(text)
    while cursor < text_length:
        hard_end = min(cursor + chunk_chars, text_length)
        end = hard_end
        if hard_end < text_length:
            newline = text.rfind("\n", cursor, hard_end)
            space = text.rfind(" ", cursor, hard_end)
            preferred = max(newline, space)
            if preferred > cursor + (chunk_chars // 2):
                end = preferred

        while end > cursor and text[end - 1].isspace():
            end -= 1
        if end <= cursor:
            end = hard_end

        chunk_text = text[cursor:end]
        chunks.append(
            AttachmentChunkInput(
                text=chunk_text,
                char_start=cursor,
                char_end=end,
            )
        )
        cursor = end
        while cursor < text_length and text[cursor].isspace():
            cursor += 1

    return chunks


def parse_text_attachment(
    data: bytes,
    *,
    content_type: str,
    limits: AttachmentProcessingLimits | None = None,
) -> list[AttachmentChunkInput]:
    effective_limits = limits or attachment_processing_limits()
    text = _validate_text_payload(
        data,
        content_type=content_type,
        limits=effective_limits,
    )
    return chunk_attachment_text(text, chunk_chars=effective_limits.chunk_chars)


async def process_text_attachment_bytes(
    conn: asyncpg.Connection,
    *,
    process_id: UUID,
    version_id: UUID,
    source_attachment_id: str,
    content_type: str,
    data: bytes,
    limits: AttachmentProcessingLimits | None = None,
) -> dict[str, object]:
    """Parse and persist authorized UTF-8 text bytes without retaining the raw bytes."""
    effective_limits = limits or attachment_processing_limits()
    digest = hashlib.sha256(data).hexdigest()
    try:
        chunks = parse_text_attachment(
            data,
            content_type=content_type,
            limits=effective_limits,
        )
    except AttachmentProcessingError as exc:
        attachment_id = await upsert_attachment_state(
            conn,
            process_id=process_id,
            version_id=version_id,
            source_attachment_id=source_attachment_id,
            status=exc.status,
            content_type=normalize_content_type(content_type),
            byte_size=len(data),
            content_sha256=digest,
            error_code=exc.error_code,
        )
        return {
            "attachment_id": attachment_id,
            "status": exc.status,
            "error_code": exc.error_code,
            "chunk_count": 0,
        }

    attachment_id = await upsert_attachment_state(
        conn,
        process_id=process_id,
        version_id=version_id,
        source_attachment_id=source_attachment_id,
        status="pending",
        content_type=normalize_content_type(content_type),
        byte_size=len(data),
        content_sha256=digest,
    )
    chunk_count = await replace_attachment_chunks(
        conn,
        attachment_id=attachment_id,
        process_id=process_id,
        version_id=version_id,
        chunks=chunks,
    )
    return {
        "attachment_id": attachment_id,
        "status": "ready",
        "error_code": None,
        "chunk_count": chunk_count,
    }
