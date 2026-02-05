#!/bin/bash
docker exec -t $POSTGRES_CONTAINER sh -c 'pg_dump -U $POSTGRES_USER $POSTGRES_DB' > "$BKP_DIR/$(date +%Y-%m-%d_%H-%M-%S)-postgres-backup.sql"
