# Changelog

All notable changes to Pre-Planner are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Pre-Planner has not yet cut a numbered release — everything below is on the
`main` branch and considered unreleased.

## [Unreleased]

### Added

**Public landing & explore-first sandbox**
- A public **splash / landing page** at `/` for logged-out visitors — the
  Pre-Planner logo, a short "about" blurb, and clear calls to action (try the
  sandbox, sign in, sign up). Signed-in members still get the map at `/`.
- A one-click **sandbox** so anyone can try the full app before signing up: it
  spins up a private, pre-seeded workspace, signs the visitor in, and lets them
  create pre-plans, draw on the map, add hydrants and symbols, and more. Each
  visitor is isolated by the usual per-department scoping and the workspace is
  auto-purged after a TTL (opportunistically, plus a `flask purge-sandboxes` CLI
  for cron). File uploads (floor-plan images, GIS imports) are disabled in the
  sandbox to avoid hosting anonymous uploads; everything else is usable. A banner
  marks sandbox mode and links to sign-up.
- A **sign-up** entry point (`/register`) — a placeholder for now while
  departments are still onboarded manually.

**Pre-planning core**
- Occupancy pre-plans — create, edit, search, delete: construction type,
  condition, fire-protection systems, Knox Box, gate/alarm codes, annunciator,
  utility shutoffs, water supply, hazards, contacts, and notes.
- Interactive Leaflet map — occupancies, footprints, hydrants, access points,
  routes, and custom zones as toggleable layers. Draw with Leaflet-Geoman, click
  to place hydrants (NFPA 291 flow-class colours), draw a footprint on the form.
- Map annotation tools — a **placeable fire-service symbol palette** (FDC, Knox
  box, standpipe, gas/electric/water shutoffs, hazmat, hazard, command post,
  staging…) whose symbols save as map features, **directional arrows** (solid /
  line / double styles) with on-hover controls to **rotate, resize and lengthen**
  them plus an always-visible label, and a **distance ruler** (feet / miles).
  Symbols are **drag-to-move**, and each symbol's label is a **separately
  draggable** tag that otherwise follows its symbol.
- Hazards & contacts on a pre-plan are **editable inline** (not just add/delete),
  and the occupancy editor shows the department's **hydrants** on its map
  (toggleable), so water supply is visible while planning a building.
- Floor plans — upload images and annotate them (rectangles/polygons) with
  Annotorious; images served only through an authenticated, ownership-checked
  route.

**Accounts & security**
- Session login (Flask-Login) with per-department multi-tenancy enforced through
  a single scoping chokepoint; admin-managed users; no public sign-up.
- **Ranks & roster** — assign fire-service ranks (Chief → Probationary
  Firefighter) on the Users page; a member-facing **Roster** lists the department
  by name and rank, ordered by seniority.
- Login rate-limiting, self-service password change, admin temporary-password
  reset, and CSRF protection on every write.

**Basemaps, overlays & GIS**
- One-click tiled basemaps — USGS Topo / Imagery / Imagery+Topo, Esri World
  Imagery, terrain hillshade, OpenTopoMap — plus a custom `{z}/{x}/{y}` option,
  rendered under the pre-plan data. The lightweight way to get raster context
  (topo, aerial, terrain) without importing gigabytes of LiDAR/imagery.
- WMS overlays with a layer picker: paste a server URL and choose from its live
  layer list — no need to know layer names. Failed tile loads surface a message
  on the map instead of failing silently.
- GIS import — GeoJSON / KML / GPX and Shapefiles, either zipped or as loose
  parts (`.shp` + `.dbf`/`.shx`/`.prj`, several at once). Projected shapefiles are
  auto-reprojected to WGS84 from their `.prj` (via pyproj); no system GDAL.
- Clip-to-area import — limit an import to your current map area so a statewide
  file brings in just the local subset (with efficient streaming for large files).
- The map reopens where you last left it (per department), which also becomes the
  default import clip area.

**Offline / PWA**
- Installable PWA with a local-first store (Dexie / IndexedDB): view **and** edit
  pre-plans and map features offline. Changes queue in an outbox and sync via
  `/api/sync` on reconnect, with optimistic-concurrency conflict resolution
  (keep-mine / keep-theirs). Signing out wipes the local store.

**Deployment**
- SQLite by default; MySQL or PostgreSQL via `DATABASE_URL`. Guided
  PythonAnywhere + MySQL installer and walkthrough. Runs on modest/free hosting —
  pure-Python, vendored front-end libraries, no system GDAL.

### Changed
- Polylines and polygons now **finish on double-click** — the instinctive gesture.
  Previously a double-click fell through to the map's zoom, so the map lurched to
  full zoom mid-draw and the shape appeared to vanish; you had to click the first
  vertex or the Finish button instead. Double-click zoom still works when not
  drawing.
- New WMS overlays default to transparent, and the form clarifies that the layers
  field takes the server's WMS *layer name* (not a display label).
- In development (`localhost`/`127.0.0.1`) the service worker is skipped so CSS/JS
  edits appear on a plain refresh; production still gets full offline/PWA caching.

### Fixed
- Drawing a polygon or line can no longer crash the map. Leaflet-Geoman keeps a
  hidden "Finish" control for every draw tool; triggering one for a tool that was
  never started left its working layer undefined and threw deep inside Geoman
  (`Cannot read properties of undefined (reading 'getLatLngs')`), taking the whole
  map down. The finish path now treats a missing working layer as
  nothing-to-finish.
- Tiled basemap overlays no longer raise the map's max zoom past the base map's
  limit (which had left the street base blank when zoomed in far).
- A single malformed/null record in a shapefile no longer aborts the whole import.
- WMS overlays added through the form are no longer forced opaque (a missing form
  field previously saved every overlay non-transparent, hiding the base map).
- The import "capped" notice now appears only when features were actually
  truncated, and reports how many of how many were found.
- A corrupt/partial saved map view is ignored instead of breaking map startup.

### Security
- Licensed under **AGPL-3.0** — modifications offered over a network must publish
  their source.
- The local `GIS Data/` working folder (large rasters/shapefiles) is gitignored.

[Unreleased]: https://github.com/dwc-s/preplanner/commits/main
