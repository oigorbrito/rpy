from __future__ import annotations

import json
import os
from typing import Any
from uuid import UUID

import asyncpg
from anthropic import AsyncAnthropic

from app.db import create_pool
from app.embeddings import embed_query, ensure_step_embeddings
from app.retrieval import load_steps, rank_steps, vector_search
from app.tasks import task
from app.validation import ValidationResult, validar

MODEL = "claude-sonnet-5"
PROMPT_VERSION = "process-summary-v1"
RETRIEVAL_QUERY = (
    "sentença acórdão citação decisão audiência pedido objeto situação atual "
    "trânsito em julgado"
)

SYSTEM_PROMPT = """
Você é um assistente jurídico responsável por produzir RESUMOS PROCESSUAIS factuais, auditáveis e estritamente fundamentados nos dados fornecidos.

OBJETIVO
Produza um resumo útil para leitura rápida do processo, preservando precisão cronológica e separando fatos processuais de inferências. O texto não é parecer jurídico e não deve fazer prognóstico de resultado.

REGRAS DE FUNDAMENTAÇÃO
1. Use somente os dados presentes em <processo> e <movimentos>. Não invente fatos, partes, pedidos, decisões, datas, valores ou fundamentos.
2. Se uma informação relevante estiver ausente ou ambígua, diga de forma curta que ela não consta no contexto fornecido.
3. Nomes de partes somente podem ser reproduzidos quando constarem exatamente na lista de partes fornecida. Ao apresentar uma parte em campo estruturado, use <Party name="NOME EXATO" />.
4. Nunca exponha CPF ou CNPJ em sequência limpa de 11 ou 14 dígitos. Se o dado vier sem máscara, omita ou masque.
5. O número CNJ deve ser exatamente o número informado no campo code. Não crie, corrija ou substitua o CNJ.
6. Não use linguagem prognóstica. São proibidas formulações como "provavelmente será condenado", "chances de", "tende a ganhar" ou "recomendo que".
7. Descreva decisão judicial apenas pelo que consta nos movimentos. Não transforme despacho em sentença nem inferira trânsito em julgado sem registro explícito.
8. Preserve a ordem temporal ao narrar os principais acontecimentos. Dê destaque a citação, audiência, decisão, sentença, acórdão e trânsito em julgado quando existirem.
9. Não mencione que houve busca vetorial, BM25, RAG, seleção de chunks ou qualquer mecanismo interno.

FORMATO
A resposta deve ser Markdown e pode conter componentes JSX. Em JSX, sempre use className= e nunca class=. Tags JSX devem estar balanceadas.

Use esta estrutura, omitindo seções sem informação:

# Resumo do processo

<ProcessHeader className="process-header">
- Processo: [CNJ exato]
- Classe: [classe]
- Tribunal: [tribunal]
</ProcessHeader>

## Partes
Liste apenas as partes recebidas. Para cada nome use <Party name="NOME EXATO" /> e, se disponível, seu papel processual.

## Síntese
Explique em poucos parágrafos o objeto aparente do processo e seu estado atual, apenas a partir do contexto.

## Linha do tempo relevante
Apresente os acontecimentos processuais mais relevantes em ordem cronológica. Prefira data + evento + consequência processual explícita.

## Situação atual
Indique o último estado processual observável. Não faça previsão.

## Pontos de atenção
Registre lacunas documentais, eventos relevantes ou inconsistências objetivas do material fornecido. Não dê recomendação jurídica.

CASOS SOB SIGILO
Se <processo secrecy_level> for maior que zero, você receberá somente cabeçalho sanitizado e classe processual. Não tente inferir nomes, movimentos, objeto, pedidos ou resultado. Produza apenas um resumo mínimo dizendo que os detalhes foram restringidos por sigilo.

CRITÉRIO DE QUALIDADE
Prefira afirmações curtas e verificáveis. Não aumente o texto com explicações jurídicas genéricas. Cada afirmação material deve ser rastreável ao conteúdo fornecido. Se houver conflito entre campos, reporte a inconsistência em vez de escolher uma versão por conta própria.
""".strip()


def _message_text(message: Any) -> str:
    return "\n".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    ).strip()


def _serialize_steps(ranked: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "step_number": item.step.step_number,
            "occurred_at": str(item.step.occurred_at) if item.step.occurred_at else None,
            "title": item.step.title,
            "text": item.step.text,
        }
        for item in ranked
    ]


