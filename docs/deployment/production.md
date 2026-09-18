# Production deployment contract

`compose.yaml` remains the developer-oriented stack. Production deployments use `compose.production.yaml` and must preserve the boundaries below.

## Immutable application artifact

Production does not build application source on the deployment host. `RPY_IMAGE` is required and must be a registry reference pinned by a full SHA-256 digest, for example:

`ghcr.io/oigorbrito/rpy@sha256:<64-hex-digest>`

The same exact digest is used by `migrate`, `api`, both workers and `scheduler`. Tags such as `latest`, `main`, semantic-version tags and commit-SHA tags are useful aliases for humans and release automation, but are not accepted as the production deployment identity because a tag can be moved.

Build and publish the image once in trusted CI. Record the resulting registry digest as release metadata. Promote that digest unchanged through environments; do not rebuild for staging or production. This keeps migration code and runtime code on the same artifact revision.

Rollback is artifact selection, not reconstruction: set `RPY_IMAGE` back to a previously known-good digest and execute the normal deployment sequence. Database migrations still determine whether an application rollback is schema-compatible, so destructive migrations require their own explicit rollback plan.

## Topology

The minimum supported topology is:

- one always-warm API service;
- two worker processes consuming the PostgreSQL queue;
- exactly one scheduler process;
- one migration job per deploy;
- PostgreSQL 16 with pgvector.

The API is intentionally configured with one Uvicorn worker. Horizontal API scaling, when needed, should happen by adding API replicas behind the ingress rather than by increasing the in-container Uvicorn worker count. Workers may scale horizontally because queue claims are fenced through PostgreSQL. The scheduler must remain singleton.

## Network boundary

PostgreSQL does not publish a host port in the production compose file and only joins the internal `backend` network. API and workers join both `backend` and `egress` so they can reach PostgreSQL and external services. The scheduler remains backend-only.

The API publishes port 8000 on `127.0.0.1` by default. Put a TLS-terminating reverse proxy or ingress in front of it. If the ingress runs on a different host/network, change `RPY_API_BIND_ADDRESS` deliberately and apply an equivalent network policy/firewall rule instead of exposing PostgreSQL.

The ingress should enforce a request-body limit no greater than `JUDIT_WEBHOOK_MAX_BODY_BYTES` and should preserve the application's `/health` and `/ready` behavior. `/health` is liveness-only; `/ready` verifies PostgreSQL reachability.

## Secrets

Production has no fallback values for:

- `POSTGRES_PASSWORD`;
- `MIGRATION_DATABASE_URL`;
- `API_DATABASE_URL`;
- `WORKER_DATABASE_URL`;
- `SCHEDULER_DATABASE_URL`;
- `BACKUP_DATABASE_URL`;
- `ANTHROPIC_API_KEY`;
- `JUDIT_WEBHOOK_TOKEN`;
- `RPY_BEARER_TOKENS`;
- `RPY_OPS_TOKEN`.

`OPENAI_API_KEY` is conditional: it is required only while `EMBEDDING_SPACE_RUNTIME_ENABLED=false`, which preserves the historical OpenAI `vector(1536)` retrieval path. With the isolated runtime enabled, BGE is the default self-hosted provider. Cohere is accepted only when the deployment explicitly selects `EMBEDDING_PROVIDER=cohere`, sets `ALLOW_EXTERNAL_EMBEDDINGS=true`, pins `COHERE_EMBEDDING_MODEL=embed-v4.0`, and supplies `COHERE_API_KEY`. Cohere is never an automatic fallback.

Inject secrets from the deployment platform's secret manager or equivalent environment mechanism. Do not place populated values in the repository or bake them into the image. `.env.production.example` is a shape-only template. Staging and production must use separate secret sources; do not point both environments at the same PostgreSQL credentials or reuse HTTP/provider secrets between them.

Database credentials are split by responsibility. The five URLs must use distinct PostgreSQL login roles and target the same application database:

