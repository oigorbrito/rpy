from types import SimpleNamespace

from app.rag import (
    PASSIVE_PARTY_NO_REPRESENTATIVE_OR_CITATION_WARNING,
    _passive_party_attention_warnings,
)


def _step(title: str, text: str = "") -> SimpleNamespace:
    return SimpleNamespace(title=title, text=text)


def test_flags_passive_party_when_no_representatives_or_citation_exist() -> None:
    warnings = _passive_party_attention_warnings(
        [{"name": "Parte Sintética", "side": "Passive"}],
        [],
        [_step("Distribuição"), _step("Juntada de petição")],
    )

    assert warnings == [PASSIVE_PARTY_NO_REPRESENTATIVE_OR_CITATION_WARNING]


def test_does_not_flag_when_explicit_citation_exists() -> None:
    warnings = _passive_party_attention_warnings(
        [{"name": "Parte Sintética", "side": "Passive"}],
        [],
        [_step("CITAÇÃO", "Citação registrada nos autos.")],
    )

    assert warnings == []


def test_does_not_flag_when_any_normalized_representative_exists() -> None:
    warnings = _passive_party_attention_warnings(
        [{"name": "Parte Sintética", "side": "Passive"}],
        [{"name": "Representante Sintético", "person_type": "LAWYER"}],
        [_step("Distribuição")],
    )

    assert warnings == []


def test_does_not_infer_passive_party_from_person_type_or_name() -> None:
    warnings = _passive_party_attention_warnings(
        [{"name": "Empresa Ré Sintética", "person_type": "Réu"}],
        [],
        [_step("Distribuição")],
    )

    assert warnings == []


def test_accepts_unaccented_explicit_citation_marker() -> None:
    warnings = _passive_party_attention_warnings(
        [{"name": "Parte Sintética", "side": "Passive"}],
        [],
        [_step("Citacao eletrônica realizada")],
    )

    assert warnings == []
