# Reranker benchmark

`python scripts/benchmark_reranker.py --scorer synthetic` is the provider-free CI probe for the reranking boundary. It compares the legacy long-process selection with the top-50 → rerank → ~top-15 path over the versioned synthetic dataset from `tests/eval/synthetic_cases.json`.

The synthetic scorer exists only to make orchestration quality invariants reproducible in CI. It must preserve mandatory milestone recall, reduce or preserve the selected-context size, and must not reduce policy-candidate precision on the synthetic fixture.

For deployment evidence, install the optional reranker extra, pre-cache or mount `BAAI/bge-reranker-v2-m3`, then run:

```bash
python scripts/benchmark_reranker.py --scorer bge
```

The report records observed wall-clock elapsed time for baseline and reranked paths plus retrieval quality metrics. `external_provider_cost_usd` is `0.0` because the BGE path is self-hosted. `hardware_cost_usd` remains `null`; the repository does not invent infrastructure pricing without an observed deployment/runtime cost model.

The CI synthetic result is a regression gate for orchestration, not a substitute for the real BGE deployment benchmark. Issue #121 should remain open until a real local BGE run is recorded and reviewed against the same dataset.