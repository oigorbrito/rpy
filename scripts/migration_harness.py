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
    rel = path.relative_to(ROOT)
    if rel == Path("scripts/migration_harness.py"):
        return []
    if path.name in {"AGENTS.md", "LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"}:
        return []
    if len(rel.parts) >= 2 and rel.parts[0] == "docs" and rel.parts[1] == "migrations":
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return []
    return [
        f"donor identity leaked into runtime/project file: {term}"
        for term in FORBIDDEN_RUNTIME_TERMS
        if term in text
    ]


def queue_invariant_violations() -> list[str]:
    queue_path = ROOT / "app" / "queue.py"
    worker_path = ROOT / "app" / "worker.py"
    if not queue_path.exists():
        return ["missing app/queue.py"]
    if not worker_path.exists():
        return ["missing app/worker.py"]

    text = queue_path.read_text(encoding="utf-8", errors="ignore").lower()
    worker_text = worker_path.read_text(encoding="utf-8", errors="ignore").lower()
    compact = re.sub(r"\s+", " ", text)
    errors: list[str] = []
    if "for update skip locked" not in compact:
        errors.append("queue claim must contain FOR UPDATE SKIP LOCKED")
    if "worker_id" not in text:
        errors.append("queue operations must enforce worker ownership")
    if "idempotency_key" not in text:
        errors.append("queue must support idempotent enqueue")
    if "pg_notify" not in text or "wake_channel" not in text:
        errors.append("queue enqueue must emit PostgreSQL NOTIFY wake-ups")
    if "add_listener" not in worker_text or "wake_channel" not in worker_text:
        errors.append("worker must LISTEN for PostgreSQL queue wake-ups")
    if "poll_interval_seconds" not in worker_text:
        errors.append("worker must retain polling fallback for missed notifications")
    return errors


def rag_invariant_violations() -> list[str]:
    errors: list[str] = []
    rag = ROOT / "app" / "rag.py"
    prompts = ROOT / "app" / "prompts.py"
    validation = ROOT / "app" / "validation.py"
    retrieval = ROOT / "app" / "retrieval.py"
    embeddings = ROOT / "app" / "embeddings.py"
    if not rag.exists():
        return ["missing app/rag.py"]
    if not validation.exists():
        return ["missing app/validation.py"]
    if not retrieval.exists():
        return ["missing app/retrieval.py"]

    rag_text = rag.read_text(encoding="utf-8", errors="ignore")
    validation_text = validation.read_text(encoding="utf-8", errors="ignore")
    retrieval_text = retrieval.read_text(encoding="utf-8", errors="ignore")
    prompt_text = prompts.read_text(encoding="utf-8", errors="ignore") if prompts.exists() else ""
    if "validar(" not in rag_text:
        errors.append("RAG publishing path must call validar()")
    if 'MODEL = "claude-sonnet-5"' not in rag_text:
        errors.append("RAG must use Claude Sonnet 5")
    if "REQUESTED_TEMPERATURE = 0.2" not in rag_text:
        errors.append("RAG must preserve the requested temperature 0.2 design intent")
    if "SONNET_5_SUPPORTS_CUSTOM_TEMPERATURE = False" not in rag_text:
        errors.append("Sonnet 5 request must document current custom-temperature incompatibility")
    if '"cache_control"' not in rag_text:
        errors.append("system prompt must use Anthropic cache_control")
    if not prompts.exists() or "PROCESS_SUMMARY_SYSTEM_PROMPT" not in prompt_text:
        errors.append("cacheable system prompt must live in app/prompts.py")
    if "secrecy_level" not in rag_text or 'base["secrecy_level"] > 0' not in rag_text:
        errors.append("secret cases must be truncated before generation")
    if "class\\s*=" not in validation_text:
        errors.append("validator must reject class= in JSX")
    if "0.5 * lexical" not in retrieval_text or "0.5 * vector" not in retrieval_text:
        errors.append("long retrieval must preserve 0.5 BM25 / 0.5 vector weighting")
    if "len(steps) > 40" in rag_text and not embeddings.exists():
        errors.append("conditional vector retrieval requires app/embeddings.py")
    return errors


