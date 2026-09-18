from __future__ import annotations

import json
import os
from uuid import UUID, uuid4

import asyncpg
import pytest

import app.judit_attachment_tasks as attachment_tasks
from app.judit_client import JuditAttachmentDownload
from app.judit_tasks import finalize_judit_request_task
from app.migrations import migrate
from app.processes import stage_version

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


def _event(*, request_id: str, response_id: str, code: str) -> dict:
    return {
        "callback_id": f"callback-{uuid4()}",
        "event_type": "response_created",
        "reference_type": "request",
        "reference_id": request_id,
        "payload": {
            "request_id": request_id,
            "response_id": response_id,
            "response_type": "lawsuit",
            "response_data": {
                "code": code,
                "instance": 1,
                "secrecy_level": 0,
                "classifications": [{"name": "Classe teste"}],
                "courts": [{"name": "TJRS"}],
                "parties": [],
                "subjects": [],
                "steps": [{"step_id": "s1", "content": "Movimento preservado"}],
                "attachments": [
                    {
                        "attachment_id": "att-done",
                        "status": "done",
                        "extension": "txt",
                        "step_id": "s1",
                    },
                    {
                        "attachment_id": "att-processing",
                        "status": "processing",
                        "extension": "pdf",
                    },
                ],
            },
            "tags": {"cached_response": False},
        },
    }


