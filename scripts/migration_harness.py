from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_IMPORTS = {
    "celery",
    "redis",
    "rq",
    "langchain",
    "llama_index",
    "llamaindex",
    "pinecone",
    "qdrant_client",
    "weaviate",
}

FORBIDDEN_RUNTIME_TERMS = {
    "hydratask",
    "azure openai sample",
    "legal-rag-engine",
}

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules"}
TEXT_SUFFIXES = {".py", ".sql", ".toml", ".yaml", ".yml", ".json", ".md"}


def iter_files() -> list[Path]:
    out: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in TEXT_SUFFIXES:
            out.append(path)
    return out


def python_import_violations(path: Path) -> list[str]:
    if path.suffix != ".py":
        return []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [f"syntax error: {exc}"]

    violations: list[str] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        for name in names:
            root = name.split(".", 1)[0]
            if root in FORBIDDEN_IMPORTS:
                violations.append(f"forbidden import: {name}")
    return violations


def donor_identity_violations(path: Path) -> list[str]:
    # Attribution and operating docs may legitimately mention donor names.
    if path.name in {"AGENTS.md", "LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"}:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return []
    return [f"donor identity leaked into runtime/project file: {term}" for term in FORBIDDEN_RUNTIME_TERMS if term in text]


def queue_invariant_violations(files: list[Path]) -> list[str]:
    queue_files = [p for p in files if p.name == "queue.py"]
    if not queue_files:
        return ["missing app/queue.py"]

    text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in queue_files).lower()
    errors: list[str] = []
    if "for update skip locked" not in re.sub(r"\s+", " ", text):
        errors.append("queue claim must contain FOR UPDATE SKIP LOCKED")
    if "worker_id" not in text:
        errors.append("queue operations must enforce worker ownership")
    if "idempotency_key" not in text:
        errors.append("queue must support idempotent enqueue")
    return errors


def dependency_violations() -> list[str]:
    pyproject = ROOT / "pyproject.toml"
    if not pyproject.exists():
        return ["missing pyproject.toml"]
    text = pyproject.read_text(encoding="utf-8").lower()
    return [f"forbidden dependency declared: {name}" for name in FORBIDDEN_IMPORTS if re.search(rf"(^|[\"']){re.escape(name)}([\[<>=\"']|$)", text)]


def main() -> int:
    files = iter_files()
    violations: list[tuple[str, str]] = []

    for path in files:
        rel = str(path.relative_to(ROOT))
        for msg in python_import_violations(path):
            violations.append((rel, msg))
        for msg in donor_identity_violations(path):
            violations.append((rel, msg))

    for msg in dependency_violations():
        violations.append(("pyproject.toml", msg))
    for msg in queue_invariant_violations(files):
        violations.append(("app/queue.py", msg))

    if violations:
        print("Migration harness: FAILED")
        for path, msg in violations:
            print(f" - {path}: {msg}")
        return 1

    print(f"Migration harness: OK ({len(files)} project files checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
