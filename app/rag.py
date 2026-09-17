from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import asyncpg

from app.attachment_context import load_attachment_context, resolve_generation_tenant
from app.attachment_signals import attachment_status_warnings
from app.datajud_provenance import datajud_conflict_warning
from app.db import create_pool
from app.embeddings import (
    embed_query,
    ensure_step_embeddings,
    vector_retrieval_configured,
)
from app.json_utils import decode_json_list, decode_json_object
from app.prompts import PROCESS_SUMMARY_SYSTEM_PROMPT
from app.providers import (
    anthropic_client,
    anthropic_settings,
    call_with_retries,
    is_retryable_anthropic_error,
)
from app.provenance import (
    replace_summary_attachment_sources,
    replace_summary_glossary_sources,
    replace_summary_sources,
    selected_movement_sources,
)
from app.reranker_bge import BGERerankerScorer, bge_reranker_enabled
from app.reranking import RERANK_CANDIDATE_LIMIT, select_context_steps
from app.retrieval import lexical_search, load_steps, vector_search
from app.tasks import PermanentTaskError, task
from app.tpu_glossary import resolve_process_tpu_definitions
from app.validation import ValidationResult, validar

SONNET_MODEL = "claude-sonnet-5"
OPUS_MODEL = "claude-opus-5"
MODEL = SONNET_MODEL  # Backward-compatible default model constant.
OPUS_STEP_THRESHOLD = 100
SHORT_SUMMARY_STEP_MAX = 15
MEDIUM_SUMMARY_STEP_MAX = 60
MAX_TOKENS = 4000
PROMPT_VERSION = "process-summary-v2"
SECRET_MODEL = "local-deterministic"
SECRET_PROMPT_VERSION = "secret-summary-v1"
REQUESTED_TEMPERATURE = 0.2
# Anthropic deprecates custom sampling parameters for current Claude models.
# Keep the product's requested value documented but omit it from API payloads.
CURRENT_MODELS_SUPPORT_CUSTOM_TEMPERATURE = False
RETRIEVAL_QUERY = (
    "sentença acórdão citação decisão audiência pedido objeto situação atual "
    "trânsito em julgado"
)
EMPTY_STEPS_WARNING = "Nenhum movimento processual foi fornecido no payload."
DEFAULT_PROVIDER_PROMPT_MAX_CHARS = 120_000
DEFAULT_PROVIDER_STEP_TEXT_MAX_CHARS = 12_000
DEFAULT_PROVIDER_STEPS_TEXT_MAX_CHARS = 80_000
TRUNCATION_MARKER = "… [truncated]"
_SAO_PAULO = ZoneInfo("America/Sao_Paulo")
_SECRET_HEADER_FIELDS = (
    ("instance", "Instância"),
    ("area", "Área"),
    ("justice_description", "Justiça"),
    ("county", "Comarca"),
    ("state", "Estado"),
    ("city", "Cidade"),
)
_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
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


def _provider_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        return str(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_SAO_PAULO).isoformat()


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
            "occurred_at": _provider_datetime(item.step.occurred_at),
            "title": item.step.title,
            "text": text,
        }
        for item, text in zip(ranked, texts, strict=True)
    ]


def _source_step_gap_warnings(steps: list[Any]) -> list[str]:
    if len(steps) < 2:
        return []
    source_numbers = [step.source_step_number for step in steps]
    if any(number is None for number in source_numbers):
        return []

    numbers = [int(number) for number in source_numbers if number is not None]
    warnings: list[str] = []
    for previous, current in zip(numbers, numbers[1:]):
        if current > previous + 1:
            warnings.append(
                f"Há salto na numeração de movimentos da fonte: {previous}→{current}."
            )
    return warnings


