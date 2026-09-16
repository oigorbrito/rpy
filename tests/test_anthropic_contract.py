from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.prompts import PROCESS_SUMMARY_SYSTEM_PROMPT
from app.rag import (
    MAX_TOKENS,
    MODEL,
    OPUS_MODEL,
    PROMPT_VERSION,
    REQUESTED_TEMPERATURE,
    SONNET_MODEL,
    _generate,
)


@dataclass
class _TextBlock:
    type: str = "text"
    text: str = "# Resumo válido"


@dataclass
class _Usage:
    input_tokens: int = 100
    output_tokens: int = 20
    cache_creation_input_tokens: int = 80
    cache_read_input_tokens: int = 10


@dataclass
class _Message:
    content: list[_TextBlock]
    usage: _Usage | None = None


class _Messages:
    def __init__(self, *, usage: _Usage | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.usage = usage

    async def create(self, **kwargs: Any) -> _Message:
        self.calls.append(kwargs)
        return _Message(content=[_TextBlock()], usage=self.usage)


class _Client:
    def __init__(self, *, usage: _Usage | None = None) -> None:
        self.messages = _Messages(usage=usage)


def _context(*, step_count: int) -> dict[str, Any]:
    return {
        "code": "0000000-00.0000.0.00.0601",
        "class_name": "Procedimento Comum",
        "court": "TJRS",
        "header": {},
        "parties": [],
        "subjects": [],
        "secrecy_level": 0,
        "step_count": step_count,
        "steps": [{"step_number": 1, "title": "DISTRIBUIÇÃO", "text": "Distribuído."}],
    }


@pytest.mark.asyncio
async def test_sonnet_5_request_uses_cacheable_system_prompt_without_custom_sampling() -> None:
    client = _Client(usage=_Usage())
    context = _context(step_count=100)

    text = await _generate(client, context)

    assert text == "# Resumo válido"
    assert len(client.messages.calls) == 1
    request = client.messages.calls[0]
    assert request["model"] == MODEL == SONNET_MODEL == "claude-sonnet-5"
    assert request["max_tokens"] == MAX_TOKENS == 4000
    assert PROMPT_VERSION == "process-summary-v2"
    assert REQUESTED_TEMPERATURE == 0.2
    assert "temperature" not in request
    assert "top_p" not in request
    assert "top_k" not in request
    assert "stream" not in request

    system = request["system"]
    assert len(system) == 1
    assert system[0]["type"] == "text"
    assert system[0]["text"] == PROCESS_SUMMARY_SYSTEM_PROMPT
    assert system[0]["cache_control"] == {"type": "ephemeral"}

    # Conservative proxy for the prompt-cache minimum without tokenizer coupling.
    assert len(PROCESS_SUMMARY_SYSTEM_PROMPT.split()) >= 1100

    user_content = request["messages"][0]["content"]
    assert "<processo>" in user_content
    assert "<movimentos>" in user_content
    assert context["code"] in user_content

    telemetry = context["_generation_telemetry"]
    assert telemetry["model"] == SONNET_MODEL
    assert telemetry["attempts"] == 1
    assert telemetry["usage"] == {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_creation_input_tokens": 80,
        "cache_read_input_tokens": 10,
    }
    assert telemetry["cache_hit"] is True


@pytest.mark.asyncio
async def test_more_than_100_movements_use_opus_5() -> None:
    client = _Client()
    context = _context(step_count=101)

    await _generate(client, context)

    request = client.messages.calls[0]
    assert request["model"] == OPUS_MODEL == "claude-opus-5"
    assert request["max_tokens"] == 4000
    assert "temperature" not in request
    assert "stream" not in request


@pytest.mark.asyncio
async def test_usage_accumulates_across_one_correction_attempt() -> None:
    client = _Client(usage=_Usage(input_tokens=50, output_tokens=10, cache_read_input_tokens=5))
    context = _context(step_count=10)

    await _generate(client, context)
    await _generate(client, context, ["erro de validação"])

    telemetry = context["_generation_telemetry"]
    assert telemetry["attempts"] == 2
    assert telemetry["usage"]["input_tokens"] == 100
    assert telemetry["usage"]["output_tokens"] == 20
    assert telemetry["usage"]["cache_read_input_tokens"] == 10
    assert telemetry["cache_hit"] is True
    # Internal telemetry must never be copied into a correction prompt.
    assert "_generation_telemetry" not in client.messages.calls[1]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_validation_errors_are_sent_only_in_dynamic_user_content() -> None:
    client = _Client()
    context = _context(step_count=1)
    context["code"] = "0000000-00.0000.0.00.0602"
    context["steps"] = []
    errors = ["prognostic language is prohibited", "JSX must use className="]

    await _generate(client, context, errors)

    request = client.messages.calls[0]
    system_text = request["system"][0]["text"]
    user_content = request["messages"][0]["content"]
    for error in errors:
        assert error not in system_text
        assert error in user_content
    assert "<validation_errors>" in user_content


@pytest.mark.asyncio
async def test_secret_case_sends_only_class_and_allowed_header_to_provider() -> None:
    client = _Client()
    context = {
        "code": "9999999-99.9999.9.99.9999",
        "class_name": "Procedimento Sigiloso",
        "court": "TRIBUNAL-NAO-ENVIAR",
        "header": {
            "instance": 1,
            "area": "Cível",
            "state": "RS",
        },
        "parties": [{"name": "PARTE-SECRETA"}],
        "subjects": [{"name": "ASSUNTO-SECRETO"}],
        "secrecy_level": 1,
        "steps": [
            {
                "step_number": 1,
                "title": "MOVIMENTO-SECRETO",
                "text": "CONTEUDO-SECRETO",
            }
        ],
    }

    await _generate(client, context)

    user_content = client.messages.calls[0]["messages"][0]["content"]
    assert '"class_name": "Procedimento Sigiloso"' in user_content
    assert '"instance": 1' in user_content
    assert '"area": "Cível"' in user_content
    assert '"state": "RS"' in user_content
    assert "<movimentos>\n[]\n</movimentos>" in user_content

    for forbidden in (
        context["code"],
        "TRIBUNAL-NAO-ENVIAR",
        "PARTE-SECRETA",
        "ASSUNTO-SECRETO",
        "MOVIMENTO-SECRETO",
        "CONTEUDO-SECRETO",
        "secrecy_level",
        "parties",
        "subjects",
        "court",
    ):
        assert forbidden not in user_content
