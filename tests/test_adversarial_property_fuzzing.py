from __future__ import annotations

import json
import re

from hypothesis import given, settings, strategies as st

from app.attachment_context import serialize_attachment_chunks
from app.rag import _prompt_json, _provider_payload, _validate_provider_summary
from app.summary_output import parse_structured_summary, render_structured_summary
from app.adversarial_mutations import MutationPlan, load_corpus, mutate_attack


CORPUS = load_corpus()
ATTACKS = tuple(
    str(part)
    for case in CORPUS
    for part in (case.get("attack_parts") or [case.get("attack")])
    if part
)

PLAN_STRATEGY = st.builds(
    MutationPlan,
    casing=st.sampled_from(("identity", "upper", "lower", "alternate")),
    spacing=st.sampled_from(("identity", "spread", "newlines")),
    markup=st.sampled_from(("identity", "comment", "hidden-div", "code")),
    delimiter_depth=st.integers(min_value=0, max_value=3),
    unicode_variant=st.sampled_from(("identity", "zero-width", "bidi", "homoglyph")),
)


def _context(attack: str) -> dict:
    return {
        "code": "0000000-00.2026.8.21.0256",
        "class_name": "Procedimento Comum",
        "court": "TJRS",
        "header": {"note": attack},
        "parties": [{"name": "Parte Sintética"}],
        "subjects": [],
        "secrecy_level": 0,
        "step_count": 2,
        "steps": [
            {"step_number": 1, "occurred_at": None, "title": "Distribuição", "text": "Processo distribuído."},
            {"step_number": 2, "occurred_at": None, "title": "Juntada", "text": attack},
        ],
    }


@settings(max_examples=96, deadline=None)
@given(base=st.sampled_from(ATTACKS), plan=PLAN_STRATEGY)
def test_adversarial_input_mutations_cannot_break_prompt_envelope(base: str, plan: MutationPlan) -> None:
    attack = mutate_attack(base, plan)
    context = _context(attack)
    process, steps = _provider_payload(context)
    serialized = _prompt_json({"processo": process, "movimentos": steps})

    assert "<" not in serialized  # nosec B101
    assert ">" not in serialized  # nosec B101
    assert "&" not in serialized  # nosec B101
    json.loads(serialized)
    assert len(steps) == 2  # nosec B101
    assert steps[1]["step_number"] == 2  # nosec B101


@settings(max_examples=72, deadline=None)
@given(base=st.sampled_from(ATTACKS), plan=PLAN_STRATEGY)
def test_adversarial_attachment_mutations_remain_data(base: str, plan: MutationPlan) -> None:
    attack = mutate_attack(base, plan)
    chunks = [{
        "source_attachment_id": "synthetic-attachment",
        "page_start": 1, "page_end": 1, "char_start": 0, "char_end": len(attack), "text": attack,
    }]
    rendered = serialize_attachment_chunks(chunks, total_text_limit=20000)

    assert chunks[0]["text"] == attack  # nosec B101
    assert len(rendered) == 1  # nosec B101
    assert rendered[0]["source_attachment_id"] == "synthetic-attachment"  # nosec B101
    json.dumps(rendered, ensure_ascii=False)


@settings(max_examples=72, deadline=None)
@given(base=st.sampled_from(ATTACKS), plan=PLAN_STRATEGY)
def test_structured_renderer_never_grants_mutated_text_new_sections(base: str, plan: MutationPlan) -> None:
    attack = mutate_attack(base, plan)
    raw = json.dumps({
        "synthesis": attack,
        "timeline": [attack],
        "current_status": attack,
        "attention": [attack],
        "decisions": [attack],
        "deadlines": [], "related_processes": [], "attachments": [attack],
    }, ensure_ascii=False)
    payload = parse_structured_summary(raw)
    rendered = render_structured_summary(payload, _context(attack))
    headings = [line for line in rendered.splitlines() if re.match(r"^#{1,6}\\s", line)]
    allowed = {
        "# Resumo do processo", "## Partes", "## Síntese", "## Linha do tempo relevante",
        "## Situação atual", "## Pontos de atenção", "## Decisões", "## Anexos",
    }
    assert set(headings) <= allowed  # nosec B101


@settings(max_examples=64, deadline=None)
@given(base=st.sampled_from(ATTACKS), plan=PLAN_STRATEGY)
def test_publication_validator_rejects_generated_unapproved_section(base: str, plan: MutationPlan) -> None:
    attack = mutate_attack(base, plan).replace("\n", " ")
    text = (
        "# Resumo do processo\\n\\n## Síntese\\nProcesso em andamento.\\n\\n"
        "## Pontos de atenção\\nNenhuma divergência objetiva identificada.\\n\\n"
        f"## {attack[:120] or 'Seção injetada'}\\nconteúdo"
    )
    result = _validate_provider_summary(text, _context(base))
    assert result.passed is False  # nosec B101
    assert result.errors  # nosec B101


@settings(max_examples=48, deadline=None)
@given(
    bases=st.lists(st.sampled_from(ATTACKS), min_size=2, max_size=4),
    plan=PLAN_STRATEGY,
)
def test_mutations_span_multiple_movements_and_attachments(
    bases: list[str], plan: MutationPlan
) -> None:
    attacks = [mutate_attack(base, plan) for base in bases]
    context = _context(attacks[0])
    context["steps"] = [
        {
            "step_number": index,
            "occurred_at": None,
            "title": "Movimento sintético",
            "text": attack,
        }
        for index, attack in enumerate(attacks, start=1)
    ]
    context["step_count"] = len(attacks)
    _, steps = _provider_payload(context)

    chunks = [
        {
            "source_attachment_id": f"synthetic-{index}",
            "page_start": index,
            "page_end": index,
            "char_start": 0,
            "char_end": len(attack),
            "text": attack,
        }
        for index, attack in enumerate(attacks, start=1)
    ]
    rendered_chunks = serialize_attachment_chunks(chunks, total_text_limit=40000)

    assert len(steps) == len(attacks)  # nosec B101
    assert len(rendered_chunks) == len(attacks)  # nosec B101
    assert [chunk["text"] for chunk in chunks] == attacks  # nosec B101
