from app.judit import extract_promotable_fields, parse_event


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
    assert "SENTENÇA" in fields["steps"][1]["text"]
    assert fields["parties"] == [
        {"name": "Parte A", "side": "Active", "person_type": "Autor"}
    ]
    assert "12345678901" not in str(fields["parties"])
