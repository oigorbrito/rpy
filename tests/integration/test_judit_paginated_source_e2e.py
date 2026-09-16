from __future__ import annotations

import json
import os
from uuid import uuid4

import httpx
import pytest

import app.rag as rag
from app.api import app
from app.db import create_pool
from app.migrations import migrate
from app.worker import Worker, WorkerSettings

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_paginated_request_uses_only_lawsuit_for_rag_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)

    pool = await create_pool(TEST_DATABASE_URL, min_size=2, max_size=6)
    app.state.pool = pool
    monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN", "paginated-e2e-webhook")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    async with pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE jobs, judit_request_completions, judit_deliveries,
                     process_summaries, process_steps, tenant_processes, access_log,
                     tenant_judit_requests, process_versions, processes, tenants
            RESTART IDENTITY CASCADE
            """
        )

    request_id = f"req-paginated-{uuid4()}"
    lawsuit_response_id = f"resp-lawsuit-{uuid4()}"
    summary_response_id = f"resp-summary-{uuid4()}"
    lawsuit_callback_id = f"cb-lawsuit-{uuid4()}"
    summary_callback_id = f"cb-summary-{uuid4()}"
    completion_callback_id = f"cb-completed-{uuid4()}"
    code = "0000000-00.2026.8.21.0111"
    external_summary = "SUMMARY_SENTINEL_MUST_NOT_REACH_RAG_CONTEXT"
    summary_personal_id = "98765432100"
    lawsuit_personal_id = "12345678901"
    party_personal_id = "11122233344"
    captured_provider_sources: list[str] = []

    async def fake_generate(client, context, validation_errors=None):
        source = rag._provider_source_text(context)
        captured_provider_sources.append(source)
        assert external_summary not in source
        assert summary_personal_id not in source
        assert lawsuit_personal_id not in source
        assert party_personal_id not in source
        assert "***.***.***-44" in source
        assert "[documento removido]" in source
        assert "ELETRÔNICA REFER" in source
        assert "ELETRÔNICAREFER" not in source
        assert "Sentença Proferida" in source
        assert "2026-06-10T09:00:00-03:00" in source
        assert "2026-06-11T09:00:00-03:00" in source
        return f"""# Resumo do processo

<ProcessHeader className=\"process-header\">
- Processo: {code}
- Classe: Procedimento Comum
- Tribunal: TJRS
</ProcessHeader>

## Síntese
Há movimentos processuais normalizados na fonte autorizada.

## Linha do tempo relevante
- Distribuição registrada.
- Sentença proferida.

## Situação atual
O último movimento fornecido é uma sentença.

