from __future__ import annotations

import os
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.migrations import migrate


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


def _ref(prefix: str, value: UUID) -> str:
    return f"{prefix}-{value.hex}"


async def _reset(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        TRUNCATE process_summary_claim_sources, process_summary_claims,
                 process_summary_attachment_sources, process_summary_sources,
                 process_summaries, process_steps, tenant_processes,
                 process_versions, processes, tenants
        RESTART IDENTITY CASCADE
        """
    )


async def _process_version(conn: asyncpg.Connection, *, code: str):
    process_id = uuid4()
    version_id = uuid4()
    await conn.execute(
        """
        INSERT INTO processes (id, code, class_name, subjects, secrecy_level)
        VALUES ($1, $2, 'Procedimento Comum', '[]'::jsonb, 0)
        """,
        process_id,
        code,
    )
    await conn.execute(
        """
        INSERT INTO process_versions (id, process_id, source_request_id, finalized)
        VALUES ($1, $2, $3, TRUE)
        """,
        version_id,
        process_id,
        f"req-{version_id}",
    )
    await conn.execute(
        "UPDATE processes SET current_version_id=$2 WHERE id=$1",
        process_id,
        version_id,
    )
    return process_id, version_id


@pytest.mark.asyncio
async def test_claim_source_trigger_binds_ref_and_underlying_movement_scope() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await _reset(conn)
        process_id, version_id = await _process_version(
            conn, code="0000000-00.2026.8.21.2501"
        )
        other_process_id, other_version_id = await _process_version(
            conn, code="0000000-00.2026.8.21.2502"
        )

        step_id = uuid4()
        other_step_id = uuid4()
        await conn.executemany(
            """
            INSERT INTO process_steps (
                id, version_id, process_id, step_number, title, text
            ) VALUES ($1, $2, $3, $4, $5, $6)
            """,
            [
                (step_id, version_id, process_id, 1, "Distribuição", "Distribuído."),
                (
                    other_step_id,
                    other_version_id,
                    other_process_id,
                    1,
                    "Outro movimento",
                    "Movimento de outro processo.",
                ),
            ],
        )

        summary_id = await conn.fetchval(
            """
            INSERT INTO process_summaries (
                process_id, version_id, markdown, validation, model, prompt_version
            ) VALUES ($1, $2, '# resumo', '{"passed":true}'::jsonb, 'fake', 'test-v1')
            RETURNING id
            """,
            process_id,
            version_id,
        )
        await conn.execute(
            """
            INSERT INTO process_summary_sources (
                summary_id, process_id, version_id, chunk_type,
                step_id, step_number, source_order
            ) VALUES ($1, $2, $3, 'movement', $4, 1, 0)
            """,
            summary_id,
            process_id,
            version_id,
            step_id,
        )
        claim_row_id = await conn.fetchval(
            """
            INSERT INTO process_summary_claims (
                summary_id, process_id, version_id, claim_id, claim_class, claim_text
            ) VALUES ($1, $2, $3, 'timeline:0', 'procedural_event', 'Distribuído.')
            RETURNING id
            """,
            summary_id,
            process_id,
            version_id,
        )

        await conn.execute(
            """
            INSERT INTO process_summary_claim_sources (
                claim_row_id, summary_id, process_id, version_id,
                evidence_ref, source_kind, step_id, source_order
            ) VALUES ($1, $2, $3, $4, $5, 'movement', $6, 0)
            """,
            claim_row_id,
            summary_id,
            process_id,
            version_id,
            _ref("m", step_id),
            step_id,
        )

        with pytest.raises(asyncpg.RaiseError):
            await conn.execute(
                """
                INSERT INTO process_summary_claim_sources (
                    claim_row_id, summary_id, process_id, version_id,
                    evidence_ref, source_kind, step_id, source_order
                ) VALUES ($1, $2, $3, $4, $5, 'movement', $6, 1)
                """,
                claim_row_id,
                summary_id,
                process_id,
                version_id,
                _ref("m", uuid4()),
                step_id,
            )

        # The legacy summary-source table can represent an inconsistent row.
        # Claim provenance must still reject it by checking process_steps directly.
        await conn.execute(
            """
            INSERT INTO process_summary_sources (
                summary_id, process_id, version_id, chunk_type,
                step_id, step_number, source_order
            ) VALUES ($1, $2, $3, 'movement', $4, 1, 1)
            """,
            summary_id,
            process_id,
            version_id,
            other_step_id,
        )
        other_claim_row_id = await conn.fetchval(
            """
            INSERT INTO process_summary_claims (
                summary_id, process_id, version_id, claim_id, claim_class, claim_text
            ) VALUES ($1, $2, $3, 'current_status', 'current_status', 'Outro movimento.')
            RETURNING id
            """,
            summary_id,
            process_id,
            version_id,
        )
        with pytest.raises(asyncpg.RaiseError):
            await conn.execute(
                """
                INSERT INTO process_summary_claim_sources (
                    claim_row_id, summary_id, process_id, version_id,
                    evidence_ref, source_kind, step_id, source_order
                ) VALUES ($1, $2, $3, $4, $5, 'movement', $6, 0)
                """,
                other_claim_row_id,
                summary_id,
                process_id,
                version_id,
                _ref("m", other_step_id),
                other_step_id,
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_process_claim_ref_must_match_summary_version() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await _reset(conn)
        process_id, version_id = await _process_version(
            conn, code="0000000-00.2026.8.21.2503"
        )
        summary_id = await conn.fetchval(
            """
            INSERT INTO process_summaries (
                process_id, version_id, markdown, validation, model, prompt_version
            ) VALUES ($1, $2, '# resumo', '{"passed":true}'::jsonb, 'fake', 'test-v1')
            RETURNING id
            """,
            process_id,
            version_id,
        )
        claim_row_id = await conn.fetchval(
            """
            INSERT INTO process_summary_claims (
                summary_id, process_id, version_id, claim_id, claim_class, claim_text
            ) VALUES ($1, $2, $3, 'synthesis', 'synthesis', 'Síntese.')
            RETURNING id
            """,
            summary_id,
            process_id,
            version_id,
        )

        await conn.execute(
            """
            INSERT INTO process_summary_claim_sources (
                claim_row_id, summary_id, process_id, version_id,
                evidence_ref, source_kind, source_order
            ) VALUES ($1, $2, $3, $4, $5, 'process', 0)
            """,
            claim_row_id,
            summary_id,
            process_id,
            version_id,
            _ref("p", version_id),
        )

        with pytest.raises(asyncpg.RaiseError):
            await conn.execute(
                """
                INSERT INTO process_summary_claim_sources (
                    claim_row_id, summary_id, process_id, version_id,
                    evidence_ref, source_kind, source_order
                ) VALUES ($1, $2, $3, $4, $5, 'process', 1)
                """,
                claim_row_id,
                summary_id,
                process_id,
                version_id,
                _ref("p", uuid4()),
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_attachment_claim_ref_must_match_authorized_chunk_scope() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await _reset(conn)
        process_id, version_id = await _process_version(
            conn, code="0000000-00.2026.8.21.2504"
        )
        other_process_id, other_version_id = await _process_version(
            conn, code="0000000-00.2026.8.21.2505"
        )

        attachment_id = await conn.fetchval(
            """
            INSERT INTO process_attachments (
                process_id, version_id, source_attachment_id, status, content_type
            ) VALUES ($1,$2,'doc-main','ready','text/plain')
            RETURNING id
            """,
            process_id,
            version_id,
        )
        chunk_id = await conn.fetchval(
            """
            INSERT INTO attachment_chunks (
                attachment_id, process_id, version_id, chunk_index,
                text, content_sha256
            ) VALUES ($1,$2,$3,0,'Documento principal.',
                      repeat('a', 64))
            RETURNING id
            """,
            attachment_id,
            process_id,
            version_id,
        )

        other_attachment_id = await conn.fetchval(
            """
            INSERT INTO process_attachments (
                process_id, version_id, source_attachment_id, status, content_type
            ) VALUES ($1,$2,'doc-other','ready','text/plain')
            RETURNING id
            """,
            other_process_id,
            other_version_id,
        )
        other_chunk_id = await conn.fetchval(
            """
            INSERT INTO attachment_chunks (
                attachment_id, process_id, version_id, chunk_index,
                text, content_sha256
            ) VALUES ($1,$2,$3,0,'Documento de outro processo.',
                      repeat('b', 64))
            RETURNING id
            """,
            other_attachment_id,
            other_process_id,
            other_version_id,
        )

        summary_id = await conn.fetchval(
            """
            INSERT INTO process_summaries (
                process_id, version_id, markdown, validation, model, prompt_version
            ) VALUES ($1,$2,'# resumo','{"passed":true}'::jsonb,'fake','test-v1')
            RETURNING id
            """,
            process_id,
            version_id,
        )
        await conn.execute(
            """
            INSERT INTO process_summary_attachment_sources (
                summary_id, process_id, version_id, attachment_id,
                attachment_chunk_id, source_attachment_id, content_sha256, source_order
            ) VALUES ($1,$2,$3,$4,$5,'doc-main',repeat('a',64),0)
            """,
            summary_id,
            process_id,
            version_id,
            attachment_id,
            chunk_id,
        )
        claim_row_id = await conn.fetchval(
            """
            INSERT INTO process_summary_claims (
                summary_id, process_id, version_id, claim_id, claim_class, claim_text
            ) VALUES ($1,$2,$3,'attachments:0','attachment','Documento principal.')
            RETURNING id
            """,
            summary_id,
            process_id,
            version_id,
        )

        await conn.execute(
            """
            INSERT INTO process_summary_claim_sources (
                claim_row_id, summary_id, process_id, version_id,
                evidence_ref, source_kind, attachment_chunk_id, source_order
            ) VALUES ($1,$2,$3,$4,$5,'attachment',$6,0)
            """,
            claim_row_id,
            summary_id,
            process_id,
            version_id,
            _ref("a", chunk_id),
            chunk_id,
        )

        with pytest.raises(asyncpg.RaiseError):
            await conn.execute(
                """
                UPDATE process_summary_claim_sources
                SET evidence_ref=$2
                WHERE claim_row_id=$1
                """,
                claim_row_id,
                _ref("a", uuid4()),
            )

        # Even if a legacy provenance row is inconsistent, claim provenance
        # must verify the underlying attachment chunk scope directly.
        await conn.execute("ALTER TABLE process_summary_attachment_sources DISABLE TRIGGER process_summary_attachment_sources_scope_check")
        try:
            await conn.execute(
                """
                INSERT INTO process_summary_attachment_sources (
                    summary_id, process_id, version_id, attachment_id,
                    attachment_chunk_id, source_attachment_id, content_sha256, source_order
                ) VALUES ($1,$2,$3,$4,$5,'doc-other',repeat('b',64),1)
                """,
                summary_id,
                process_id,
                version_id,
                other_attachment_id,
                other_chunk_id,
            )
        finally:
            await conn.execute("ALTER TABLE process_summary_attachment_sources ENABLE TRIGGER process_summary_attachment_sources_scope_check")

        other_claim_row_id = await conn.fetchval(
            """
            INSERT INTO process_summary_claims (
                summary_id, process_id, version_id, claim_id, claim_class, claim_text
            ) VALUES ($1,$2,$3,'attachments:1','attachment','Documento de outro processo.')
            RETURNING id
            """,
            summary_id,
            process_id,
            version_id,
        )
        with pytest.raises(asyncpg.RaiseError):
            await conn.execute(
                """
                INSERT INTO process_summary_claim_sources (
                    claim_row_id, summary_id, process_id, version_id,
                    evidence_ref, source_kind, attachment_chunk_id, source_order
                ) VALUES ($1,$2,$3,$4,$5,'attachment',$6,0)
                """,
                other_claim_row_id,
                summary_id,
                process_id,
                version_id,
                _ref("a", other_chunk_id),
                other_chunk_id,
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_claim_provenance_cascades_when_summary_is_deleted() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await _reset(conn)
        process_id, version_id = await _process_version(
            conn, code="0000000-00.2026.8.21.2506"
        )
        summary_id = await conn.fetchval(
            """
            INSERT INTO process_summaries (
                process_id, version_id, markdown, validation, model, prompt_version
            ) VALUES ($1,$2,'# resumo','{"passed":true}'::jsonb,'fake','test-v1')
            RETURNING id
            """,
            process_id,
            version_id,
        )
        claim_row_id = await conn.fetchval(
            """
            INSERT INTO process_summary_claims (
                summary_id, process_id, version_id, claim_id, claim_class, claim_text
            ) VALUES ($1,$2,$3,'synthesis','synthesis','Síntese.')
            RETURNING id
            """,
            summary_id,
            process_id,
            version_id,
        )
        await conn.execute(
            """
            INSERT INTO process_summary_claim_sources (
                claim_row_id, summary_id, process_id, version_id,
                evidence_ref, source_kind, source_order
            ) VALUES ($1,$2,$3,$4,$5,'process',0)
            """,
            claim_row_id,
            summary_id,
            process_id,
            version_id,
            _ref("p", version_id),
        )

        await conn.execute("DELETE FROM process_summaries WHERE id=$1", summary_id)

        assert await conn.fetchval(
            "SELECT count(*) FROM process_summary_claims WHERE summary_id=$1",
            summary_id,
        ) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM process_summary_claim_sources WHERE summary_id=$1",
            summary_id,
        ) == 0
    finally:
        await conn.close()
