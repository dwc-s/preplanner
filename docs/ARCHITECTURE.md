# Architecture

How Pre-Planner is built. If you're changing code, read this and then
[`app/models.py`](../app/models.py).

## Stack & principles

- **Flask** app-factory + blueprints, **SQLAlchemy** (Flask-SQLAlchemy),
  **Flask-Migrate** (Alembic), **Flask-Login**, **Flask-WTF** (CSRF),
  **Flask-Limiter**.
- **Pure-Python everywhere** — GIS import uses `pyshp` + `pyproj` (no system GDAL);
  the asset pipeline uses `Pillow` / `pypdf` / `pillow-heif`; image OCR uses
  `pytesseract`, the one *optional* piece that shells out to a `tesseract` binary.
- **Vendored front end** — Leaflet, Leaflet-Geoman, Annotorious, and Dexie are
  checked into `app/static/vendor/`; there is no build step and no CDN.
- **Runs on free/modest hosting** — SQLite by default, no always-on worker required
  (background work is cron-driven), no external services.
- **Database-agnostic** — geometry is stored as **GeoJSON text**, so the schema runs
  unchanged on SQLite, MySQL, or PostgreSQL. (A PostGIS instance can later promote
  those text columns to real geometry types.)

## The application factory

[`app/__init__.py`](../app/__init__.py) `create_app()` wires extensions
(`db`, `login_manager`, `csrf`, `migrate`, `limiter`), registers four blueprints,
serves the service worker from `/sw.js` (root scope), registers CLI commands, and
installs a context processor that feeds controlled vocabularies (occupancy types,
ranks, statuses, element kinds…) to every template.

Blueprints:

| Blueprint | File | Responsibility |
|-----------|------|----------------|
| `main` | [`main.py`](../app/main.py) | Server-rendered pages: dashboard, map, pre-plan CRUD, builder, asset library, floor plans, overlays/GIS |
| `api` | [`api.py`](../app/api.py) | Read GeoJSON + map-feature CRUD (JSON) |
| `sync` | [`sync.py`](../app/sync.py) | `POST /api/sync` — the offline sync engine (CSRF-exempt) |
| `auth` | [`auth.py`](../app/auth.py) | Login/logout, user management, ranks, roster, account, register stub |

## Data model

Thirteen models in [`app/models.py`](../app/models.py):

```
Department ──< User
    │
    ├──< Occupancy ──< Contact
    │        │      ──< Hazard
    │        │      ──< FloorPlan            (uploaded image + annotations)
    │        └──< PreplanElement ──> Asset   (ordered builder document)
    ├──< Hydrant
    ├──< MapFeature                          (access points, routes, zones, symbols)
    ├──< WmsLayer                            (WMS + XYZ tile overlays)
    ├──< Asset                               (shared library: floor plans/photos/SDS/docs)
    ├──< Announcement
    └──< Deletion                            (offline-sync tombstones)
```

- **`Occupancy`** is the central pre-plan record. It also carries ownership/review
  fields (`created_by`, `status`, `submitted_to_id`) and, via `PreplanElement`, an
  ordered document assembled in the builder.
- **`Asset`** is a *department-wide* reusable file; `PreplanElement` attaches one to a
  pre-plan (or represents a map/inspection element with no file). Deleting an
  occupancy cascades its elements; deleting an asset detaches it from any elements.
- **`Deletion`** is a tombstone so offline clients learn about deletes on their next
  pull. Syncable records (`Occupancy`, `Hydrant`, `MapFeature`, `Contact`, `Hazard`)
  carry a client-generatable `uuid` and an `updated_at` for optimistic concurrency.
- `Department` cascades only to `User`; other dept-scoped rows are cleaned up
  explicitly (see the sandbox purge).

## Multi-tenancy — the one chokepoint

Every top-level record has a `department_id`, and **all reads flow through**
[`app/scoping.py`](../app/scoping.py):

- `dept_query(Model)` → `Model.query.filter_by(department_id=current_user.department_id)`
- `get_owned(Model, id)` → fetch by id, 404 unless it belongs to the caller's dept
- `get_owned_child(Model, id)` → same, via the parent `occupancy_id`

Keeping isolation in one place makes it auditable — a route that forgets scoping is a
visible bug, and there's a cross-tenant isolation test. Writes stamp
`department_id = current_user.department_id`.

## Request paths

1. **Server-rendered pages** (`main`, `auth`) — classic Flask forms that POST back
   and redirect. No JS required for data entry (works on flaky field connections).
2. **Read API** (`api`) — GeoJSON `FeatureCollection`s for the map, plus map-feature
   CRUD used by the drawing tools.
3. **Offline sync** (`sync`) — the local-first path (below).

## Offline-first sync

The map and pre-plan editor are **local-first**: they read/write a client store and
sync in the background, so crews work with no signal.