- `MIGRATION_DATABASE_URL` is the one-shot deploy/admin credential used by migrations and role provisioning;
- `API_DATABASE_URL` is injected into `api` as its `DATABASE_URL`;
- `WORKER_DATABASE_URL` is injected into both workers as their `DATABASE_URL`;
- `SCHEDULER_DATABASE_URL` is injected into the singleton scheduler as its `DATABASE_URL`;
- `BACKUP_DATABASE_URL` is reserved for backup jobs and role provisioning and is not injected into normal runtime services.

Secrets are scoped by service instead of being copied to the whole stack:

- `api` receives `API_DATABASE_URL` plus Judit, bearer-token and ops credentials;
- `worker-*` receives `WORKER_DATABASE_URL`, Anthropic and embedding-provider settings; external embedding credentials stay worker-only;
- `scheduler` receives only `SCHEDULER_DATABASE_URL` and retention/scheduling settings;
- `migrate` receives the migration URL plus the four runtime/backup URLs needed to provision and rotate their roles;
- provider keys must not be present in API, scheduler or migration environments;
- HTTP-facing credentials must not be present in workers, scheduler or migration environments.

If a PostgreSQL password contains reserved URL characters, URL-encode it in the corresponding database URL. Do not reuse the migration/admin role for API, worker, scheduler or backup access.

Bearer-token rotation is performed by temporarily mapping both old and new tokens to the same tenant, deploying that overlap, migrating clients, and then removing the old token in a later deploy.

## Embedding rollout and rollback

The production worker environment always carries the rollout selectors so both workers agree on one semantic space:

- `EMBEDDING_SPACE_RUNTIME_ENABLED` defaults to `false`;
- `EMBEDDING_PROVIDER` defaults to `bge` for the isolated runtime;
- `BGE_EMBEDDING_MODEL` is pinned to `BAAI/bge-m3`;
- `BGE_EMBEDDING_PATH` is the absolute path inside the worker container where the pre-provisioned BGE artifact is available;
- `BGE_EMBEDDING_DEVICE` and `BGE_EMBEDDING_USE_FP16` control local inference characteristics;
- `ALLOW_EXTERNAL_EMBEDDINGS` defaults to `false`;
- `COHERE_EMBEDDING_MODEL` is pinned to `embed-v4.0`;
- `EMBEDDING_MODEL` remains the legacy OpenAI model selector.

When BGE is active, production preflight requires `BGE_EMBEDDING_PATH` to be an absolute container path. This prevents production activation from silently falling back to a model-hub download. The deployment must install the optional embeddings dependencies and either bake or mount the complete BGE artifact at that path before workers start. Runtime model use then fails explicitly if the path is missing or not a directory.

Cohere is an external-data boundary. `ALLOW_EXTERNAL_EMBEDDINGS=true` may be set only after explicit environment-specific approval by the data/governance authority responsible for that deployment. CI and automated tests never authorize real external embeddings. Staging and production require separate approval. See `docs/engineering/external-embedding-authorization.md`.

Secret process versions never cross the Cohere boundary. The normal RAG path short-circuits secret processes, the Cohere historical reindex excludes secret versions, and the embedding runtime independently rejects external embedding of a secret version. A Cohere error does not fall back to BGE, OpenAI or another provider.

Switching BGE ↔ Cohere requires controlled re-embedding/reindexing into the target provider/model space even though both use 1024 dimensions. Old provider/model rows remain available for rollback and are never mixed into the active ranking. Rollback to the legacy path disables the isolated runtime and restores `OPENAI_API_KEY`.

## Reranker activation

Long-process reranking is independently controlled from embeddings and is disabled by default.

- `RERANKER_ENABLED=false` keeps the existing retrieval-selection path without reranking.
- `RERANKER_PROVIDER=bge` selects the default self-hosted `BAAI/bge-reranker-v2-m3`.
- production BGE activation requires an absolute `BGE_RERANKER_PATH` containing the prepared local artifact; `RERANKER_MODEL` remains pinned to the semantic model identity.
- `RERANKER_USE_FP16` controls local inference only.
- `RERANKER_PROVIDER=cohere` selects the optional external Cohere Rerank path.
- Cohere reranking requires a separate `ALLOW_EXTERNAL_RERANKER=true` authorization, `COHERE_RERANKER_MODEL=rerank-v4.0-pro`, and worker-only `COHERE_API_KEY`.
- `RERANKER_TIMEOUT_SECONDS` controls the external reranker timeout.
- no provider is an automatic fallback for the other.

