#!/bin/bash
set -e

# Apply database migrations
echo "Applying database migrations..."
for i in {1..3}; do
    python manage.py migrate --noinput && break
    echo "Migration attempt $i failed, retrying..."
    sleep 3
done

# Start Daphne server
echo "Starting Daphne server..."
exec "$@"
