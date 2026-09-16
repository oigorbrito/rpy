from datetime import datetime, timezone

from app.process_semantics import semantic_fingerprint


def _fields() -> dict:
    return {
        "header": {"instance": 1, "state": "RS"},
        "parties": [{"name": "Parte Sintética", "masked_person_id": "***.***.***-44"}],
        "subjects": [{"code": "1", "name": "Obrigação"}],
        "steps": [
            {
                "step_number": 1,
                "occurred_at": datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc),
                "title": "CITAÇÃO",
                "text": "Citação realizada",
                "metadata": {
                    "source_step_number": 10,
                    "private": False,
                    "secrecy_level": 0,
                    "tags": {"kind": "movement"},
                    "source_step_date": "source-format-not-semantic",
                },
            }
        ],
        "court": "TJRS",
        "class_name": "Procedimento Comum",
        "secrecy_level": 0,
    }


def test_semantic_fingerprint_is_stable_for_equivalent_normalized_data() -> None:
    left = _fields()
    right = _fields()
    right["header"] = {"state": "RS", "instance": 1}
    right["steps"][0]["metadata"]["source_step_date"] = "different-envelope-format"

    assert semantic_fingerprint(**left) == semantic_fingerprint(**right)


def test_semantic_fingerprint_changes_for_new_movement_content() -> None:
    left = _fields()
    right = _fields()
    right["steps"][0]["text"] = "Sentença proferida"

    assert semantic_fingerprint(**left) != semantic_fingerprint(**right)


def test_semantic_fingerprint_changes_for_relevant_structured_metadata() -> None:
    left = _fields()
    right = _fields()
    right["class_name"] = "Execução Fiscal"

    assert semantic_fingerprint(**left) != semantic_fingerprint(**right)
