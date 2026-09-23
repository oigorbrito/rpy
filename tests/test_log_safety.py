from app.log_safety import REDACTED, sanitize_error_message


def test_sanitize_error_message_redacts_configured_secret(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-provider-value")

    rendered = sanitize_error_message(
        "provider rejected api key sk-secret-provider-value"
    )

    assert "sk-secret-provider-value" not in rendered
    assert REDACTED in rendered


def test_sanitize_error_message_redacts_all_provider_api_keys(monkeypatch) -> None:
    secrets = {
        "JUDIT_API_KEY": "judit-provider-secret",
        "DATAJUD_API_KEY": "datajud-provider-secret",
        "COHERE_API_KEY": "cohere-provider-secret",
        "LANGFUSE_SECRET_KEY": "langfuse-observability-secret",
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)

    rendered = sanitize_error_message(" ".join(secrets.values()))

    for value in secrets.values():
        assert value not in rendered
    assert rendered.count(REDACTED) == len(secrets)


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


def test_sanitize_error_message_redacts_api_key_tokens() -> None:
    rendered = sanitize_error_message(
        "Failed authentication for sk_live_abc1234567890abcdef and sk_test_xyz9876543210fedcba"
    )

    assert "sk_live_abc1234567890abcdef" not in rendered
    assert "sk_test_xyz9876543210fedcba" not in rendered
    assert rendered == f"Failed authentication for {REDACTED} and {REDACTED}"


def test_sanitize_error_message_redacts_json_bearer_tokens(monkeypatch) -> None:
    monkeypatch.setenv(
        "RPY_BEARER_TOKENS",
        '{"legacy-secret-token-123": "00000000-0000-0000-0000-000000000000"}',
    )

    rendered = sanitize_error_message("Request failed using legacy-secret-token-123")

    assert "legacy-secret-token-123" not in rendered
    assert REDACTED in rendered
