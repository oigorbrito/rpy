## 2026-03-30 - Fast-Path Unicode Normalization & Soft Hyphen Edge Case

**Learning:** `model_view_text` is called pervasively across RAG, retrieval, prompt formatting, reranking, and embeddings. Profiling showed character-by-character calls to `unicodedata.name()` and generator expressions in default-ignorable checks created a bottleneck (~3.6ms for 1.7KB text). Adding fast-paths for ASCII/Latin (`0x0041..0x024F`) reduced execution time by 3.2x (~1.1ms). However, `U+00AD` (SOFT HYPHEN, codepoint 173 / `0x00AD`) is a Category `Cf` default-ignorable character in the Latin-1 range (`< 0x0300`) and must be explicitly excluded from fast-path bypasses to avoid missing security/formatting flags.

**Action:** Always verify category `Cf` edge cases like `U+00AD` when building codepoint fast-paths for Unicode inspection functions.
