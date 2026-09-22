from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest

from app.db import create_pool
from app.judit_tasks import finalize_judit_request_task
from app.migrations import migrate
from app.processes import stage_version

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


def _lawsuit_event(
    *,
    request_id: str,
    response_id: str,
    callback_id: str,
    code: str,
    class_name: str = "Procedimento Comum",
    extra_step: bool = False,
    attachments: list[dict] | None = None,
) -> dict:
    steps = [
        {
            "step_id": "step-1",
            "step_number": 10,
            "step_date": "2026-06-10T12:00:00Z",
            "step_type": "CITAÇÃO",
            "content": "10 - CitaçãoRealizada",
            "private": False,
        }
    ]
    if extra_step:
        steps.append(
            {
                "step_id": "step-2",
                "step_number": 20,
                "step_date": "2026-06-11T12:00:00Z",
                "step_type": "SENTENÇA",
                "content": "20 - SentençaProferida",
                "private": False,
            }
        )
    return {
        "callback_id": callback_id,
        "event_type": "response_created",
        "reference_type": "request",
        "reference_id": request_id,
        "transport_nonce": str(uuid4()),
        "payload": {
            "request_id": request_id,
            "response_id": response_id,
            "response_type": "lawsuit",
            "response_data": {
                "code": code,
                "instance": 1,
                "classifications": [{"name": class_name}],
                "courts": [{"name": "TJRS"}],
                "parties": [],
                "subjects": [{"code": "1", "name": "Obrigação"}],
                "steps": steps,
                "attachments": attachments or [],
            },
            "tags": {"cached_response": False},
        },
    }


async def _stage(pool, event: dict) -> tuple:
    payload = event["payload"]
    data = payload["response_data"]
    async with pool.acquire() as conn:
        return await stage_version(
            conn,
            code=data["code"],
            source_request_id=payload["response_id"],
            cached_response=False,
            payload=event,
            judit_request_id=payload["request_id"],
            judit_response_id=payload["response_id"],
            judit_callback_id=event["callback_id"],
        )


@pytest.mark.asyncio
async def test_semantically_equal_response_reuses_current_version_and_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=4)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE jobs, process_summaries, process_steps, tenant_processes,
                     access_log, process_versions, processes, tenants
            RESTART IDENTITY CASCADE
            """
        )

    code = "0000000-00.2026.8.21.0136"
    first_request = f"req-first-{uuid4()}"
    first_response = f"resp-first-{uuid4()}"
    _, first_version = await _stage(
        pool,
        _lawsuit_event(
            request_id=first_request,
            response_id=first_response,
            callback_id=f"cb-first-{uuid4()}",
            code=code,
        ),
    )
    first_result = await finalize_judit_request_task({"request_id": first_request})
    assert first_result["status"] == "finalized"
    assert first_result["promoted"] is True
    assert first_result["summary_enqueued"] is True

    async with pool.acquire() as conn:
        process_id = await conn.fetchval("SELECT id FROM processes WHERE code = $1", code)
        await conn.execute(
            """
            INSERT INTO process_summaries (
                process_id, version_id, markdown, validation, model, prompt_version, generation_ms
            ) VALUES ($1, $2, '# resumo sintético', $3::jsonb, 'fake', 'semantic-reuse-test', 1)
            """,
            process_id,
            first_version,
            json.dumps({"passed": True, "errors": []}),
        )

    duplicate_request = f"req-duplicate-{uuid4()}"
    duplicate_response = f"resp-duplicate-{uuid4()}"
    _, duplicate_version = await _stage(
        pool,
        _lawsuit_event(
            request_id=duplicate_request,
            response_id=duplicate_response,
            callback_id=f"cb-duplicate-{uuid4()}",
            code=code,
        ),
    )
    duplicate_result = await finalize_judit_request_task({"request_id": duplicate_request})

    assert duplicate_result["status"] == "finalized_unchanged"
    assert duplicate_result["promoted"] is False
    assert duplicate_result["summary_enqueued"] is False
    assert duplicate_result["equivalent_to_version_id"] == str(first_version)

    async with pool.acquire() as conn:
        current_version = await conn.fetchval(
            "SELECT current_version_id FROM processes WHERE id = $1", process_id
        )
        duplicate = await conn.fetchrow(
            """
            SELECT finalized, semantic_fingerprint, semantic_schema_version,
                   equivalent_to_version_id, source_payload
            FROM process_versions WHERE id = $1
            """,
            duplicate_version,
        )
        duplicate_steps = await conn.fetchval(
            "SELECT count(*) FROM process_steps WHERE version_id = $1",
            duplicate_version,
        )
        generation_jobs = await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE task_name = 'generate_process_summary'"
        )
        summaries = await conn.fetchval(
            "SELECT count(*) FROM process_summaries WHERE process_id = $1", process_id
        )

    assert current_version == first_version
    assert duplicate["finalized"] is True
    assert duplicate["semantic_fingerprint"]
    assert duplicate["semantic_schema_version"] == 3
    assert duplicate["equivalent_to_version_id"] == first_version
    assert duplicate_response in json.dumps(duplicate["source_payload"], ensure_ascii=False)
    assert duplicate_steps == 0
    assert generation_jobs == 1
    assert summaries == 1

    await pool.close()


@pytest.mark.asyncio
async def test_new_movement_or_relevant_metadata_creates_new_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=4)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE jobs, process_summaries, process_steps, tenant_processes,
                     access_log, process_versions, processes, tenants
            RESTART IDENTITY CASCADE
            """
        )

    code = "0000000-00.2026.8.21.0137"
    request_1 = f"req-1-{uuid4()}"
    _, version_1 = await _stage(
        pool,
        _lawsuit_event(
            request_id=request_1,
            response_id=f"resp-1-{uuid4()}",
            callback_id=f"cb-1-{uuid4()}",
            code=code,
        ),
    )
    assert (await finalize_judit_request_task({"request_id": request_1}))["promoted"] is True

    request_2 = f"req-2-{uuid4()}"
    _, version_2 = await _stage(
        pool,
        _lawsuit_event(
            request_id=request_2,
            response_id=f"resp-2-{uuid4()}",
            callback_id=f"cb-2-{uuid4()}",
            code=code,
            extra_step=True,
        ),
    )
    result_2 = await finalize_judit_request_task({"request_id": request_2})
    assert result_2["status"] == "finalized"
    assert result_2["promoted"] is True
    assert result_2["summary_enqueued"] is True

    request_3 = f"req-3-{uuid4()}"
    _, version_3 = await _stage(
        pool,
        _lawsuit_event(
            request_id=request_3,
            response_id=f"resp-3-{uuid4()}",
            callback_id=f"cb-3-{uuid4()}",
            code=code,
            class_name="Execução Fiscal",
            extra_step=True,
        ),
    )
    result_3 = await finalize_judit_request_task({"request_id": request_3})
    assert result_3["status"] == "finalized"
    assert result_3["promoted"] is True
    assert result_3["summary_enqueued"] is True

    async with pool.acquire() as conn:
        current_version = await conn.fetchval(
            "SELECT current_version_id FROM processes WHERE code = $1", code
        )
        generation_jobs = await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE task_name = 'generate_process_summary'"
        )
        step_counts = [
            await conn.fetchval(
                "SELECT count(*) FROM process_steps WHERE version_id = $1", version
            )
            for version in (version_1, version_2, version_3)
        ]

    assert current_version == version_3
    assert generation_jobs == 3
    assert step_counts == [1, 2, 2]

    await pool.close()



