import json
from pathlib import Path

from app.judit import extract_promotable_fields, parse_event

FIXTURES = Path(__file__).parent / "fixtures" / "judit"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_parse_current_lawsuit_response_envelope() -> None:
    event = parse_event(
        {
            "callback_id": "cb-1",
            "event_type": "response_created",
            "reference_type": "request",
            "reference_id": "req-1",
            "payload": {
                "request_id": "req-1",
                "response_id": "resp-1",
                "response_type": "lawsuit",
                "response_data": {
                    "code": "0000000-00.0000.0.00.0000",
                    "steps": [],
                },
                "tags": {"cached_response": False},
            },
        }
    )
    assert event.is_lawsuit_response is True
    assert event.request_completed is False
    assert event.cached_response is False
    assert event.request_id == "req-1"
    assert event.response_id == "resp-1"
    assert event.code == "0000000-00.0000.0.00.0000"


def test_request_completed_does_not_require_process_code() -> None:
    event = parse_event(
        {
            "callback_id": "cb-done",
            "event_type": "request_completed",
            "reference_type": "request",
            "reference_id": "req-1",
            "payload": {"status": "completed"},
        }
    )
    assert event.request_completed is True
    assert event.code is None
    assert event.request_id == "req-1"


def test_tracking_lawsuit_prefers_payload_request_id_over_tracking_reference() -> None:
    event = parse_event(_fixture("tracking_lawsuit_response.json"))

    assert event.is_lawsuit_response is True
    assert event.request_id == "request-abc"
    assert event.request_id != "tracking-123"
    assert event.response_id == "response-lawsuit-001"
    assert event.cached_response is False
    assert event.code == "0000000-00.2026.8.21.0001"


def test_application_info_600_is_request_completion() -> None:
    event = parse_event(_fixture("tracking_request_completed.json"))

    assert event.is_lawsuit_response is False
    assert event.request_completed is True
    assert event.request_id == "request-abc"
    assert event.response_type == "application_info"
    assert event.code == "600"


def test_application_info_without_completion_marker_is_not_complete() -> None:
    payload = _fixture("tracking_request_completed.json")
    payload["payload"]["response_data"] = {"code": 601, "message": "OTHER_INFO"}

    event = parse_event(payload)

    assert event.request_completed is False


def test_extract_current_judit_steps_and_sanitizes_parties() -> None:
    fields = extract_promotable_fields(
        {
            "code": "0000000-00.0000.0.00.0000",
            "tribunal_acronym": "TJRS",
            "secrecy_level": 0,
            "classifications": [{"code": "7", "name": "PROCEDIMENTO COMUM CÍVEL"}],
            "subjects": [{"code": "1", "name": "DIREITO CIVIL"}],
            "parties": [
                {
                    "name": "Parte A",
                    "side": "Active",
                    "person_type": "Autor",
                    "main_document": "12345678901",
                    "documents": [{"document": "12345678901", "document_type": "cpf"}],
                }
            ],
            "steps": [
                {
                    "step_id": "s1",
                    "step_date": "2026-01-01T00:00:00Z",
                    "content": "CITAÇÃO expedida",
                    "private": False,
                },
                {
                    "step_id": "s2",
                    "step_date": "2026-02-01T00:00:00Z",
                    "step_type": "SENTENCA",
                    "content": "SENTENÇA proferida",
                    "private": False,
                },
            ],
        }
    )
    assert fields["court"] == "TJRS"
    assert fields["class_name"] == "PROCEDIMENTO COMUM CÍVEL"
    assert len(fields["steps"]) == 2
    assert fields["steps"][0]["step_number"] == 1
    assert fields["steps"][0]["metadata"]["source_step_number"] is None
    assert "SENTENÇA" in fields["steps"][1]["text"]
    assert fields["parties"] == [
        {"name": "Parte A", "side": "Active", "person_type": "Autor"}
    ]
    assert "12345678901" not in str(fields["parties"])


def test_preserves_explicit_source_step_numbers_without_changing_internal_order() -> None:
    fields = extract_promotable_fields(
        {
            "code": "0000000-00.0000.0.00.0000",
            "steps": [
                {"step_number": 7, "content": "Primeiro movimento recebido"},
                {"event_number": "9", "content": "Segundo movimento recebido"},
                {"movement_number": 12, "content": "Terceiro movimento recebido"},
                {"sequence_number": "15", "content": "Quarto movimento recebido"},
            ],
        }
    )

    assert [step["step_number"] for step in fields["steps"]] == [1, 2, 3, 4]
    assert [
        step["metadata"]["source_step_number"] for step in fields["steps"]
    ] == [7, 9, 12, 15]


def test_does_not_infer_source_step_number_from_step_id_or_invalid_values() -> None:
    fields = extract_promotable_fields(
        {
            "code": "0000000-00.0000.0.00.0000",
            "steps": [
                {"step_id": "123", "content": "Sem número explícito"},
                {"step_number": "evento-8", "content": "Número não estritamente inteiro"},
                {"event_number": -1, "content": "Número negativo"},
                {"movement_number": True, "content": "Booleano não é sequência"},
            ],
        }
    )

    assert [
        step["metadata"]["source_step_number"] for step in fields["steps"]
    ] == [None, None, None, None]


def test_extracts_promotable_fields_from_tracking_fixture_without_documents() -> None:
    event = parse_event(_fixture("tracking_lawsuit_response.json"))
    fields = extract_promotable_fields(event.response_data or {})

    assert fields["court"] == "TJRS"
    assert fields["class_name"] == "PROCEDIMENTO COMUM CÍVEL"
    assert fields["header"]["state"] == "RS"
    assert fields["header"]["city"] == "Porto Alegre"
    assert len(fields["steps"]) == 2
    assert fields["parties"][0] == {
        "name": "PARTE AUTORA TESTE",
        "side": "Active",
        "person_type": "Autor",
    }
    assert "00000000000" not in str(fields["parties"])
