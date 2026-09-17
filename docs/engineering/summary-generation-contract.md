# Summary generation contract

This document records the runtime generation contract for `iaSummary`.

## Model selection

- processes with at most 100 persisted movements use `claude-sonnet-5`;
- processes with more than 100 persisted movements use `claude-opus-5`;
- the threshold uses the total persisted `step_count`, not the smaller retrieval candidate set;
- secret processes remain on the local deterministic path and never select an external generation model.

The model identifiers above are active Claude API model identifiers as of the implementation date. They are deliberately centralized in `app.rag` so a future provider migration is explicit and testable.

## Request contract

External generation requests use:

- `max_tokens=4000`;
- prompt caching on the stable system prompt through `cache_control={"type": "ephemeral"}`;
- no streaming, because the full result must pass post-generation validation before publication;
- exactly one correction request after a first validation failure.

The source document requested `temperature=0.2`. Current Claude models deprecate custom `temperature`, `top_p`, and `top_k` sampling parameters. Rpy therefore retains `REQUESTED_TEMPERATURE = 0.2` as documentary intent but does not send a custom temperature to Sonnet 5 or Opus 5. Sending an unsupported parameter would make the documented intent less reliable, not more reliable.

## Telemetry

`process_summaries` persists generation metadata alongside the summary:

- `model`;
- `prompt_version`;
- `generation_ms`;
- provider-reported token usage in `usage`;
- `cache_hit`, derived from positive `cache_read_input_tokens`;
- optional `cost_usd` only when the provider response supplies a monetary cost field.

Rpy does **not** calculate cost from a hard-coded pricing table in the generation path. Pricing changes independently from application releases; a guessed or stale value would be misleading. Observability can add a separately versioned pricing layer later if required.

When validation requires the one permitted correction attempt, token/cache usage is accumulated across both provider calls. The private aggregation object is excluded from the provider payload and cannot leak into the correction prompt.

## Public exposure

The `/v1` summary responses expose persisted generation telemetry inside `usage`. Invalid summaries remain unpublished: telemetry may exist for operational evidence, but `iaSummary` is returned only when persisted validation passed.

## Privacy

The existing provider boundary remains authoritative. Secret processes use local deterministic generation, and internal generation telemetry keys are explicitly excluded from provider serialization. No prompt, movement text, parties, credentials, or raw provider response is persisted as telemetry.
