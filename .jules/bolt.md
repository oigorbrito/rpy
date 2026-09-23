## 2026-03-30 - Fast-path & Single-pass Unicode Security Processing
**Learning:** `unicodedata.name()` and `unicodedata.category()` carry high C-extension overhead when called repeatedly per character in hot paths (like embedding generation and RAG prompt preparation). Checking character codepoint bounds (`< 0x0300`, Latin ranges) and using `.isascii()` fast paths significantly speeds up string normalization/sanitization.
**Action:** Always check `.isascii()` or codepoint integer ranges before delegating to `unicodedata` functions in per-character string processing loops.
