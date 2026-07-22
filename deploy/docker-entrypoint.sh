#!/bin/sh
# Docker entrypoint: ensure a SECRET_KEY exists, bring the database schema up to
# date, then run the container's command (gunicorn by default). Idempotent — a fresh
# instance/ volume becomes usable on first boot without any manual step. Create the
# first admin afterwards with:
#     docker exec -it <container> flask create-admin
set -e
export FLASK_APP=run

mkdir -p instance

# Turnkey secret: if the operator didn't pass -e SECRET_KEY, generate one once and
# persist it on the instance volume so sessions/CSRF stay valid across restarts.
# An explicit SECRET_KEY in the environment always wins (config reads os.environ).
if [ -z "${SECRET_KEY:-}" ]; then
  if [ ! -f instance/.secret_key ]; then
    python -c 'import secrets; print(secrets.token_hex(32))' > instance/.secret_key
    chmod 600 instance/.secret_key
    echo "Pre-Planner: generated a SECRET_KEY (stored at instance/.secret_key)."
  fi
  SECRET_KEY="$(cat instance/.secret_key)"
  export SECRET_KEY
fi

echo "Pre-Planner: applying database migrations ..."
flask db upgrade

exec "$@"
