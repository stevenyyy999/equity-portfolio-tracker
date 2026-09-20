#!/usr/bin/env bash
# -e: stops immediately if command fails
# -u: throws error if the script uses an unset variable
# -o: if a command in this pipeline fails, consider the whole pipeline as failed
set -euo pipefail

# WAIT_FOR_DB isn't set so it automatically becomes 1, but if we want to skip waiting, just set WAIT_FOR_DB=0 in .env
# If it waits, we check whether Postgres is reachable before continuing.
if [ "${WAIT_FOR_DB:-1}" = "1" ]; then
    python - <<'PY'
import os
import socket
import sys
import time

host = os.environ.get("DB_HOST", "db")
port = int(os.environ.get("DB_PORT", "5432"))
timeout = int(os.environ.get("DB_WAIT_TIMEOUT", "60"))
deadline = time.time() + timeout

while True:
    try:
        with socket.create_connection((host, port), timeout=2):
            print(f"Database is reachable at {host}:{port}")
            break
    except OSError as exc:
        if time.time() >= deadline:
            print(f"Timed out waiting for database at {host}:{port}: {exc}", file=sys.stderr)
            sys.exit(1)

        print(f"Waiting for database at {host}:{port}...")
        time.sleep(2)
PY
fi

# Same as above, set RUN_MIGRATIONS=0 in .env if you don't want to migrate.
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    python manage.py migrate --noinput
fi

exec "$@"