def embedding_invariant_violations() -> list[str]:
    embeddings = ROOT / "app" / "embeddings.py"
    schema = ROOT / "sql" / "002_process_data.sql"
    errors: list[str] = []
    if not embeddings.exists():
        return ["missing app/embeddings.py"]
    if not schema.exists():
        return ["missing sql/002_process_data.sql"]

    embedding_text = embeddings.read_text(encoding="utf-8", errors="ignore")
    schema_text = schema.read_text(encoding="utf-8", errors="ignore").lower()
    if "VECTOR_DIMENSIONS = 1536" not in embedding_text:
        errors.append("embedding provider must stay aligned to vector(1536)")
    if "embedding vector(1536)" not in schema_text:
        errors.append("process_steps embedding column must remain vector(1536)")
    if 'request["dimensions"] = VECTOR_DIMENSIONS' not in embedding_text:
        errors.append("text-embedding-3 requests must pin output dimensions")
    return errors


def webhook_invariant_violations() -> list[str]:
    api = ROOT / "app" / "api.py"
    judit = ROOT / "app" / "judit.py"
    judit_tasks = ROOT / "app" / "judit_tasks.py"
    errors: list[str] = []
    if not api.exists():
        return ["missing app/api.py"]
    if not judit.exists():
        return ["missing app/judit.py"]
    if not judit_tasks.exists():
        return ["missing app/judit_tasks.py"]

    api_text = api.read_text(encoding="utf-8", errors="ignore")
    judit_text = judit.read_text(encoding="utf-8", errors="ignore")
    task_text = judit_tasks.read_text(encoding="utf-8", errors="ignore")

    if "status_code=404" not in api_text:
        errors.append("invalid webhook token must return 404")
    if 'task_name="finalize_judit_request"' not in api_text:
        errors.append("request_completed must enqueue finalization instead of doing heavy work inline")
    if "finalize_version(" in api_text:
        errors.append("webhook API must not finalize process data inline")
    if "callback_id" not in api_text or "judit_deliveries" not in api_text:
        errors.append("webhook deliveries must be persisted/idempotent by callback_id")
    if 'event_type == "response_created"' not in judit_text:
        errors.append("Judit adapter must understand response_created envelope")
    if 'self.event_type == "request_completed"' not in judit_text:
        errors.append("Judit adapter must understand request_completed envelope")
    if "cached_response" not in judit_text or "tags" not in judit_text:
        errors.append("Judit adapter must read cached_response from current payload/tags shape")
    if "source_cached_response" not in task_text:
        errors.append("finalizer must suppress LLM enqueue for cached-only responses")
    return errors


def dependency_violations() -> list[str]:
    pyproject = ROOT / "pyproject.toml"
    if not pyproject.exists():
        return ["missing pyproject.toml"]
    text = pyproject.read_text(encoding="utf-8").lower()
    return [
        f"forbidden dependency declared: {name}"
        for name in FORBIDDEN_IMPORTS
        if re.search(rf"(^|[\"']){re.escape(name)}([\[<>=\"']|$)", text)
    ]


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
    for msg in queue_invariant_violations():
        violations.append(("app/queue.py", msg))
    for msg in rag_invariant_violations():
        violations.append(("app/rag.py", msg))
    for msg in embedding_invariant_violations():
        violations.append(("app/embeddings.py", msg))
    for msg in webhook_invariant_violations():
        violations.append(("app/api.py", msg))

    if violations:
        print("Migration harness: FAILED")
        for path, msg in violations:
            print(f" - {path}: {msg}")
        return 1

    print(f"Migration harness: OK ({len(files)} project files checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
