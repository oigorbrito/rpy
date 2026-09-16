from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import httpx

import app.rag as rag
from app.api import app
from app.db import create_pool
from app.json_utils import decode_json_object
from app.worker import Worker, WorkerSettings


DATABASE_URL = os.environ["DATABASE_URL"]

CODE = "0000000-00.2026.8.21.0999"
WEBHOOK_TOKEN = os.environ["JUDIT_WEBHOOK_TOKEN"]


async def fake_generate(client, context, validation_errors=None):
    if validation_errors is None:
        return f"""# Resumo do processo

<ProcessHeader className="process-header">
- Processo: {CODE}
- Classe: Ação de Improbidade Administrativa
- Tribunal: TJRS
</ProcessHeader>

## Síntese
O Ministério Público ajuizou ação de improbidade administrativa em face de agente público municipal e sociedade empresária, em razão de fraude em procedimento licitatório.

## Linha do tempo relevante
- Ajuizamento da ação de improbidade administrativa.
- Formalização e cumprimento de acordo de leniência pela sociedade empresária.
- Comunicação do acordo ao juízo.
- Prolação de sentença condenatória.
- Oposição de embargos de declaração.
- Rejeição dos embargos de declaração.

## Situação atual
O último movimento processual informado é a rejeição dos embargos de declaração.
"""

    return f"""# Resumo do processo

<ProcessHeader className="process-header">
- Processo: {CODE}
- Classe: Ação de Improbidade Administrativa
- Tribunal: TJRS
</ProcessHeader>

## Síntese
O Ministério Público ajuizou ação de improbidade administrativa em face de agente público municipal e sociedade empresária, em razão de fraude em procedimento licitatório.

## Linha do tempo relevante
- Ajuizamento da ação.
- Celebração e cumprimento de acordo de leniência.
- Comunicação do acordo ao juízo.
- Sentença condenatória.
- Embargos de declaração.
- Rejeição dos embargos.

## Situação atual
O processo encontra-se após a rejeição dos embargos de declaração.
"""


