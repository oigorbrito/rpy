from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg


async def get_authorized_process(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    code: str,
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT p.*
        FROM processes p
        JOIN tenant_processes tp ON tp.process_id = p.id
        WHERE tp.tenant_id = $1 AND p.code = $2
        """,
        tenant_id,
        code,
    )


async def log_access(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    process_id: UUID | None,
    process_code: str,
    action: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO access_log (tenant_id, process_id, process_code, action, metadata)
        VALUES ($1, $2, $3, $4, $5::jsonb)
        """,
        tenant_id,
        process_id,
        process_code,
        action,
        json.dumps(metadata or {}),
    )


async def stage_version(
    conn: asyncpg.Connection,
    *,
    code: str,
    source_request_id: str | None,
    cached_response: bool,
    payload: dict[str, Any],
) -> tuple[UUID, UUID]:
    async with conn.transaction():
        process_id = await conn.fetchval(
            """
            INSERT INTO processes (code)
            VALUES ($1)
            ON CONFLICT (code) DO UPDATE SET updated_at = NOW()
            RETURNING id
            """,
            code,
        )
        version_id = await conn.fetchval(
            """
            INSERT INTO process_versions (
                process_id, source_request_id, source_cached_response, source_payload
            )
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (process_id, source_request_id)
            DO UPDATE SET
                source_cached_response = EXCLUDED.source_cached_response,
                source_payload = EXCLUDED.source_payload
            RETURNING id
            """,
            process_id,
            source_request_id,
            cached_response,
            json.dumps(payload),
        )
    return process_id, version_id


async def finalize_version(
    conn: asyncpg.Connection,
    *,
    process_id: UUID,
    version_id: UUID,
    header: dict[str, Any],
    parties: list[dict[str, Any]],
    subjects: list[Any],
    steps: list[dict[str, Any]],
    court: str | None = None,
    class_name: str | None = None,
    secrecy_level: int = 0,
) -> None:
    async with conn.transaction():
        await conn.execute(
            """
            UPDATE processes
            SET court = $2,
                class_name = $3,
                subjects = $4::jsonb,
                parties = $5::jsonb,
                secrecy_level = $6,
                header = $7::jsonb,
                current_version_id = $8,
                updated_at = NOW()
            WHERE id = $1
            """,
            process_id,
            court,
            class_name,
            json.dumps(subjects),
            json.dumps(parties),
            secrecy_level,
            json.dumps(header),
            version_id,
        )
        await conn.execute("DELETE FROM process_steps WHERE version_id = $1", version_id)
        if steps:
            await conn.executemany(
                """
                INSERT INTO process_steps (
                    version_id, process_id, step_number, occurred_at, title, text, metadata
                ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                """,
                [
                    (
                        version_id,
                        process_id,
                        int(step["step_number"]),
                        step.get("occurred_at"),
                        step.get("title"),
                        str(step.get("text") or ""),
                        json.dumps(step.get("metadata") or {}),
                    )
                    for step in steps
                ],
            )
        await conn.execute(
            """
            UPDATE process_versions
            SET finalized = TRUE, finalized_at = NOW()
            WHERE id = $1 AND process_id = $2
            """,
            version_id,
            process_id,
        )