async def _load_process(
    pool: asyncpg.Pool,
    process_id: UUID,
    version_id: UUID,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        process = await conn.fetchrow(
            """
            SELECT p.id, p.code, p.court, p.class_name, p.subjects, p.parties,
                   p.secrecy_level, p.header,
                   COALESCE(
                       (
                           SELECT jsonb_agg(dfp.field_name ORDER BY dfp.field_name)
                           FROM process_datajud_field_provenance dfp
                           WHERE dfp.process_id = p.id
                             AND dfp.version_id = $2
                             AND dfp.conflict = TRUE
                             AND dfp.selected_source = 'datajud'
                       ),
                       '[]'::jsonb
                   ) AS datajud_conflict_fields
            FROM processes p
            WHERE p.id = $1 AND p.current_version_id = $2
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
        "_datajud_conflict_fields": decode_json_list(
            process["datajud_conflict_fields"],
            label="DataJud conflict fields",
        ),
    }


async def _load_context(
    pool: asyncpg.Pool,
    process_id: UUID,
    version_id: UUID,
    tenant_id: UUID | None = None,
) -> dict[str, Any]:
    base = await _load_process(pool, process_id, version_id)

    if base["secrecy_level"] > 0:
        return {
            "code": base["code"],
            "class_name": base["class_name"],
            "secrecy_level": base["secrecy_level"],
            "header": base["header"],
            "validation_parties": base["parties"],
            "parties": [],
            "subjects": [],
            "steps": [],
            "_selected_sources": [],
            "_attachment_sources": [],
            "_glossary_sources": [],
        }

    definitions = resolve_process_tpu_definitions(
        class_code=base["header"].get("class_code"),
        subjects=base["subjects"],
    )
    if definitions:
        base["tpu_glossary"] = [definition.as_context() for definition in definitions]
        base["_glossary_sources"] = [
            definition.as_provenance(source_order=index)
            for index, definition in enumerate(definitions)
        ]
    else:
        base["_glossary_sources"] = []

    async with pool.acquire() as conn:
        steps = await load_steps(conn, version_id=version_id)

    base["step_count"] = len(steps)
    source_warnings = (
        [EMPTY_STEPS_WARNING] if not steps else _source_step_gap_warnings(steps)
    )
    source_warnings.extend(
        datajud_conflict_warning(str(field))
        for field in base.get("_datajud_conflict_fields", [])
    )
    if source_warnings:
        base["source_warnings"] = source_warnings

    reranker_scorer = None
    if len(steps) > 40 and bge_reranker_enabled():
        reranker_scorer = BGERerankerScorer()
    retrieval_limit = RERANK_CANDIDATE_LIMIT if reranker_scorer is not None else 40

    lexical_scores: dict[UUID, float] | None = None
    vector_scores: dict[UUID, float] | None = None
    if len(steps) > 40:
        async with pool.acquire() as conn:
            lexical_scores = await lexical_search(
                conn,
                version_id=version_id,
                query=RETRIEVAL_QUERY,
                limit=retrieval_limit,
            )

        if vector_retrieval_configured():
            await ensure_step_embeddings(pool, version_id=version_id)
            query_vector = await embed_query(RETRIEVAL_QUERY)
            async with pool.acquire() as conn:
                vector_scores = await vector_search(
                    conn,
                    version_id=version_id,
                    embedding=query_vector,
                    limit=retrieval_limit,
                )

    ranked = await select_context_steps(
        query=RETRIEVAL_QUERY,
        steps=steps,
        lexical_scores=lexical_scores,
        vector_scores=vector_scores,
        scorer=reranker_scorer,
    )
    base["_selected_sources"] = selected_movement_sources(ranked)
    base["steps"] = _serialize_steps(ranked)

    base["_attachment_sources"] = []
    if tenant_id is not None:
        attachments, attachment_sources, status_counts = await load_attachment_context(
            pool,
            tenant_id=tenant_id,
            process_id=process_id,
            version_id=version_id,
            process_context=base,
            base_query=RETRIEVAL_QUERY,
        )
        base["_attachment_sources"] = attachment_sources
        if status_counts:
            base["attachment_status"] = status_counts
            warnings = attachment_status_warnings(status_counts)
            if warnings:
                base.setdefault("source_warnings", []).extend(warnings)
        if attachments:
            base["attachments"] = attachments
    return base


def _is_secret_context(context: dict[str, Any]) -> bool:
    return int(context.get("secrecy_level") or 0) > 0


def _secret_summary(context: dict[str, Any]) -> str:
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
    if _is_secret_context(context):
        return (
            {
                "class_name": context.get("class_name"),
                "header": context.get("header") or {},
            },
            [],
        )

    return (
        {
            key: value
            for key, value in context.items()
            if key != "steps" and not key.startswith("_")
        },
        list(context.get("steps", [])),
    )


def _provider_source_text(context: dict[str, Any]) -> str:
    provider_process, provider_steps = _provider_payload(context)
    return json.dumps(
        {"processo": provider_process, "movimentos": provider_steps},
        ensure_ascii=False,
        default=str,
    )


def _validate_provider_summary(text: str, context: dict[str, Any]) -> ValidationResult:
    return validar(
        text=text,
        code=context["code"],
        parties=context.get("parties", []),
        expected_step_count=int(context.get("step_count") or 0),
        source_text=_provider_source_text(context),
        require_attention_section=True,
        required_attention_phrases=list(context.get("source_warnings", [])),
    )


def summary_volume_instruction(step_count: int) -> str:
    """Return a qualitative density profile without inventing word-count limits."""
    if step_count < 0:
        raise ValueError("step_count must be non-negative")
    if step_count <= SHORT_SUMMARY_STEP_MAX:
        return (
            "Processo curto (até 15 movimentos): produza uma síntese curta e direta, "
            "sem expandir eventos simples nem repetir informação entre seções."
        )
    if step_count <= MEDIUM_SUMMARY_STEP_MAX:
        return (
            "Processo de volume intermediário (16 a 60 movimentos): use síntese "
            "moderada, agrupando atos repetitivos e preservando os marcos que "
            "explicam a situação atual."
        )
    return (
        "Processo longo (mais de 60 movimentos): aplique compressão forte, "
        "priorize marcos, decisões e o estado atual, e não tente reproduzir "
        "cronologicamente todo ato de expediente."
    )


def _generation_model(context: dict[str, Any]) -> str:
    return (
        OPUS_MODEL
        if int(context.get("step_count") or 0) > OPUS_STEP_THRESHOLD
        else SONNET_MODEL
    )


def _usage_value(usage: Any, field: str) -> int | None:
    value = getattr(usage, field, None)
    if value is None and isinstance(usage, dict):
        value = usage.get(field)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _record_generation_telemetry(
    context: dict[str, Any], *, message: Any, model: str
) -> None:
    usage_obj = getattr(message, "usage", None)
    attempt_usage: dict[str, int] = {}
    for field in _USAGE_FIELDS:
        value = _usage_value(usage_obj, field)
        if value is not None:
            attempt_usage[field] = value

    telemetry = context.setdefault(
        "_generation_telemetry",
        {
            "model": model,
            "attempts": 0,
            "usage": {},
            "cache_hit": False,
            "cost_usd": None,
        },
    )
    telemetry["model"] = model
    telemetry["attempts"] = int(telemetry.get("attempts") or 0) + 1
    aggregate = telemetry.setdefault("usage", {})
    for field, value in attempt_usage.items():
        aggregate[field] = int(aggregate.get(field) or 0) + value
    if attempt_usage.get("cache_read_input_tokens", 0) > 0:
        telemetry["cache_hit"] = True

    # Claude's Messages API exposes token/cache usage but does not guarantee a
    # monetary cost field. Persist provider-reported cost only when present.
    cost = getattr(usage_obj, "cost_usd", None)
    if cost is None and isinstance(usage_obj, dict):
        cost = usage_obj.get("cost_usd")
    if cost is not None:
        try:
            telemetry["cost_usd"] = float(cost)
        except (TypeError, ValueError):
            pass


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
    volume_instruction = summary_volume_instruction(int(context.get("step_count") or 0))
    user_prompt = (
        "<perfil_de_extensao>\n"
        + volume_instruction
        + "\n</perfil_de_extensao>\n<processo>\n"
        + json.dumps(provider_process, ensure_ascii=False, default=str)
        + "\n</processo>\n<movimentos>\n"
        + json.dumps(provider_steps, ensure_ascii=False, default=str)
        + "\n</movimentos>\n"
        + correction
        + "\nProduza o resumo processual agora."
    )
    prompt_max, _, _ = provider_context_limits()
    if len(user_prompt) > prompt_max:
        raise PermanentTaskError(
            f"provider prompt exceeds PROVIDER_PROMPT_MAX_CHARS ({len(user_prompt)} > {prompt_max})"
        )

    model = _generation_model(context)
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": [
            {
                "type": "text",
                "text": PROCESS_SUMMARY_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": user_prompt}],
    }
    if CURRENT_MODELS_SUPPORT_CUSTOM_TEMPERATURE:
        request["temperature"] = REQUESTED_TEMPERATURE

    async def create_message():
        return await client.messages.create(**request)

    message = await call_with_retries(
        create_message,
        is_retryable=is_retryable_anthropic_error,
        settings=anthropic_settings(),
    )
    _record_generation_telemetry(context, message=message, model=model)
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
    usage: dict[str, Any] | None = None,
    cache_hit: bool | None = None,
    cost_usd: float | None = None,
    selected_sources: list[dict[str, Any]] | None = None,
    attachment_sources: list[dict[str, Any]] | None = None,
    glossary_sources: list[dict[str, Any]] | None = None,
) -> bool:
    async with conn.transaction():
        row = await conn.fetchrow(
            """
            INSERT INTO process_summaries (
                process_id, version_id, markdown, validation, model, prompt_version,
                generation_ms, usage, cache_hit, cost_usd
            ) VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8::jsonb, $9, $10)
            ON CONFLICT (process_id, version_id)
            DO UPDATE SET markdown = EXCLUDED.markdown,
                          validation = EXCLUDED.validation,
                          model = EXCLUDED.model,
                          prompt_version = EXCLUDED.prompt_version,
                          generation_ms = EXCLUDED.generation_ms,
                          usage = EXCLUDED.usage,
                          cache_hit = EXCLUDED.cache_hit,
                          cost_usd = EXCLUDED.cost_usd,
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
            json.dumps(usage or {}),
            cache_hit,
            cost_usd,
        )
        if row is None:
            return False
        await replace_summary_sources(
            conn,
            summary_id=row["id"],
            process_id=process_id,
            version_id=version_id,
            sources=selected_sources or [],
        )
        await replace_summary_attachment_sources(
            conn,
            summary_id=row["id"],
            process_id=process_id,
            version_id=version_id,
            sources=attachment_sources or [],
        )
        await replace_summary_glossary_sources(
            conn,
            summary_id=row["id"],
            process_id=process_id,
            version_id=version_id,
            sources=glossary_sources or [],
        )
    return True


