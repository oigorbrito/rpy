from __future__ import annotations

import json
import os
import re

REDACTED = "[REDACTED]"
MAX_ERROR_MESSAGE_CHARS = 2000

_SECRET_ENV_NAMES = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "JUDIT_API_KEY",
    "DATAJUD_API_KEY",
    "COHERE_API_KEY",
    "LANGFUSE_SECRET_KEY",
    "JUDIT_WEBHOOK_TOKEN",
    "RPY_BEARER_TOKENS",
    "RPY_DEMO_BEARER_TOKEN",
    "RPY_OPS_TOKEN",
    "POSTGRES_PASSWORD",
    "DATABASE_URL",
    "MIGRATION_DATABASE_URL",
    "API_DATABASE_URL",
    "WORKER_DATABASE_URL",
    "SCHEDULER_DATABASE_URL",
    "BACKUP_DATABASE_URL",
)

# Redact credentials embedded in URLs even when the full URL is not available in
# environment variables (for example when emitted by a lower-level client).
_URI_CREDENTIALS_RE = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<user>[^\s/:@]+):(?P<secret>[^\s/@]+)@")
_AUTHORIZATION_RE = re.compile(
    r"(?i)(authorization\s*[:=]\s*(?:bearer|apikey|basic|token|digest|negotiate|oauth)\s+)([^\s,;]+)"
)
_QUERY_SECRET_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|token|access[_-]?token|password|secret)\s*[=:]\s*)([^\s,;&]+)"
)
# Redact application/provider API keys even when not configured in env or when
# appearing without authorization headers or query parameter names.
_API_KEY_TOKEN_RE = re.compile(r"\b(?:sk_(?:live|test)|sk-ant-api)[a-zA-Z0-9_-]+\b")


def _configured_bearer_token_keys(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, dict):
        return []
    return [
        token
        for token in parsed
        if isinstance(token, str) and len(token) >= 4
    ]


def _configured_secret_values() -> list[str]:
    values: list[str] = []
    for name in _SECRET_ENV_NAMES:
        value = os.environ.get(name)
        if not value or len(value) < 4:
            continue
        values.append(value)
        if name == "RPY_BEARER_TOKENS":
            values.extend(_configured_bearer_token_keys(value))
    return sorted(set(values), key=len, reverse=True)


def sanitize_error_message(value: object, *, max_chars: int = MAX_ERROR_MESSAGE_CHARS) -> str:
    """Return a bounded error string safe for logs and durable job error storage."""
    text = str(value)

    for secret in _configured_secret_values():
        text = text.replace(secret, REDACTED)

    text = _URI_CREDENTIALS_RE.sub(
        lambda match: f"{match.group('scheme')}{match.group('user')}:{REDACTED}@",
        text,
    )
    text = _AUTHORIZATION_RE.sub(
        lambda match: f"{match.group(1)}{REDACTED}", text
    )
    text = _QUERY_SECRET_RE.sub(lambda match: f"{match.group(1)}{REDACTED}", text)
    text = _API_KEY_TOKEN_RE.sub(REDACTED, text)

    if max_chars <= 0:
        return ""
    if len(text) > max_chars:
        suffix = "… [truncated]"
        if max_chars <= len(suffix):
            return text[:max_chars]
        text = text[: max_chars - len(suffix)] + suffix
    return text
