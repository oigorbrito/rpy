from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.datajud_enrichment import DataJudMetadata
from app.judit import normalize_cnj

DEFAULT_DATAJUD_BASE_URL = "https://api-publica.datajud.cnj.jus.br"
DEFAULT_DATAJUD_TIMEOUT_SECONDS = 20.0

LookupStatus = Literal[
    "ok",
    "disabled",
    "skipped_secrecy",
    "not_found",
    "auth_error",
    "unavailable",
]

_STATE_TR_TO_UF = {
    "01": "ac",
    "02": "al",
    "03": "ap",
    "04": "am",
    "05": "ba",
    "06": "ce",
    "07": "df",
    "08": "es",
    "09": "go",
    "10": "ma",
    "11": "mt",
    "12": "ms",
    "13": "mg",
    "14": "pa",
    "15": "pb",
    "16": "pr",
    "17": "pe",
    "18": "pi",
    "19": "rj",
    "20": "rn",
    "21": "rs",
    "22": "ro",
    "23": "rr",
    "24": "sc",
    "25": "se",
    "26": "sp",
    "27": "to",
}


@dataclass(frozen=True, slots=True)
class DataJudConfig:
    enabled: bool
    authorized_use: bool
    api_key: str | None
    base_url: str = DEFAULT_DATAJUD_BASE_URL
    timeout_seconds: float = DEFAULT_DATAJUD_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class DataJudLookupResult:
    status: LookupStatus
    metadata: DataJudMetadata | None = None
    error_code: str | None = None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean")


def datajud_config() -> DataJudConfig:
    enabled = _env_bool("DATAJUD_ENABLED", False)
    authorized_use = _env_bool("DATAJUD_AUTHORIZED_USE", False)
    api_key = str(os.getenv("DATAJUD_API_KEY") or "").strip() or None
    base_url = str(os.getenv("DATAJUD_BASE_URL") or DEFAULT_DATAJUD_BASE_URL).strip().rstrip("/")
    raw_timeout = str(
        os.getenv("DATAJUD_TIMEOUT_SECONDS") or DEFAULT_DATAJUD_TIMEOUT_SECONDS
    ).strip()
    try:
        timeout_seconds = float(raw_timeout)
    except ValueError as exc:
        raise RuntimeError("DATAJUD_TIMEOUT_SECONDS must be numeric") from exc
    if timeout_seconds <= 0:
        raise RuntimeError("DATAJUD_TIMEOUT_SECONDS must be greater than zero")
    if enabled and not authorized_use:
        raise RuntimeError(
            "DATAJUD_ENABLED requires DATAJUD_AUTHORIZED_USE=true after deployment/legal review"
        )
    if enabled and not api_key:
        raise RuntimeError("DATAJUD_ENABLED requires DATAJUD_API_KEY")
    if not base_url.startswith("https://"):
        raise RuntimeError("DATAJUD_BASE_URL must use https")
    return DataJudConfig(
        enabled=enabled,
        authorized_use=authorized_use,
        api_key=api_key,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
    )


def datajud_alias_from_cnj(code: str) -> str:
    canonical = normalize_cnj(code)
    digits = "".join(character for character in canonical if character.isdigit())
    segment = digits[13]
    tribunal = digits[14:16]

    if segment == "4" and tribunal in {"01", "02", "03", "04", "05", "06"}:
        return f"trf{int(tribunal)}"
    if segment == "5":
        if tribunal == "00":
            return "tst"
        if 1 <= int(tribunal) <= 24:
            return f"trt{int(tribunal)}"
    if segment == "6":
        if tribunal == "00":
            return "tse"
        uf = _STATE_TR_TO_UF.get(tribunal)
        if uf:
            return f"tre-{uf}"
    if segment == "7" and tribunal == "00":
        return "stm"
    if segment == "8":
        uf = _STATE_TR_TO_UF.get(tribunal)
        if uf:
            return f"tj{uf}"
    if segment == "9":
        military_aliases = {
            "13": "tjmmg",
            "21": "tjmrs",
            "26": "tjmsp",
        }
        alias = military_aliases.get(tribunal)
        if alias:
            return alias

    raise ValueError(
        f"unsupported DataJud tribunal segment/TR for CNJ: J={segment}, TR={tribunal}"
    )


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None


