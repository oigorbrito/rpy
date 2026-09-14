# Rpy agent operating contract

This repository is being assembled rapidly from verified open-source building blocks. The goal is to maximize reuse of expensive infrastructure while preventing unnecessary product identity, dependencies, abstractions, and behavior from entering Rpy.

## Source-of-truth product constraints

Rpy is a Python/FastAPI service for judicial-process summarization using PostgreSQL and pgvector. PostgreSQL is also the job queue. The queue must claim work with `FOR UPDATE SKIP LOCKED`. The main runtime services are API, workers, and one scheduler/reclaimer process.

Do not introduce Redis, Celery, RabbitMQ, BullMQ, Pinecone, LangChain, or LlamaIndex as core infrastructure unless the project owner explicitly changes that decision.

## Migration policy

Treat external repositories as component donors, not architectural authorities.

Before transplanting code from another repository:

1. Identify the expensive capability being reused.
2. List the exact donor files/functions/queries required.
3. List transitive dependencies introduced by those files.
4. Classify each donor element as `COPY`, `ADAPT`, `REFERENCE_ONLY`, or `DROP`.
5. Prefer the smallest coherent slice that retains tested behavior.
6. Rename donor-domain concepts to Rpy concepts while integrating, not in a later cleanup pass.
7. Remove donor UI, dashboards, marketing, telemetry, deployment assumptions, unrelated APIs, demo tasks, sample data, and provider-specific abstractions unless Rpy requires them.
8. Preserve attribution/license files when legally required. Do not remove notices that must remain with copied code.
9. Add or update tests that prove the transplanted behavior in Rpy.
10. Run the migration harness before considering the transplant complete.

## Donor allowlist

Current approved donors and intended scope:

- `ALAN-PSUDO/HydraTask`
  - Allowed: PostgreSQL queue claim pattern, heartbeat, retry/backoff, stale-job reclaim, LISTEN/NOTIFY ideas, graceful worker shutdown, operational queue tests.
  - Drop by default: dashboard/frontend, DAG/dependency features, generic task marketplace/product concepts, webhook product surface, queue administration UI.

- `Azure-Samples/rag-postgres-openai-python`
  - Allowed: PostgreSQL/pgvector retrieval SQL, lexical + vector retrieval, reciprocal-rank fusion patterns, async database integration patterns.
  - Adapt: provider-specific OpenAI/Azure code, English FTS configuration, unsafe/interpolated dynamic filters.
  - Drop by default: Azure infrastructure, frontend, sample domain models, chat-product surface.

- `link178/legal-rag-engine`
  - Reference primarily: retrieval/context/generation separation, evidence/citation verification, evaluation organization.
  - Do not adopt its mock generation provider or its domain abstractions wholesale.

## Explicit denylist for migrations

Reject or remove donor code that introduces any of the following without an explicit Rpy requirement:

- donor product names in runtime-facing code;
- donor logos, screenshots, dashboards, marketing copy, demo data;
- Redis/Celery/RabbitMQ/BullMQ;
- external vector databases;
- generic chat-history or conversational-memory subsystems;
- document-upload pipelines unrelated to judicial-process movements;
- generic DAG orchestration;
- browser/frontend code;
- cloud-vendor-specific IaC;
- unused observability stacks;
- dynamic SQL identifiers/operators originating directly from untrusted request values;
- sample tasks, fake providers, mock production paths.

## Required Rpy invariants

### Queue

- Claims are atomic and use `FOR UPDATE SKIP LOCKED`.
- No job is concurrently owned by two workers.
- `worker_id` ownership is checked on complete/fail/heartbeat.
- Failed work retries with bounded backoff.
- Stale processing jobs can be reclaimed safely.
- Enqueue supports idempotency for externally repeated events.

### Retrieval

- Scalar process metadata is injected directly, not retrieved semantically.
- Short processes can bypass vector retrieval.
- Long processes use lexical + vector retrieval and deterministic post-ranking rules.
- First, last, and recent movements can be force-included independently of semantic ranking.
- High-value judicial milestones can be force-included.

### Generation and validation

- LLM generation is provider-isolated behind a small module.
- Validation runs before publication.
- One regeneration attempt is allowed after validation failure.
- A second validation failure is persisted explicitly rather than silently accepted.

### Security

- Tenant/process authorization must happen before sensitive process data is returned or sent to the LLM.
- Secret cases are truncated before LLM processing.
- Expunge deletes relational and vector data.
- Access to judicial processes is auditable.

## Definition of done for a transplant

A migration is complete only when:

- donor-specific names are absent from runtime code unless present only in attribution/docs;
- no denied dependency was introduced;
- imported files have an explicit reason to exist;
- tests cover the reused expensive behavior;
- `python scripts/migration_harness.py` passes;
- the code fits Rpy's current module boundaries rather than preserving donor folder structure for convenience.

## Working style for coding agents

Prefer working behavior over speculative architecture. Make small vertical slices executable early. When an external implementation is already correct and generic, adapt it rather than rewriting it for stylistic reasons. When donor abstractions exceed Rpy's needs, delete them aggressively.
