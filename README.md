# Pre-Planner

Free, open-source **fire-service pre-incident planning** software for volunteer
and call departments. Store building pre-plans (critical fire-ground data,
hazards, contacts), annotate floor plans, and map hydrants, footprints, access
points and routes — with per-department accounts so each department sees only
its own data.

## Quick start

```bash
# 1. Create a virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Create the database schema
export FLASK_APP=run                 # Windows: set FLASK_APP=run
flask db upgrade

# 3. Create your department + first admin account
flask create-admin

# 4. Run it
python run.py
```

Open <http://127.0.0.1:5000> and sign in. Prefer a demo with sample data?
Instead of steps 3–4, run `python seed.py` (loads a demo department plus sample
occupancies/hydrants) — it prints a demo login.

## What works today

- **Accounts & multi-tenancy** — session login (Flask-Login), department-scoped
  data, admin-managed users. **No public sign-up**: admins add crew; new
  departments are created with `flask create-admin`. Every route is behind login
  and every query is scoped to the user's department. Login is **rate-limited**
  (Flask-Limiter); users can **change their own password** and admins can issue a
  **temporary password reset**.
- **Occupancy pre-plans** — create, edit, search, delete. Construction type,
  condition, fire-protection systems, Knox Box, gate/alarm codes, annunciator,
  utility shutoffs, water supply, hazards, contacts, and notes.