Authorization for external reranking is independent from authorization for external embeddings. Enabling Cohere embeddings does not implicitly authorize Cohere reranking, and vice versa. Secret processes short-circuit before external reranker resolution.

Both workers must receive identical reranker enablement, provider, model, artifact and authorization settings. Production preflight rejects an enabled BGE reranker without an absolute artifact path and rejects Cohere without explicit authorization/key. Before enabling BGE in production, run `scripts/verify_bge_reranker_artifact.py` against the mounted artifact. The real benchmark in `docs/evaluation/reranker-benchmark.md` remains a separate release-quality evidence requirement for #121.

## Deploy sequence

1. Build and publish the application image in trusted CI, then record its immutable registry digest.
2. Set `RPY_IMAGE` to that digest and inject the environment-specific configuration and secrets.
3. Run `python scripts/validate_deploy_env.py` against the exported environment, or `python scripts/validate_deploy_env.py --env-file /secure/path/production.env` for a local secret-managed file.
4. Render and validate the compose file with `docker compose -f compose.production.yaml config` and `scripts/validate_production_compose.py`.
5. Pull the exact digest before changing running services.
6. Start PostgreSQL or verify the managed PostgreSQL endpoint is healthy.
7. Run the one-shot `migrate` service to completion using the same `RPY_IMAGE` digest. This applies migrations and provisions/rotates the runtime roles.
8. Reindex into the selected isolated embedding space before switching production retrieval to it.
9. Start API, both workers, and the singleton scheduler using that digest.
10. Route traffic only after `/ready` succeeds through the TLS-terminating reverse proxy or ingress.

A deploy must stop if preflight, compose validation or migrations fail. Do not start a second scheduler to compensate for scheduler failure; restart or replace the singleton instance instead.

## CI guardrail

CI first runs `scripts/validate_deploy_env.py` with non-secret fixture values, then renders `compose.production.yaml` and runs `scripts/validate_production_compose.py`.

The deploy-environment preflight rejects configuration that:

- uses a mutable image reference instead of a full SHA-256 digest;
- leaves required values empty or at documented placeholder values;
- omits `OPENAI_API_KEY` while the legacy embedding path is selected;
- enables BGE without an absolute `BGE_EMBEDDING_PATH` inside the worker container;
- selects Cohere without `ALLOW_EXTERNAL_EMBEDDINGS=true` and `COHERE_API_KEY`;
- selects an unsupported embedding or reranker provider/model pair;
- enables BGE reranking without an absolute `BGE_RERANKER_PATH`;
- selects Cohere reranking without separate `ALLOW_EXTERNAL_RERANKER=true` authorization and `COHERE_API_KEY`;
- reuses a PostgreSQL login identity across migration/API/worker/scheduler/backup responsibilities;
- points the role-specific URLs at different PostgreSQL databases;
- supplies an invalid bearer-token-to-tenant mapping.

The compose validator rejects changes that:

- use `build:` for any application service;
- use a mutable application image tag instead of a SHA-256 registry digest;
- use different application digests for migration/API/workers/scheduler;
- expose PostgreSQL on a host port;
- remove the internal backend network;
- change the default API bind away from loopback;
- change the explicit API worker count;
- alter the two-worker / one-scheduler topology;
- remove required service-specific configuration;
- allow the two workers to disagree on embedding or reranker rollout/provider/model/artifact settings;
- distribute provider or HTTP-facing secrets to unrelated services.

This contract is intentionally small. Platform-specific manifests (Kubernetes, ECS, Nomad, Fly.io, Render, etc.) should reproduce these invariants rather than introduce a second application architecture.
