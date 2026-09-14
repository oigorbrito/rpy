from __future__ import annotations

from typing import Any

import asyncpg


async def collect_operational_metrics(conn: asyncpg.Connection) -> dict[str, Any]:
    queue_rows = await conn.fetch(
        """
        SELECT status::text AS status, count(*)::bigint AS count
        FROM jobs
        GROUP BY status
        """
    )
    queue = {row["status"]: int(row["count"]) for row in queue_rows}
    for status in ("pending", "processing", "completed", "dead"):
        queue.setdefault(status, 0)

    oldest_pending_seconds = await conn.fetchval(
        """
        SELECT COALESCE(EXTRACT(EPOCH FROM (NOW() - min(created_at))), 0)
        FROM jobs
        WHERE status = 'pending'
        """
    )
    stale_processing = await conn.fetchval(
        """
        SELECT count(*)
        FROM jobs
        WHERE status = 'processing'
          AND last_heartbeat IS NOT NULL
          AND last_heartbeat < NOW() - interval '60 seconds'
        """
    )
    summary_metrics = await conn.fetchrow(
        """
        SELECT
            count(*)::bigint AS total,
            count(*) FILTER (WHERE validation->>'passed' = 'false')::bigint AS validation_failed,
            COALESCE(avg(generation_ms) FILTER (WHERE generation_ms IS NOT NULL), 0) AS avg_generation_ms,
            COALESCE(
                percentile_cont(0.95) WITHIN GROUP (ORDER BY generation_ms)
                    FILTER (WHERE generation_ms IS NOT NULL),
                0
            ) AS p95_generation_ms
        FROM process_summaries
        """
    )

    total = int(summary_metrics["total"] or 0)
    failed = int(summary_metrics["validation_failed"] or 0)
    return {
        "queue": queue,
        "oldest_pending_seconds": float(oldest_pending_seconds or 0),
        "stale_processing": int(stale_processing or 0),
        "summaries": {
            "total": total,
            "validation_failed": failed,
            "validation_failure_rate": (failed / total) if total else 0.0,
            "avg_generation_ms": float(summary_metrics["avg_generation_ms"] or 0),
            "p95_generation_ms": float(summary_metrics["p95_generation_ms"] or 0),
        },
    }
