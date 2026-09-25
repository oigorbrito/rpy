from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
from typing import Any

from app.datajud_client import lookup_datajud_metadata
from app.judit import normalize_cnj
from app.judit_client import (
    JuditRequestError,
    check_judit_connectivity,
    create_lawsuit_request,
    judit_attachments_enabled,
)

_CAPTURE_SCHEMA_VERSION = 1


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
    if isinstance(exc, JuditRequestError):
        report["error_code"] = exc.error_code
        report["http_status"] = exc.http_status
        report["retry_safe"] = exc.retry_safe
        report["network_call_attempted"] = True
    return report


def _capture_document(*, code: str, report: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_cnj(code)
    return {
        "schema_version": _CAPTURE_SCHEMA_VERSION,
        "kind": "provider_acceptance_capture",
        "request": {
            "provider": report.get("provider"),
            "cnj_sha256": _hash_identifier(normalized),
            "judit": {
                "operation": "lawsuit_cnj",
                "with_attachments": False,
            },
            "datajud": {
                "operation": "public_cnj_lookup",
                "secrecy_level": 0,
            },
        },
        "response": report,
    }


def write_capture(path: Path, *, code: str, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = _capture_document(code=code, report=report)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_replay(path: Path, *, code: str) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RuntimeError("provider replay capture must be a JSON object")
    if document.get("schema_version") != _CAPTURE_SCHEMA_VERSION:
        raise RuntimeError("unsupported provider replay capture schema")
    if document.get("kind") != "provider_acceptance_capture":
        raise RuntimeError("invalid provider replay capture kind")

    request = document.get("request")
    response = document.get("response")
    if not isinstance(request, dict) or not isinstance(response, dict):
        raise RuntimeError("provider replay capture is incomplete")

    expected_hash = _hash_identifier(normalize_cnj(code))
    if request.get("cnj_sha256") != expected_hash:
        raise RuntimeError("provider replay capture does not match the requested CNJ")

    replay = dict(response)
    replay["executed"] = True
    replay["network_calls_performed"] = False
    replay["replayed"] = True
    return replay


async def diagnose_judit() -> dict[str, Any]:
    report = _base_report("judit")
    if not os.getenv("JUDIT_API_KEY", "").strip():
        report["status"] = "skipped_missing_credentials"
        return report

    started = perf_counter()
    try:
        await check_judit_connectivity()
    except JuditRequestError as exc:
        elapsed_ms = (perf_counter() - started) * 1000
        report.update(
            {
                "executed": True,
                "network_calls_performed": True,
                "network_call_type": "non_creating_connectivity_check",
                "status": "error",
                "latency_ms": round(elapsed_ms, 3),
                "error_class": type(exc).__name__,
                "error_code": exc.error_code,
                "http_status": exc.http_status,
                "retry_safe": exc.retry_safe,
            }
        )
        return report

    elapsed_ms = (perf_counter() - started) * 1000
    report.update(
        {
            "executed": True,
            "network_calls_performed": True,
            "network_call_type": "non_creating_connectivity_check",
            "status": "ok",
            "latency_ms": round(elapsed_ms, 3),
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
        report = _base_report("both")
        if not _env_bool("PROVIDER_ACCEPTANCE_AUTHORIZED", False):
            report["status"] = "skipped_missing_authorization"
            return report
        if judit_attachments_enabled():
            raise RuntimeError("Judit live smoke requires JUDIT_ATTACHMENTS_ENABLED=false")
        if not _env_bool("DATAJUD_ENABLED", False):
            report["status"] = "skipped_disabled"
            return report
        if not _env_bool("DATAJUD_AUTHORIZED_USE", False):
            report["status"] = "skipped_missing_authorization"
            return report
        if (
            not os.getenv("JUDIT_API_KEY", "").strip()
            or not os.getenv("DATAJUD_API_KEY", "").strip()
        ):
            report["status"] = "skipped_missing_credentials"
            return report

        diagnostic = await diagnose_judit()
        if diagnostic["status"] != "ok":
            return {
                "provider": "both",
                "executed": False,
                "network_calls_performed": False,
                "status": "blocked_judit_diagnostic",
                "diagnostic": diagnostic,
            }

        judit, datajud = await asyncio.gather(
            _smoke_judit(normalized),
            _smoke_datajud(normalized),
        )
        return {
            "provider": "both",
            "executed": bool(judit["executed"] and datajud["executed"]),
            "network_calls_performed": bool(
                judit["network_calls_performed"] and datajud["network_calls_performed"]
            ),
            "status": "ok"
            if (
                judit["status"] == "request_created"
                and datajud["status"] in {"ok", "not_found"}
            )
            else "incomplete",
            "results": {
                "judit": judit,
                "datajud": datajud,
            },
        }
    raise ValueError(f"unsupported provider: {provider}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run one controlled live provider smoke or replay a sanitized capture "
            "against an explicitly authorized CNJ."
        )
    )
    parser.add_argument("--provider", choices=("judit", "datajud", "both"), default="both")
    parser.add_argument(
        "--diagnose-judit",
        action="store_true",
        help="Validate Judit API-key connectivity with a non-creating GET and exit",
    )
    parser.add_argument(
        "--cnj",
        default=os.getenv("PROVIDER_ACCEPTANCE_CNJ", ""),
        help="Explicitly authorized CNJ; defaults to PROVIDER_ACCEPTANCE_CNJ",
    )
    parser.add_argument(
        "--capture-file",
        type=Path,
        default=None,
        help="Write a sanitized request/response capture after a successful live execution",
    )
    parser.add_argument(
        "--replay-file",
        type=Path,
        default=None,
        help="Replay a prior sanitized capture without provider network calls",
    )
    args = parser.parse_args()

    if args.diagnose_judit:
        report = asyncio.run(diagnose_judit())
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["status"] == "ok" else 1

    if not str(args.cnj).strip():
        report = _base_report(args.provider)
        report["status"] = "skipped_missing_cnj"
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    try:
        if args.replay_file is not None:
            report = load_replay(args.replay_file, code=args.cnj)
        else:
            report = asyncio.run(run_smoke(args.provider, args.cnj))
            if args.capture_file is not None and report.get("executed"):
                write_capture(args.capture_file, code=args.cnj, report=report)
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
