from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app import bge_runtime_contract

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_bge_reranker_artifact.py"
spec = importlib.util.spec_from_file_location("verify_bge_reranker_artifact", MODULE_PATH)
assert spec is not None and spec.loader is not None
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def _offline_env(model_dir: Path) -> dict[str, str]:
    return {
        "PIP_NO_INDEX": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
        "BGE_RERANKER_PATH": str(model_dir),
    }


def test_reranker_artifact_accepts_local_offline_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "bge-reranker-v2-m3"
    model_dir.mkdir()
    (model_dir / "config.json").write_text('{"model_type": "xlm-roberta"}', encoding="utf-8")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(bge_runtime_contract, "package_version", lambda name: "1.4.2")

    assert verifier.validate(model_dir=model_dir, environ=_offline_env(model_dir)) == []


def test_reranker_artifact_rejects_missing_dependency(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "bge-reranker-v2-m3"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)

    errors = verifier.validate(model_dir=model_dir, environ=_offline_env(model_dir))

    assert "FlagEmbedding is not installed" in errors


def test_reranker_artifact_rejects_missing_or_invalid_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "bge-reranker-v2-m3"
    model_dir.mkdir()
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(bge_runtime_contract, "package_version", lambda name: "1.4.2")

    errors = verifier.validate(model_dir=model_dir, environ=_offline_env(model_dir))
    assert any("missing config.json" in error for error in errors)

    (model_dir / "config.json").write_text("not-json", encoding="utf-8")
    errors = verifier.validate(model_dir=model_dir, environ=_offline_env(model_dir))
    assert any("config.json is invalid" in error for error in errors)


def test_reranker_artifact_rejects_online_or_wrong_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "bge-reranker-v2-m3"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(bge_runtime_contract, "package_version", lambda name: "1.4.2")
    env = _offline_env(model_dir)
    env["HF_HUB_OFFLINE"] = "0"
    env["BGE_RERANKER_PATH"] = "/other/model"

    errors = verifier.validate(model_dir=model_dir, environ=env)

    assert "HF_HUB_OFFLINE must force offline mode for the BGE reranker" in errors
    assert any("BGE_RERANKER_PATH must equal" in error for error in errors)


@pytest.mark.parametrize(
    "raw",
    [
        '{"model_type":"xlm-roberta","model_type":"bert"}',
        '{"hidden_size":NaN}',
    ],
)
def test_reranker_artifact_rejects_ambiguous_config_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    raw: str,
) -> None:
    model_dir = tmp_path / "bge-reranker-v2-m3"
    model_dir.mkdir()
    (model_dir / "config.json").write_text(raw, encoding="utf-8")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(bge_runtime_contract, "package_version", lambda name: "1.4.2")

    errors = verifier.validate(model_dir=model_dir, environ=_offline_env(model_dir))
    if not any("config.json is invalid" in error for error in errors):
        raise AssertionError(f"expected invalid config error, got {errors!r}")


@pytest.mark.parametrize("installed_version", ["1.4.1", "1.4.3", "2.0.0"])
def test_reranker_artifact_rejects_unlocked_flagembedding_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    installed_version: str,
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(bge_runtime_contract, "package_version", lambda name: installed_version)

    errors = verifier.validate(model_dir=model_dir, environ=_offline_env(model_dir))

    if not any("FlagEmbedding version must match the locked runtime 1.4.2" in error for error in errors):
        raise AssertionError(f"expected locked-version error, got {errors!r}")


def test_reranker_artifact_rejects_missing_distribution_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())

    def missing_metadata(name: str) -> str:
        raise bge_runtime_contract.PackageNotFoundError(name)

    monkeypatch.setattr(bge_runtime_contract, "package_version", missing_metadata)

    errors = verifier.validate(model_dir=model_dir, environ=_offline_env(model_dir))

    if "FlagEmbedding distribution metadata is unavailable" not in errors:
        raise AssertionError(f"expected missing-metadata error, got {errors!r}")
