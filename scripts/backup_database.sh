#!/bin/sh
set -eu

if [ -z "${DATABASE_URL:-}" ]; then
  echo "DATABASE_URL is required" >&2
  exit 2
fi

if [ "$#" -ne 1 ]; then
  echo "usage: DATABASE_URL=... backup_database.sh <output.dump>" >&2
  exit 2
fi

output=$1
output_dir=$(dirname "$output")
mkdir -p "$output_dir"

umask 077
pg_dump \
  --format=custom \
  --compress=6 \
  --no-owner \
  --no-privileges \
  --file="$output" \
  "$DATABASE_URL"

sha256sum "$output" > "$output.sha256"

# Validate that the custom archive can be read before considering it a backup.
pg_restore --list "$output" >/dev/null

echo "backup created: $output"
echo "checksum: $output.sha256"
