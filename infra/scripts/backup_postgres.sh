#!/usr/bin/env bash
set -euo pipefail

# APEX ONE PostgreSQL Logical Backup Script (pg_dump custom format + SHA-256 manifest)
# Usage: APEX_BACKUP_DSN="postgresql://..." ./infra/scripts/backup_postgres.sh [output_dir]

BACKUP_DIR="${1:-./backups}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "${BACKUP_DIR}"

DSN="${APEX_BACKUP_DSN:-${DATABASE_URL:-}}"
if [[ -z "${DSN}" ]]; then
  echo "ERROR: Set APEX_BACKUP_DSN or DATABASE_URL before running backup." >&2
  exit 1
fi

OUT_FILE="${BACKUP_DIR}/apex_one_${TIMESTAMP}.dump"
pg_dump --format=custom --no-owner --no-privileges --dbname="${DSN}" --file="${OUT_FILE}"
sha256sum "${OUT_FILE}" > "${OUT_FILE}.sha256"

echo "Backup completed: ${OUT_FILE} (checksum recorded in ${OUT_FILE}.sha256)"
