from __future__ import annotations

import pytest

from app.attachment_processing import (
    AttachmentProcessingError,
    AttachmentProcessingLimits,
    chunk_attachment_text,
    normalize_content_type,
    parse_text_attachment,
)


def test_content_type_normalization_strips_parameters():
    assert normalize_content_type(" Text/Plain; charset=UTF-8 ") == "text/plain"


def test_parse_text_attachment_normalizes_utf8_and_preserves_positions():
    chunks = parse_text_attachment(
        b"Primeira linha\r\nSegunda linha\r\nTerceira linha",
        content_type="text/plain; charset=utf-8",
        limits=AttachmentProcessingLimits(max_bytes=1024, chunk_chars=256),
    )
    assert len(chunks) == 1
    assert chunks[0].text == "Primeira linha\nSegunda linha\nTerceira linha"
    assert chunks[0].char_start == 0
    assert chunks[0].char_end == len(chunks[0].text)


def test_chunking_is_deterministic_without_overlap():
    text = ("abc " * 200).strip()
    chunks = chunk_attachment_text(text, chunk_chars=256)
    assert len(chunks) > 1
    for previous, current in zip(chunks, chunks[1:]):
        assert previous.char_end is not None
        assert current.char_start is not None
        assert previous.char_end <= current.char_start
    reconstructed = " ".join(chunk.text for chunk in chunks)
    assert reconstructed.replace("  ", " ") == text


@pytest.mark.parametrize(
    ("data", "content_type", "status", "error_code"),
    [
        (b"", "text/plain", "unreadable", "empty_attachment"),
        (b"\xff\xfe", "text/plain", "corrupt", "invalid_utf8"),
        (b"abc\x00def", "text/plain", "corrupt", "invalid_text_bytes"),
        (b"texto", "application/pdf", "unreadable", "unsupported_content_type"),
    ],
)
def test_parse_text_attachment_classifies_failures(data, content_type, status, error_code):
    with pytest.raises(AttachmentProcessingError) as exc_info:
        parse_text_attachment(
            data,
            content_type=content_type,
            limits=AttachmentProcessingLimits(max_bytes=1024, chunk_chars=256),
        )
    assert exc_info.value.status == status
    assert exc_info.value.error_code == error_code


def test_parse_text_attachment_rejects_oversized_payload():
    with pytest.raises(AttachmentProcessingError) as exc_info:
        parse_text_attachment(
            b"a" * 11,
            content_type="text/plain",
            limits=AttachmentProcessingLimits(max_bytes=10, chunk_chars=256),
        )
    assert exc_info.value.status == "unreadable"
    assert exc_info.value.error_code == "attachment_too_large"
