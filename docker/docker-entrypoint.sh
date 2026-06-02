#!/bin/bash
set -e

# Apply database migrations. Retry a few times so a slow-to-accept-connections DB
# (just-started container) doesn't kill the boot, but FAIL HARD if they never succeed:
# starting Daphne on a half-migrated schema serves a broken app (e.g. queries referencing
# columns a pending migration would add) and masks the real failure behind 500s.
echo "Applying database migrations..."
migrated=false
for i in {1..3}; do
    if python manage.py migrate --noinput; then
        migrated=true
        break
    fi
    echo "Migration attempt $i failed, retrying..."
    sleep 3
done

if [ "$migrated" != "true" ]; then
    echo "Migrations failed after 3 attempts; refusing to start the server." >&2
    exit 1
fi

# Start Daphne server
echo "Starting Daphne server..."
exec "$@"