- **Floor plans** — upload images and annotate them (rectangles/polygons for
  hazards, shutoffs, Knox Box…) with [Annotorious](https://annotorious.dev).
  Images are served only through an authenticated, ownership-checked route.
- **Interactive map** — occupancies, footprints, hydrants, access points,
  routes and custom zones as toggleable layers. Draw features with
  [Leaflet-Geoman](https://geoman.io); click to place hydrants; draw a building
  footprint and set its point right on the pre-plan form.
- **Hydrants** — add/list/delete, NFPA 291 flow-class colour coding.
- **WMS overlays & GIS import** — configure state/county WMS parcel layers
  (toggleable on the map), and bulk-import **GeoJSON / KML / GPX / zipped
  Shapefiles** (pure-Python, no system GDAL) as map features.
- **Installable PWA with offline editing + sync** — a local-first store (Dexie /
  IndexedDB) backs the map and occupancy records, so crews can **view *and* edit**
  pre-plans with no signal (draw features, edit fields, add hazards/contacts).
  Changes queue in an outbox and sync via `/api/sync` on reconnect, with
  **optimistic-concurrency conflict resolution** (keep-mine / keep-theirs on a
  Conflicts page). Signing out wipes the local store. New floor-plan *image
  uploads* still need a connection. Add it to a phone's home screen.
- **JSON/GeoJSON API** — `/api/occupancies`, `/api/footprints`, `/api/hydrants`,
  `/api/map-features` (CRUD), `/api/wms-layers`.
- **CSRF protection** on every form and AJAX write (Flask-WTF).

## Project layout

```
preplanner/
├── run.py               # dev entry point  (python run.py)
├── config.py            # config; DATABASE_URL, UPLOAD_FOLDER, SECRET_KEY
├── seed.py              # sample data loader
├── migrations/          # Alembic (Flask-Migrate) migrations
├── app/
│   ├── __init__.py      # app factory + CLI (create-admin, seed-db)
│   ├── extensions.py    # SQLAlchemy instance
│   ├── models.py        # THE DATA MODEL — read this first
│   ├── scoping.py       # dept_query / get_owned — tenant isolation chokepoint
│   ├── auth.py          # login/logout + user management
│   ├── main.py          # server-rendered pages (map, CRUD, floor plans, overlays)
│   ├── api.py           # JSON/GeoJSON endpoints
│   ├── sync.py          # POST /api/sync — offline sync (apply, conflicts, delta)
│   ├── gis_import.py    # pure-Python GeoJSON/KML/GPX/Shapefile parsers
│   ├── templates/
│   └── static/          # css + js (store.js = local-first store + sync engine);
│                        # vendor/ (Leaflet/Geoman/Annotorious/Dexie), sw.js, icons/
└── tests/               # pytest (auth, isolation, floor plans, features, GIS, limits)
```

## Data model & tenancy

`Department` owns `User`s and all data. `Occupancy` is the central pre-plan
(with child `Contact` / `Hazard` / `FloorPlan`); `Hydrant` and `MapFeature`
(access points, routes, zones, custom) are standalone map features. Every
top-level record carries a `department_id`, and **all** reads go through
`app/scoping.py` (`dept_query` / `get_owned`) — the single place tenant
isolation is enforced. Geometry is stored as GeoJSON text so the schema runs
unchanged on SQLite or Postgres; the hosted PostGIS instance can promote those
columns to spatial types later. Full detail in [`app/models.py`](app/models.py).

## Database: SQLite → MySQL / PostgreSQL

Default is a zero-config SQLite file under `instance/`. Point at another database
by setting `DATABASE_URL` (in the environment or a `.env` file), then run
migrations:

```bash
# MySQL / MariaDB (e.g. PythonAnywhere)
DATABASE_URL='mysql+pymysql://user:pass@host/dbname?charset=utf8mb4'
# PostgreSQL
DATABASE_URL='postgresql+psycopg://user:pass@localhost/preplanner'

flask db upgrade
```

Drivers: `PyMySQL` ships in `requirements.txt`; uncomment `psycopg` for Postgres.
Config can come from a `.env` file (loaded by `config.py`) — see the deploy guide.
Schema changes are managed with Flask-Migrate: after editing models, run
`flask db migrate -m "..."` then `flask db upgrade`.

## Running the tests

```bash
pip install -r requirements.txt
pytest
```

## Deployment

**PythonAnywhere + MySQL** — a guided installer and full walkthrough live in
[`deploy/PYTHONANYWHERE.md`](deploy/PYTHONANYWHERE.md). In a PythonAnywhere Bash
console: `bash deploy/install_pythonanywhere.sh` sets up the virtualenv, `.env`,
database schema, and your admin account, then prints the Web-tab settings.

**Generic (nginx/caddy + gunicorn):**

```bash
export SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')
export DATABASE_URL='postgresql+psycopg://...'   # or mysql+pymysql://...
flask db upgrade
flask create-admin
gunicorn "app:create_app()"          # behind nginx/caddy for TLS
```

Or build the container: `docker build -t preplanner . && docker run -p 8000:8000 -e SECRET_KEY=... preplanner`
(run `flask db upgrade` / `flask create-admin` inside the container on first
boot; mount a volume for `instance/` so the SQLite DB and uploads persist).

## Security notes

Auth, per-department isolation, CSRF, authenticated file serving, login
rate-limiting, and password change/reset are in place. Before exposing a real
instance to the internet, still add:

- **HTTPS** (terminate TLS at nginx/caddy) and a strong `SECRET_KEY` from the
  environment — sessions and CSRF depend on it.
- A **"forgot password" email flow** if you want unattended recovery (today an
  admin issues a temporary password). For multi-worker deployments, point
  `RATELIMIT_STORAGE_URI` at Redis so the login limiter is shared across workers.
- Consider role-gating the most sensitive fields (gate codes / alarm PINs) and
  an audit log, depending on your threat model.

## Roadmap

Done: auth + multi-tenancy, CSRF, floor-plan annotation, map drawing (footprints,
access points, routes, custom layers, hydrant placement), account hardening
(rate-limiting, password change/reset), WMS overlays + GIS import
(GeoJSON/KML/GPX/Shapefile), and an installable PWA with **offline editing +
sync** (local-first store, outbox, optimistic-concurrency conflict resolution).
Next, in rough order:

1. **Offline for floor plans** — queue new image uploads offline (binary sync);
   currently annotation edits sync but new uploads need a connection.
2. **Field-level merge** — auto-merge non-overlapping field edits instead of
   whole-record keep-mine/keep-theirs.
3. **Forgot-password email** flow (today an admin issues a temporary password).
4. **Heavier GIS formats** — GeoTIFF/DXF and arbitrary-CRS reprojection via
   optional GDAL/`ogr2ogr`; report/PDF export for training.
5. **Layer polish** — opacity, reorder, per-feature styling.
6. **Dispatch / NERIS / NFPA 1620** alignment.

## License

Pre-Planner is licensed under the **GNU Affero General Public License v3.0**
(AGPL-3.0) — see [LICENSE](LICENSE). The AGPL is deliberate: it keeps the project
open **even when run as a hosted service** — anyone who offers a modified version
over a network must make their source available.

Copyright © 2026 dwcs. See [LICENSE](LICENSE) for the full terms.
