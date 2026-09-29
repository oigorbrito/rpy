from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlsplit

from starlette.middleware.cors import CORSMiddleware

_PROCESS_API_PREFIXES = ("/processes/", "/v1/processos/")


def parse_browser_origins(raw: str | None) -> list[str]:
    if raw is None or not raw.strip():
        return []

    origins: list[str] = []
    seen: set[str] = set()
    for value in raw.split(","):
        origin = value.strip()
        if not origin:
            continue
        if "*" in origin:
            raise ValueError("RPY_BROWSER_ORIGINS must not contain wildcards")
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or parsed.port not in {None, 443}
        ):
            raise ValueError(
                "RPY_BROWSER_ORIGINS entries must be bare HTTPS origins"
            )
        hostname = parsed.hostname.rstrip(".").casefold()
        if hostname == "localhost" or hostname.endswith(".localhost"):
            raise ValueError("RPY_BROWSER_ORIGINS must use public DNS hostnames")
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            raise ValueError("RPY_BROWSER_ORIGINS must use public DNS hostnames")
        canonical = f"https://{hostname}"
        if canonical in seen:
            raise ValueError("RPY_BROWSER_ORIGINS must not contain duplicates")
        seen.add(canonical)
        origins.append(canonical)
    return origins


def configured_browser_origins() -> list[str]:
    return parse_browser_origins(os.environ.get("RPY_BROWSER_ORIGINS"))


def _browser_api_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in _PROCESS_API_PREFIXES)


class BrowserProcessCorsMiddleware:
    """Enable CORS only for browser-facing process APIs."""

    def __init__(self, app) -> None:
        self.app = app
        self._cors = CORSMiddleware(
            app,
            allow_origins=configured_browser_origins(),
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Accept", "Content-Type"],
            max_age=600,
        )

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") == "http" and _browser_api_path(
            str(scope.get("path") or "")
        ):
            await self._cors(scope, receive, send)
            return
        await self.app(scope, receive, send)
