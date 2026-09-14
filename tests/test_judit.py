from app.judit import extract_promotable_fields, parse_event


def test_parse_completed_uncached_event() -> None:
    event = parse_event(
        {
            "code": "0000000-00.0000.0.00.0000",
            "request_id": "req-1",
            "cached_response": False,
            "status": "request_completed",
        }
    )
    assert event.request_completed is True
    assert event.cached_response is False
    assert event.request_id == "req-1"


def test_extract_steps_normalizes_movements() -> None:
    fields = extract_promotable_fields(
        {
            "process": {
                "parties": [{"name": "Parte A"}],
                "movements": [
                    {"date": "2026-01-01T00:00:00Z", "description": "CITAÇÃO expedida"},
                    {"date": "2026-02-01T00:00:00Z", "description": "SENTENÇA proferida"},
                ],
            }
        }
    )
    assert len(fields["steps"]) == 2
    assert fields["steps"][0]["step_number"] == 1
    assert "SENTENÇA" in fields["steps"][1]["text"]
