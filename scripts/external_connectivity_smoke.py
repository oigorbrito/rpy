from __future__ import annotations

import argparse
import json
import socket
import urllib.error
import urllib.request
from time import perf_counter
from typing import Any, Callable

from app.http_safety import ResponseTooLargeError, read_bounded_response
from app.json_utils import loads_strict_json

POSTMAN_ECHO_URL = "https://postman-echo.com/post"
MAX_RESPONSE_BYTES = 64 * 1024
TIMEOUT_SECONDS = 15.0
PROBE_HEADER = "external-connectivity-v1"
PROBE_BODY = {
    "probe": "rpy-external-connectivity",
    "version": 1,
}


def _header_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key).strip().casefold(): str(item).strip()
        for key, item in value.items()
    }


def run_echo_probe(
    *,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
    timeout_seconds: float = TIMEOUT_SECONDS,
) -> dict[str, Any]:
    payload = json.dumps(PROBE_BODY, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        POSTMAN_ECHO_URL,
        data=payload,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "rpy-external-connectivity/1",
            "X-Rpy-Probe": PROBE_HEADER,
        },
    )

    started = perf_counter()
    report: dict[str, Any] = {
        "target": "postman_echo",
        "url_host": "postman-echo.com",
        "request_method": "POST",
        "executed": True,
        "network_calls_performed": True,
        "status": "error",
        "http_status": None,
        "latency_ms": None,
        "json_roundtrip": False,
        "probe_header_roundtrip": False,
        "content_type_roundtrip": False,
        "error_code": None,
    }

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            report["http_status"] = int(getattr(response, "status", 0) or 0)
            raw = read_bounded_response(response, max_bytes=MAX_RESPONSE_BYTES)
    except ResponseTooLargeError:
        report["error_code"] = "response_too_large"
    except urllib.error.HTTPError as exc:
        report["http_status"] = int(exc.code)
        report["error_code"] = f"http_{exc.code}"
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        report["error_code"] = (
            "dns_or_transport_error"
            if isinstance(reason, socket.gaierror)
            else "transport_error"
        )
    except (TimeoutError, OSError):
        report["error_code"] = "transport_error"
    else:
        try:
            body = loads_strict_json(raw)
        except (TypeError, ValueError):
            report["error_code"] = "invalid_json"
        else:
            if not isinstance(body, dict):
                report["error_code"] = "invalid_response_shape"
            else:
                echoed_headers = _header_map(body.get("headers"))
                echoed_content_type = echoed_headers.get("content-type", "")
                report["json_roundtrip"] = body.get("json") == PROBE_BODY
                report["probe_header_roundtrip"] = (
                    echoed_headers.get("x-rpy-probe") == PROBE_HEADER
                )
                report["content_type_roundtrip"] = (
                    echoed_content_type.casefold().startswith("application/json")
                )
                if (
                    report["http_status"] == 200
                    and report["json_roundtrip"]
                    and report["probe_header_roundtrip"]
                    and report["content_type_roundtrip"]
                ):
                    report["status"] = "ok"
                    report["error_code"] = None
                else:
                    report["status"] = "mismatch"
                    report["error_code"] = "echo_contract_mismatch"
    finally:
        report["latency_ms"] = round((perf_counter() - started) * 1000, 3)

    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Perform a credential-free HTTPS POST probe against Postman Echo "
            "using the same Python stdlib HTTP stack used by Rpy providers."
        )
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=TIMEOUT_SECONDS,
        help="HTTPS request timeout (default: 15 seconds)",
    )
    args = parser.parse_args()
    if not 0 < args.timeout_seconds <= 60:
        raise SystemExit("--timeout-seconds must be > 0 and <= 60")

    report = run_echo_probe(timeout_seconds=args.timeout_seconds)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