@pytest.mark.asyncio
async def test_new_attachment_manifest_promotes_version_and_persists_pending_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=4)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE jobs, process_summary_attachment_sources, attachment_chunks,
                     process_attachments, process_summaries, process_steps,
                     tenant_processes, access_log, process_versions, processes, tenants
            RESTART IDENTITY CASCADE
            """
        )

    code = "0000000-00.2026.8.21.0138"
    request_1 = f"req-1-{uuid4()}"
    _, version_1 = await _stage(
        pool,
        _lawsuit_event(
            request_id=request_1,
            response_id=f"resp-1-{uuid4()}",
            callback_id=f"cb-1-{uuid4()}",
            code=code,
        ),
    )
    assert (await finalize_judit_request_task({"request_id": request_1}))["promoted"] is True

    request_2 = f"req-2-{uuid4()}"
    _, version_2 = await _stage(
        pool,
        _lawsuit_event(
            request_id=request_2,
            response_id=f"resp-2-{uuid4()}",
            callback_id=f"cb-2-{uuid4()}",
            code=code,
            attachments=[
                {
                    "attachment_id": "att-decisao-1",
                    "attachment_date": "2026-09-17T12:00:00Z",
                    "attachment_name": "DECISAO 1.pdf",
                    "status": "done",
                    "signed_url": "https://must-not-persist.invalid/attachment",
                }
            ],
        ),
    )
    result_2 = await finalize_judit_request_task({"request_id": request_2})

    assert result_2["status"] == "finalized"
    assert result_2["promoted"] is True
    assert result_2["summary_enqueued"] is True

    async with pool.acquire() as conn:
        current_version = await conn.fetchval(
            "SELECT current_version_id FROM processes WHERE code=$1", code
        )
        attachment = await conn.fetchrow(
            """
            SELECT source_attachment_id, source_name, source_date, provider_status,
                   status, content_sha256
            FROM process_attachments
            WHERE version_id=$1
            """,
            version_2,
        )
        schema_version = await conn.fetchval(
            "SELECT semantic_schema_version FROM process_versions WHERE id=$1",
            version_2,
        )

    assert current_version == version_2
    assert current_version != version_1
    assert attachment["source_attachment_id"] == "att-decisao-1"
    assert attachment["source_name"] == "DECISAO 1.pdf"
    assert attachment["source_date"].isoformat() == "2026-09-17T12:00:00+00:00"
    assert attachment["provider_status"] == "done"
    assert attachment["status"] == "pending"
    assert attachment["content_sha256"] is None
    assert schema_version == 2

    await pool.close()
