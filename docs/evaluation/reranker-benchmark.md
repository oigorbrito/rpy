# Reranker benchmark

`python scripts/benchmark_reranker.py --scorer synthetic` is the provider-free CI probe for the reranking boundary. It compares the legacy long-process selection with the top-50 → rerank → ~top-15 path over the versioned synthetic dataset from `tests/eval/synthetic_cases.json`.

The synthetic scorer exists only to make orchestration quality invariants reproducible in CI. It must preserve mandatory milestone recall, reduce or preserve the selected-context size, and must not reduce policy-candidate precision on the synthetic fixture.

## Real BGE evidence

For deployment evidence, install the optional reranker extra and provide `BAAI/bge-reranker-v2-m3` as a deployment-local artifact directory. Set:

```bash
export BGE_RERANKER_PATH=/absolute/path/to/bge-reranker-v2-m3
python scripts/benchmark_reranker.py --scorer bge
```

`BGE_RERANKER_PATH` must exist and be a directory. The runtime keeps the semantic/model identity pinned to `BAAI/bge-reranker-v2-m3` while loading model bytes from that local path. This avoids treating a model-hub download during benchmark execution as production evidence.

If `BGE_RERANKER_PATH` is unset, the scorer can still resolve the configured model identifier through FlagEmbedding for local development. Such a run is not sufficient evidence that the production artifact is prepared and reproducible.

The report records observed wall-clock elapsed time for baseline and reranked paths plus retrieval quality metrics. `external_provider_cost_usd` is `0.0` because the BGE path is self-hosted. `hardware_cost_usd` remains `null`; the repository does not invent infrastructure pricing without an observed deployment/runtime cost model.

The CI synthetic result is a regression gate for orchestration, not a substitute for the real BGE deployment benchmark. Issue #121 should remain open until a real local-artifact BGE run is recorded and reviewed against the same dataset.