async def _load_process(
    pool: asyncpg.Pool,
    process_id: UUID,
    version_id: UUID,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        process = await conn.fetchrow(
            """
            SELECT id, code, court, class_name, subjects, parties, secrecy_level, header
            FROM processes
            WHERE id = $1 AND current_version_id = $2
            """,
            process_id,
            version_id,
        )
    if process is None:
        raise LookupError("process/version is not current or does not exist")

    return {
        "code": process["code"],
        "court": process["court"],
        "class_name": process["class_name"],
        "subjects": list(process["subjects"] or []),
        "parties": list(process["parties"] or []),
        "secrecy_level": int(process["secrecy_level"] or 0),
        "header": dict(process["header"] or {}),
    }


async def _load_context(
    pool: asyncpg.Pool,
    process_id: UUID,
    version_id: UUID,
) -> dict[str, Any]:
    base = await _load_process(pool, process_id, version_id)

    # LGPD blocker: no parties, subjects, movement text or embeddings leave the database
    # for secret proceedings.
    if base["secrecy_level"] > 0:
        return {
            "code": base["code"],
            "class_name": base["class_name"],
            "secrecy_level": base["secrecy_level"],
            "header": {},
            "parties": [],
            "subjects": [],
            "steps": [],
        }

    async with pool.acquire() as conn:
        steps = await load_steps(conn, version_id=version_id)

    vector_scores: dict[UUID, float] | None = None
    if len(steps) > 40:
        # Embeddings are conditional: short proceedings never pay the vector cost.
        await ensure_step_embeddings(pool, version_id=version_id)
        query_vector = await embed_query(RETRIEVAL_QUERY)
        async with pool.acquire() as conn:
            vector_scores = await vector_search(
                conn,
                version_id=version_id,
                embedding=query_vector,
                limit=40,
            )

    ranked = rank_steps(
        query=RETRIEVAL_QUERY,
        steps=steps,
        vector_scores=vector_scores,
        limit=20,
    )
    base["steps"] = _serialize_steps(ranked)
    return base


async def _generate(
    client: AsyncAnthropic,
    context: dict[str, Any],
    validation_errors: list[str] | None = None,
) -> str:
    correction = ""
    if validation_errors:
        correction = (
            "\n<validation_errors>\n"
            + "\n".join(f"- {error}" for error in validation_errors)
            + "\n</validation_errors>\nCorrija todos os erros acima sem alterar fatos."
        )
    user_prompt = (
        "<processo>\n"
        + json.dumps(
            {key: value for key, value in context.items() if key != "steps"},
            ensure_ascii=False,
            default=str,
        )
        + "\n</processo>\n<movimentos>\n"
        + json.dumps(context.get("steps", []), ensure_ascii=False, default=str)
        + "\n</movimentos>\n"
        + correction
        + "\nProduza o resumo processual agora."
    )
    message = await client.messages.create(
        model=MODEL,
        max_tokens=5000,
        temperature=0.2,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
    )
    return _message_text(message)


async def generate_summary(
    pool: asyncpg.Pool,
    process_id: UUID,
    version_id: UUID,
) -> dict[str, Any]:
    context = await _load_context(pool, process_id, version_id)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required")
    client = AsyncAnthropic(api_key=api_key)

    text = await _generate(client, context)
    result: ValidationResult = validar(
        text=text,
        code=context["code"],
        parties=context.get("parties", []),
    )
    if not result.passed:
        text = await _generate(client, context, result.errors)
        result = validar(
            text=text,
            code=context["code"],
            parties=context.get("parties", []),
        )

    validation = {"passed": result.passed, "errors": result.errors}
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO process_summaries (
                process_id, version_id, markdown, validation, model, prompt_version
            ) VALUES ($1, $2, $3, $4::jsonb, $5, $6)
            ON CONFLICT (process_id, version_id)
            DO UPDATE SET markdown = EXCLUDED.markdown,
                          validation = EXCLUDED.validation,
                          model = EXCLUDED.model,
                          prompt_version = EXCLUDED.prompt_version,
                          created_at = NOW()
            """,
            process_id,
            version_id,
            text,
            json.dumps(validation),
            MODEL,
            PROMPT_VERSION,
        )
    return {"validation": validation, "model": MODEL}


@task("generate_process_summary")
async def generate_process_summary_task(payload: dict[str, Any]) -> dict[str, Any]:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    process_id = UUID(str(payload["process_id"]))
    version_id = UUID(str(payload["version_id"]))
    pool = await create_pool(database_url, min_size=1, max_size=4)
    try:
        return await generate_summary(pool, process_id, version_id)
    finally:
        await pool.close()