- **Client store** — [`app/static/js/store.js`](../app/static/js/store.js) is a Dexie
  (IndexedDB) wrapper: per-entity tables, an **outbox** of pending ops (coalesced),
  and sync triggers (online / visibility / manual / periodic). Sign-out **wipes** the
  store.
- **Server** — [`app/sync.py`](../app/sync.py) exposes `POST /api/sync`. A `SYNCABLE`
  dict of `EntitySpec`s declares each entity's fields, parent, and `on_create` hook
  (e.g. stamping `created_by`). One request applies a batch of creates/updates/deletes
  **parent-before-child** (resolving client `uuid`s to server ids), does
  **optimistic-concurrency** conflict detection (a stale `base_updated_at` becomes a
  *conflict*, not an overwrite), and returns a **delta** of everything changed since
  the client's watermark, including `Deletion` tombstones. It is CSRF-exempt (a token
  cached before going offline mustn't expire mid-sync) but still session-authenticated
  and department-scoped.
- **Conflicts** surface on a Conflicts page (`conflicts.js`) as keep-mine /
  keep-theirs.

Not local-first: file uploads (floor plans, library assets) and the server-rendered
pages (dashboard, library, builder) need a connection.

## The asset pipeline

[`app/assets.py`](../app/assets.py) handles uploaded library files:

1. **Naming** — stored as `<id>_<kind>_<slug(title)>.<ext>` under
   `UPLOAD_FOLDER/<dept>/assets/`, served only via an authenticated, ownership-checked
   route (never a static URL).
2. **GPS** — decimal lat/lng parsed from a photo's EXIF (Pillow), so assets are
   findable by location.
3. **HEIC** — iPhone HEIC/HEIF is **transcoded to JPEG** (most browsers can't render
   HEIC in `<img>`), with EXIF orientation baked in and GPS read from the original.
   A corrupt image fails gracefully (flash, no dangling row).
4. **Text search** — a PDF's text layer is extracted inline (`pypdf`); image **OCR**
   is the slow step, so it's **deferred**: uploads set `Asset.ocr_pending` and the
   `flask ocr-pending` task drains the queue later (a no-op without `tesseract`, so
   the queue simply waits for a capable host). `text_content` is indexed for search.

## Background tasks

No always-on worker. Two idempotent CLI commands (registered in the factory) are
wired to cron / a scheduled task via
[`deploy/scheduled_tasks.sh`](../deploy/scheduled_tasks.sh):

- `flask ocr-pending` → `assets.process_pending_ocr()` (commits per asset, so a killed
  run resumes).
- `flask purge-sandboxes` → `sandbox.purge_expired_sandboxes()`.

## Sandboxes (explore-first demo)

[`app/sandbox.py`](../app/sandbox.py): `POST /sandbox` creates a throwaway
`Department(is_sandbox=True)` + a random admin user, seeds it with demo data, and logs
the visitor in — so the full app is reachable with no signup. Sandboxes are isolated
by the normal scoping and **auto-purged** after a TTL (opportunistically on each new
sandbox, plus the scheduled task). Purge deletes all dept-scoped rows in FK-safe order
and removes uploaded files. A `sandbox_forbidden` guard blocks file uploads (the one
real anonymous-abuse vector); `GET /sandbox` just redirects (so crawlers can't spawn
workspaces — creation is a CSRF-protected POST, rate-limited).

## PWA / service worker

[`app/static/sw.js`](../app/static/sw.js) precaches the app shell + vendored libs
(cache-first), serves pages network-first (falling back to cache offline), and runtime-
caches OSM tiles. **Bump `APP_CACHE` when shell JS/CSS changes** so clients refresh. In
development the SW is skipped on `localhost`/`127.0.0.1` (see `base.html`) so edits show
on a plain refresh.

## Security model

- **Auth on everything** except the public landing, the register stub, and sandbox
  creation. Per-department isolation via `scoping.py`. **CSRF** on every form + AJAX
  write (the reorder endpoint sends `X-CSRFToken`); `/api/sync` is the one exemption.
- **File serving** is authenticated + ownership-checked; uploads live outside
  `static/`. Uploads are capped (`MAX_CONTENT_LENGTH`, 5 GB) and images are
  extension-checked.
- **Login rate-limiting** (Flask-Limiter). For multiple workers set
  `RATELIMIT_STORAGE_URI` to Redis so limits are shared.
- **Sandboxes** can't upload files and are purged; `GET /sandbox` never mutates.
- **Known gaps (by design, for now):** sensitive fields (`gate_code`, `alarm_pin`) are
  stored in plaintext; there's no audit log and no "forgot password" email (an admin
  issues a temporary password). Weigh these against your threat model — role-gating the
  most sensitive fields and TLS at the proxy are the first hardening steps.

## Database portability

`config.py` reads `DATABASE_URL` (SQLite default). MySQL gets `pool_recycle` +
`pool_pre_ping` (idle-drop safe); the driver is pure-Python `PyMySQL`. Schema changes
are Alembic migrations — see [DEVELOPMENT.md](DEVELOPMENT.md#migrations).
