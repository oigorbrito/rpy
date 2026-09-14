from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any
from uuid import UUID

import asyncpg

from app.providers import (
    call_with_retries,
    embedding_settings,
    is_retryable_openai_error,
    openai_client,
)

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
# Schema contract: sql/002_process_data.sql defines process_steps.embedding as
# vector(1536). Changing this value requires a database migration and index rebuild;
# it is intentionally not configurable through the environment.
VECTOR_DIMENSIONS = 1536
EMBEDDING_BATCH_SIZE = 64
_DIMENSION_CONFIGURABLE_MODELS = {"text-embedding-3-small", "text-embedding-3-large"}
_FIXED_1536_MODELS = {"text-embedding-ada-002"}


def _client():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required when vector retrieval is used")
    return openai_client(api_key)


def validate_embedding_model(model: str | None = None) -> str:
    configured = str(model or EMBEDDING_MODEL).strip()
    if not configured:
        raise RuntimeError("EMBEDDING_MODEL must not be empty")
    if configured in _DIMENSION_CONFIGURABLE_MODELS or configured in _FIXED_1536_MODELS:
        return configured
    raise RuntimeError(
        "unsupported EMBEDDING_MODEL for vector(1536) schema: "
        f"{configured}; update the embedding/schema contract deliberately before using it"
    )


def _request_kwargs(texts: Sequence[str]) -> dict[str, Any]:
    model = validate_embedding_model()
    request: dict[str, Any] = {
        "model": model,
        "input": list(texts),
    }
    # OpenAI text-embedding-3 models support explicit dimensionality. Pin them to
    # PostgreSQL vector(1536) even when the larger model is selected.
    if model in _DIMENSION_CONFIGURABLE_MODELS:
        request["dimensions"] = VECTOR_DIMENSIONS
    return request


def _validate_response(data: Sequence[Any], *, expected_count: int) -> list[list[float]]:
    if len(data) != expected_count:
        raise RuntimeError("embedding provider returned an unexpected number of vectors")

    ordered = sorted(data, key=lambda item: int(item.index))
    expected_indexes = list(range(expected_count))
    actual_indexes = [int(item.index) for item in ordered]
    if actual_indexes != expected_indexes:
        raise RuntimeError("embedding provider returned unexpected vector indexes")

    vectors = [list(item.embedding) for item in ordered]
    for vector in vectors:
        if len(vector) != VECTOR_DIMENSIONS:
            raise RuntimeError(
                f"embedding dimension mismatch: expected {VECTOR_DIMENSIONS}, got {len(vector)}"
            )
    return vectors


async def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    if not texts:
        return []
    if any(not str(text).strip() for text in texts):
        raise ValueError("embedding inputs must be non-empty text")

    # Validate before API-key lookup and before any provider/network work so bad
    # deployment configuration fails deterministically and cheaply.
    validate_embedding_model()
    client = _client()
    settings = embedding_settings()

    async def create_embedding():
        return await client.embeddings.create(**_request_kwargs(texts))

    response = await call_with_retries(
        create_embedding,
        is_retryable=is_retryable_openai_error,
        settings=settings,
    )
    return _validate_response(response.data, expected_count=len(texts))


async def embed_query(text: str) -> list[float]:
    embeddings = await embed_texts([text])
    return embeddings[0]


async def ensure_step_embeddings(
    pool: asyncpg.Pool,
    *,
    version_id: UUID,
) -> int:
    """Embed only non-empty movements that do not already have vectors."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id,
                   coalesce(title, '')
                   || CASE WHEN title IS NULL THEN '' ELSE E'\n' END
                   || text AS content
            FROM process_steps
            WHERE version_id = $1
              AND embedding IS NULL
              AND length(trim(coalesce(title, '') || ' ' || text)) > 0
            ORDER BY step_number ASC
            """,
            version_id,
        )

    if not rows:
        return 0

    updated = 0
    for start in range(0, len(rows), EMBEDDING_BATCH_SIZE):
        batch = rows[start : start + EMBEDDING_BATCH_SIZE]
        vectors = await embed_texts([str(row["content"]) for row in batch])

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.executemany(
                    """
                    UPDATE process_steps
                    SET embedding = $2::vector
                    WHERE id = $1 AND version_id = $3
                    """,
                    [
                        (row["id"], vector, version_id)
                        for row, vector in zip(batch, vectors, strict=True)
                    ],
                )
        updated += len(batch)

    return updated
