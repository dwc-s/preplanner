#!/bin/sh
# Docker entrypoint: bring the database schema up to date, then run the container's
# command (gunicorn by default). Idempotent — `flask db upgrade` is a no-op once the
# schema is current, so a fresh volume becomes usable on first boot without any
# manual step. Create the first admin afterwards with:
#     docker exec -it <container> flask create-admin
set -e
export FLASK_APP=run

echo "Pre-Planner: applying database migrations ..."
flask db upgrade

exec "$@"
