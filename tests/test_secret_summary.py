from __future__ import annotations

from uuid import uuid4

import pytest

import app.rag as rag


class _Acquire:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _Pool:
    def acquire(self) -> _Acquire:
        return _Acquire()


def _secret_context() -> dict:
    return {
        "code": "9999999-99.9999.9.99.9999",
        "class_name": "Procedimento Sigiloso",
        "secrecy_level": 1,
        "header": {
            "name": "NOME QUE NAO DEVE SER EXIBIDO",
            "instance": 1,
            "area": "Cível",
            "justice_description": "Justiça Estadual",
            "county": "Porto Alegre",
            "state": "RS",
            "city": "Porto Alegre",
            "amount": 999999.99,
        },
        "parties": [],
        "subjects": [],
        "steps": [],
    }


def test_secret_summary_is_minimal_and_deterministic() -> None:
    text = rag._secret_summary(_secret_context())

    assert "Procedimento Sigiloso" in text
    assert "Instância: 1" in text
    assert "Área: Cível" in text
    assert "Estado: RS" in text
    assert "restringidos por sigilo" in text
    assert "9999999-99.9999.9.99.9999" not in text
    assert "NOME QUE NAO DEVE SER EXIBIDO" not in text
    assert "999999.99" not in text


@pytest.mark.asyncio
async def test_secret_generation_does_not_require_or_call_anthropic(monkeypatch) -> None:
    context = _secret_context()
    persisted: dict = {}

    async def fake_load_context(pool, process_id, version_id):
        return context

    async def fake_persist_summary(conn, **kwargs):
        persisted.update(kwargs)
        return True

    def provider_must_not_be_created(api_key: str):
        raise AssertionError("Anthropic client must not be created for secret proceedings")

    monkeypatch.setattr(rag, "_load_context", fake_load_context)
    monkeypatch.setattr(rag, "_persist_summary", fake_persist_summary)
    monkeypatch.setattr(rag, "anthropic_client", provider_must_not_be_created)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = await rag.generate_summary(_Pool(), uuid4(), uuid4())

    assert result["model"] == rag.SECRET_MODEL
    assert result["validation"]["passed"] is True
    assert result["persisted"] is True
    assert persisted["model"] == rag.SECRET_MODEL
    assert persisted["prompt_version"] == rag.SECRET_PROMPT_VERSION
    assert persisted["text"] == rag._secret_summary(context)
