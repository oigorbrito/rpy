from __future__ import annotations

import os
from dataclasses import dataclass
from uuid import UUID

import asyncpg

from app.embedding_spaces import BGE_PROVIDER, EmbeddingSpace, active_embedding_space
from app.embedding_store import upsert_step_embeddings, vector_search_space
from app.embeddings_bge import BGEEmbeddingEncoder

EMBEDDING_BATCH_SIZE = 64


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean")


def embedding_space_runtime_enabled() -> bool:
    """Gate the provider/model-isolated runtime during controlled reindex rollout."""
    return _env_bool("EMBEDDING_SPACE_RUNTIME_ENABLED", False)


@dataclass(slots=True)
class ActiveEmbeddingRuntime:
    space: EmbeddingSpace
    encoder: BGEEmbeddingEncoder

    async def ensure_step_embeddings(
        self,
        pool: asyncpg.Pool,
        *,
        version_id: UUID,
    ) -> int:
        """Fill only vectors missing from this exact provider/model space."""
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT ps.id,
                       coalesce(ps.title, '')
                       || CASE WHEN ps.title IS NULL THEN '' ELSE E'\n' END
                       || ps.text AS content
                FROM process_steps ps
                LEFT JOIN process_step_embeddings pse
                  ON pse.step_id = ps.id
                 AND pse.provider = $2
                 AND pse.model = $3
                WHERE ps.version_id = $1
                  AND pse.step_id IS NULL
                  AND length(trim(coalesce(ps.title, '') || ' ' || ps.text)) > 0
                ORDER BY ps.step_number ASC
                """,
                version_id,
                self.space.provider,
                self.space.model,
            )

        updated = 0
        for start in range(0, len(rows), EMBEDDING_BATCH_SIZE):
            batch = rows[start : start + EMBEDDING_BATCH_SIZE]
            vectors = await self.encoder.embed_documents(
                [str(row["content"]) for row in batch]
            )
            async with pool.acquire() as conn:
                async with conn.transaction():
                    updated += await upsert_step_embeddings(
                        conn,
                        space=self.space,
                        items=[
                            (row["id"], vector)
                            for row, vector in zip(batch, vectors, strict=True)
                        ],
                    )
        return updated

    async def embed_query(self, text: str) -> list[float]:
        return await self.encoder.embed_query(text)

    async def vector_search(
        self,
        conn: asyncpg.Connection,
        *,
        version_id: UUID,
        embedding: list[float],
        limit: int,
    ) -> dict[UUID, float]:
        return await vector_search_space(
            conn,
            version_id=version_id,
            embedding=embedding,
            space=self.space,
            limit=limit,
        )


def build_active_embedding_runtime() -> ActiveEmbeddingRuntime:
    """Build the explicitly enabled deployment runtime without provider fallback."""
    if not embedding_space_runtime_enabled():
        raise RuntimeError("embedding-space runtime is not enabled")
    space = active_embedding_space()
    if space.provider != BGE_PROVIDER:
        raise RuntimeError(
            "Cohere embedding runtime is not implemented yet; "
            "do not enable it or fall back across semantic spaces"
        )
    encoder = BGEEmbeddingEncoder(model=space.model)
    return ActiveEmbeddingRuntime(space=space, encoder=encoder)
