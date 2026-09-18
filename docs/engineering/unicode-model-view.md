# Unicode model-view security

Rpy preserves judicial source evidence and derives a separate model-facing representation for retrieval models, rerankers and summary generation. The source text stored in PostgreSQL is never rewritten merely to make model input safer.

## Threat model

Unicode can hide or visually disguise instructions through bidirectional controls, zero-width/default-ignorable characters and mixed-script homoglyphs. Unicode Technical Standard #39 (UTS #39), *Unicode Security Mechanisms*, provides mechanisms for detecting these classes of security problems.

Rpy does **not** claim full UTS #39 identifier-profile or confusable-skeleton conformance. Judicial prose is general text, not an identifier namespace, and UTS #39 explicitly warns that confusable skeletons are for internal comparison rather than display/normalization. Instead, Rpy uses a documented conservative model-input profile:

- normalize model input to NFC for canonical equivalence;
- flag and visibly expand Unicode format/default-ignorable characters;
- separately flag bidi controls and common zero-width controls;
- detect tokens that mix Latin, Greek and Cyrillic letters, and visibly expand the cross-script characters;
- leave ordinary Portuguese accents and single-script prose unchanged.

The model view is versioned as `unicode-model-view-v1`.

## Provenance and privacy

Movement provenance stores only a SHA-256 of the NFC-normalized original movement text plus the closed set of Unicode security flag names. Attachment provenance already stores the original chunk SHA-256 and now also stores the same flag names. The summary row stores the aggregate flags observed across the provider payload.

No Unicode flag contains source text, code-point positions, party names, prompt contents or document excerpts. Logs must not add those values.

## False positives

Mixed-script detection is deliberately narrow: it only considers Latin, Greek and Cyrillic letters inside the same token. Legitimate single-script Greek/Cyrillic prose is not rewritten. A legitimate mixed-script legal identifier or party name may be marked; the original evidence remains unchanged and auditable, while the model receives an explicit code-point marker.

Default-ignorable handling is intentionally conservative for model input. If a future legal corpus demonstrates a legitimate need for a currently exposed character, change the model-view profile with a regression fixture rather than mutating stored evidence.

## References

- Unicode Technical Standard #39, Unicode Security Mechanisms: https://www.unicode.org/reports/tr39/
- Python `unicodedata`: https://docs.python.org/3/library/unicodedata.html
