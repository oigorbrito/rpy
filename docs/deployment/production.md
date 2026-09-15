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
- `DATABASE_URL`;
- `ANTHROPIC_API_KEY`;
- `OPENAI_API_KEY`;
- `JUDIT_WEBHOOK_TOKEN`;
- `RPY_BEARER_TOKENS`;
- `RPY_OPS_TOKEN`.

Inject them from the deployment platform's secret manager or equivalent environment mechanism. Do not place populated values in the repository or bake them into the image. `.env.production.example` is a shape-only template.

Secrets are scoped by service instead of being copied to the whole stack:

- `api` receives database access plus Judit, bearer-token and ops credentials;
- `worker-*` receives database access plus Anthropic/OpenAI credentials and provider settings;
- `scheduler` receives only database access and retention/scheduling settings;
- `migrate` receives only `DATABASE_URL`;
- provider keys must not be present in API, scheduler or migration environments;
- HTTP-facing credentials must not be present in workers, scheduler or migration environments.

If the PostgreSQL password contains reserved URL characters, URL-encode it in `DATABASE_URL`. `POSTGRES_PASSWORD` and the credentials encoded in `DATABASE_URL` must refer to the same database user.

Bearer-token rotation is performed by temporarily mapping both old and new tokens to the same tenant, deploying that overlap, migrating clients, and then removing the old token in a later deploy.

## Deploy sequence

1. Build and publish the application image in trusted CI, then record its immutable registry digest.
2. Set `RPY_IMAGE` to that digest and inject required configuration and secrets.
3. Render and validate the compose file with `docker compose -f compose.production.yaml config`.
4. Pull the exact digest before changing running services.
5. Start PostgreSQL or verify the managed PostgreSQL endpoint is healthy.
6. Run the one-shot `migrate` service to completion using the same `RPY_IMAGE` digest.
7. Start API, both workers, and the singleton scheduler using that digest.
8. Route traffic only after `/ready` succeeds.

A deploy must stop if migrations fail. Do not start a second scheduler to compensate for scheduler failure; restart or replace the singleton instance instead.

## CI guardrail

CI renders `compose.production.yaml` with non-secret fixture values and runs `scripts/validate_production_compose.py`. The validator rejects changes that:

- use `build:` for any application service;
- use a mutable application image tag instead of a SHA-256 registry digest;
- use different application digests for migration/API/workers/scheduler;
- expose PostgreSQL on a host port;
- remove the internal backend network;
- change the default API bind away from loopback;
- change the explicit API worker count;
- alter the two-worker / one-scheduler topology;
- remove required service-specific configuration;
- distribute provider or HTTP-facing secrets to unrelated services.

This contract is intentionally small. Platform-specific manifests (Kubernetes, ECS, Nomad, Fly.io, Render, etc.) should reproduce these invariants rather than introduce a second application architecture.
