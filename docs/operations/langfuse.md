# Langfuse self-hosted observability

Rpy treats Langfuse as an optional, fail-open observability sink. PostgreSQL remains the durable product source of truth and Langfuse availability must never decide whether a summary is generated, validated, persisted, or published.

## Runtime contract

Install the optional dependency with `rpy[observability]` and enable tracing explicitly with `LANGFUSE_ENABLED=true`. Configure the standard Langfuse SDK credentials and self-hosted base URL for the deployment. When the extra is not installed, configuration is missing, or the Langfuse client raises, the worker continues without tracing.

The integration targets the Langfuse Python SDK v4/OpenTelemetry observation API. It does not use the legacy trace/span/generation ingestion APIs.

## Data minimization

The trace boundary is an allowlist. Rpy may export only:

- queue `job_id`;
- internal `process_id` and `version_id`;
- Judit request identifier used for pipeline correlation;
- persisted summary identifier;
- model and prompt version when available;
- generation latency;
- provider-reported token/cache usage and provider-reported cost;
- cache hit, persistence/reuse and validation pass/fail flags;
- count of validation errors, never the validation messages;
- identifiers of movement sources persisted in `process_summary_sources`.

Rpy must not export process text, movement text, parties, raw Judit/DataJud payloads, provider prompts, provider responses, generated Markdown, validation error strings, credentials, tokens, attachment contents, or other document content. Secret processes use the same metadata-only boundary; restricted content is never added to the trace.

## Retention and access

Langfuse traces are operational telemetry, not the legal/audit record. Production deployments must:

1. restrict Langfuse access to the operations/engineering roles that already have production observability access;
2. disable public trace sharing;
3. use the shortest retention window that still supports incident response and performance analysis; the deployment default for Rpy is **30 days**, unless the data-protection owner approves a different period;
4. keep PostgreSQL audit/provenance retention independent from Langfuse retention;
5. rotate Langfuse credentials independently from application/provider credentials.

A deployment that cannot enforce access control and retention must leave `LANGFUSE_ENABLED=false`.

## Failure behavior

Start, update and end failures at the Langfuse boundary are swallowed after a content-free warning containing only the exception type. The queue job continues with its normal success/failure semantics. Tests must use fake clients and must not make network calls or require Langfuse credentials.
