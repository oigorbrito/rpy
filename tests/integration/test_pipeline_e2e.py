from __future__ import annotations

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
async def test_judit_to_summary_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)

    pool = await create_pool(TEST_DATABASE_URL, min_size=2, max_size=6)
    app.state.pool = pool
    monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN", "e2e-webhook")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    async with pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE jobs, judit_deliveries, process_summaries, process_steps,
                     tenant_processes, access_log, process_versions, processes
            RESTART IDENTITY CASCADE
            """
        )

    request_id = f"req-e2e-{uuid4()}"
    response_id = f"resp-e2e-{uuid4()}"
    callback_response = f"cb-e2e-response-{uuid4()}"
    callback_completed = f"cb-e2e-completed-{uuid4()}"
    code = "0000000-00.0000.0.00.0201"
    generation_attempts: list[list[str] | None] = []

    async def fake_generate(client, context, validation_errors=None):
        assert context["code"] == code
        assert len(context["steps"]) == 3
        generation_attempts.append(validation_errors)

        if validation_errors is None:
            return f"Processo {code}. Provavelmente será condenado."

        assert any("prognostic" in error for error in validation_errors)
        return f"""# Resumo do processo

<ProcessHeader className=\"process-header\">
- Processo: {code}
- Classe: Procedimento Comum
- Tribunal: TJRS
</ProcessHeader>

## Síntese
O processo contém registros de distribuição, citação e sentença.

## Linha do tempo relevante
- Distribuição inicial.
- Citação registrada.
- Sentença registrada.

## Situação atual
O último movimento fornecido é uma sentença.
"""

    monkeypatch.setattr(rag, "_generate", fake_generate)

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response_created = await client.post(
                "/webhooks/judit/e2e-webhook",
                json={
                    "callback_id": callback_response,
                    "event_type": "response_created",
                    "reference_type": "request",
                    "reference_id": request_id,
                    "payload": {
                        "request_id": request_id,
                        "response_id": response_id,
                        "response_type": "lawsuit",
                        "response_data": {
                            "code": code,
                            "classifications": [{"name": "Procedimento Comum"}],
                            "courts": [{"name": "TJRS"}],
                            "parties": [],
                            "subjects": [{"code": "1", "name": "Obrigação"}],
                            "steps": [
                                {
                                    "step_id": "step-1",
                                    "step_date": "2026-01-10T12:00:00Z",
                                    "step_type": "DISTRIBUIÇÃO",
                                    "content": "Processo distribuído.",
                                },
                                {
                                    "step_id": "step-2",
                                    "step_date": "2026-02-10T12:00:00Z",
                                    "step_type": "CITAÇÃO",
                                    "content": "Citação registrada nos autos.",
                                },
                                {
                                    "step_id": "step-3",
                                    "step_date": "2026-03-10T12:00:00Z",
                                    "step_type": "SENTENÇA",
                                    "content": "Sentença registrada nos autos.",
                                },
                            ],
                        },
                        "tags": {"cached_response": False},
                    },
                },
            )
            assert response_created.status_code == 200

            request_completed = await client.post(
                "/webhooks/judit/e2e-webhook",
                json={
                    "callback_id": callback_completed,
                    "event_type": "request_completed",
                    "reference_type": "request",
                    "reference_id": request_id,
                    "payload": {"status": "completed"},
                },
            )
            assert request_completed.status_code == 200

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

        assert await worker.process_one() is True  # finalize_judit_request
        assert await worker.process_one() is True  # generate_process_summary
        assert await worker.process_one() is False
        assert len(generation_attempts) == 2
        assert generation_attempts[0] is None
        assert generation_attempts[1]

        async with pool.acquire() as conn:
            process = await conn.fetchrow(
                "SELECT id, current_version_id, class_name, court FROM processes WHERE code = $1",
                code,
            )
            assert process is not None
            assert process["current_version_id"] is not None
            assert process["class_name"] == "Procedimento Comum"
            assert process["court"] == "TJRS"

            step_count = await conn.fetchval(
                "SELECT count(*) FROM process_steps WHERE process_id = $1",
                process["id"],
            )
            assert step_count == 3

            summary = await conn.fetchrow(
                """
                SELECT markdown, validation, model, prompt_version
                FROM process_summaries
                WHERE process_id = $1 AND version_id = $2
                """,
                process["id"],
                process["current_version_id"],
            )
            assert summary is not None
            assert code in summary["markdown"]
            assert summary["validation"]["passed"] is True
            assert summary["model"] == "claude-sonnet-5"
            assert summary["prompt_version"] == "process-summary-v1"

            states = await conn.fetch(
                """
                SELECT task_name, status
                FROM jobs
                WHERE idempotency_key IN ($1, $2)
                ORDER BY task_name
                """,
                f"judit-finalize:{request_id}",
                f"summary:{process['current_version_id']}",
            )
            assert {row["task_name"]: str(row["status"]) for row in states} == {
                "finalize_judit_request": "completed",
                "generate_process_summary": "completed",
            }
    finally:
        await pool.close()
