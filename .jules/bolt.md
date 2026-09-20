## 2026-03-20 - Character-level unicodedata lookups in security model view
**Learning:** `model_view_text` in `app/unicode_security.py` is called across all process rendering, RAG context, embeddings, and provenance paths. Uncached character-level calls to `unicodedata.name` and category checks added significant per-character C-level overhead (~4ms/text block).
**Action:** Use `@lru_cache` for pure character-level Unicode property helpers and avoid evaluating token script functions twice per character.
