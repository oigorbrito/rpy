# PostgreSQL backup and restore runbook

Rpy uses PostgreSQL as the durable source of truth for process state, queue state, summaries, audit records and pgvector embeddings. A deploy is not production-ready unless backups are stored outside the database host and restores are exercised.

## Backup format

`scripts/backup_database.sh` creates a PostgreSQL custom-format archive with:

- `pg_dump --format=custom`;
- ownership and ACL metadata excluded so restores are portable between environments;
- file mode restricted by `umask 077`;
- a SHA-256 sidecar checksum;
- an immediate `pg_restore --list` archive-integrity check.

Example, on an operations host with PostgreSQL 16 client tools:

```sh
DATABASE_URL='postgresql://...' \
  sh scripts/backup_database.sh /secure/backups/rpy-2026-09-14.dump
```

The resulting `.dump` and `.dump.sha256` files are one backup unit. Copy both to encrypted, access-controlled storage outside the PostgreSQL host. Do not commit either file to Git.

## Restore

Restore is deliberately guarded because it uses `pg_restore --clean --if-exists` and can replace existing database objects. Prefer restoring into a new, empty database first.

```sh
DATABASE_URL='postgresql://.../rpy_restore' \
ALLOW_DESTRUCTIVE_RESTORE=YES \
  sh scripts/restore_database.sh /secure/backups/rpy-2026-09-14.dump
```

The restore command verifies the SHA-256 checksum and archive readability before modifying the target database, then uses `--exit-on-error` so partial failures are surfaced immediately.

After restore, verify at minimum:

1. `/ready` against an application instance pointed at the restored database;
2. `schema_migrations` matches the expected release;
3. the `vector` extension exists;
4. representative process summaries can be read through the authenticated API;
5. recent jobs/audit rows are present according to the backup timestamp.

## Restore drill

`scripts/verify_backup_restore.sh` is the automated CI drill. It migrates the source test database, writes a sentinel row, creates a custom-format backup, restores it into a separate database, and verifies:

- the sentinel data row;
- migration-ledger count;
- pgvector extension presence.

This catches a backup process that can create files but cannot actually restore the application schema.

## RPO and RTO

The scripts do not pretend to define business recovery objectives. RPO is determined by backup/PITR cadence; RTO is determined by database size, storage bandwidth, operator automation and validation time.

For a simple MVP deployment without managed point-in-time recovery, a daily logical backup means the worst-case logical-backup RPO can approach 24 hours. If that loss window is unacceptable, use managed PostgreSQL continuous backups/PITR in addition to these logical dumps.

Measure RTO from a real restore drill and record the observed duration. Re-run the drill after meaningful database growth or storage changes rather than assuming the original timing still holds.

## Retention and security

- keep at least one backup copy outside the database host/volume;
- encrypt backup storage at rest and in transit;
- restrict read access because dumps contain tenant/process data;
- define retention independently from application `RETENTION_DAYS` so an operator error or bad purge can be recovered from an older backup;
- monitor backup freshness and alert when the newest verified backup exceeds the chosen RPO window;
- periodically restore a randomly selected retained backup, not only the newest one.

Logical dumps complement, rather than replace, provider snapshots or PITR. Managed PostgreSQL deployments should use the provider-native backup mechanism as the primary fast recovery path and keep logical dumps as a portable secondary recovery path.
