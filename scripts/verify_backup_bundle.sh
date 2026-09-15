#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: verify_backup_bundle.sh <backup.dump>" >&2
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

# Verify the checksum from the archive directory so a sidecar created with a
# relative path remains portable after the backup pair is copied elsewhere.
backup_dir=$(dirname "$backup")
backup_name=$(basename "$backup")
checksum_name=$(basename "$checksum")
(
  cd "$backup_dir"
  sha256sum -c "$checksum_name"
)

pg_restore --list "$backup" >/dev/null

echo "backup bundle verified: $backup_name"
