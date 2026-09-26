# Bolt's Journal

## 2026-09-24 - Memoize character property lookups in unicode_security
**Learning:** Character-by-character `unicodedata` lookups (`unicodedata.category` and `unicodedata.name`) in text normalization and security verification pipeline create significant CPU bottlenecks on large text chunks. Memoizing pure single-character helper functions with `functools.lru_cache(maxsize=1024)` yields ~2.8x execution speedup.
**Action:** Always check for character-by-character C-extension or stdlib lookups in text transformation pipelines and apply bounded `lru_cache`.

## 2026-09-25 - Handle unhashable types when memoizing normalization wrappers
**Learning:** Functions accepting `value: Any` (such as `_normalized_person_type` or `_normalize_party_name`) can receive unhashable inputs like `dict` or `list` in loose payload processing pipelines. Decorating public functions directly with `@lru_cache` causes runtime `TypeError` when unhashable arguments are passed. Converting arbitrary inputs to `str` before delegating to an inner `@lru_cache` helper function preserves the original type contract while retaining performance gains (~2x speedup in document validation).
**Action:** Always delegate `@lru_cache` to an inner helper function that accepts `str` when the public wrapper signature accepts `Any` or arbitrary payload values.
