from app.log_safety import REDACTED, sanitize_error_message


def test_sanitize_error_message_redacts_configured_secret(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-provider-value")
    monkeypatch.setenv("JUDIT_API_KEY", "judit-secret-key-999")
    monkeypatch.setenv("DATAJUD_API_KEY", "datajud-secret-key-888")
    monkeypatch.setenv("COHERE_API_KEY", "cohere-secret-key-777")

    rendered = sanitize_error_message(
        "provider rejected api key sk-secret-provider-value with judit-secret-key-999 "
        "and datajud-secret-key-888 and cohere-secret-key-777"
    )

    assert "sk-secret-provider-value" not in rendered
    assert "judit-secret-key-999" not in rendered
    assert "datajud-secret-key-888" not in rendered
    assert "cohere-secret-key-777" not in rendered
    assert REDACTED in rendered


def test_sanitize_error_message_redacts_uri_password_and_bearer() -> None:
    rendered = sanitize_error_message(
        "failed postgresql://rpy_worker:db-super-secret@postgres:5432/rpy "
        "Authorization: Bearer opaque-token-value"
    )

    assert "db-super-secret" not in rendered
    assert "opaque-token-value" not in rendered
    assert "postgresql://rpy_worker:[REDACTED]@postgres:5432/rpy" in rendered
    assert "Authorization: Bearer [REDACTED]" in rendered


def test_sanitize_error_message_is_bounded() -> None:
    rendered = sanitize_error_message("x" * 5000, max_chars=120)

    assert len(rendered) == 120
    assert rendered.endswith("… [truncated]")
