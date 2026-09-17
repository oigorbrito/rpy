from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_deploy_env.py"
spec = importlib.util.spec_from_file_location("validate_deploy_env", MODULE_PATH)
assert spec is not None and spec.loader is not None
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def _valid_values() -> dict[str, str]:
    return {
        "RPY_IMAGE": "ghcr.io/oigorbrito/rpy@sha256:" + "a" * 64,
        "POSTGRES_PASSWORD": "prod-password",
        "MIGRATION_DATABASE_URL": "postgresql://rpy:pw@postgres:5432/rpy",
        "API_DATABASE_URL": "postgresql://rpy_api:pw@postgres:5432/rpy",
        "WORKER_DATABASE_URL": "postgresql://rpy_worker:pw@postgres:5432/rpy",
        "SCHEDULER_DATABASE_URL": "postgresql://rpy_scheduler:pw@postgres:5432/rpy",
        "BACKUP_DATABASE_URL": "postgresql://rpy_backup:pw@postgres:5432/rpy",
        "ANTHROPIC_API_KEY": "anthropic-key",
        "OPENAI_API_KEY": "openai-key",
        "JUDIT_API_KEY": "judit-api-key",
        "JUDIT_WEBHOOK_TOKEN": "judit-token",
        "RPY_BEARER_TOKENS": '{"tenant-token":"00000000-0000-0000-0000-000000000001"}',
        "RPY_OPS_TOKEN": "ops-token",
    }


def test_valid_deploy_environment_passes() -> None:
    assert preflight.validate(_valid_values()) == []


def test_bge_runtime_does_not_require_openai_key() -> None:
    values = _valid_values()
    values.pop("OPENAI_API_KEY")
    values["EMBEDDING_SPACE_RUNTIME_ENABLED"] = "true"
    values["EMBEDDING_PROVIDER"] = "bge"
    values["BGE_EMBEDDING_MODEL"] = "BAAI/bge-m3"
    assert preflight.validate(values) == []


def test_legacy_runtime_still_requires_openai_key() -> None:
    values = _valid_values()
    values.pop("OPENAI_API_KEY")
    errors = preflight.validate(values)
    assert (
        "OPENAI_API_KEY is required when EMBEDDING_SPACE_RUNTIME_ENABLED is false"
        in errors
    )


def test_bge_runtime_rejects_unimplemented_cohere_adapter() -> None:
    values = _valid_values()
    values.pop("OPENAI_API_KEY")
    values["EMBEDDING_SPACE_RUNTIME_ENABLED"] = "true"
    values["EMBEDDING_PROVIDER"] = "cohere"
    errors = preflight.validate(values)
    assert any("Cohere runtime adapter is not implemented" in error for error in errors)


def test_bge_runtime_rejects_wrong_model() -> None:
    values = _valid_values()
    values.pop("OPENAI_API_KEY")
    values["EMBEDDING_SPACE_RUNTIME_ENABLED"] = "true"
    values["BGE_EMBEDDING_MODEL"] = "other/model"
    assert "BGE_EMBEDDING_MODEL must be 'BAAI/bge-m3'" in preflight.validate(values)


def test_embedding_runtime_flag_must_be_boolean() -> None:
    values = _valid_values()
    values["EMBEDDING_SPACE_RUNTIME_ENABLED"] = "sometimes"
    assert "EMBEDDING_SPACE_RUNTIME_ENABLED must be a boolean" in preflight.validate(values)


def test_mutable_image_reference_is_rejected() -> None:
    values = _valid_values()
    values["RPY_IMAGE"] = "ghcr.io/oigorbrito/rpy:v1.0.0"
    assert "RPY_IMAGE must be an immutable sha256 registry digest" in preflight.validate(values)


def test_runtime_database_role_cannot_reuse_migration_identity() -> None:
    values = _valid_values()
    values["API_DATABASE_URL"] = "postgresql://rpy:other@postgres:5432/rpy"
    errors = preflight.validate(values)
    assert any("database role 'rpy' is reused" in error for error in errors)


def test_database_urls_must_target_same_database() -> None:
    values = _valid_values()
    values["BACKUP_DATABASE_URL"] = "postgresql://rpy_backup:pw@other-host:5432/rpy"
    assert "all database URLs must target the same PostgreSQL database" in preflight.validate(values)


def test_placeholder_values_are_rejected() -> None:
    values = _valid_values()
    values["ANTHROPIC_API_KEY"] = "replace-with-anthropic-key"
    assert "ANTHROPIC_API_KEY still contains a placeholder value" in preflight.validate(values)


def test_judit_api_key_is_required() -> None:
    values = _valid_values()
    values.pop("JUDIT_API_KEY")
    assert "JUDIT_API_KEY is required" in preflight.validate(values)


def test_bearer_mapping_requires_tenant_uuid() -> None:
    values = _valid_values()
    values["RPY_BEARER_TOKENS"] = '{"tenant-token":"not-a-uuid"}'
    assert any(error.startswith("RPY_BEARER_TOKENS is invalid:") for error in preflight.validate(values))


def test_env_file_parser_accepts_export_and_quotes(tmp_path: Path) -> None:
    env_file = tmp_path / "production.env"
    env_file.write_text(
        "# comment\nexport RPY_IMAGE='ghcr.io/oigorbrito/rpy@sha256:" + "a" * 64 + "'\n",
        encoding="utf-8",
    )
    assert preflight._load_env_file(env_file)["RPY_IMAGE"].endswith("a" * 64)
