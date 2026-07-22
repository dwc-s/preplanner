# Pre-Planner

Free, open-source **fire-service pre-incident planning** for volunteer and call
departments. Build and share building pre-plans — fire-ground data, hazards,
contacts, floor plans, photos, and SDS — assemble them into a standard document,
and map hydrants, footprints, access points, and routes. Every department sees
only its own data, and it all works **offline in the field** and syncs when you're
back online.

Runs on modest, free-tier hosting: pure-Python, vendored front-end libraries, no
system GDAL. **Licensed AGPL-3.0.**

```bash
git clone https://github.com/dwc-s/preplanner && cd preplanner
./install.sh          # venv + deps + .env (generates a SECRET_KEY) + schema; offers to add an admin or demo data
source .venv/bin/activate
python run.py         # http://127.0.0.1:5000
```

`./install.sh` (macOS/Linux) is re-runnable and never clobbers an existing `.env` or
database. Prefer manual steps, or on Windows? See [Quick start](#quick-start).

---

## Features

**Getting in**
- **Public splash + explore-first sandbox** — logged-out visitors get a landing
  page and can spin up a private, throwaway demo workspace in one click (no signup)
  to try the whole app. Sandboxes auto-purge.
- **Accounts, ranks & roster** — session login (Flask-Login), per-department
  multi-tenancy, admin-managed users (no public signup). Assign fire-service ranks
  (Chief → Probationary Firefighter); everyone sees the seniority-ordered roster.
  Login rate-limiting, self-service password change, admin temporary-password reset.

**Home & workflow**
- **Private dashboard** — your logged-in home: recent department activity, admin
  **announcements**, and a sortable list of **your pre-plans** with their review
  **status** and who they're submitted to. (The review workflow itself — reviewer
  notifications, approve / request-changes — is scaffolded; submission is live.)

**Pre-plans**
- **Occupancy pre-plans** — construction, condition, fire-protection systems,
  Knox Box, gate/alarm codes, annunciator, utility shutoffs, water supply, notes,
  plus inline-editable **hazards** and **contacts**. Edits **autosave** as you type
  (a subtle "All changes saved" cue — no Save button); the same goes for hydrants and
  builder captions.
- **Drag-and-drop builder** — assemble a pre-plan document from ordered **elements**
  (map · floor plans · photos · SDS · inspection reports) in your department's
  standard order; reorder by dragging. Elements come from the shared library or a
  fresh upload; SDS links out to chemicalsafety.com's search.
- **PDF export** — download any pre-plan as a formatted PDF; photos and floor plans
  embed inline and attached SDS/PDF documents are merged in as **appendices**
  (pure-Python: reportlab + pypdf).
- **Shared asset library** — upload floor plans, photos, SDS, and documents once and
  reuse them anywhere. **GPS** is read from photo EXIF, **iPhone HEIC** is transcoded
  to JPEG, and PDFs/photos are **indexed for full-text search** (image OCR runs in the
  background). Files are served only through an authenticated, ownership-checked route.
