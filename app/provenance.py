from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import asyncpg

from app.retrieval import RankedStep


def selected_movement_sources(ranked: Sequence[RankedStep]) -> list[dict[str, Any]]:
    """Return safe metadata for the exact movement context selected for generation."""
    return [
        {
            "step_id": item.step.id,
            "step_number": int(item.step.step_number),
            "occurred_at": item.step.occurred_at,
            "source_order": source_order,
        }
        for source_order, item in enumerate(ranked)
    ]


async def replace_summary_sources(
    conn: asyncpg.Connection,
    *,
    summary_id: UUID,
    process_id: UUID,
    version_id: UUID,
    sources: Sequence[dict[str, Any]],
) -> None:
    """Atomically replace movement provenance for a persisted summary.

    Only identifiers and safe positional metadata are stored. Movement text,
    provider prompts, raw source payloads and party data never cross this table.
    """
    await conn.execute(
        "DELETE FROM process_summary_sources WHERE summary_id = $1",
        summary_id,
    )
    if not sources:
        return

    records: list[tuple[Any, ...]] = []
    for source in sources:
        step_id = source.get("step_id")
        if not isinstance(step_id, UUID):
            raise ValueError("summary source step_id must be a UUID")
        step_number = int(source["step_number"])
        source_order = int(source["source_order"])
        records.append(
            (
                summary_id,
                process_id,
                version_id,
                "movement",
                step_id,
                step_number,
                source.get("occurred_at"),
                source_order,
            )
        )

    await conn.executemany(
        """
        INSERT INTO process_summary_sources (
            summary_id, process_id, version_id, chunk_type,
            step_id, step_number, occurred_at, source_order
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        records,
    )


async def replace_summary_glossary_sources(
    conn: asyncpg.Connection,
    *,
    summary_id: UUID,
    process_id: UUID,
    version_id: UUID,
    sources: Sequence[dict[str, Any]],
) -> None:
    """Replace the exact versioned TPU definitions used for one summary."""
    await conn.execute(
        "DELETE FROM process_summary_glossary_sources WHERE summary_id = $1",
        summary_id,
    )
    if not sources:
        return

    records: list[tuple[Any, ...]] = []
    for source in sources:
        kind = str(source.get("kind") or "").strip()
        code = str(source.get("code") or "").strip()
        tpu_version = str(source.get("tpu_version") or "").strip()
        publisher = str(source.get("publisher") or "").strip()
        origin = str(source.get("source") or "").strip()
        source_ref = str(source.get("source_ref") or "").strip()
        digest = str(source.get("definition_sha256") or "").strip()
        source_order = int(source.get("source_order", 0))
        if kind not in {"class", "subject"}:
            raise ValueError("invalid TPU glossary provenance kind")
        if not all((code, tpu_version, publisher, origin, source_ref)):
            raise ValueError("TPU glossary provenance is missing required metadata")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid TPU glossary definition_sha256")
        records.append(
            (
                summary_id,
                process_id,
                version_id,
                kind,
                code,
                tpu_version,
                publisher,
                origin,
                source_ref,
                digest,
                source_order,
            )
        )

    await conn.executemany(
        """
        INSERT INTO process_summary_glossary_sources (
            summary_id, process_id, version_id, kind, code, tpu_version,
            publisher, source, source_ref, definition_sha256, source_order
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """,
        records,
    )


async def load_used_summary_sources(
    conn: asyncpg.Connection,
    *,
    summary_id: UUID,
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT pss.chunk_type,
               pss.step_id,
               pss.step_number,
               pss.occurred_at,
               pss.source_order,
               ps.title,
               CASE
                   WHEN jsonb_typeof(ps.metadata->'source_step_number') = 'number'
                   THEN (ps.metadata->>'source_step_number')::bigint
                   ELSE NULL
               END AS source_step_number
        FROM process_summary_sources pss
        JOIN process_steps ps
          ON ps.id = pss.step_id
         AND ps.process_id = pss.process_id
         AND ps.version_id = pss.version_id
        WHERE pss.summary_id = $1
        ORDER BY pss.source_order
        """,
        summary_id,
    )
    return [
        {
            "kind": "movement",
            "chunk_type": str(row["chunk_type"]),
            "used_for_summary": True,
            "step_id": str(row["step_id"]),
            "step_number": int(row["step_number"]),
            "source_step_number": (
                int(row["source_step_number"])
                if row["source_step_number"] is not None
                else None
            ),
            "occurred_at": row["occurred_at"],
            "title": row["title"],
            "source_order": int(row["source_order"]),
        }
        for row in rows
    ]
