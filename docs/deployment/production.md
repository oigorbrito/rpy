# Production deployment contract

`compose.yaml` remains the developer-oriented stack. Production deployments use `compose.production.yaml` and must preserve the boundaries below.

## Topology

The minimum supported topology is:

- one always-warm API service;
- two worker processes consuming the PostgreSQL queue;
- exactly one scheduler process;
- one migration job per deploy;
- PostgreSQL 16 with pgvector.

The API is intentionally configured with one Uvicorn worker. Horizontal API scaling, when needed, should happen by adding API replicas behind the ingress rather than by increasing the in-container Uvicorn worker count. Workers may scale horizontally because queue claims are fenced through PostgreSQL. The scheduler must remain singleton.

## Network boundary

PostgreSQL does not publish a host port in the production compose file and only joins the internal `backend` network. API and workers join both `backend` and `egress` so they can reach PostgreSQL and external model providers. The scheduler remains backend-only.

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

If the PostgreSQL password contains reserved URL characters, URL-encode it in `DATABASE_URL`. `POSTGRES_PASSWORD` and the credentials encoded in `DATABASE_URL` must refer to the same database user.

Bearer-token rotation is performed by temporarily mapping both old and new tokens to the same tenant, deploying that overlap, migrating clients, and then removing the old token in a later deploy.

## Deploy sequence

1. Inject required configuration and secrets.
2. Render and validate the compose file with `docker compose -f compose.production.yaml config`.
3. Start PostgreSQL or verify the managed PostgreSQL endpoint is healthy.
4. Run the one-shot `migrate` service to completion.
5. Start API, both workers, and the singleton scheduler.
6. Route traffic only after `/ready` succeeds.

A deploy must stop if migrations fail. Do not start a second scheduler to compensate for scheduler failure; restart or replace the singleton instance instead.

## CI guardrail

CI renders `compose.production.yaml` with non-secret fixture values and runs `scripts/validate_production_compose.py`. The validator rejects changes that:

- expose PostgreSQL on a host port;
- remove the internal backend network;
- change the default API bind away from loopback;
- change the explicit API worker count;
- alter the two-worker / one-scheduler topology;
- remove required production configuration from the API environment.

This contract is intentionally small. Platform-specific manifests (Kubernetes, ECS, Nomad, Fly.io, Render, etc.) should reproduce these invariants rather than introduce a second application architecture.
