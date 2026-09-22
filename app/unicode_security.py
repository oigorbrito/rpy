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


def _is_default_ignorable(character: str, codepoint: int) -> bool:
    # Bolt optimization: Fast path for ASCII / Latin codepoints (except U+00AD SOFT HYPHEN)
    if codepoint < 0x034F and codepoint != 0x00AD:
        return False
    if codepoint == 0x00AD or codepoint in _EXTRA_DEFAULT_IGNORABLE_CODEPOINTS:
        return True
    if (
        (0x180B <= codepoint <= 0x180F)
        or (0xFE00 <= codepoint <= 0xFE0F)
        or (0xE0100 <= codepoint <= 0xE01EF)
    ):
        return True
    return unicodedata.category(character) == "Cf"


def _script(character: str, codepoint: int) -> str | None:
    # Bolt optimization: Fast path for Latin script (covers >99% of Portuguese legal text)
    # Basic Latin A-Z, a-z and Latin-1/Extended A-B diacritics without expensive unicodedata.name() calls
    if (
        (0x0041 <= codepoint <= 0x005A)
        or (0x0061 <= codepoint <= 0x007A)
        or (0x00C0 <= codepoint <= 0x024F and codepoint != 0x00D7 and codepoint != 0x00F7)
    ):
        return "Latin"
    if not character.isalpha():
        return None
    name = unicodedata.name(character, "")
    if name.startswith("LATIN "):
        return "Latin"
    if name.startswith("GREEK "):
        return "Greek"
    if name.startswith("CYRILLIC "):
        return "Cyrillic"
    return None


def _mixed_script_positions(text: str) -> set[int]:
    suspicious: set[int] = set()
    token: list[tuple[int, str]] = []

    def flush() -> None:
        if not token:
            return
        # Bolt optimization: Evaluate _script once per character in token rather than twice
        item_scripts = [(index, _script(char, ord(char))) for index, char in token]
        scripts = {s for _, s in item_scripts if s is not None}
        if len(scripts) < 2:
            token.clear()
            return
        primary = "Latin" if "Latin" in scripts else sorted(scripts)[0]
        for index, script in item_scripts:
            if script is not None and script != primary:
                suspicious.add(index)
        token.clear()

    for index, character in enumerate(text):
        if character.isalpha() or unicodedata.combining(character):
            token.append((index, character))
        else:
            flush()
    flush()
    return suspicious


def _visible_codepoint(character: str) -> str:
    name = unicodedata.name(character, "UNNAMED")
    return f"⟦U+{ord(character):04X} {name}⟧"


def model_view_text(value: str) -> UnicodeModelView:
    normalized = unicodedata.normalize("NFC", str(value))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    mixed_positions = _mixed_script_positions(normalized)
    flags: set[str] = set()
    rendered: list[str] = []
    has_replacements = False

    for index, character in enumerate(normalized):
        codepoint = ord(character)
        # Bolt optimization: Skip default_ignorable, bidi and zero-width checks for standard ASCII/Latin (except U+00AD)
        if codepoint < 0x0300 and codepoint != 0x00AD:
            default_ignorable = False
        else:
            default_ignorable = _is_default_ignorable(character, codepoint)
            if codepoint in _BIDI_CONTROL_CODEPOINTS:
                flags.add("bidi_control")
            if codepoint in _ZERO_WIDTH_CODEPOINTS:
                flags.add("zero_width")

        if default_ignorable:
            flags.add("default_ignorable")
        is_mixed = index in mixed_positions
        if is_mixed:
            flags.add("mixed_script")

        # Bolt optimization: Avoid string copying into list unless a character actually requires replacement
        if default_ignorable or is_mixed:
            if not has_replacements:
                has_replacements = True
                rendered = list(normalized[:index])
            rendered.append(_visible_codepoint(character))
        elif has_replacements:
            rendered.append(character)

    ordered_flags = tuple(flag for flag in _FLAG_ORDER if flag in flags)
    final_text = "".join(rendered) if has_replacements else normalized
    return UnicodeModelView(
        text=final_text,
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
