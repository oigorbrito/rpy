#!/bin/sh
set -eu

if [ -z "${DATABASE_URL:-}" ]; then
  echo "DATABASE_URL is required" >&2
  exit 2
fi

if [ "${ALLOW_DESTRUCTIVE_RESTORE:-}" != "YES" ]; then
  echo "refusing restore: set ALLOW_DESTRUCTIVE_RESTORE=YES" >&2
  exit 2
fi

if [ "$#" -ne 1 ]; then
  echo "usage: DATABASE_URL=... ALLOW_DESTRUCTIVE_RESTORE=YES restore_database.sh <backup.dump>" >&2
  exit 2
fi

backup=$1
checksum="$backup.sha256"

if [ ! -f "$backup" ]; then
  echo "backup not found: $backup" >&2
  exit 2
fi

if [ ! -f "$checksum" ]; then
  echo "checksum not found: $checksum" >&2
  exit 2
fi

sha256sum -c "$checksum"
pg_restore --list "$backup" >/dev/null

pg_restore \
  --clean \
  --if-exists \
  --no-owner \
  --no-privileges \
  --exit-on-error \
  --dbname="$DATABASE_URL" \
  "$backup"

echo "restore completed from: $backup"
