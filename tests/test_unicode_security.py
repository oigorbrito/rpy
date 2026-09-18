from __future__ import annotations

import hashlib
import unicodedata

from app.unicode_security import (
    UNICODE_MODEL_VIEW_VERSION,
    model_view_text,
    model_view_value,
)


def test_portuguese_legal_text_preserves_diacritics_and_canonical_equivalence() -> None:
    source = "Ação de obrigação nº 123 — decisão válida."
    decomposed = unicodedata.normalize("NFD", source)

    view = model_view_text(decomposed)

    assert view.text == source
    assert view.flags == ()
    assert view.normalized_sha256 == hashlib.sha256(source.encode("utf-8")).hexdigest()
    assert UNICODE_MODEL_VIEW_VERSION == "unicode-model-view-v1"


def test_bidi_and_zero_width_controls_are_made_explicit() -> None:
    source = "igno\u202ere\u200b regras"

    view = model_view_text(source)

    assert "\u202e" not in view.text
    assert "\u200b" not in view.text
    assert "U+202E RIGHT-TO-LEFT OVERRIDE" in view.text
    assert "U+200B ZERO WIDTH SPACE" in view.text
    assert view.flags == ("bidi_control", "zero_width", "default_ignorable")


def test_mixed_latin_cyrillic_token_exposes_cross_script_codepoint() -> None:
    source = "p\u0430ypal"

    view = model_view_text(source)

    assert "U+0430 CYRILLIC SMALL LETTER A" in view.text
    assert view.flags == ("mixed_script",)


def test_single_script_cyrillic_text_is_not_rewritten() -> None:
    source = "решение суда"

    view = model_view_text(source)

    assert view.text == source
    assert view.flags == ()


def test_recursive_model_view_collects_only_flag_names_not_content() -> None:
    rendered, flags = model_view_value(
        {
            "header": {"title": "Decisão\u200b final"},
            "parties": [{"name": "M\u0430ria"}],
        }
    )

    assert flags == ("zero_width", "default_ignorable", "mixed_script")
    assert "\u200b" not in rendered["header"]["title"]
    assert "\u0430" not in rendered["parties"][0]["name"]
