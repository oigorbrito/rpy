from __future__ import annotations

import json
import os
from time import perf_counter
from typing import Any
from uuid import UUID

import asyncpg

from app.db import create_pool
from app.embeddings import embed_query, ensure_step_embeddings
from app.json_utils import decode_json_list, decode_json_object
from app.prompts import PROCESS_SUMMARY_SYSTEM_PROMPT
from app.providers import (
    anthropic_client,
    anthropic_settings,
    call_with_retries,
    is_retryable_anthropic_error,
)
from app.retrieval import load_steps, rank_steps, vector_search
from app.tasks import task
from app.validation import ValidationResult, validar

MODEL = "claude-sonnet-5"
PROMPT_VERSION = "process-summary-v2"
SECRET_MODEL = "local-deterministic"
SECRET_PROMPT_VERSION = "secret-summary-v1"
REQUESTED_TEMPERATURE = 0.2
# Historical design intent is temperature=0.2. Claude Sonnet 5 currently rejects
# non-default sampling parameters, so the production request must omit temperature.
SONNET_5_SUPPORTS_CUSTOM_TEMPERATURE = False
RETRIEVAL_QUERY = (
    "sentença acórdão citação decisão audiência pedido objeto situação atual "
    "trânsito em julgado"
)
DEFAULT_PROVIDER_PROMPT_MAX_CHARS = 120_000
DEFAULT_PROVIDER_STEP_TEXT_MAX_CHARS = 12_000
DEFAULT_PROVIDER_STEPS_TEXT_MAX_CHARS = 80_000
TRUNCATION_MARKER = "… [truncated]"
_SECRET_HEADER_FIELDS = (
    ("instance", "Instância"),
    ("area", "Área"),
    ("justice_description", "Justiça"),
    ("county", "Comarca"),
    ("state", "Estado"),
    ("city", "Cidade"),
)


def _positive_env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero")
    return value


def provider_context_limits() -> tuple[int, int, int]:
    prompt_max = _positive_env_int(
        "PROVIDER_PROMPT_MAX_CHARS", DEFAULT_PROVIDER_PROMPT_MAX_CHARS
    )
    step_max = _positive_env_int(
        "PROVIDER_STEP_TEXT_MAX_CHARS", DEFAULT_PROVIDER_STEP_TEXT_MAX_CHARS
    )
    steps_total_max = _positive_env_int(
        "PROVIDER_STEPS_TEXT_MAX_CHARS", DEFAULT_PROVIDER_STEPS_TEXT_MAX_CHARS
    )
    if step_max > steps_total_max:
        raise RuntimeError(
            "PROVIDER_STEP_TEXT_MAX_CHARS must not exceed PROVIDER_STEPS_TEXT_MAX_CHARS"
        )
    if steps_total_max >= prompt_max:
        raise RuntimeError(
            "PROVIDER_STEPS_TEXT_MAX_CHARS must be lower than PROVIDER_PROMPT_MAX_CHARS"
        )
    return prompt_max, step_max, steps_total_max


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= len(TRUNCATION_MARKER):
        return text[:limit]
    return text[: limit - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER


def _message_text(message: Any) -> str:
    return "\n".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    ).strip()


def _serialize_steps(ranked: list[Any]) -> list[dict[str, Any]]:
    _, step_max, steps_total_max = provider_context_limits()
    texts = [_truncate_text(str(item.step.text or ""), step_max) for item in ranked]

    if sum(len(text) for text in texts) > steps_total_max:
        bounded: list[str] = []
        remaining = steps_total_max
        remaining_items = len(texts)
        for text in texts:
            allowance = remaining // remaining_items if remaining_items else 0
            rendered = _truncate_text(text, allowance)
            bounded.append(rendered)
            remaining -= len(rendered)
            remaining_items -= 1
        texts = bounded

    return [
        {
            "step_number": item.step.step_number,
            "occurred_at": str(item.step.occurred_at) if item.step.occurred_at else None,
            "title": item.step.title,
            "text": text,
        }
        for item, text in zip(ranked, texts, strict=True)
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
        "subjects": decode_json_list(process["subjects"], label="process subjects"),
        "parties": decode_json_list(process["parties"], label="process parties"),
        "secrecy_level": int(process["secrecy_level"] or 0),
        "header": decode_json_object(process["header"], label="process header"),
    }


