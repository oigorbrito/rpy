from __future__ import annotations

import os
from collections.abc import Sequence
from uuid import UUID

import asyncpg
from openai import AsyncOpenAI

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_BATCH_SIZE = 64


def _client() -> AsyncOpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required when vector retrieval is used")
    return AsyncOpenAI(api_key=api_key)


async def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    if not texts:
        return []
    response = await _client().embeddings.create(
        model=EMBEDDING_MODEL,
        input=list(texts),
    )
    ordered = sorted(response.data, key=lambda item: item.index)
    return [list(item.embedding) for item in ordered]


async def embed_query(text: str) -> list[float]:
    embeddings = await embed_texts([text])
    return embeddings[0]


async def ensure_step_embeddings(
    pool: asyncpg.Pool,
    *,
    version_id: UUID,
) -> int:
    """Embed only movements that do not already have vectors for this version."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, coalesce(title, '') || CASE WHEN title IS NULL THEN '' ELSE E'\n' END || text AS content
            FROM process_steps
            WHERE version_id = $1 AND embedding IS NULL
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
        if len(vectors) != len(batch):
            raise RuntimeError("embedding provider returned an unexpected number of vectors")

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
