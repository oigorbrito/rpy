from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Sequence
from uuid import UUID

import asyncpg

_TOKEN_RE = re.compile(r"[\wÀ-ÿ]+", re.UNICODE)
MILESTONE_RE = re.compile(
    r"\b(SENTEN[CÇ]A|AC[ÓO]RD[ÃA]O|CITA[CÇ][ÃA]O|DECIS[ÃA]O|TR[ÂA]NSITO\s+EM\s+JULGADO|AUDI[ÊE]NCIA)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class Step:
    id: UUID
    step_number: int
    text: str
    title: str | None = None
    occurred_at: Any = None

    @property
    def searchable_text(self) -> str:
        return f"{self.title or ''} {self.text}".strip()


@dataclass(slots=True)
class RankedStep:
    step: Step
    bm25: float = 0.0
    vector: float = 0.0
    score: float = 0.0
    forced: bool = False


def tokenize(text: str) -> list[str]:
    return [match.group(0).casefold() for match in _TOKEN_RE.finditer(text)]


def bm25_scores(query: str, steps: Sequence[Step], *, k1: float = 1.5, b: float = 0.75) -> dict[UUID, float]:
    if not steps:
        return {}
    query_terms = tokenize(query)
    if not query_terms:
        return {step.id: 0.0 for step in steps}

    docs = [tokenize(step.searchable_text) for step in steps]
    avgdl = sum(len(doc) for doc in docs) / len(docs) or 1.0
    n_docs = len(docs)
    doc_freq: dict[str, int] = {}
    for term in set(query_terms):
        doc_freq[term] = sum(1 for doc in docs if term in doc)

    scores: dict[UUID, float] = {}
    for step, doc in zip(steps, docs, strict=True):
        frequencies: dict[str, int] = {}
        for token in doc:
            frequencies[token] = frequencies.get(token, 0) + 1
        score = 0.0
        doc_len = len(doc)
        for term in query_terms:
            tf = frequencies.get(term, 0)
            if tf == 0:
                continue
            df = doc_freq.get(term, 0)
            idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
            denominator = tf + k1 * (1 - b + b * doc_len / avgdl)
            score += idf * ((tf * (k1 + 1)) / denominator)
        scores[step.id] = score
    return scores


def _normalize(values: dict[UUID, float]) -> dict[UUID, float]:
    if not values:
        return {}
    maximum = max(values.values(), default=0.0)
    if maximum <= 0:
        return {key: 0.0 for key in values}
    return {key: value / maximum for key, value in values.items()}


def rank_steps(
    *,
    query: str,
    steps: Sequence[Step],
    vector_scores: dict[UUID, float] | None = None,
    limit: int = 20,
) -> list[RankedStep]:
    if len(steps) <= 40:
        return [RankedStep(step=step, forced=True, score=1.0) for step in sorted(steps, key=lambda item: item.step_number)]

    lexical = _normalize(bm25_scores(query, steps))
    vector = _normalize(vector_scores or {})
    max_step = max(step.step_number for step in steps) or 1

    ranked: list[RankedStep] = []
    for step in steps:
        base_score = 0.5 * lexical.get(step.id, 0.0) + 0.5 * vector.get(step.id, 0.0)
        recency = 1.0 + 0.3 * (step.step_number / max_step)
        forced = bool(MILESTONE_RE.search(step.searchable_text))
        ranked.append(
            RankedStep(
                step=step,
                bm25=lexical.get(step.id, 0.0),
                vector=vector.get(step.id, 0.0),
                score=base_score * recency,
                forced=forced,
            )
        )

    by_number = sorted(ranked, key=lambda item: item.step.step_number)
    mandatory_ids = {by_number[0].step.id, by_number[-1].step.id}
    mandatory_ids.update(item.step.id for item in by_number[-5:])
    mandatory_ids.update(item.step.id for item in ranked if item.forced)

    selected: dict[UUID, RankedStep] = {
        item.step.id: item for item in ranked if item.step.id in mandatory_ids
    }
    for item in sorted(ranked, key=lambda candidate: candidate.score, reverse=True):
        if len(selected) >= max(limit, len(mandatory_ids)):
            break
        selected.setdefault(item.step.id, item)

    return sorted(selected.values(), key=lambda item: item.step.step_number)


async def load_steps(conn: asyncpg.Connection, *, version_id: UUID) -> list[Step]:
    rows = await conn.fetch(
        """
        SELECT id, step_number, title, text, occurred_at
        FROM process_steps
        WHERE version_id = $1
        ORDER BY step_number ASC
        """,
        version_id,
    )
    return [
        Step(
            id=row["id"],
            step_number=int(row["step_number"]),
            title=row["title"],
            text=row["text"],
            occurred_at=row["occurred_at"],
        )
        for row in rows
    ]


async def vector_search(
    conn: asyncpg.Connection,
    *,
    version_id: UUID,
    embedding: Sequence[float],
    limit: int = 30,
) -> dict[UUID, float]:
    rows = await conn.fetch(
        """
        SELECT id, 1 - (embedding <=> $2::vector) AS similarity
        FROM process_steps
        WHERE version_id = $1 AND embedding IS NOT NULL
        ORDER BY embedding <=> $2::vector
        LIMIT $3
        """,
        version_id,
        list(embedding),
        limit,
    )
    return {row["id"]: max(0.0, float(row["similarity"])) for row in rows}


async def lexical_search(
    conn: asyncpg.Connection,
    *,
    version_id: UUID,
    query: str,
    limit: int = 30,
) -> dict[UUID, float]:
    """Search one finalized version with PostgreSQL Portuguese FTS.

    The expression matches the repository GIN index and the version predicate
    is mandatory so lexical candidates cannot cross process/version boundaries.
    """
    if not query.strip():
        return {}
    rows = await conn.fetch(
        """
        WITH query AS (
            SELECT websearch_to_tsquery('portuguese', $2) AS value
        )
        SELECT ps.id,
               ts_rank_cd(
                   to_tsvector('portuguese', coalesce(ps.title, '') || ' ' || ps.text),
                   query.value
               ) AS score
        FROM process_steps ps
        CROSS JOIN query
        WHERE ps.version_id = $1
          AND to_tsvector(
              'portuguese', coalesce(ps.title, '') || ' ' || ps.text
          ) @@ query.value
        ORDER BY score DESC, ps.step_number ASC
        LIMIT $3
        """,
        version_id,
        query,
        limit,
    )
    return {row["id"]: max(0.0, float(row["score"])) for row in rows}
