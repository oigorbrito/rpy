# Hybrid retrieval contract

Rpy uses two retrieval signals for long, non-secret processes:

- PostgreSQL full-text search with `to_tsvector('portuguese', ...)` and the GIN index on `process_steps`;
- pgvector cosine similarity.

The initial hybrid score is `0.5 * lexical + 0.5 * vector`, followed by the existing recency multiplier. Mandatory context remains independent of ranking: the first movement, last movement, five most recent movements, and recognized procedural milestones are always preserved.

Processes with at most 40 movements bypass retrieval and inject all movements in deterministic `step_number` order.

## Lexical source of truth

The PostgreSQL lexical query is the production lexical signal. It is filtered by `version_id` before ranking, uses the Portuguese text-search configuration, and is backed by `process_steps_fts_idx`.

The existing Python BM25 implementation is intentionally retained, but it is not combined with PostgreSQL lexical scores in production. It serves two narrower purposes:

1. deterministic in-memory fallback for tests/callers that do not have a PostgreSQL connection;
2. comparison baseline for regression tests that evaluate whether the SQL lexical result introduces unexpected ranking divergence.

Combining BM25 and PostgreSQL lexical as two simultaneous lexical signals would overweight correlated lexical evidence and violate the intended 0.5 lexical / 0.5 vector split.

## Isolation

Both lexical and vector queries are scoped to the exact `version_id`. Cross-process/version matches must never enter the candidate score map. Tenant/process authorization occurs before provider exposure in the surrounding pipeline.

## Evidence

`tests/integration/test_retrieval_postgres.py` verifies that:

- PostgreSQL uses `process_steps_fts_idx` for the Portuguese lexical predicate when sequential scan is disabled for plan inspection;
- the SQL lexical result and BM25 comparison baseline identify the same synthetic target for exact/accented legal terms;
- pgvector identifies the same target from the synthetic embedding;
- a matching movement from another process/version is excluded from both score maps;
- the 0.5/0.5 hybrid still preserves forced milestones, the first/last movement, and the five most recent movements.

No external provider is required for this evidence.