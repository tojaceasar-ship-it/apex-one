#!/usr/bin/env bash
set -euo pipefail

# APEX ONE PostgreSQL Restore & Verification Script
# Usage: APEX_RESTORE_DSN="postgresql://..." ./infra/scripts/restore_postgres.sh <dump_file>

DUMP_FILE="${1:-}"
if [[ -z "${DUMP_FILE}" || ! -f "${DUMP_FILE}" ]]; then
  echo "ERROR: Provide path to an existing .dump archive." >&2
  exit 1
fi

DSN="${APEX_RESTORE_DSN:-}"
if [[ -z "${DSN}" ]]; then
  echo "ERROR: Set APEX_RESTORE_DSN before restoring." >&2
  exit 1
fi

if [[ -f "${DUMP_FILE}.sha256" ]]; then
  sha256sum --check "${DUMP_FILE}.sha256"
fi

pg_restore --clean --if-exists --no-owner --no-privileges --dbname="${DSN}" "${DUMP_FILE}"
echo "Restore completed cleanly into target PostgreSQL database."
