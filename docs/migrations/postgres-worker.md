# Migration: PostgreSQL worker core

## Source inspirations

- HydraTask: production-shaped queue lifecycle, worker heartbeat, retry/reclaim patterns.
- Iank314/task-queue: lease/recovery reference.

No donor repository is copied wholesale. Rpy retains only queue/worker mechanics required by the project.

## COPY

- PostgreSQL-backed claim model.
- `FOR UPDATE SKIP LOCKED` concurrency primitive.
- Heartbeat ownership by `worker_id`.
- Retry with bounded exponential backoff.
- Reclaim of jobs whose heartbeat expired.
- Graceful process shutdown.

## ADAPT

- Task registry reduced to an explicit Python mapping/decorator.
- Queue schema reduced to fields required by Rpy.
- Reclaimer runs with each worker for now; scheduler-specific responsibilities remain separate.
- Job payloads/results are JSONB and domain-neutral.

## DROP

- Dashboard and UI.
- DAG/dependency engine.
- Queue names and routing abstractions not yet required.
- Donor-specific event model and metrics stack.
- Donor product naming.

## Required invariants

1. Claim must use `FOR UPDATE SKIP LOCKED`.
2. A processing job is owned by a `worker_id`.
3. Heartbeat/complete/fail operations must verify that owner.
4. Failed jobs retry until `max_attempts`, then become `dead`.
5. A crashed worker must not leave a job permanently stuck in `processing`.
6. Redis, Celery, RabbitMQ and BullMQ are prohibited.

## Current Rpy files

- `app/queue.py`
- `app/db.py`
- `app/tasks.py`
- `app/worker.py`
- `sql/001_init.sql`
- `tests/test_tasks.py`
- `tests/test_worker.py`

## Follow-up tests before production

- Integration test against PostgreSQL with two concurrent workers proving a job is claimed once.
- Retry/dead transition test.
- Reclaim-after-worker-crash test.
- Graceful shutdown during an in-flight task.
