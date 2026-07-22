# Deployment

Pre-Planner runs on modest hosting: pure-Python, no system libraries beyond an
optional `tesseract`, SQLite by default. Pick one path below.

- [Configuration](#configuration)
- [Database](#database)
- [Option A: Docker (turnkey)](#option-a-docker-turnkey)
- [Option B: gunicorn behind a reverse proxy](#option-b-gunicorn-behind-a-reverse-proxy)
- [Option C: PythonAnywhere](#option-c-pythonanywhere)
- [Scheduled tasks](#scheduled-tasks) · [OCR](#ocr-tesseract) ·
  [Backups](#backups) · [Updating](#updating) · [Checklist](#production-checklist)

## Configuration

Config comes from the environment or a `.env` file (copy
[`.env.example`](../.env.example)); real env vars win.

| Variable | Default | Notes |
|----------|---------|-------|
| `SECRET_KEY` | insecure dev value | **Set a random one** — `python -c "import secrets;print(secrets.token_hex(32))"` |
| `DATABASE_URL` | SQLite in `instance/` | See below |
| `UPLOAD_FOLDER` | `instance/uploads` | Absolute path on a persistent disk in prod |
| `RATELIMIT_STORAGE_URI` | `memory://` | Redis (`redis://…`) for multi-worker login limits |

## Database

- **SQLite** (default) — zero config; the file lives in `instance/`. Fine for a
  single department; back up the file + the uploads folder.
- **MySQL / MariaDB** — `DATABASE_URL=mysql+pymysql://user:pass@host/db?charset=utf8mb4`
  (driver `PyMySQL` is already in `requirements.txt`).
- **PostgreSQL** — `DATABASE_URL=postgresql+psycopg://user:pass@host/db`; uncomment
  `psycopg` in `requirements.txt`.

Create/upgrade the schema with `flask db upgrade` (the Docker image and `run.py` do
this automatically; other paths run it once).

## Option A: Docker (turnkey)

The image bundles `tesseract` (for OCR) and runs migrations on start.

```bash
docker build -t preplanner .
docker run -d --name preplanner -p 8000:8000 \
  -e SECRET_KEY=$(openssl rand -hex 32) \
  -v preplanner-data:/app/instance \
  preplanner
docker exec -it preplanner flask create-admin      # first admin (interactive)
```

- The `-v …:/app/instance` volume persists the SQLite DB **and** uploaded files. To
  use an external DB instead, add `-e DATABASE_URL=…`.
- Put a TLS-terminating reverse proxy (nginx/caddy/Traefik) in front of port 8000.
- Run the background jobs periodically, e.g. a host cron:
  `docker exec preplanner flask ocr-pending && docker exec preplanner flask purge-sandboxes`.

## Option B: gunicorn behind a reverse proxy

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')
export DATABASE_URL='postgresql+psycopg://…'        # or mysql+pymysql://…, or omit for SQLite
export FLASK_APP=run
flask db upgrade
flask create-admin
gunicorn --workers 3 --bind 127.0.0.1:8000 "app:create_app()"
```

Front it with nginx/caddy for **TLS** and proxy to `127.0.0.1:8000`; serve
`/static/` directly for speed. Run it under systemd (or your process manager) so it
restarts on boot. With multiple workers, set `RATELIMIT_STORAGE_URI=redis://…` so the
login limiter is shared. Wire the [scheduled tasks](#scheduled-tasks) into cron.

## Option C: PythonAnywhere

A guided installer + full walkthrough: [deploy/PYTHONANYWHERE.md](../deploy/PYTHONANYWHERE.md).
In a Bash console, `bash deploy/install_pythonanywhere.sh` sets up the virtualenv,
`.env`, MySQL schema, and admin account, then prints the Web-tab settings. Add the
scheduled task in step 6.

## Scheduled tasks

Two idempotent background jobs — bundled in
[`deploy/scheduled_tasks.sh`](../deploy/scheduled_tasks.sh):

- `flask ocr-pending` — OCRs photos (deferred at upload so uploads stay fast).
- `flask purge-sandboxes` — deletes expired "try the sandbox" demos.

Run them however your host schedules work. Example host cron (every 10 min):

```
*/10 * * * * /home/me/preplanner/deploy/scheduled_tasks.sh >> /home/me/preplanner/instance/scheduled.log 2>&1
```

The script auto-detects the project root and virtualenv (override with
`PREPLANNER_ROOT` / `PREPLANNER_VENV`). On PythonAnywhere use the **Tasks** tab (free
tier: daily; paid: every few minutes). For Docker, `docker exec` the two commands
from a host cron.

## OCR (tesseract)

Image text search needs the `tesseract` binary. It's **bundled in the Docker image**
and preinstalled on PythonAnywhere. Elsewhere: `apt install tesseract-ocr` (Debian/
Ubuntu) or `brew install tesseract` (macOS). Without it, everything works except image
OCR, and the queue waits until a capable host drains it. PDF text search never needs
it.

## Backups

Back up two things:

- **The database** — the SQLite file under `instance/` (or a `mysqldump` /
  `pg_dump`).
- **`UPLOAD_FOLDER`** — floor plans and library assets (defaults to `instance/uploads`).

With Docker, both live in the `preplanner-data` volume.

## Updating

```bash
git pull
pip install -r requirements.txt
flask db upgrade          # apply any new migrations
# then restart: reload the web app / restart gunicorn / rebuild the image
```

Bump `APP_CACHE` in `sw.js` ships with the update, so PWA clients pick up new assets.

## Production checklist

- [ ] Strong random `SECRET_KEY` from the environment.
- [ ] **TLS** at a reverse proxy (sessions + CSRF assume HTTPS in production).
- [ ] Persistent disk for the DB + `UPLOAD_FOLDER`, with backups.
- [ ] `RATELIMIT_STORAGE_URI` → Redis if running multiple workers.
- [ ] Scheduled task wired for `ocr-pending` + `purge-sandboxes`.
- [ ] `tesseract` installed if you want photo OCR.
- [ ] Consider role-gating sensitive fields (gate codes / alarm PINs) for your threat
      model — see [ARCHITECTURE.md](ARCHITECTURE.md#security-model).