def _metadata_from_source(source: dict[str, Any], *, source_ref: str) -> DataJudMetadata:
    raw_class = source.get("classe") if isinstance(source.get("classe"), dict) else {}
    raw_subjects = source.get("assuntos") if isinstance(source.get("assuntos"), list) else []
    raw_body = (
        source.get("orgaoJulgador")
        if isinstance(source.get("orgaoJulgador"), dict)
        else {}
    )

    subjects: list[dict[str, str]] = []
    for raw in raw_subjects:
        if not isinstance(raw, dict):
            continue
        item: dict[str, str] = {}
        code = _clean(raw.get("codigo"))
        name = _clean(raw.get("nome"))
        if code:
            item["code"] = code
        if name:
            item["name"] = name
        if item:
            subjects.append(item)

    county = _clean(source.get("comarca"))
    return DataJudMetadata(
        class_name=_clean(raw_class.get("nome")),
        class_code=_clean(raw_class.get("codigo")),
        subjects=tuple(subjects),
        adjudicating_body=_clean(raw_body.get("nome")),
        county=county,
        source_ref=source_ref,
    )


def _request_payload(code: str) -> bytes:
    digits = "".join(character for character in normalize_cnj(code) if character.isdigit())
    return json.dumps(
        {
            "size": 1,
            "query": {"match": {"numeroProcesso": digits}},
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _perform_lookup(
    *,
    endpoint: str,
    api_key: str,
    code: str,
    timeout_seconds: float,
) -> DataJudLookupResult:
    request = Request(
        endpoint,
        data=_request_payload(code),
        method="POST",
        headers={
            "Authorization": f"APIKey {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "rpy-datajud/0.1",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            raw = response.read()
    except HTTPError as exc:
        if exc.code in {401, 403}:
            return DataJudLookupResult(status="auth_error", error_code=f"http_{exc.code}")
        return DataJudLookupResult(status="unavailable", error_code=f"http_{exc.code}")
    except (URLError, TimeoutError, OSError):
        return DataJudLookupResult(status="unavailable", error_code="transport_error")

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return DataJudLookupResult(status="unavailable", error_code="invalid_json")

    hits = payload.get("hits") if isinstance(payload, dict) else None
    hit_list = hits.get("hits") if isinstance(hits, dict) else None
    if not isinstance(hit_list, list) or not hit_list:
        return DataJudLookupResult(status="not_found")

    first = hit_list[0] if isinstance(hit_list[0], dict) else {}
    source = first.get("_source") if isinstance(first.get("_source"), dict) else None
    if source is None:
        return DataJudLookupResult(status="unavailable", error_code="missing_source")

    return DataJudLookupResult(
        status="ok",
        metadata=_metadata_from_source(source, source_ref=endpoint),
    )


async def lookup_datajud_metadata(
    *,
    code: str,
    secrecy_level: int,
    config: DataJudConfig | None = None,
) -> DataJudLookupResult:
    if secrecy_level > 0:
        return DataJudLookupResult(status="skipped_secrecy")
    active = config or datajud_config()
    if not active.enabled:
        return DataJudLookupResult(status="disabled")
    if not active.authorized_use or not active.api_key:
        raise RuntimeError("enabled DataJud lookup requires authorized use and an API key")

    try:
        alias = datajud_alias_from_cnj(code)
    except ValueError:
        return DataJudLookupResult(status="unavailable", error_code="unsupported_tribunal")

    endpoint = f"{active.base_url}/api_publica_{alias}/_search"
    return await asyncio.to_thread(
        _perform_lookup,
        endpoint=endpoint,
        api_key=active.api_key,
        code=code,
        timeout_seconds=active.timeout_seconds,
    )
