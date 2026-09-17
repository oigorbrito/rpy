## 2026-03-31 - Redact Outbound Provider API Credentials from Exception Sanitization
**Vulnerability:** `JUDIT_API_KEY` was missing from `_SECRET_ENV_NAMES` in `app/log_safety.py`, leaving Judit API key values unredacted if leaked in error messages or logs processed by `sanitize_error_message()`.
**Learning:** While LLM provider credentials (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) and database credentials were included in the secret redaction list, secondary third-party provider keys like `JUDIT_API_KEY` were overlooked.
**Prevention:** Keep `_SECRET_ENV_NAMES` in sync with all sensitive provider API keys defined in `.env.production.example` and enforce test coverage for each configured secret environment variable.
