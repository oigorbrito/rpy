from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest

import app.rag as rag
from app.db import create_pool
from app.migrations import migrate

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_secret_process_never_loads_steps_or_exposes_parties(monkeypatch: pytest.MonkeyPatch) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)

    process_id = uuid4()
    version_id = uuid4()
    code = "0000000-00.0000.0.00.0401"
    allowed_header = {"instance": 1, "area": "Cível", "state": "RS"}

    async def forbidden_load_steps(*args, **kwargs):
        raise AssertionError("secret proceedings must not load movement text")

    monkeypatch.setattr(rag, "load_steps", forbidden_load_steps)

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                TRUNCATE process_summaries, process_steps, tenant_processes,
                         access_log, process_versions, processes
                RESTART IDENTITY CASCADE
                """
            )
            await conn.execute(
                """
                INSERT INTO processes (
                    id, code, court, class_name, subjects, parties,
                    secrecy_level, header
                ) VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, 1, $7::jsonb)
                """,
                process_id,
                code,
                "TJRS",
                "Procedimento sob sigilo",
                json.dumps([{"name": "Assunto sensível"}]),
                json.dumps([{"name": "Pessoa Segredada", "external_id": "secret-party-01"}]),
                json.dumps(allowed_header),
            )
            await conn.execute(
                """
                INSERT INTO process_versions (id, process_id, source_request_id, finalized)
                VALUES ($1, $2, 'secret-integration', TRUE)
                """,
                version_id,
                process_id,
            )
            await conn.execute(
                "UPDATE processes SET current_version_id = $2 WHERE id = $1",
                process_id,
                version_id,
            )
            await conn.execute(
                """
                INSERT INTO process_steps (
                    version_id, process_id, step_number, title, text
                ) VALUES ($1, $2, 1, 'SEGREDO', 'Conteúdo processual altamente sensível')
                """,
                version_id,
                process_id,
            )

        context = await rag._load_context(pool, process_id, version_id)

        assert context == {
            "code": code,
            "class_name": "Procedimento sob sigilo",
            "secrecy_level": 1,
            "header": allowed_header,
            "parties": [],
            "subjects": [],
            "steps": [],
        }
        serialized = json.dumps(context, ensure_ascii=False)
        assert "Pessoa Segredada" not in serialized
        assert "secret-party-01" not in serialized
        assert "Conteúdo processual altamente sensível" not in serialized
        assert "Assunto sensível" not in serialized
    finally:
        await pool.close()
