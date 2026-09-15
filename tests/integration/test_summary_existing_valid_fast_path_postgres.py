from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.db import create_pool
from app.migrations import migrate
from app.rag import _persist_summary, generate_summary

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.fixture(scope="module", autouse=True)
async def database() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)


async def _fixture(conn):
    process_id = uuid4()
    version_id = uuid4()
    code = f"0000000-00.0000.0.00.{str(process_id.int)[-4:]}"
    await conn.execute(
        "INSERT INTO processes (id, code) VALUES ($1, $2)",
        process_id,
        code,
    )
    await conn.execute(
        "INSERT INTO process_versions (id, process_id, source_request_id) VALUES ($1, $2, $3)",
        version_id,
        process_id,
        f"test-{version_id}",
    )
    await conn.execute(
        "UPDATE processes SET current_version_id = $2 WHERE id = $1",
        process_id,
        version_id,
    )
    return process_id, version_id, code


@pytest.mark.asyncio
async def test_existing_valid_summary_skips_context_retrieval_and_providers(monkeypatch) -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            process_id, version_id, _ = await _fixture(conn)
            assert await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text="accepted summary",
                validation={"passed": True, "errors": []},
                generation_ms=123,
            )

        async def fail_context(*args, **kwargs):
            raise AssertionError("context/retrieval pipeline must not run for an accepted summary")

        def fail_provider(*args, **kwargs):
            raise AssertionError("Anthropic client must not be created for an accepted summary")

        monkeypatch.setattr("app.rag._load_context", fail_context)
        monkeypatch.setattr("app.rag.anthropic_client", fail_provider)

        result = await generate_summary(pool, process_id, version_id)

        assert result == {
            "validation": {"passed": True, "errors": []},
            "model": "claude-sonnet-5",
            "generation_ms": 123,
            "persisted": False,
            "reused": True,
        }
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_existing_invalid_summary_still_regenerates(monkeypatch) -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            process_id, version_id, code = await _fixture(conn)
            assert await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text="invalid first attempt",
                validation={"passed": False, "errors": ["bad"]},
                generation_ms=321,
            )

        context_calls = 0
        generation_calls = 0

        async def fake_context(*args, **kwargs):
            nonlocal context_calls
            context_calls += 1
            return {
                "code": code,
                "court": None,
                "class_name": None,
                "subjects": [],
                "parties": [],
                "secrecy_level": 0,
                "header": {},
                "steps": [],
            }

        async def fake_generate(client, context, validation_errors=None):
            nonlocal generation_calls
            generation_calls += 1
            return "Resumo válido sem dados sensíveis."

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr("app.rag._load_context", fake_context)
        monkeypatch.setattr("app.rag.anthropic_client", lambda api_key: object())
        monkeypatch.setattr("app.rag._generate", fake_generate)

        result = await generate_summary(pool, process_id, version_id)

        async with pool.acquire() as conn:
            stored = await conn.fetchrow(
                """
                SELECT markdown, validation
                FROM process_summaries
                WHERE process_id = $1 AND version_id = $2
                """,
                process_id,
                version_id,
            )

        assert context_calls == 1
        assert generation_calls == 1
        assert result["validation"] == {"passed": True, "errors": []}
        assert result["persisted"] is True
        assert result["reused"] is False
        assert stored is not None
        assert stored["markdown"] == "Resumo válido sem dados sensíveis."
        assert stored["validation"]["passed"] is True
    finally:
        await pool.close()