async def main():
    print("=== RPY LOCAL E2E SIMULATED ===")

    pool = await create_pool(
        DATABASE_URL,
        min_size=2,
        max_size=6,
    )
    app.state.pool = pool

    original_generate = rag._generate
    original_anthropic_client = rag.anthropic_client
    original_anthropic_key = os.environ.get("ANTHROPIC_API_KEY")

    # Boundary stub:
    # nenhuma chamada real à Anthropic é realizada neste harness.
    os.environ["ANTHROPIC_API_KEY"] = "local-e2e-fake-key"
    rag.anthropic_client = lambda api_key: object()
    rag._generate = fake_generate

    request_id = f"req-local-{uuid4()}"
    response_id = f"resp-local-{uuid4()}"
    callback_response = f"cb-local-{uuid4()}"
    callback_completed = f"cb-local-{uuid4()}"
    tenant_id = uuid4()
    bearer_token = "local-e2e-token"

    app.state.bearer_tokens = {
        bearer_token: tenant_id,
    }

    lawsuit = {
        "callback_id": callback_response,
        "event_type": "response_created",
        "reference_type": "request",
        "reference_id": request_id,
        "payload": {
            "request_id": request_id,
            "response_id": response_id,
            "response_type": "lawsuit",
            "response_data": {
                "code": CODE,
                "tribunal_acronym": "TJRS",
                "secrecy_level": 0,
                "classifications": [
                    {
                        "code": "SIM-001",
                        "name": "AÇÃO DE IMPROBIDADE ADMINISTRATIVA",
                    }
                ],
                "subjects": [
                    {
                        "code": "SIM-IMPROBIDADE",
                        "name": "IMPROBIDADE ADMINISTRATIVA",
                    }
                ],
                "parties": [
                    {
                        "name": "MINISTÉRIO PÚBLICO",
                        "side": "Active",
                        "person_type": "Autor",
                    },
                    {
                        "name": "RÔMULO",
                        "side": "Passive",
                        "person_type": "Réu",
                    },
                    {
                        "name": "BOAZINHA LTDA.",
                        "side": "Passive",
                        "person_type": "Réu",
                    },
                ],
                "steps": [
                    {
                        "step_id": "step-1",
                        "step_date": "2022-02-01T12:00:00Z",
                        "step_type": "AJUIZAMENTO",
                        "content": (
                            "Ministério Público ajuizou ação por improbidade "
                            "administrativa em razão de fraude em procedimento "
                            "licitatório."
                        ),
                        "private": False,
                    },
                    {
                        "step_id": "step-2",
                        "step_date": "2022-06-01T12:00:00Z",
                        "step_type": "ACORDO",
                        "content": (
                            "Sociedade empresária formalizou e cumpriu acordo "
                            "de leniência com ressarcimento ao erário e redução "
                            "de multa."
                        ),
                        "private": False,
                    },
                    {
                        "step_id": "step-3",
                        "step_date": "2022-06-10T12:00:00Z",
                        "step_type": "PETICAO",
                        "content": (
                            "Acordo de leniência foi comunicado ao juízo, "
                            "com intimação das partes e do Ministério Público."
                        ),
                        "private": False,
                    },
                    {
                        "step_id": "step-4",
                        "step_date": "2023-01-10T12:00:00Z",
                        "step_type": "SENTENCA",
                        "content": (
                            "Sentença condenou os réus e aplicou ressarcimento, "
                            "multa e proibição de contratar com a Administração "
                            "Pública."
                        ),
                        "private": False,
                    },
                    {
                        "step_id": "step-5",
                        "step_date": "2023-01-20T12:00:00Z",
                        "step_type": "EMBARGOS_DE_DECLARACAO",
                        "content": (
                            "Foram opostos embargos de declaração contra "
                            "a sentença."
                        ),
                        "private": False,
                    },
                    {
                        "step_id": "step-6",
                        "step_date": "2023-01-27T12:00:00Z",
                        "step_type": "DECISAO",
                        "content": (
                            "Embargos de declaração foram rejeitados."
                        ),
                        "private": False,
                    },
                ],
                "instance": 1,
                "area": "Administrativo",
                "state": "RS",
                "city": "Porto Alegre",
            },
            "tags": {
                "cached_response": False,
            },
        },
    }

    completed = {
        "callback_id": callback_completed,
        "event_type": "response_created",
        "reference_type": "request",
        "reference_id": request_id,
        "payload": {
            "request_id": request_id,
            "response_id": f"response-info-{uuid4()}",
            "response_type": "application_info",
            "response_data": {
                "code": 600,
                "message": "REQUEST_COMPLETED",
            },
            "tags": {
                "cached_response": False,
            },
        },
    }

    transport = httpx.ASGITransport(app=app)

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                DELETE FROM tenant_processes
                WHERE process_id IN (
                    SELECT id
                    FROM processes
                    WHERE code = $1
                )
                """,
                CODE,
            )

            await conn.execute(
                """
                DELETE FROM processes
                WHERE code = $1
                """,
                CODE,
            )

            await conn.execute(
                """
                INSERT INTO tenants (id, name)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                """,
                tenant_id,
                "local-e2e",
            )

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            r1 = await client.post(
                f"/webhooks/judit/{WEBHOOK_TOKEN}",
                json=lawsuit,
            )

            print(
                "webhook lawsuit:",
                r1.status_code,
                r1.text,
            )

            r2 = await client.post(
                f"/webhooks/judit/{WEBHOOK_TOKEN}",
                json=completed,
            )

            print(
                "webhook completed:",
                r2.status_code,
                r2.text,
            )

        if r1.status_code != 200:
            raise RuntimeError(
                f"lawsuit webhook failed: {r1.status_code} {r1.text}"
            )

        if r2.status_code != 200:
            raise RuntimeError(
                f"completion webhook failed: {r2.status_code} {r2.text}"
            )

        worker = Worker(
            pool,
            WorkerSettings(
                database_url=DATABASE_URL,
                concurrency=1,
                heartbeat_interval_seconds=60,
                stale_after_seconds=120,
                task_timeout_seconds=30,
                reclaim_interval_seconds=60,
            ),
        )

        processed = 0

        while await worker.process_one():
            processed += 1

        print("jobs processed:", processed)

        async with pool.acquire() as conn:
            process = await conn.fetchrow(
                """
                SELECT
                    id,
                    current_version_id,
                    class_name,
                    court
                FROM processes
                WHERE code = $1
                """,
                CODE,
            )

            if process is None:
                raise RuntimeError("process not created")

            step_count = await conn.fetchval(
                """
                SELECT count(*)
                FROM process_steps
                WHERE process_id = $1
                """,
                process["id"],
            )

            summary = await conn.fetchrow(
                """
                SELECT
                    markdown,
                    validation,
                    model,
                    prompt_version
                FROM process_summaries
                WHERE process_id = $1
                  AND version_id = $2
                """,
                process["id"],
                process["current_version_id"],
            )

            await conn.execute(
                """
                INSERT INTO tenant_processes (
                    tenant_id,
                    process_id
                )
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                """,
                tenant_id,
                process["id"],
            )

            jobs = await conn.fetch(
                """
                SELECT
                    task_name,
                    status,
                    attempts,
                    idempotency_key
                FROM jobs
                WHERE idempotency_key IN ($1, $2)
                ORDER BY task_name
                """,
                f"judit-finalize:{request_id}",
                f"summary:{process['current_version_id']}",
            )

        if step_count != 6:
            raise RuntimeError(
                f"expected 6 steps, got {step_count}"
            )

        if summary is None:
            raise RuntimeError(
                "summary not generated"
            )

        validation = decode_json_object(
            summary["validation"],
            label="summary validation",
        )

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.get(
                f"/processes/{CODE}",
                headers={
                    "Authorization": f"Bearer {bearer_token}",
                },
            )

        print("")
        print("=== RESULT ===")
        print("process:", CODE)
        print("class:", process["class_name"])
        print("court:", process["court"])
        print("steps:", step_count)
        print("summary model:", summary["model"])
        print("summary validation:", validation)
        print(
            "jobs:",
            [
                {
                    "task": row["task_name"],
                    "status": str(row["status"]),
                    "attempts": row["attempts"],
                }
                for row in jobs
            ],
        )
        print("api status:", response.status_code)

        if response.status_code != 200:
            raise RuntimeError(
                f"API read failed: {response.status_code} {response.text}"
            )

        body = response.json()

        if not validation.get("passed"):
            raise RuntimeError(
                "summary validation failed"
            )

        if CODE not in body["iaSummary"]:
            raise RuntimeError(
                "summary not exposed by API"
            )

        completed_tasks = {
            row["task_name"]
            for row in jobs
            if str(row["status"]) == "completed"
        }

        if "finalize_judit_request" not in completed_tasks:
            raise RuntimeError(
                "finalize_judit_request did not complete"
            )

        if "generate_process_summary" not in completed_tasks:
            raise RuntimeError(
                "generate_process_summary did not complete"
            )

        print("")
        print(
            "PASS: simulated Judit -> finalize -> "
            "offline summary -> API"
        )

    finally:
        rag._generate = original_generate
        rag.anthropic_client = original_anthropic_client

        if original_anthropic_key is None:
            os.environ.pop(
                "ANTHROPIC_API_KEY",
                None,
            )
        else:
            os.environ["ANTHROPIC_API_KEY"] = (
                original_anthropic_key
            )

        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())