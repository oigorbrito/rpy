from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.db import create_pool
from app.migrations import migrate
from app.retrieval import bm25_scores, lexical_search, load_steps, rank_steps, vector_search

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_long_process_hybrid_retrieval_uses_pgvector_and_forces_mandatory_steps() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)

    process_id = uuid4()
    version_id = uuid4()
    code = "0000000-00.0000.0.00.0301"
    query_vector = [0.0] * 1536
    query_vector[0] = 1.0

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
                "INSERT INTO processes (id, code) VALUES ($1, $2)",
                process_id,
                code,
            )
            await conn.execute(
                """
                INSERT INTO process_versions (id, process_id, source_request_id, finalized)
                VALUES ($1, $2, 'retrieval-integration', TRUE)
                """,
                version_id,
                process_id,
            )
            await conn.execute(
                "UPDATE processes SET current_version_id = $2 WHERE id = $1",
                process_id,
                version_id,
            )

            target_id = None
            milestone_id = None
            for number in range(1, 46):
                step_id = uuid4()
                title = "MOVIMENTO"
                text = f"Movimento processual ordinário número {number}."
                embedding = [0.0] * 1536
                embedding[1] = 1.0

                if number == 12:
                    target_id = step_id
                    text = "Tutela provisória analisada em decisão interlocutória."
                    embedding = list(query_vector)
                elif number == 20:
                    milestone_id = step_id
                    title = "SENTENÇA"
                    text = "Sentença registrada nos autos sem termo da consulta lexical."

                await conn.execute(
                    """
                    INSERT INTO process_steps (
                        id, version_id, process_id, step_number, title, text, embedding
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    step_id,
                    version_id,
                    process_id,
                    number,
                    title,
                    text,
                    embedding,
                )

            assert target_id is not None
            assert milestone_id is not None

            steps = await load_steps(conn, version_id=version_id)
            assert len(steps) == 45

            vector_scores = await vector_search(
                conn,
                version_id=version_id,
                embedding=query_vector,
                limit=45,
            )

        assert target_id in vector_scores
        assert vector_scores[target_id] > 0.99

        ranked = rank_steps(
            query="tutela decisão",
            steps=steps,
            vector_scores=vector_scores,
            limit=10,
        )
        selected = {item.step.id: item for item in ranked}
        selected_numbers = {item.step.step_number for item in ranked}

        assert target_id in selected
        assert selected[target_id].vector > 0.99
        assert selected[target_id].bm25 > 0.0
        assert milestone_id in selected
        assert selected[milestone_id].forced is True

        # Mandatory context independent of ranking score.
        assert 1 in selected_numbers
        assert {41, 42, 43, 44, 45}.issubset(selected_numbers)
        assert 45 in selected_numbers
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_postgres_portuguese_lexical_search_matches_bm25_without_cross_version_leak() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=3)
    process_id = uuid4()
    version_id = uuid4()
    foreign_process_id = uuid4()
    foreign_version_id = uuid4()
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
                "INSERT INTO processes (id, code) VALUES ($1, $2), ($3, $4)",
                process_id,
                "0000000-00.2026.8.21.0120",
                foreign_process_id,
                "0000000-00.2026.8.21.0121",
            )
            await conn.execute(
                """
                INSERT INTO process_versions (id, process_id, source_request_id, finalized)
                VALUES ($1, $2, 'lexical-version', TRUE),
                       ($3, $4, 'lexical-foreign-version', TRUE)
                """,
                version_id,
                process_id,
                foreign_version_id,
                foreign_process_id,
            )
            await conn.execute(
                """
                INSERT INTO process_steps (id, version_id, process_id, step_number, title, text)
                VALUES ($1, $2, $3, 1, 'CITAÇÃO', 'Citação realizada e prazo aberto.'),
                       ($4, $5, $6, 1, 'CITAÇÃO', 'Citação de outro processo não deve aparecer.')
                """,
                uuid4(),
                version_id,
                process_id,
                uuid4(),
                foreign_version_id,
                foreign_process_id,
            )
            lexical = await lexical_search(
                conn,
                version_id=version_id,
                query="citação prazo",
            )
            assert await lexical_search(conn, version_id=version_id, query="") == {}
            steps = await load_steps(conn, version_id=version_id)

        python_scores = bm25_scores("citação prazo", steps)
        assert set(lexical) == {steps[0].id}
        assert set(python_scores) == {steps[0].id}
        assert lexical[steps[0].id] > 0
        assert python_scores[steps[0].id] > 0
    finally:
        await pool.close()