async def _load_context(
    pool: asyncpg.Pool,
    process_id: UUID,
    version_id: UUID,
) -> dict[str, Any]:
    base = await _load_process(pool, process_id, version_id)

    # LGPD blocker: secret proceedings expose only the allowed header and class;
    # no parties, subjects, movement text or embeddings leave the database.
    if base["secrecy_level"] > 0:
        return {
            "code": base["code"],
            "class_name": base["class_name"],
            "secrecy_level": base["secrecy_level"],
            "header": base["header"],
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


def _is_secret_context(context: dict[str, Any]) -> bool:
    return int(context.get("secrecy_level") or 0) > 0


def _secret_summary(context: dict[str, Any]) -> str:
    """Build the minimum useful summary without invoking an external model."""
    lines = [
        "# Resumo do processo",
        "",
        "## Sigilo",
        "Os detalhes processuais foram restringidos por sigilo.",
    ]

    allowed_lines: list[str] = []
    class_name = str(context.get("class_name") or "").strip()
    if class_name:
        allowed_lines.append(f"- Classe: {class_name}")

    header = context.get("header") if isinstance(context.get("header"), dict) else {}
    for key, label in _SECRET_HEADER_FIELDS:
        value = header.get(key)
        if value is None:
            continue
        rendered = str(value).strip()
        if rendered:
            allowed_lines.append(f"- {label}: {rendered}")

    if allowed_lines:
        lines.extend(["", "## Dados permitidos", *allowed_lines])

    return "\n".join(lines).strip()


def _provider_payload(context: dict[str, Any]) -> tuple[dict[str, Any], list[Any]]:
    """Return the exact data boundary allowed to leave the application."""
    if _is_secret_context(context):
        return (
            {
                "class_name": context.get("class_name"),
                "header": context.get("header") or {},
            },
            [],
        )

    return (
        {key: value for key, value in context.items() if key != "steps"},
        list(context.get("steps", [])),
    )


async def _generate(
    client: Any,
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

    provider_process, provider_steps = _provider_payload(context)
    user_prompt = (
        "<processo>\n"
        + json.dumps(provider_process, ensure_ascii=False, default=str)
        + "\n</processo>\n<movimentos>\n"
        + json.dumps(provider_steps, ensure_ascii=False, default=str)
        + "\n</movimentos>\n"
        + correction
        + "\nProduza o resumo processual agora."
    )
    prompt_max, _, _ = provider_context_limits()
    if len(user_prompt) > prompt_max:
        raise ValueError(
            f"provider prompt exceeds PROVIDER_PROMPT_MAX_CHARS ({len(user_prompt)} > {prompt_max})"
        )

    request: dict[str, Any] = {
        "model": MODEL,
        "max_tokens": 5000,
        "system": [
            {
                "type": "text",
                "text": PROCESS_SUMMARY_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": user_prompt}],
    }
    if SONNET_5_SUPPORTS_CUSTOM_TEMPERATURE:
        request["temperature"] = REQUESTED_TEMPERATURE

    async def create_message():
        return await client.messages.create(**request)

    message = await call_with_retries(
        create_message,
        is_retryable=is_retryable_anthropic_error,
        settings=anthropic_settings(),
    )
    return _message_text(message)


async def _persist_summary(
    conn: asyncpg.Connection,
    *,
    process_id: UUID,
    version_id: UUID,
    text: str,
    validation: dict[str, Any],
    generation_ms: int,
    model: str = MODEL,
    prompt_version: str = PROMPT_VERSION,
) -> bool:
    """Persist without allowing duplicate/stale executions to degrade a valid summary.

    Invalid summaries may be replaced by later attempts. Once a valid summary exists,
    only a valid result from a different prompt/model revision may replace it. An
    invalid duplicate can therefore never overwrite content already accepted by the
    validator.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO process_summaries (
            process_id, version_id, markdown, validation, model, prompt_version, generation_ms
        ) VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
        ON CONFLICT (process_id, version_id)
        DO UPDATE SET markdown = EXCLUDED.markdown,
                      validation = EXCLUDED.validation,
                      model = EXCLUDED.model,
                      prompt_version = EXCLUDED.prompt_version,
                      generation_ms = EXCLUDED.generation_ms,
                      created_at = NOW()
        WHERE COALESCE((process_summaries.validation->>'passed')::boolean, false) = false
           OR (
                COALESCE((EXCLUDED.validation->>'passed')::boolean, false) = true
                AND (
                    process_summaries.prompt_version IS DISTINCT FROM EXCLUDED.prompt_version
                    OR process_summaries.model IS DISTINCT FROM EXCLUDED.model
                )
           )
        RETURNING id
        """,
        process_id,
        version_id,
        text,
        validation,
        model,
        prompt_version,
        generation_ms,
    )
    return row is not None


async def generate_summary(
    pool: asyncpg.Pool,
    process_id: UUID,
    version_id: UUID,
) -> dict[str, Any]:
    started = perf_counter()
    context = await _load_context(pool, process_id, version_id)

    model = MODEL
    prompt_version = PROMPT_VERSION
    if _is_secret_context(context):
        text = _secret_summary(context)
        result: ValidationResult = validar(
            text=text,
            code=context["code"],
            parties=[],
        )
        model = SECRET_MODEL
        prompt_version = SECRET_PROMPT_VERSION
    else:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required")
        client = anthropic_client(api_key)

        text = await _generate(client, context)
        result = validar(
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

    generation_ms = max(0, round((perf_counter() - started) * 1000))
    validation = {"passed": result.passed, "errors": result.errors}
    async with pool.acquire() as conn:
        persisted = await _persist_summary(
            conn,
            process_id=process_id,
            version_id=version_id,
            text=text,
            validation=validation,
            model=model,
            prompt_version=prompt_version,
            generation_ms=generation_ms,
        )
    return {
        "validation": validation,
        "model": model,
        "generation_ms": generation_ms,
        "persisted": persisted,
    }


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
