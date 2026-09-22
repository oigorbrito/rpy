from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from app.auth import configured_bearer_tokens
from app.db import create_pool
from app.processes import finalize_version, stage_version
from app.rag import _load_context, _persist_summary, _validate_provider_summary

DEMO_TOKEN = os.getenv("RPY_DEMO_BEARER_TOKEN", "dev-local-token")
DEMO_CODE = "0000000-00.2026.8.21.0001"
DEMO_SOURCE_ID = "local-zero-cost-demo-v1"


async def seed_demo(database_url: str) -> None:
    tokens = configured_bearer_tokens()
    tenant_id = tokens.get(DEMO_TOKEN)
    if tenant_id is None:
        raise RuntimeError(
            f"demo token {DEMO_TOKEN!r} is not present in RPY_BEARER_TOKENS"
        )

    pool = await create_pool(database_url, min_size=1, max_size=3)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tenants (id, name)
                VALUES ($1, 'Local zero-cost demo')
                ON CONFLICT (id) DO NOTHING
                """,
                tenant_id,
            )

            existing = await conn.fetchrow(
                """
                SELECT p.id, p.current_version_id
                FROM processes p
                JOIN tenant_processes tp ON tp.process_id = p.id
                WHERE tp.tenant_id = $1 AND p.code = $2
                """,
                tenant_id,
                DEMO_CODE,
            )
            if existing is not None and existing["current_version_id"] is not None:
                summary_ok = await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1
                        FROM process_summaries
                        WHERE process_id = $1
                          AND version_id = $2
                          AND COALESCE((validation->>'passed')::boolean, false)
                    )
                    """,
                    existing["id"],
                    existing["current_version_id"],
                )
                if summary_ok:
                    print("RPY LOCAL DEMO: READY")
                    print(f"process={DEMO_CODE}")
                    print(f"token={DEMO_TOKEN}")
                    print("providers=0")
                    return

            payload = {
                "source": "local-zero-cost-demo",
                "response_type": "lawsuit",
                "response_data": {
                    "code": DEMO_CODE,
                    "class_name": "Ação Cível",
                    "court": "TJRS",
                },
            }
            process_id, version_id = await stage_version(
                conn,
                code=DEMO_CODE,
                source_request_id=DEMO_SOURCE_ID,
                cached_response=False,
                payload=payload,
                judit_request_id=None,
                judit_response_id=None,
                judit_callback_id=None,
                tenant_id=tenant_id,
            )

            steps = [
                {
                    "step_number": 1,
                    "occurred_at": datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc),
                    "title": "Distribuição",
                    "text": "Processo sintético distribuído para fins de demonstração local.",
                    "metadata": {},
                },
                {
                    "step_number": 2,
                    "occurred_at": datetime(2026, 2, 5, 12, 0, tzinfo=timezone.utc),
                    "title": "Citação",
                    "text": "Citação sintética registrada no ambiente de demonstração.",
                    "metadata": {},
                },
                {
                    "step_number": 3,
                    "occurred_at": datetime(2026, 3, 18, 12, 0, tzinfo=timezone.utc),
                    "title": "Contestação",
                    "text": "Contestação sintética apresentada para teste do fluxo do produto.",
                    "metadata": {},
                },
            ]
            await finalize_version(
                conn,
                process_id=process_id,
                version_id=version_id,
                header={
                    "instance": 1,
                    "state": "RS",
                    "county": "Porto Alegre",
                    "demo": True,
                },
                parties=[
                    {"name": "Empresa Alfa Ltda.", "side": "active"},
                    {"name": "Empresa Beta Ltda.", "side": "passive"},
                ],
                subjects=[{"name": "Contrato"}],
                steps=steps,
                attachments=[],
                court="TJRS",
                class_name="Ação Cível",
                secrecy_level=0,
            )

        context = await _load_context(
            pool, process_id, version_id, tenant_id=tenant_id
        )
        summary = (
            f"# Resumo do processo\n\n"
            f"Processo {DEMO_CODE}. Ambiente sintético local, sem consulta a "
            "provedores externos. O processo foi distribuído, houve citação e "
            "foi registrada contestação.\n\n"
            "## Situação atual\n"
            "A contestação sintética é o movimento mais recente fornecido para "
            "esta demonstração.\n\n"
            "## Pontos de atenção\n"
            "Nenhuma divergência objetiva identificada."
        )
        result = _validate_provider_summary(summary, context)
        if not result.passed:
            raise RuntimeError(
                "synthetic demo summary failed validation: "
                + "; ".join(result.errors)
            )

        async with pool.acquire() as conn:
            await _persist_summary(
                conn,
                process_id=process_id,
                version_id=version_id,
                text=summary,
                validation={"passed": True, "errors": []},
                generation_ms=0,
                model="local-demo-no-provider",
                prompt_version="local-demo-v1",
                usage={},
                cache_hit=False,
                cost_usd=0.0,
                selected_sources=list(context.get("_selected_sources", [])),
                attachment_sources=[],
                glossary_sources=list(context.get("_glossary_sources", [])),
                unicode_security_flags=list(
                    context.get("_unicode_security_flags", [])
                ),
            )

        print("RPY LOCAL DEMO: READY")
        print(f"process={DEMO_CODE}")
        print(f"token={DEMO_TOKEN}")
        print("providers=0")
    finally:
        await pool.close()


async def _main() -> None:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    if os.getenv("JUDIT_API_KEY") or os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("local demo must run with provider credentials unset")
    await seed_demo(database_url)


if __name__ == "__main__":
    asyncio.run(_main())
