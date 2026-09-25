# Reranker benchmark

`python scripts/benchmark_reranker.py --scorer synthetic` is the provider-free CI probe for the reranking boundary. It compares the legacy long-process selection with the top-50 → rerank → ~top-15 path over the versioned synthetic dataset from `tests/eval/synthetic_cases.json`.

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


## Evaluation evidence classification

The synthetic reranker path is repository evidence, not deployment evidence.

For every recorded benchmark result, preserve:

- exact Git commit;
- scorer mode (`synthetic` or `bge`);
- dataset path/version;
- command line;
- execution environment;
- observed JSON report;
- CI/job/run identifier when applicable.

The synthetic scorer demonstrates only the deterministic retrieval/reranking contract encoded by the fixture: candidate-window behavior, mandatory milestone preservation, selected-context size and fixture-relative policy precision. Its elapsed time is diagnostic for that run and must not be used as a production latency claim.

A real BGE result becomes deployment evidence only when the local artifact has passed the artifact verifier and the benchmark records the actual model artifact and hardware/runtime environment. Until then, real-model quality and latency remain unmeasured.

Methodology references:

- SWE-bench harness and evaluation outputs: https://www.swebench.com/SWE-bench/reference/harness/ and https://www.swebench.com/SWE-bench/guides/evaluation/
- Harbor task/dataset/environment/result model: https://www.harborframework.com/docs/core-concepts and https://www.harborframework.com/docs/run-jobs/run-evals

These references define evidence structure only. They do not supply Rpy acceptance thresholds or benchmark scores.