- **Floor-plan annotation** — mark up uploaded images (rectangles/polygons) with
  [Annotorious](https://annotorious.dev).

**Map**
- **Interactive map** — occupancies, footprints, hydrants, access points, routes,
  and custom zones as toggleable layers. Draw with [Leaflet-Geoman](https://geoman.io),
  place fire-service **symbols** (FDC, Knox, shutoffs, hazmat, command post, arrows…),
  **measure** distances, click to place **hydrants** (NFPA 291 flow-class colours),
  and draw a footprint. Switch the base map — **street / satellite / topographic** —
  from the layer control; the map reopens where you last left it.
- **Basemaps & overlays** — one-click tiled basemaps (USGS topo/imagery, terrain
  hillshade, OpenTopoMap) plus **WMS** overlays you add by pasting a server URL and
  picking from its live layer list.
- **GIS import** — bulk-import **GeoJSON / KML / GPX / Shapefiles** (zipped or loose
  parts), auto-reprojected to WGS84 from the `.prj` (pure-Python; no system GDAL),
  optionally clipped to your map area.

**Platform**
- **Installable PWA with offline editing + sync** — a local-first store
  (Dexie / IndexedDB) backs the map and pre-plans, so crews can **view *and* edit**
  with no signal. Changes queue in an outbox and sync via `/api/sync` on reconnect,
  with optimistic-concurrency **conflict resolution** (keep-mine / keep-theirs).
- **JSON / GeoJSON API** — `/api/occupancies`, `/api/footprints`, `/api/hydrants`,
  `/api/map-features` (CRUD), `/api/wms-layers`.
- **CSRF** on every write; **database-agnostic** (SQLite → MySQL / PostgreSQL).

---

## Documentation

| Doc | What's in it |
|-----|--------------|
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | For department admins & members — using every feature |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How it's built — factory, models, multi-tenancy, offline sync, the asset pipeline |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Local setup, tests, migrations, CLI, project conventions |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Production: Docker, gunicorn, database, scheduled tasks |
| [deploy/PYTHONANYWHERE.md](deploy/PYTHONANYWHERE.md) | Step-by-step PythonAnywhere + MySQL walkthrough |
| [CHANGELOG.md](CHANGELOG.md) | Release notes |

---

## Quick start

Requires **Python 3.10+**.

**Easiest (macOS / Linux):**

```bash
git clone https://github.com/dwc-s/preplanner && cd preplanner
./install.sh          # venv + deps + .env (generated SECRET_KEY) + migrations
source .venv/bin/activate
python run.py          # http://127.0.0.1:5000
```

`install.sh` prompts to create your first admin or load demo data, and is safe to
re-run (it won't overwrite an existing `.env` or database). Override the interpreter
or venv path with `PYTHON=python3.12 ./install.sh` or `VENV=/path ./install.sh`.

**Manual (or Windows):**

```bash
git clone https://github.com/dwc-s/preplanner && cd preplanner
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**With demo data (fastest):**

```bash
python seed.py     # applies migrations + loads a demo department; prints a login
python run.py      # serves http://127.0.0.1:5000
```

`seed.py` prints the demo login (`admin@example.com` / `changeme`). `run.py` applies
any pending migrations on startup, so a fresh clone just runs.

**Empty install (your own department):**

```bash
export FLASK_APP=run                 # provided by .flaskenv; Windows: set FLASK_APP=run
flask db upgrade                     # create the schema
flask create-admin                   # create your department + first admin
python run.py
```

Not signed in? The landing page's **"Try the sandbox"** gives a throwaway demo with
no account.

**Optional — photo text search (OCR):** needs the `tesseract` binary
(`tesseract --version`; `apt install tesseract-ocr`, or bundled in the Docker image).
Everything else works without it — image OCR simply waits in a queue.

---

## Project layout

```
preplanner/
├── install.sh             # one-command setup: venv + deps + .env (SECRET_KEY) + migrations
├── run.py                 # dev entry point (applies migrations, then serves)
├── config.py              # config: DATABASE_URL, SECRET_KEY, UPLOAD_FOLDER, limits
├── seed.py                # demo-data loader (also a reusable department seeder)
├── requirements.txt       # pure-Python deps (no system GDAL)
├── Dockerfile             # turnkey container (migrations + SECRET_KEY on start, tesseract bundled)
├── .env.example           # copy to .env for prod config
├── migrations/            # Alembic (Flask-Migrate) migrations
├── app/
│   ├── __init__.py        # app factory + CLI (create-admin, seed-db, purge-sandboxes, ocr-pending)
│   ├── extensions.py      # SQLAlchemy + Limiter instances
│   ├── models.py          # THE DATA MODEL — read this first
│   ├── scoping.py         # dept_query / get_owned — the tenant-isolation chokepoint
│   ├── auth.py            # login/logout, users, ranks, roster, account
│   ├── main.py            # server-rendered pages: dashboard, map, pre-plans, builder, library, overlays
│   ├── api.py             # JSON / GeoJSON endpoints
│   ├── sync.py            # POST /api/sync — offline sync (apply, conflicts, delta)
│   ├── assets.py          # asset library file pipeline (naming, GPS, text/OCR, HEIC)
│   ├── export.py          # pre-plan PDF export (reportlab + pypdf appendix merge)
│   ├── sandbox.py         # ephemeral demo workspaces (create + purge + upload guard)
│   ├── gis_import.py      # pure-Python GeoJSON/KML/GPX/Shapefile parsers + reprojection
│   ├── templates/
│   └── static/            # css + js (store.js = local-first store + sync engine),
│                          # vendor/ (Leaflet/Geoman/Annotorious/Dexie), sw.js, icons/
├── deploy/                # PythonAnywhere installer, docker-entrypoint, scheduled_tasks.sh, docs
└── tests/                 # pytest (auth, isolation, pre-plans, builder, GIS, sync, limits)
```

Full architecture in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Configuration

Config is read from the environment (or a `.env` file — copy
[`.env.example`](.env.example)). Real environment variables win over `.env`.

| Variable | Default | Purpose |
|----------|---------|---------|
| `SECRET_KEY` | dev value | Signs sessions + CSRF. **Set a random one in production.** |
| `DATABASE_URL` | SQLite in `instance/` | e.g. `mysql+pymysql://…?charset=utf8mb4` or `postgresql+psycopg://…` |
| `UPLOAD_FOLDER` | `instance/uploads` | Where floor plans + library files are stored |
| `RATELIMIT_STORAGE_URI` | `memory://` | Point at Redis (`redis://…`) to share login limits across workers |

## CLI commands

With `FLASK_APP=run` (set by `.flaskenv`):

| Command | Does |
|---------|------|
| `flask db upgrade` | Create / migrate the schema |
| `flask create-admin` | Create a department and its first admin (interactive) |
| `flask seed-db` | Load the demo department + sample data (idempotent) |
| `flask ocr-pending` | OCR queued photos (deferred at upload) — wire to cron |
| `flask purge-sandboxes` | Delete expired demo sandboxes — wire to cron |

The last two are bundled in [`deploy/scheduled_tasks.sh`](deploy/scheduled_tasks.sh)
for a cron job / scheduled task. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Tests

```bash
pip install -r requirements.txt
pytest        # or: PYTHONPATH=. python -m pytest
```

Covers auth + cross-tenant isolation, pre-plan CRUD + **PDF export**, **autosave**,
the builder/asset library (incl. GPS + OCR extraction), offline sync + conflicts,
GIS import, form value-retention, and rate limits.

## Deployment

- **Docker (turnkey):** `docker build -t preplanner .` then
  `docker run -p 8000:8000 -v preplanner-data:/app/instance preplanner`
  (the container migrates the DB **and generates a persistent `SECRET_KEY`** on first
  start — pass `-e SECRET_KEY=…` to supply your own; create the first admin with
  `docker exec -it <c> flask create-admin`).
- **PythonAnywhere + MySQL:** run `bash deploy/install_pythonanywhere.sh` — see
  [deploy/PYTHONANYWHERE.md](deploy/PYTHONANYWHERE.md).
- **Generic (nginx/caddy + gunicorn):** see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Security

Auth, per-department isolation (one chokepoint in `scoping.py`), CSRF on every
write, authenticated file serving, and login rate-limiting are built in. Before
exposing a real instance: terminate **TLS** at a reverse proxy, set a strong
`SECRET_KEY`, and point `RATELIMIT_STORAGE_URI` at Redis if you run multiple
workers. Sensitive fields (gate codes / alarm PINs) are stored in plaintext — see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#security-model) for the threat model.

## License

**GNU Affero General Public License v3.0 (AGPL-3.0)** — see [LICENSE](LICENSE). The
AGPL keeps the project open even when run as a hosted service: anyone offering a
modified version over a network must publish their source. If you self-host and
re-brand, swap the logo/splash under `app/static/images/` and the "Pre-Planner" name.

Copyright © 2026 dwcs.
