# Reranker benchmark

`python scripts/benchmark_reranker.py --scorer synthetic --check-contract` is the provider-free named CI benchmark gate for the reranking boundary. It compares the legacy long-process selection with the top-50 → rerank → ~top-15 path over the versioned synthetic dataset from `tests/eval/synthetic_cases.json`.

The synthetic scorer exists only to make orchestration quality invariants reproducible in CI. It must preserve mandatory milestone recall, reduce or preserve the selected-context size, and must not reduce policy-candidate precision on the synthetic fixture.

## Real BGE evidence

For deployment evidence, install the optional reranker extra and provide `BAAI/bge-reranker-v2-m3` as a deployment-local artifact directory. Set:

```bash
export BGE_RERANKER_PATH=/absolute/path/to/bge-reranker-v2-m3
export PIP_NO_INDEX=1
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_DATASETS_OFFLINE=1
python scripts/verify_bge_reranker_artifact.py --model-dir "$BGE_RERANKER_PATH"
python scripts/benchmark_reranker.py --scorer bge
```

`BGE_RERANKER_PATH` must exist and be a directory. The verifier also requires `FlagEmbedding`, a parseable local `config.json`, and offline Hugging Face/Transformers/Datasets settings before the real benchmark runs. The runtime keeps the semantic/model identity pinned to `BAAI/bge-reranker-v2-m3` while loading model bytes from that local path. This avoids treating a model-hub download during benchmark execution as production evidence.

If `BGE_RERANKER_PATH` is unset, the scorer can still resolve the configured model identifier through FlagEmbedding for local development. Such a run is not sufficient evidence that the production artifact is prepared and reproducible.

The report records observed wall-clock elapsed time for baseline and reranked paths plus retrieval quality metrics. `external_provider_cost_usd` is `0.0` because the BGE path is self-hosted. `hardware_cost_usd` remains `null`; the repository does not invent infrastructure pricing without an observed deployment/runtime cost model.

The CI synthetic result is a regression gate for orchestration, not a substitute for the real BGE deployment benchmark. Issue #121 should remain open until a real local-artifact BGE run is recorded and reviewed against the same dataset.


## Evidence classification

The synthetic benchmark is a deterministic repository benchmark, not a real BGE performance result. Its CI report is tied to the exact Git commit and GitHub Actions run that executed it.

The contract enforced by `--check-contract` is intentionally limited to invariants already covered by repository tests:

- at least one long-process fixture is measured;
- baseline mandatory milestone recall remains 1.0;
- reranked mandatory milestone recall remains 1.0;
- reranked selected context does not exceed the baseline selected context;
- reranked synthetic policy-candidate precision does not regress below baseline.

No latency threshold is enforced because CI wall-clock timing is not a production hardware benchmark. The elapsed values remain diagnostic observations only.

Methodology references:

- SWE-bench evaluation harness: https://www.swebench.com/SWE-bench/reference/harness/
- SWE-bench evaluation guide: https://www.swebench.com/SWE-bench/guides/evaluation/
- Harbor framework: https://github.com/harbor-framework/harbor

Rpy uses these references for evaluation structure—isolated execution, named runs and structured results—not for benchmark scores or acceptance thresholds.
