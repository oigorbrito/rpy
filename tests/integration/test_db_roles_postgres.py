from __future__ import annotations

import os

import asyncpg
import pytest

from app.migrations import migrate
from scripts.provision_db_roles import provision

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_runtime_database_roles_are_least_privilege(monkeypatch: pytest.MonkeyPatch) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)

    urls = {
        "API_DATABASE_URL": TEST_DATABASE_URL.replace(
            "postgres:postgres@", "rpy_api:api-password@"
        ),
        "WORKER_DATABASE_URL": TEST_DATABASE_URL.replace(
            "postgres:postgres@", "rpy_worker:worker-password@"
        ),
        "SCHEDULER_DATABASE_URL": TEST_DATABASE_URL.replace(
            "postgres:postgres@", "rpy_scheduler:scheduler-password@"
        ),
        "BACKUP_DATABASE_URL": TEST_DATABASE_URL.replace(
            "postgres:postgres@", "rpy_backup:backup-password@"
        ),
    }
    for name, value in urls.items():
        monkeypatch.setenv(name, value)

    await provision(TEST_DATABASE_URL)

    admin = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        rows = await admin.fetch(
            """
            SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
            FROM pg_roles
            WHERE rolname = ANY($1::text[])
            ORDER BY rolname
            """,
            ["rpy_api", "rpy_worker", "rpy_scheduler", "rpy_backup"],
        )
        assert len(rows) == 4
        for row in rows:
            assert row["rolsuper"] is False
            assert row["rolcreatedb"] is False
            assert row["rolcreaterole"] is False
            assert row["rolreplication"] is False
            assert row["rolbypassrls"] is False

        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_api', 'processes', 'SELECT')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_api', 'access_log', 'INSERT')"
        )
        assert not await admin.fetchval(
            "SELECT has_table_privilege('rpy_api', 'processes', 'DELETE')"
        )
        assert not await admin.fetchval(
            "SELECT has_schema_privilege('rpy_api', 'public', 'CREATE')"
        )

        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_worker', 'jobs', 'UPDATE')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_worker', 'process_steps', 'DELETE')"
        )
        assert not await admin.fetchval(
            "SELECT has_table_privilege('rpy_worker', 'processes', 'DELETE')"
        )

        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_scheduler', 'processes', 'DELETE')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_scheduler', 'jobs', 'DELETE')"
        )
        assert not await admin.fetchval(
            "SELECT has_table_privilege('rpy_scheduler', 'process_summaries', 'SELECT')"
        )

        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_backup', 'processes', 'SELECT')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('rpy_backup', 'backup_runs', 'INSERT')"
        )
        assert not await admin.fetchval(
            "SELECT has_table_privilege('rpy_backup', 'processes', 'UPDATE')"
        )
    finally:
        await admin.close()

    api = await asyncpg.connect(urls["API_DATABASE_URL"])
    try:
        assert await api.fetchval("SELECT count(*) FROM processes") is not None
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await api.execute("CREATE TABLE db_role_escape_probe (id integer)")
    finally:
        await api.close()
