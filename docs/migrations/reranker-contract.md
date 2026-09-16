# Reranker contract

The retrieval pipeline may obtain up to 50 candidates and apply a separate
reranker before selecting approximately 15 movements. `rerank_steps()` keeps
that boundary independent from the scoring provider.

The scorer supplies only candidate scores. Hard filters must already have been
applied, and the post-ranking step always preserves the first, last, five most
recent and milestone movements. Ties are resolved by `step_number`, making the
selection deterministic.

This is a provider-neutral contract, not a BGE or Cohere implementation. The
BM25/vector path remains unchanged until a representative benchmark shows that
an authorized reranker improves quality without violating latency, privacy or
offline requirements.
