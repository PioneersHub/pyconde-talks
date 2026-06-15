#!/bin/bash
# Dump the Postgres database in $POSTGRES_CONTAINER to a timestamped .sql file in $BKP_DIR.
#
# Fails loudly instead of leaving a fake backup: a plain `... > file` truncates the output file
# before pg_dump runs, so a failed dump (DB down, wrong container, auth error) used to leave a
# 0-byte file and still exit 0. Here the required env vars are checked, the dump goes to a temp
# file, and that file is only promoted to the final name once pg_dump actually succeeds.
set -euo pipefail

: "${POSTGRES_CONTAINER:?Set POSTGRES_CONTAINER to the running Postgres container name}"
: "${BKP_DIR:?Set BKP_DIR to the directory where backups should be written}"

mkdir -p "${BKP_DIR}"

timestamp="$(date +%Y-%m-%d_%H-%M-%S)"
final="${BKP_DIR}/${timestamp}-postgres-backup.sql"
tmp="${final}.partial"

# POSTGRES_USER / POSTGRES_DB are resolved inside the container, hence the single quotes.
if ! docker exec -t "${POSTGRES_CONTAINER}" \
    sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > "${tmp}"; then
    rm -f "${tmp}"
    echo "pg_dump failed; no backup written." >&2
    exit 1
fi

mv "${tmp}" "${final}"
echo "Backup written to ${final}"
