# RAG claim verification

## Current deterministic layer

Rpy's claim-level verification is intentionally split into structural provenance and semantic verification.

Issue #250 implements the structural layer. For each material structured-summary field, the provider returns a deterministic `claim_id`, text identical to the corresponding structured field, and one or more `evidence_refs` supplied by the application.

The application then validates, before publication:

- the claim id is expected for the structured payload;
- every expected material field has exactly one claim identity;
- claim text matches the structured field;
- each claim has at least one evidence ref;
- every ref has a valid opaque format;
- every ref exists in the authorized evidence catalog for that generation;
- duplicate or unknown refs fail closed;
- persisted refs remain scoped to the same summary/process/version.

This validation is deterministic and does not call another model.

## Material coverage

Required provenance currently covers:

- `synthesis`;
- `current_status`;
- every item in `timeline`;
- every item in `attention`;
- every item in `decisions`;
- every item in `deadlines`;
- every item in `related_processes`;
- every item in `attachments`.

`attention` is covered because it is part of the provider-authored structured output and may contain factual conflicts or objective warnings. Some attention text is constrained by deterministic application warnings, such as source gaps or attachment-processing status, but the published sentence still crosses the provider boundary and therefore carries a claim identity and authorized evidence refs like the other structured fields. This remains structural provenance only; it does not assert semantic entailment of the warning from a particular ref.

## Process-level refs

A process/version ref may support a claim only when the claimed fact is actually present in structured process metadata supplied to generation.

Procedural events, decisions, deadlines, attachment facts, and similar event-level assertions should use movement or attachment refs when those are the actual supporting sources.

The deterministic validator checks reference identity and scope; it does not infer which source *ought* to support a sentence.

## Semantic boundary

Claim-level provenance is not semantic entailment.

A valid evidence ref means that the source was authorized, supplied to generation, and deterministically linked to the claim. It does not establish that the source logically implies every proposition in the claim, nor does it detect all subtle contradictions or overstatements.

Accordingly, #250 must not be described as resolving #267.

Issue #267 owns any later semantic layer, including classifications such as:

- `supported`;
- `contradicted`;
- `insufficient`.

A probabilistic verifier or second model should be introduced only if evaluation shows that deterministic checks leave a material residual unsupported-assertion rate that justifies the added cost and failure modes.

## Evaluation

The deterministic layer should be measured at claim granularity. At minimum, evaluation should report:

- count of material claims;
- claims with missing refs;
- claims with unknown/stale/cross-version refs;
- claims whose text diverges from the structured field;
- publishable vs rejected summaries due to provenance.

Future semantic evaluation should report unsupported-assertion rate separately. Structural provenance success must not be counted as semantic support.
