# Next release readiness

**Release version: 0.1.1**

This document tracks the current `main` after the historical `v0.1.0` release. It is intentionally separate from `docs/release/v0.1.0.md`, which records the immutable first release.

## Technical artifact readiness

| Area | Current evidence | Status |
|---|---|---|
| Project guardrails | `scripts/project_harness.py`, migration/release guardrails | ready |
| Unit/integration | unit + PostgreSQL integration suites in CI | ready |
| Retrieval/generation evals | synthetic/offline evaluation gates | ready |
| Frontend | behavioral harness + real Chromium smoke (Playwright) | ready |
| Container runtime | local image smoke + published digest smoke | ready |
| Backup/recovery | PostgreSQL backup/restore drill | ready |
| Release smoke | provider-free offline release smoke | ready |
| Image identity | immutable GHCR digest contract | ready |
| Supply-chain provenance | GitHub artifact attestation wired to published digest | ready |
| Production preflight | secret/configuration + Compose contract validation | ready |
| Provider handoff | controlled provider acceptance runbook | ready |

“Ready” means the repository has deterministic evidence or a controlled operational procedure. It does not mean an external provider, legal authority or production environment has approved activation.

## External acceptance / authorization

| Boundary | What the software already provides | Still required outside repository |
|---|---|---|
| Judit | async request/tracking/webhook contract, idempotency, tenant correlation, safe errors | account/API key, budget, authorized CNJ/tenant, live provider acceptance |
| DataJud | optional metadata enrichment, secrecy skip, explicit authorized-use gate, worker-only key contract | applicable CNJ/legal/product authorization, current key, controlled acceptance |
| Langfuse observability | metadata-only fail-open tracing boundary; SDK/image/config/redaction contract | project credentials, HTTPS endpoint, access control and verified retention policy if enabled |
| Anthropic | pinned model routing, retry/validation/telemetry, secrecy short-circuit | API key/budget and controlled live acceptance |
| OpenAI legacy embeddings | pinned model and explicit 1536-dimension contract | key/budget only if legacy rollback path is selected |
| Cohere Embed | isolated 1024-dimension semantic space, no fallback, secrecy guards | environment-specific authorization, key, controlled acceptance |
| Cohere Rerank | explicit provider selection, no fallback, secrecy guards | separate environment-specific authorization, key, controlled acceptance |
| BGE embeddings | local artifact/image/reindex tooling | real pinned artifacts, historical reindex and retrieval-quality evidence (#124) |
| BGE reranker | local artifact path + verifier + benchmark harness | real artifact, prepared hardware, real quality/latency benchmark (#121) |
| LGPD/governance | technical flow/control dossier and minimization controls | legal basis, contractual roles/clauses, retention/erasure policy and responsible approval (#148) |
| iaSummary canonical contract | #113 completed with the canonical document/output contract; deterministic validation rules are implemented where source-backed | residual normative mappings/structured evidence required by #114 |

## Release blockers vs activation blockers

A **release blocker** prevents creation of a new software release artifact. An **activation blocker** prevents enabling a particular provider/data boundary in a target environment.

At present, the repository has no known code/CI blocker for producing another **offline-qualified software artifact**, and the default branch now has an active repository ruleset enforcing pull requests and required checks. The private/secrecy retrieval hard-filter follow-up (#265) is closed with PostgreSQL integration evidence across load, lexical, vector and embedding paths. The unresolved open issues are primarily activation/acceptance/domain-governance or additional hardening work, except that product may choose to require #113/#114 completion as a release criterion.

Before a formal new tag, the release owner has selected `0.1.1`. One release-scope decision remains explicit: decide whether the unresolved document-contract work (#113/#114) is required for `0.1.1` or remains a documented post-release product acceptance item.

Engineering must not silently resolve that remaining scope decision.

## Current repository-qualified candidate

The latest fully observed `main` candidate before this release-preparation branch is `1017883cf98c1bd207931c01686e1ea22ae677e4`.

Exact-commit GitHub evidence:

- CI run #3324 / id `36195400558`: success, including production Compose contract validation, project harness, provider-readiness gate, static quality, unit tests, synthetic RAG eval, offline pipeline eval, offline generation eval, frontend behavior/browser smoke, container image smoke, PostgreSQL backup/restore drill, PostgreSQL integration and offline release smoke.
- vulnerability-scan run #132 / id `36195400615`: success.
- CodeQL "Push on main" run #905 / id `36195400305`: success.
- image run #3 / id `36195868927`: success for tests, image publication, digest smoke, Trivy gates, SBOM/provenance and GitHub artifact attestation.
- immutable image evidence: `ghcr.io/oigorbrito/rpy@sha256:d6420e9110d94e725b834a08368ce04acdeaa0092f49bfd5d403898b0636d5db`.
- Repository ruleset `Protect main` is active for the default branch, requires pull requests and required status checks, blocks deletion and non-fast-forward updates, and has no bypass actors.
- provider-live-smoke run #9 / id `36188280364` failed in the Judit diagnostic path and is not qualifying evidence for provider acceptance; the DataJud-only step was skipped in that run.

This evidence qualifies repository-local behavior only for that exact commit and tested environment. It does not demonstrate live provider compatibility, real BGE quality/latency, production ingress behavior, legal authorization, production RTO/SLO, or hardware cost.

This commit is a **repository/offline candidate**, not a production activation decision. Any later code change creates a new candidate and must repeat the exact-head gates.

## Final release-preparation checklist

When those decisions are made:

- update package version and create matching release notes;
- ensure no release document claims current `main` is `v0.1.0`;
- run `python scripts/project_harness.py`;
- run the exact release head through complete CI;
- execute a clean offline smoke;
- publish the exact image digest from trusted CI for the frozen release head;
- smoke-test that published digest;
- verify its GitHub artifact attestation;
- record the immutable digest and release evidence;
- create a **new** tag; never retarget `v0.1.0`.

Provider/legal activation follows `docs/release/provider-acceptance.md` and the applicable governance approvals after software release qualification.