async def _reset(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        TRUNCATE jobs, judit_deliveries, process_summaries, process_summary_sources,
                 process_summary_attachment_sources, attachment_chunks, process_attachments,
                 process_steps, tenant_processes, tenant_judit_requests, access_log,
                 process_versions, processes, tenants
        RESTART IDENTITY CASCADE
        """
    )


@pytest.mark.asyncio
async def test_judit_attachment_job_gates_summary_and_processes_authorized_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("JUDIT_ATTACHMENTS_ENABLED", "true")
    monkeypatch.setenv("DATAJUD_ENABLED", "false")

    tenant_id = uuid4()
    request_id = f"request-{uuid4()}"
    response_id = f"response-{uuid4()}"
    code = "0000000-00.2026.8.21.0137"

    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await migrate(TEST_DATABASE_URL)
        await _reset(conn)
        await conn.execute(
            "INSERT INTO tenants (id, name) VALUES ($1, 'Tenant attachments')",
            tenant_id,
        )
        process_id, version_id = await stage_version(
            conn,
            code=code,
            source_request_id=response_id,
            cached_response=False,
            payload=_event(request_id=request_id, response_id=response_id, code=code),
            judit_request_id=request_id,
            judit_response_id=response_id,
            tenant_id=tenant_id,
        )
        await conn.execute(
            """
            INSERT INTO tenant_judit_requests (tenant_id, process_code, judit_request_id)
            VALUES ($1, $2, $3)
            """,
            tenant_id,
            code,
            request_id,
        )
    finally:
        await conn.close()

    finalized = await finalize_judit_request_task({"request_id": request_id})

    assert finalized["status"] == "finalized"
    assert finalized["attachment_job_enqueued"] is True
    assert finalized["summary_enqueued"] is False

    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        rows = await conn.fetch(
            """
            SELECT source_attachment_id, status
            FROM process_attachments
            WHERE version_id=$1
            ORDER BY source_attachment_id
            """,
            version_id,
        )
        jobs = await conn.fetch(
            "SELECT task_name, payload FROM jobs ORDER BY created_at",
        )
    finally:
        await conn.close()

    assert [(row["source_attachment_id"], row["status"]) for row in rows] == [
        ("att-done", "pending"),
        ("att-processing", "pending"),
    ]
    assert [row["task_name"] for row in jobs] == ["process_judit_attachments"]
    attachment_payload = jobs[0]["payload"]
    if isinstance(attachment_payload, str):
        attachment_payload = json.loads(attachment_payload)
    assert attachment_payload["attachments"] == [
        {
            "attachment_id": "att-done",
            "instance": 1,
            "status": "done",
            "extension": "txt",
            "step_id": "s1",
        }
    ]

    observed = {}

    async def fake_url(code_value: str, *, instance: int, attachment_id: str) -> str:
        observed["url_args"] = (code_value, instance, attachment_id)
        return "https://signed-storage.example/private-token"

    async def fake_download(url: str, *, max_bytes: int) -> JuditAttachmentDownload:
        observed["download_url"] = url
        observed["max_bytes"] = max_bytes
        return JuditAttachmentDownload(
            content_type="text/plain",
            data=b"Conteudo sintetico do anexo autorizado.",
        )

    monkeypatch.setattr(attachment_tasks, "get_lawsuit_attachment_url", fake_url)
    monkeypatch.setattr(attachment_tasks, "download_signed_attachment", fake_download)

    result = await attachment_tasks.process_judit_attachments_task(attachment_payload)

    assert result["processed"] == 1
    assert result["unavailable"] == 0
    assert result["summary_enqueued"] is True
    assert observed["url_args"] == (code, 1, "att-done")
    assert observed["download_url"].startswith("https://signed-storage.example/")

    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        states = await conn.fetch(
            """
            SELECT source_attachment_id, status, error_code
            FROM process_attachments
            WHERE version_id=$1
            ORDER BY source_attachment_id
            """,
            version_id,
        )
        chunk = await conn.fetchrow(
            """
            SELECT ac.text
            FROM attachment_chunks ac
            JOIN process_attachments pa ON pa.id=ac.attachment_id
            WHERE pa.version_id=$1 AND pa.source_attachment_id='att-done'
            """,
            version_id,
        )
        job_names = await conn.fetch(
            "SELECT task_name FROM jobs ORDER BY created_at",
        )
        serialized_jobs = await conn.fetchval(
            "SELECT string_agg(payload::text, ' ') FROM jobs",
        )
    finally:
        await conn.close()

    assert [(row["source_attachment_id"], row["status"]) for row in states] == [
        ("att-done", "ready"),
        ("att-processing", "pending"),
    ]
    assert chunk is not None
    assert "Conteudo sintetico" in chunk["text"]
    assert [row["task_name"] for row in job_names] == [
        "process_judit_attachments",
        "generate_process_summary",
    ]
    assert "signed-storage.example" not in str(serialized_jobs)


@pytest.mark.asyncio
async def test_attachment_task_fails_closed_without_tenant_binding_and_still_releases_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)

    process_id = uuid4()
    version_id = uuid4()
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await migrate(TEST_DATABASE_URL)
        await _reset(conn)
        await conn.execute(
            "INSERT INTO processes (id, code, secrecy_level) VALUES ($1, $2, 0)",
            process_id,
            "0000000-00.2026.8.21.0138",
        )
        await conn.execute(
            """
            INSERT INTO process_versions (id, process_id, source_request_id, finalized)
            VALUES ($1, $2, 'source-no-tenant', TRUE)
            """,
            version_id,
            process_id,
        )
        await conn.execute(
            "UPDATE processes SET current_version_id=$2 WHERE id=$1",
            process_id,
            version_id,
        )
        await conn.execute(
            """
            INSERT INTO process_attachments (
                process_id, version_id, source_attachment_id, status
            ) VALUES ($1, $2, 'att-no-tenant', 'pending')
            """,
            process_id,
            version_id,
        )
    finally:
        await conn.close()

    async def forbidden(*args, **kwargs):
        raise AssertionError("provider transport must not run without tenant authorization")

    monkeypatch.setattr(attachment_tasks, "get_lawsuit_attachment_url", forbidden)
    payload = {
        "process_id": str(process_id),
        "version_id": str(version_id),
        "code": "0000000-00.2026.8.21.0138",
        "judit_request_id": "unbound-request",
        "attachments": [
            {
                "attachment_id": "att-no-tenant",
                "instance": 1,
                "status": "done",
            }
        ],
    }

    result = await attachment_tasks.process_judit_attachments_task(payload)

    assert result["processed"] == 0
    assert result["unavailable"] == 1
    assert result["summary_enqueued"] is True

    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        state = await conn.fetchrow(
            """
            SELECT status, error_code
            FROM process_attachments
            WHERE version_id=$1 AND source_attachment_id='att-no-tenant'
            """,
            version_id,
        )
        summary_jobs = await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE task_name='generate_process_summary'"
        )
    finally:
        await conn.close()

    assert state["status"] == "unavailable"
    assert state["error_code"] == "attachment_download_unauthorized"
    assert summary_jobs == 1
