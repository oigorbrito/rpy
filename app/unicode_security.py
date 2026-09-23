from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from typing import Any

UNICODE_MODEL_VIEW_VERSION = "unicode-model-view-v1"

_BIDI_CONTROL_CODEPOINTS = {
    0x061C,
    0x200E,
    0x200F,
    *range(0x202A, 0x202F),
    *range(0x2066, 0x206A),
}
_ZERO_WIDTH_CODEPOINTS = {0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF}
_EXTRA_DEFAULT_IGNORABLE_CODEPOINTS = {0x034F}
_EXTRA_DEFAULT_IGNORABLE_RANGES = (
    (0x180B, 0x180F),
    (0xFE00, 0xFE0F),
    (0xE0100, 0xE01EF),
)
_FLAG_ORDER = (
    "bidi_control",
    "zero_width",
    "default_ignorable",
    "mixed_script",
)


@dataclass(frozen=True, slots=True)
class UnicodeModelView:
    text: str
    flags: tuple[str, ...]
    normalized_sha256: str


def _is_default_ignorable(character: str) -> bool:
    codepoint = ord(character)
    if codepoint < 0x0300:
        return codepoint == 0x00AD
    if unicodedata.category(character) == "Cf":
        return True
    if codepoint in _EXTRA_DEFAULT_IGNORABLE_CODEPOINTS:
        return True
    return any(start <= codepoint <= end for start, end in _EXTRA_DEFAULT_IGNORABLE_RANGES)


def _script(character: str) -> str | None:
    if not character.isalpha():
        return None
    codepoint = ord(character)
    # Fast path for common Latin ranges (ASCII letters, Latin-1 Supplement, Latin Extended-A/B)
    if (
        (65 <= codepoint <= 90)
        or (97 <= codepoint <= 122)
        or (192 <= codepoint <= 255 and codepoint != 215 and codepoint != 247)
        or (256 <= codepoint <= 591)
    ):
        return "Latin"
    name = unicodedata.name(character, "")
    for prefix, script in (
        ("LATIN ", "Latin"),
        ("GREEK ", "Greek"),
        ("CYRILLIC ", "Cyrillic"),
    ):
        if name.startswith(prefix):
            return script
    return None


def _visible_codepoint(character: str) -> str:
    name = unicodedata.name(character, "UNNAMED")
    return f"⟦U+{ord(character):04X} {name}⟧"


def model_view_text(value: str) -> UnicodeModelView:
    # Performance optimization:
    # 1. Fast-path ASCII strings (no bidi, zero-width, ignorable or mixed script possible).
    # 2. Combined single-pass analysis and rendering to avoid multi-pass string scans.
    normalized = unicodedata.normalize("NFC", str(value))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    if normalized.isascii():
        return UnicodeModelView(
            text=normalized,
            flags=(),
            normalized_sha256=digest,
        )

    flags: set[str] = set()
    rendered: list[str] = []

    token_indices: list[int] = []
    token_scripts: list[str | None] = []

    def flush_token() -> None:
        if not token_indices:
            return
        distinct_scripts = {s for s in token_scripts if s is not None}
        if len(distinct_scripts) >= 2:
            flags.add("mixed_script")
            primary = "Latin" if "Latin" in distinct_scripts else sorted(distinct_scripts)[0]
            for idx, script in zip(token_indices, token_scripts):
                if script is not None and script != primary:
                    char = normalized[idx]
                    rendered[idx] = _visible_codepoint(char)
        token_indices.clear()
        token_scripts.clear()

    for index, character in enumerate(normalized):
        codepoint = ord(character)
        default_ignorable = _is_default_ignorable(character)

        if codepoint in _BIDI_CONTROL_CODEPOINTS:
            flags.add("bidi_control")
        if codepoint in _ZERO_WIDTH_CODEPOINTS:
            flags.add("zero_width")
        if default_ignorable:
            flags.add("default_ignorable")

        if default_ignorable:
            rendered.append(_visible_codepoint(character))
        else:
            rendered.append(character)

        if character.isalpha() or unicodedata.combining(character):
            token_indices.append(index)
            token_scripts.append(_script(character))
        else:
            flush_token()

    flush_token()

    ordered_flags = tuple(flag for flag in _FLAG_ORDER if flag in flags)
    return UnicodeModelView(
        text="".join(rendered),
        flags=ordered_flags,
        normalized_sha256=digest,
    )


def model_view_value(value: Any) -> tuple[Any, tuple[str, ...]]:
    flags: set[str] = set()

    def transform(item: Any) -> Any:
        if isinstance(item, str):
            view = model_view_text(item)
            flags.update(view.flags)
            return view.text
        if isinstance(item, dict):
            return {key: transform(child) for key, child in item.items()}
        if isinstance(item, list):
            return [transform(child) for child in item]
        if isinstance(item, tuple):
            return [transform(child) for child in item]
        return item

    rendered = transform(value)
    ordered_flags = tuple(flag for flag in _FLAG_ORDER if flag in flags)
    return rendered, ordered_flags
