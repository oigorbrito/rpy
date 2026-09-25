from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from time import perf_counter
from typing import Any

from app.datajud_client import lookup_datajud_metadata
from app.judit import normalize_cnj
from app.judit_client import create_lawsuit_request, judit_attachments_enabled


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean")


def _hash_identifier(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _base_report(provider: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "executed": False,
        "network_calls_performed": False,
        "status": "not_started",
        "latency_ms": None,
    }


def _error_report(provider: str, exc: Exception) -> dict[str, Any]:
    report = _base_report(provider)
    report.update(
        {
            "status": "error",
            "error_class": type(exc).__name__,
        }
    )
    return report


async def _smoke_judit(code: str) -> dict[str, Any]:
    report = _base_report("judit")
    if not os.getenv("JUDIT_API_KEY", "").strip():
        report["status"] = "skipped_missing_credentials"
        return report
    if not _env_bool("PROVIDER_ACCEPTANCE_AUTHORIZED", False):
        report["status"] = "skipped_missing_authorization"
        return report
    if judit_attachments_enabled():
        raise RuntimeError("Judit live smoke requires JUDIT_ATTACHMENTS_ENABLED=false")

    started = perf_counter()
    result = await create_lawsuit_request(code)
    elapsed_ms = (perf_counter() - started) * 1000
    report.update(
        {
            "executed": True,
            "network_calls_performed": True,
            "status": "request_created",
            "latency_ms": round(elapsed_ms, 3),
            "request_id_sha256": _hash_identifier(result.request_id),
        }
    )
    return report


async def _smoke_datajud(code: str) -> dict[str, Any]:
    report = _base_report("datajud")
    if not _env_bool("DATAJUD_ENABLED", False):
        report["status"] = "skipped_disabled"
        return report
    if not _env_bool("DATAJUD_AUTHORIZED_USE", False):
        report["status"] = "skipped_missing_authorization"
        return report
    if not os.getenv("DATAJUD_API_KEY", "").strip():
        report["status"] = "skipped_missing_credentials"
        return report
    if not _env_bool("PROVIDER_ACCEPTANCE_AUTHORIZED", False):
        report["status"] = "skipped_missing_authorization"
        return report

    started = perf_counter()
    result = await lookup_datajud_metadata(code=code, secrecy_level=0)
    elapsed_ms = (perf_counter() - started) * 1000
    report.update(
        {
            "executed": True,
            "network_calls_performed": True,
            "status": result.status,
            "latency_ms": round(elapsed_ms, 3),
            "metadata_present": result.metadata is not None,
            "error_code": result.error_code,
        }
    )
    return report


async def run_smoke(provider: str, code: str) -> dict[str, Any]:
    normalized = normalize_cnj(code)
    if provider == "judit":
        return await _smoke_judit(normalized)
    if provider == "datajud":
        return await _smoke_datajud(normalized)
    if provider == "both":
        judit, datajud = await asyncio.gather(
            _smoke_judit(normalized),
            _smoke_datajud(normalized),
        )
        return {
            "provider": "both",
            "executed": bool(judit["executed"] and datajud["executed"]),
            "network_calls_performed": bool(
                judit["network_calls_performed"] or datajud["network_calls_performed"]
            ),
            "status": "ok" if (
                judit["status"] == "request_created"
                and datajud["status"] in {"ok", "not_found"}
            ) else "incomplete",
            "results": {
                "judit": judit,
                "datajud": datajud,
            },
        }
    raise ValueError(f"unsupported provider: {provider}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run one controlled live provider smoke against an explicitly authorized CNJ. "
            "Missing credentials/authorization produce a non-accepting skipped result."
        )
    )
    parser.add_argument("--provider", choices=("judit", "datajud", "both"), default="both")
    parser.add_argument(
        "--cnj",
        default=os.getenv("PROVIDER_ACCEPTANCE_CNJ", ""),
        help="Explicitly authorized CNJ; defaults to PROVIDER_ACCEPTANCE_CNJ",
    )
    args = parser.parse_args()

    if not str(args.cnj).strip():
        report = _base_report(args.provider)
        report["status"] = "skipped_missing_cnj"
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    try:
        report = asyncio.run(run_smoke(args.provider, args.cnj))
    except Exception as exc:
        report = _error_report(args.provider, exc)

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report["status"] == "error":
        return 1
    if not report["executed"]:
        return 0
    if args.provider == "both":
        return 0 if report["status"] == "ok" else 1
    if args.provider == "datajud":
        return 0 if report["status"] in {"ok", "not_found"} else 1
    return 0 if report["status"] == "request_created" else 1


if __name__ == "__main__":
    raise SystemExit(main())
