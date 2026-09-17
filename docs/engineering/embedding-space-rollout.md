# Embedding-space rollout

Rpy is migrating vector retrieval from the legacy OpenAI `process_steps.embedding vector(1536)` column to provider/model-isolated `process_step_embeddings vector(1024)` rows.

## Deployment switch

`EMBEDDING_SPACE_RUNTIME_ENABLED=false` is the compatibility/rollback mode. It preserves the historical OpenAI path and does not read or write the new embedding table.

`EMBEDDING_SPACE_RUNTIME_ENABLED=true` enables the provider/model-isolated runtime. The active space is resolved by `EMBEDDING_PROVIDER` and currently supports production execution only for local BGE-M3:

- `EMBEDDING_PROVIDER=bge`;
- model `BAAI/bge-m3`;
- 1024 dimensions;
- optional `BGE_EMBEDDING_DEVICE`;
- optional `BGE_EMBEDDING_USE_FP16`.

The deployment must install the `embeddings` extra and pre-cache/provide the BGE model before enabling the switch. CI does not download the model.

Selecting `EMBEDDING_PROVIDER=cohere` requires `ALLOW_EXTERNAL_EMBEDDINGS=true`, but the runtime adapter is intentionally not implemented yet. Enabling that combination fails explicitly rather than falling back to BGE or OpenAI.

## Reindex behavior

On a long-process vector retrieval with the new runtime enabled, Rpy:

1. finds movements for the requested version that do not have a vector in the exact active provider/model space;
2. encodes only those missing movements;
3. upserts rows in `process_step_embeddings` keyed by `step_id + provider + model`;
4. encodes the query with the same model instance;
5. searches only rows matching the exact provider/model space.

Existing vectors from other spaces remain untouched for controlled rollback or later deletion. They are never mixed in one ranking.

## Rollback

To roll back during migration, disable `EMBEDDING_SPACE_RUNTIME_ENABLED` and keep the existing OpenAI configuration. The legacy `process_steps.embedding` column remains unchanged by the new runtime.

Do not switch provider/model by editing rows in place. A provider/model change requires re-embedding into its own space and validation before the deployment switch changes.

## Secrecy and external providers

Embedding-provider authorization does not override process secrecy rules. Cohere must never be used as an automatic fallback, and external-provider enablement requires explicit deployment authorization. Automated tests use fake/local encoders only and make no paid-provider calls.
