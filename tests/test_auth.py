from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.auth import configured_bearer_tokens, tenant_from_request


def _request(authorization: str, configured: dict[str, UUID] | None = None):
    state = SimpleNamespace()
    if configured is not None:
        state.bearer_tokens = configured
    return SimpleNamespace(
        headers={"authorization": authorization},
        app=SimpleNamespace(state=state),
    )


def test_configured_tokens_parse_to_uuid_values(monkeypatch) -> None:
    tenant_id = uuid4()
    monkeypatch.setenv("RPY_BEARER_TOKENS", json.dumps({"token-a": str(tenant_id)}))

    configured = configured_bearer_tokens()

    assert configured == {"token-a": tenant_id}
    assert isinstance(configured["token-a"], UUID)


def test_invalid_json_fails_configuration(monkeypatch) -> None:
    monkeypatch.setenv("RPY_BEARER_TOKENS", "{not-json")

    with pytest.raises(RuntimeError, match="valid JSON"):
        configured_bearer_tokens()


def test_invalid_tenant_uuid_fails_configuration(monkeypatch) -> None:
    monkeypatch.setenv("RPY_BEARER_TOKENS", json.dumps({"token-a": "not-a-uuid"}))

    with pytest.raises(RuntimeError, match="invalid tenant UUID"):
        configured_bearer_tokens()


def test_empty_map_fails_configuration(monkeypatch) -> None:
    monkeypatch.setenv("RPY_BEARER_TOKENS", "{}")

    with pytest.raises(RuntimeError, match="at least one bearer token"):
        configured_bearer_tokens()


def test_empty_token_fails_configuration(monkeypatch) -> None:
    monkeypatch.setenv("RPY_BEARER_TOKENS", json.dumps({"": str(uuid4())}))

    with pytest.raises(RuntimeError, match="empty bearer token"):
        configured_bearer_tokens()


@pytest.mark.parametrize("token", [" token-a", "token-a ", "token a", "token\ta", "token\na"])
def test_whitespace_in_configured_token_fails_configuration(monkeypatch, token: str) -> None:
    monkeypatch.setenv("RPY_BEARER_TOKENS", json.dumps({token: str(uuid4())}))

    with pytest.raises(RuntimeError, match="must not contain whitespace"):
        configured_bearer_tokens()


@pytest.mark.parametrize("token", ["sk_live_legacytoken", "sk_test_legacytoken"])
def test_api_key_prefix_is_rejected_for_legacy_bearer_configuration(
    monkeypatch,
    token: str,
) -> None:
    monkeypatch.setenv("RPY_BEARER_TOKENS", json.dumps({token: str(uuid4())}))

    with pytest.raises(RuntimeError, match="must not use sk_live_ or sk_test_ prefixes"):
        configured_bearer_tokens()


def test_non_string_tenant_id_fails_configuration(monkeypatch) -> None:
    monkeypatch.setenv("RPY_BEARER_TOKENS", json.dumps({"token-a": 123}))

    with pytest.raises(RuntimeError, match="UUID strings"):
        configured_bearer_tokens()


def test_rotation_allows_two_tokens_for_same_tenant() -> None:
    tenant_id = uuid4()
    configured = {
        "old-token": tenant_id,
        "new-token": tenant_id,
    }

    old_request = _request("Bearer old-token", configured)
    new_request = _request("Bearer new-token", configured)

    assert tenant_from_request(old_request) == tenant_id
    assert tenant_from_request(new_request) == tenant_id


def test_unknown_token_is_unauthorized() -> None:
    request = _request("Bearer wrong-token", {"known-token": uuid4()})

    with pytest.raises(HTTPException) as exc_info:
        tenant_from_request(request)

    assert exc_info.value.status_code == 401


def test_bearer_value_trims_transport_whitespace() -> None:
    tenant_id = uuid4()
    request = _request("Bearer   token-a   ", {"token-a": tenant_id})

    assert tenant_from_request(request) == tenant_id