## Pontos de atenção
Nenhuma divergência objetiva identificada.
"""

    monkeypatch.setattr(rag, "_generate", fake_generate)

    lawsuit_body = {
        "callback_id": lawsuit_callback_id,
        "event_type": "response_created",
        "reference_type": "request",
        "reference_id": request_id,
        "payload": {
            "request_id": request_id,
            "response_id": lawsuit_response_id,
            "response_type": "lawsuit",
            "response_data": {
                "code": code,
                "instance": 1,
                "classifications": [{"name": "Procedimento Comum"}],
                "courts": [{"name": "TJRS"}],
                "parties": [
                    {
                        "name": "Parte Sintética",
                        "side": "Active",
                        "person_type": "Autor",
                        "main_document": party_personal_id,
                    }
                ],
                "subjects": [{"code": "1", "name": "Obrigação"}],
                "steps": [
                    {
                        "step_id": "step-1",
                        "step_number": 10,
                        "step_date": "2026-06-10T12:00:00Z",
                        "step_type": "DISTRIBUIÇÃO",
                        "content": "10   ProcessoDistribuído ELETRÔNICAREFER nos autos.",
                    },
                    {
                        "step_id": "step-2",
                        "step_number": 20,
                        "step_date": "2026-06-11T12:00:00Z",
                        "step_type": "SENTENÇA",
                        "content": f"20 - SentençaProferida CPF {lawsuit_personal_id}",
                    },
                ],
            },
            "tags": {"cached_response": False},
        },
    }
    summary_body = {
        "callback_id": summary_callback_id,
        "event_type": "response_created",
        "reference_type": "request",
        "reference_id": request_id,
        "payload": {
            "request_id": request_id,
            "response_id": summary_response_id,
            "response_type": "summary",
            "response_data": {
                "code": code,
                "summary": external_summary,
                "person_id": summary_personal_id,
            },
            "tags": {"cached_response": False},
        },
    }
    completion_body = {
        "callback_id": completion_callback_id,
        "event_type": "request_completed",
        "reference_type": "request",
        "reference_id": request_id,
        "payload": {"status": "completed"},
    }

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for body in (lawsuit_body, summary_body, completion_body):
                response = await client.post(
                    "/webhooks/judit/paginated-e2e-webhook",
                    json=body,
                )
                assert response.status_code == 200

        worker = Worker(
            pool,
            WorkerSettings(
                database_url=TEST_DATABASE_URL,
                concurrency=1,
                heartbeat_interval_seconds=60,
                stale_after_seconds=120,
                task_timeout_seconds=20,
                reclaim_interval_seconds=60,
            ),
        )
        assert await worker.process_one() is True
        assert await worker.process_one() is True
        assert await worker.process_one() is False
        assert captured_provider_sources

        async with pool.acquire() as conn:
            process = await conn.fetchrow(
                "SELECT id, current_version_id, parties FROM processes WHERE code = $1",
                code,
            )
            assert process is not None
            version_id = process["current_version_id"]
            assert version_id is not None

            normalized_parties = process["parties"]
            assert len(normalized_parties) == 1
            assert normalized_parties[0]["name"] == "Parte Sintética"
            assert normalized_parties[0]["masked_person_id"] == "***.***.***-44"
            assert party_personal_id not in json.dumps(normalized_parties, ensure_ascii=False)

            version = await conn.fetchrow(
                """
                SELECT source_payload, judit_response_id, judit_request_id
                FROM process_versions
                WHERE id = $1
                """,
                version_id,
            )
            assert version is not None
            rendered_source = json.dumps(version["source_payload"], ensure_ascii=False)
            assert version["judit_response_id"] == lawsuit_response_id
            assert version["judit_request_id"] == request_id
            assert external_summary not in rendered_source
            assert summary_personal_id not in rendered_source
            assert party_personal_id in rendered_source
            assert "ELETRÔNICAREFER" in rendered_source

            deliveries = await conn.fetch(
                """
                SELECT callback_id, raw_payload
                FROM judit_deliveries
                WHERE request_id = $1
                ORDER BY callback_id
                """,
                request_id,
            )
            assert len(deliveries) == 3
            audit_payload = "\n".join(
                json.dumps(row["raw_payload"], ensure_ascii=False) for row in deliveries
            )
            assert external_summary in audit_payload
            assert summary_personal_id in audit_payload

            steps = await conn.fetch(
                """
                SELECT step_number, title, text, metadata
                FROM process_steps
                WHERE version_id = $1
                ORDER BY step_number
                """,
                version_id,
            )
            assert [row["step_number"] for row in steps] == [1, 2]
            assert [row["metadata"]["source_step_number"] for row in steps] == [10, 20]
            assert steps[0]["text"] == "Processo Distribuído ELETRÔNICA REFER nos autos."
            assert steps[1]["text"] == "Sentença Proferida CPF [documento removido]"
            assert steps[0]["metadata"]["occurred_at_sao_paulo"] == "2026-06-10T09:00:00-03:00"
            assert steps[1]["metadata"]["occurred_at_sao_paulo"] == "2026-06-11T09:00:00-03:00"
            assert external_summary not in "\n".join(row["text"] for row in steps)
            assert summary_personal_id not in "\n".join(row["text"] for row in steps)
            assert party_personal_id not in "\n".join(row["text"] for row in steps)

            generation_jobs = await conn.fetch(
                """
                SELECT status, payload
                FROM jobs
                WHERE task_name = 'generate_process_summary'
                """
            )
            assert len(generation_jobs) == 1
            assert str(generation_jobs[0]["status"]) == "completed"
            job_payload = json.dumps(generation_jobs[0]["payload"], ensure_ascii=False)
            assert external_summary not in job_payload
            assert summary_personal_id not in job_payload
            assert party_personal_id not in job_payload

            summary_rows = await conn.fetchval(
                "SELECT count(*) FROM process_summaries WHERE process_id = $1",
                process["id"],
            )
            assert summary_rows == 1
    finally:
        await pool.close()
