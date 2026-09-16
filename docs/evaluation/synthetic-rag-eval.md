# Synthetic RAG evaluation baseline

This evaluation is a provider-free baseline for the iaSummary quality work tracked in #115. It is intentionally narrower than a legal-quality guarantee.

## Dataset

`tests/eval/synthetic_cases.json` contains 30 manually labelled synthetic cases. The corpus contains no real case data and no intentionally included personal identifiers. Coverage includes civil, labour, criminal and tax-enforcement proceedings; first and second instance; closed proceedings; secrecy; more than 200 movements; empty steps; and the inconsistency categories listed in #115.

Each case stores the expected labels and a synthetic candidate snapshot. The candidate annotations are versioned so metric changes remain reproducible and reviewable in Git.

## Metrics

`scripts/evaluate_synthetic_rag.py` computes four aggregate metrics:

- `unsupported_assertion_rate`: unsupported labelled claims divided by all candidate claims;
- `milestone_recall`: expected process milestones recovered by the candidate divided by all expected milestones;
- `inconsistency_detection_recall`: expected inconsistencies detected by the candidate divided by all expected inconsistencies;
- `attention_false_positive_rate`: predicted `Pontos de atenção` items without a corresponding expected attention label divided by all predicted attention items.

For the initial 30-case baseline the versioned snapshot scores:

- unsupported assertion rate: `0.05`;
- milestone recall: `0.90`;
- inconsistency detection recall: `17/18` (`~0.9444`);
- attention false-positive rate: `1/19` (`~0.0526`).

These values are descriptive baseline measurements, not acceptance thresholds and not claims about production legal accuracy.

## Reproduction

Run:

```bash
python scripts/evaluate_synthetic_rag.py
```

The evaluator uses only Python standard-library code and local repository data. Unit coverage also blocks socket connection attempts while the scorer runs. Judit, Anthropic, OpenAI and Gemini are not required or contacted by this baseline.

## Limitations and next work

The initial corpus scores versioned synthetic candidate annotations. It does not yet execute the full offline generation pipeline against each case, so it cannot by itself measure end-to-end generation quality. It also does not establish that the synthetic distribution represents live Brazilian court data.

The next step for #115 is to connect the same labelled corpus to the provider-free application pipeline and derive candidate annotations from actual offline pipeline outputs while preserving deterministic execution. Only then should metric deltas be interpreted as regressions or improvements in the implementation rather than changes to the manually versioned baseline.
