# MVP release readiness

This document separates what is proven in-repository from what still requires an external environment. It is a release gate, not a roadmap.

## Product acceptance

The MVP is acceptable for a provisioned legal user when all of the following hold:

- the user can provide an already-issued tenant bearer token and a CNJ;
- an authorized existing process is readable without provider work;
- an absent process can be requested without exposing the Judit credential or provider request identifier;
- repeated tenant/CNJ requests are idempotent;
- a matching Judit lawsuit callback grants access only through the durable tenant request association;
- process details become readable before summary generation finishes;
- summary generation exposes only the public lifecycle state;
- a validated current-version summary is eventually readable and copyable;
- secret-process content is not sent to external LLM/embedding providers;
- audit, retention and tenant boundaries remain active.

The PostgreSQL E2E suite proves the central requested-CNJ path with external request/generation calls stubbed, while exercising the real API, database, webhook, queue, finalization, tenant grant and summary persistence code.

## Repository-proven gates

A candidate is repository-ready only when CI is green for its exact head and covers:

- migration harness;
- unit tests;
- production compose contract;
- local application image runtime smoke;
- relocated backup/restore drill;
- PostgreSQL integration and E2E tests.

The production contract additionally requires an immutable image digest, split database roles, a singleton scheduler, two queue workers, readiness before traffic, and no provider/HTTP credentials distributed to unrelated services.

## External gates before a real user

These cannot be proven by repository CI alone and must be completed in the target environment:

1. Publish the exact candidate image and record its registry SHA-256 digest.
2. Provision PostgreSQL 16 + pgvector and distinct migration/API/worker/scheduler/backup credentials.
3. Provision at least one tenant and bearer token through the deployment configuration.
4. Configure a valid Judit API key and webhook destination/token; verify one real CNJ request reaches the webhook.
5. Configure valid AI provider credentials; verify one non-secret fresh process reaches a validated summary.
6. Put TLS ingress/reverse proxy in front of the API, preserve `/health` and `/ready`, and keep the configured webhook body limit.
7. Execute the production deploy sequence using the immutable digest and wait for `/ready` before traffic.
8. Run one target-environment smoke: browser → CNJ request/read → Judit callback → process details → summary publication.
9. Run a backup to the real off-host destination and prove a restore from that stored artifact in a disposable database.
10. Record the deployed digest, migration result, smoke evidence and rollback digest in the release record.

A failed external gate blocks real-user release; it does not justify weakening the repository invariants.

## Explicitly not blocking this MVP

The following are useful later but are not required for the first provisioned-user release:

- interactive login, password reset or self-service tenant provisioning;
- dashboard, recent searches, favorites, alerts or portfolio management;
- React/Next/Vite migration;
- automatic polling beyond the bounded frontend follow-up;
- Kubernetes or another second deployment architecture;
- real-provider calls in CI.

## Release decision

Current repository state is **code-ready, environment-pending** when the exact candidate head is green. The product path and operational contracts are implemented and tested; registry publication, real provider credentials, ingress, target database and off-host backup remain external release gates.
