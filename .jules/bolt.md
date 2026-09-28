# Bolt's Journal

## 2026-09-24 - Memoize character property lookups in unicode_security
**Learning:** Character-by-character `unicodedata` lookups (`unicodedata.category` and `unicodedata.name`) in text normalization and security verification pipeline create significant CPU bottlenecks on large text chunks. Memoizing pure single-character helper functions with `functools.lru_cache(maxsize=1024)` yields ~2.8x execution speedup.
**Action:** Always check for character-by-character C-extension or stdlib lookups in text transformation pipelines and apply bounded `lru_cache`.

## 2026-09-28 - Cache only hashable normalization inputs
**Learning:** Public normalization helpers that accept `Any` may receive unhashable values such as dictionaries or lists. Decorating those public helpers directly with `lru_cache` can raise `TypeError` before normalization runs.
**Action:** Convert arbitrary inputs to `str` in the public wrapper and delegate to a bounded cached helper whose input type is `str`. Keep regression tests for dict, list, and `None` inputs.

