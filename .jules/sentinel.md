## 2026-03-31 - Redacting Dynamic Application API Keys in Error Logs
**Vulnerability:** Dynamic API key tokens (`sk_live_...` and `sk_test_...`) created at runtime and stored hashed in PostgreSQL were not redacted by `sanitize_error_message()` when formatted as standalone tokens or within unhandled exception strings, leaking secrets to persistent logs and job error fields.
**Learning:** Env-var based secret redaction and header-based regexes miss application-specific bearer/API tokens when tokens are passed or printed without standard `Authorization: Bearer` or `api_key=` prefixes.
**Prevention:** Include token format regexes (such as `\bsk_(?:live|test)_[a-zA-Z0-9_-]+\b`) directly in centralized log sanitization routines.

## 2026-03-31 - Redacting JSON-Structured Secret Environment Variables
**Vulnerability:** Bearer tokens configured via JSON object in `RPY_BEARER_TOKENS` were not extracted individually by `_configured_secret_values()`, causing standalone bearer tokens appearing in exception strings or error logs to bypass redaction.
**Learning:** Checking `os.environ` secret values directly assumes scalar string secrets; environment variables containing JSON dictionaries map secrets as keys that must be parsed to be individually redacted.
**Prevention:** Parse JSON secret environment variables in centralized log sanitization functions to extract individual token keys.
