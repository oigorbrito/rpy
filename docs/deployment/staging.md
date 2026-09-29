# Persistent staging contract

This document defines the repository-side contract for Rpy staging. It deliberately does not select
a cloud vendor or create a second deployment architecture. Staging uses the same immutable image and
`compose.production.yaml` contract as production, with separate infrastructure and secrets.

## Purpose

The diagnostic provider work has already proven:

- Rpy can create a real Judit request;
- Judit can return lawsuit payloads;
- Judit can initiate a public HTTPS callback to the real Rpy webhook;
- Rpy can persist/stage that callback and grant tenant access;
- the internal finalization and summary pipeline passes PostgreSQL E2E/offline evidence.

Persistent staging is therefore an environment acceptance gate, not another connectivity experiment.

## GitHub environment

Create a GitHub Environment named `staging`. Keep staging credentials separate from production.

Required **environment variables**:

- `RPY_IMAGE`: immutable `ghcr.io/oigorbrito/rpy@sha256:...` reference;
- `STAGING_BASE_URL`: bare public HTTPS origin, for example `https://staging.rpy.example`;
- `EGRESS_PROXY_ALLOWED_HOSTS`: exact hostname allowlist required by enabled providers.

Feature selectors and non-secret tunables may also be environment variables. The readiness workflow
carries repository defaults for disabled/standard options and reads explicit environment variables
when a staging deployment intentionally changes them.

Required **environment secrets** for the default contract:

- `POSTGRES_PASSWORD`;
- `MIGRATION_DATABASE_URL`;
- `API_DATABASE_URL`;
- `WORKER_DATABASE_URL`;
- `SCHEDULER_DATABASE_URL`;
- `BACKUP_DATABASE_URL`;
- `ANTHROPIC_API_KEY`;
- `OPENAI_API_KEY` while the legacy embedding path remains active;
- `JUDIT_API_KEY`;
- `JUDIT_CALLBACK_URL`;
- `JUDIT_WEBHOOK_TOKEN`;
- `RPY_BEARER_TOKENS`;
- `RPY_OPS_TOKEN`.

Optional secrets such as `COHERE_API_KEY`, `DATAJUD_API_KEY`, and Langfuse credentials are supplied
only when the corresponding feature is explicitly enabled and authorized.

`JUDIT_CALLBACK_URL` is sensitive configuration because the callback token is embedded in its path.
It must use the same origin as `STAGING_BASE_URL` and the exact
`/webhooks/judit/<JUDIT_WEBHOOK_TOKEN>` path.

## Readiness workflow

Run `.github/workflows/staging-readiness.yml` manually after a candidate has actually been deployed
to the persistent staging target.

The workflow:

1. validates the stable HTTPS origin and callback/origin relationship;
2. runs `scripts/validate_deploy_env.py`;
3. renders `compose.production.yaml` and runs `scripts/validate_production_compose.py`;
4. pulls the exact `RPY_IMAGE` digest;
5. smoke-tests the published image;
6. verifies the GitHub artifact attestation for that digest;
7. calls public `/health` and `/ready` over HTTPS;
8. sends a webhook request with a deliberately invalid token and requires HTTP 404.

The workflow does **not** deploy infrastructure, mutate staging data, or create a paid Judit request.

## Deployment boundary

Provider-specific deployment automation should be added only after the target hosting platform is
selected. Until then, the repository contract remains portable:

- TLS must terminate at an owned ingress/reverse proxy;
- the API must not expose PostgreSQL;
- workers must retain egress through the allowlisted proxy;
- there must be exactly two workers and one scheduler;
- staging must use its own database credentials, HTTP tokens, provider keys and webhook token;
- the same already-built image digest is promoted between environments; staging must not rebuild it.

Quick Tunnel/trycloudflare evidence is diagnostic only and must not be reused as persistent staging.

## Single-host deployment command

After the VM, Docker/Compose, TLS ingress and secret source exist, deploy the same immutable image
with the repository's canonical runner:

```bash
chmod 600 /secure/path/staging.env
python scripts/deploy_production.py --env-file /secure/path/staging.env --dry-run
python scripts/deploy_production.py --env-file /secure/path/staging.env
```

The staging env file must stay outside the Git checkout. The runner does not create provider requests;
starting empty workers is not provider acceptance. Run the manual `staging-readiness` workflow only
after the stable public ingress is serving the deployed stack.

## Target-environment acceptance

After `staging-readiness` is green, perform one bounded target-environment acceptance:

1. create/read a process through the public staging API/browser using a staging tenant;
2. allow one authorized Judit request with attachments disabled;
3. verify the callback reaches the stable staging ingress;
4. verify a lawsuit callback stages a process version and grants tenant access;
5. observe the provider terminal completion signal without creating duplicate paid requests;
6. verify finalization and one non-secret validated summary using the selected generation/retrieval path;
7. create an off-host backup and restore it into a disposable database;
8. record the deployed digest and known-good rollback digest.

Do not repeat provider calls to prove transport already covered by
`docs/release/judit-live-callback-acceptance-2026-09-28.md`.