async def _load_publishable_summary(
    pool: asyncpg.Pool,
    process_id: UUID,
    version_id: UUID,
) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT ps.validation, ps.model, ps.prompt_version, ps.generation_ms,
                   ps.usage, ps.cache_hit, ps.cost_usd
            FROM process_summaries ps
            JOIN processes p
              ON p.id = ps.process_id
             AND p.current_version_id = ps.version_id
            WHERE ps.process_id = $1
              AND ps.version_id = $2
              AND COALESCE((ps.validation->>'passed')::boolean, false) = true
            """,
            process_id,
            version_id,
        )
    if row is None:
        return None
    return {
        "validation": decode_json_object(row["validation"], label="summary validation"),
        "model": row["model"],
        "prompt_version": row["prompt_version"],
        "generation_ms": int(row["generation_ms"] or 0),
        "usage": decode_json_object(row["usage"], label="summary usage"),
        "cache_hit": row["cache_hit"],
        "cost_usd": float(row["cost_usd"]) if row["cost_usd"] is not None else None,
    }


async def generate_summary(
    pool: asyncpg.Pool,
    process_id: UUID,
    version_id: UUID,
    *,
    tenant_id: UUID | None = None,
) -> dict[str, Any]:
    existing = await _load_publishable_summary(pool, process_id, version_id)
    if existing is not None:
        return {
            "validation": existing["validation"],
            "model": existing["model"],
            "generation_ms": existing["generation_ms"],
            "usage": existing["usage"],
            "cache_hit": existing["cache_hit"],
            "cost_usd": existing["cost_usd"],
            "persisted": False,
            "reused": True,
        }

    started = perf_counter()
    context = await _load_context(pool, process_id, version_id, tenant_id=tenant_id)

    model = MODEL
    prompt_version = PROMPT_VERSION
    usage: dict[str, Any] = {}
    cache_hit: bool | None = None
    cost_usd: float | None = None
    if _is_secret_context(context):
        text = _secret_summary(context)
        result: ValidationResult = validar(
            text=text,
            code=context["code"],
            parties=context.get("validation_parties", []),
            forbid_party_names=True,
        )
        model = SECRET_MODEL
        prompt_version = SECRET_PROMPT_VERSION
    else:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required")
        client = anthropic_client(api_key)
        model = _generation_model(context)

        text = await _generate(client, context)
        result = _validate_provider_summary(text, context)
        if not result.passed:
            text = await _generate(client, context, result.errors)
            result = _validate_provider_summary(text, context)

        telemetry = context.get("_generation_telemetry", {})
        if isinstance(telemetry, dict):
            raw_usage = telemetry.get("usage")
            if isinstance(raw_usage, dict):
                usage = dict(raw_usage)
            cache_hit = bool(telemetry.get("cache_hit"))
            raw_cost = telemetry.get("cost_usd")
            if raw_cost is not None:
                try:
                    cost_usd = float(raw_cost)
                except (TypeError, ValueError):
                    cost_usd = None

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
            usage=usage,
            cache_hit=cache_hit,
            cost_usd=cost_usd,
            selected_sources=list(context.get("_selected_sources", [])),
            attachment_sources=list(context.get("_attachment_sources", [])),
            glossary_sources=list(context.get("_glossary_sources", [])),
        )
    return {
        "validation": validation,
        "model": model,
        "generation_ms": generation_ms,
        "usage": usage,
        "cache_hit": cache_hit,
        "cost_usd": cost_usd,
        "persisted": persisted,
        "reused": False,
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
        tenant_id = await resolve_generation_tenant(
            pool,
            judit_request_id=(
                str(payload["judit_request_id"])
                if payload.get("judit_request_id")
                else None
            ),
            process_id=process_id,
        )
        return await generate_summary(
            pool,
            process_id,
            version_id,
            tenant_id=tenant_id,
        )
    finally:
        await pool.close()
