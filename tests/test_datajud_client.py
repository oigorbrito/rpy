from __future__ import annotations

import json
from urllib.error import HTTPError

import pytest

import app.datajud_client as client
from app.datajud_client import (
    DataJudConfig,
    datajud_alias_from_cnj,
    datajud_config,
    lookup_datajud_metadata,
)


@pytest.mark.parametrize(
    ("code", "alias"),
    [
        ("0000000-00.2026.4.04.0001", "trf4"),
        ("0000000-00.2026.5.04.0001", "trt4"),
        ("0000000-00.2026.6.21.0001", "tre-rs"),
        ("0000000-00.2026.8.21.0001", "tjrs"),
        ("0000000-00.2026.9.21.0001", "tjmrs"),
        ("0000000-00.2026.5.00.0000", "tst"),
        ("0000000-00.2026.6.00.0000", "tse"),
        ("0000000-00.2026.7.00.0000", "stm"),
    ],
)
def test_datajud_alias_from_cnj_uses_segment_and_tribunal(code: str, alias: str) -> None:
    assert datajud_alias_from_cnj(code) == alias


def test_datajud_alias_rejects_unsupported_segment() -> None:
    with pytest.raises(ValueError, match="unsupported DataJud tribunal"):
        datajud_alias_from_cnj("0000000-00.2026.2.00.0000")


def test_datajud_config_requires_explicit_authorized_use(monkeypatch) -> None:
    monkeypatch.setenv("DATAJUD_ENABLED", "true")
    monkeypatch.setenv("DATAJUD_API_KEY", "public-key")
    monkeypatch.delenv("DATAJUD_AUTHORIZED_USE", raising=False)

    with pytest.raises(RuntimeError, match="DATAJUD_AUTHORIZED_USE=true"):
        datajud_config()


class _Response:
    def __init__(self, payload: dict) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._raw


@pytest.mark.asyncio
async def test_datajud_lookup_parses_official_metadata_without_network(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        observed["url"] = request.full_url
        observed["authorization"] = request.headers.get("Authorization")
        observed["body"] = json.loads(request.data.decode("utf-8"))
        observed["timeout"] = timeout
        return _Response(
            {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "numeroProcesso": "00000000020268210001",
                                "classe": {"codigo": 7, "nome": "Procedimento Comum Cível"},
                                "assuntos": [
                                    {"codigo": 5804, "nome": "Investigação de Paternidade"}
                                ],
                                "orgaoJulgador": {
                                    "codigo": 123,
                                    "nome": "1ª Vara Cível",
                                    "codigoMunicipioIBGE": 4314902,
                                },
                                "comarca": "Porto Alegre",
                            }
                        }
                    ]
                }
            }
        )

    monkeypatch.setattr(client, "urlopen", fake_urlopen)
    result = await lookup_datajud_metadata(
        code="0000000-00.2026.8.21.0001",
        secrecy_level=0,
        config=DataJudConfig(
            enabled=True,
            authorized_use=True,
            api_key="rotating-public-key",
            timeout_seconds=3.5,
        ),
    )

    assert result.status == "ok"
    assert result.metadata is not None
    assert result.metadata.class_name == "Procedimento Comum Cível"
    assert result.metadata.class_code == "7"
    assert result.metadata.subjects == (
        {"code": "5804", "name": "Investigação de Paternidade"},
    )
    assert result.metadata.adjudicating_body == "1ª Vara Cível"
    assert result.metadata.county == "Porto Alegre"
    assert observed["url"] == (
        "https://api-publica.datajud.cnj.jus.br/api_publica_tjrs/_search"
    )
    assert observed["authorization"] == "APIKey rotating-public-key"
    assert observed["body"] == {
        "size": 1,
        "query": {"match": {"numeroProcesso": "00000000020268210001"}},
    }
    assert observed["timeout"] == 3.5


@pytest.mark.asyncio
async def test_datajud_lookup_is_disabled_and_secret_safe_without_transport(monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("transport must not be called")

    monkeypatch.setattr(client, "urlopen", forbidden)

    disabled = await lookup_datajud_metadata(
        code="0000000-00.2026.8.21.0001",
        secrecy_level=0,
        config=DataJudConfig(enabled=False, authorized_use=False, api_key=None),
    )
    secret = await lookup_datajud_metadata(
        code="0000000-00.2026.8.21.0001",
        secrecy_level=1,
        config=DataJudConfig(
            enabled=True,
            authorized_use=True,
            api_key="public-key",
        ),
    )

    assert disabled.status == "disabled"
    assert secret.status == "skipped_secrecy"


@pytest.mark.asyncio
@pytest.mark.parametrize(("status_code", "status"), [(401, "auth_error"), (403, "auth_error"), (429, "unavailable"), (503, "unavailable")])
async def test_datajud_http_failures_are_best_effort(
    monkeypatch,
    status_code: int,
    status: str,
) -> None:
    def failing(request, timeout):
        raise HTTPError(request.full_url, status_code, "failure", {}, None)

    monkeypatch.setattr(client, "urlopen", failing)
    result = await lookup_datajud_metadata(
        code="0000000-00.2026.8.21.0001",
        secrecy_level=0,
        config=DataJudConfig(
            enabled=True,
            authorized_use=True,
            api_key="rotating-public-key",
        ),
    )

    assert result.status == status
    assert result.error_code == f"http_{status_code}"


@pytest.mark.asyncio
async def test_secret_lookup_skips_before_invalid_runtime_config(monkeypatch) -> None:
    monkeypatch.setenv("DATAJUD_ENABLED", "true")
    monkeypatch.delenv("DATAJUD_AUTHORIZED_USE", raising=False)
    monkeypatch.delenv("DATAJUD_API_KEY", raising=False)

    result = await lookup_datajud_metadata(
        code="0000000-00.2026.8.21.0001",
        secrecy_level=1,
    )

    assert result.status == "skipped_secrecy"
