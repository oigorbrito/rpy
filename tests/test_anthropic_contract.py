from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.prompts import PROCESS_SUMMARY_SYSTEM_PROMPT
from app.rag import MODEL, PROMPT_VERSION, _generate


@dataclass
class _TextBlock:
    type: str = "text"
    text: str = "# Resumo válido"


@dataclass
class _Message:
    content: list[_TextBlock]


class _Messages:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _Message:
        self.calls.append(kwargs)
        return _Message(content=[_TextBlock()])


class _Client:
    def __init__(self) -> None:
        self.messages = _Messages()


@pytest.mark.asyncio
async def test_sonnet_5_request_uses_cacheable_system_prompt_without_custom_sampling() -> None:
    client = _Client()
    context = {
        "code": "0000000-00.0000.0.00.0601",
        "class_name": "Procedimento Comum",
        "court": "TJRS",
        "header": {},
        "parties": [],
        "subjects": [],
        "secrecy_level": 0,
        "steps": [{"step_number": 1, "title": "DISTRIBUIÇÃO", "text": "Distribuído."}],
    }

    text = await _generate(client, context)

    assert text == "# Resumo válido"
    assert len(client.messages.calls) == 1
    request = client.messages.calls[0]
    assert request["model"] == MODEL == "claude-sonnet-5"
    assert PROMPT_VERSION == "process-summary-v2"
    assert "temperature" not in request
    assert "top_p" not in request
    assert "top_k" not in request

    system = request["system"]
    assert len(system) == 1
    assert system[0]["type"] == "text"
    assert system[0]["text"] == PROCESS_SUMMARY_SYSTEM_PROMPT
    assert system[0]["cache_control"] == {"type": "ephemeral"}

    # Conservative proxy for Sonnet 5's 1,024-token prompt-cache minimum.
    # Portuguese legal prose typically tokenizes to more than one token per word,
    # so 1,100+ whitespace-delimited words provides margin without tokenizer coupling.
    assert len(PROCESS_SUMMARY_SYSTEM_PROMPT.split()) >= 1100

    user_content = request["messages"][0]["content"]
    assert "<processo>" in user_content
    assert "<movimentos>" in user_content
    assert context["code"] in user_content


@pytest.mark.asyncio
async def test_validation_errors_are_sent_only_in_dynamic_user_content() -> None:
    client = _Client()
    context = {
        "code": "0000000-00.0000.0.00.0602",
        "class_name": "Classe",
        "court": None,
        "header": {},
        "parties": [],
        "subjects": [],
        "secrecy_level": 0,
        "steps": [],
    }
    errors = ["prognostic language is prohibited", "JSX must use className="]

    await _generate(client, context, errors)

    request = client.messages.calls[0]
    system_text = request["system"][0]["text"]
    user_content = request["messages"][0]["content"]
    for error in errors:
        assert error not in system_text
        assert error in user_content
    assert "<validation_errors>" in user_content
