# Changelog

All notable changes to Pre-Planner are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Pre-Planner has not yet cut a numbered release — everything below is on the
`main` branch and considered unreleased.

## [Unreleased]

### Added

**Pre-planning core**
- Occupancy pre-plans — create, edit, search, delete: construction type,
  condition, fire-protection systems, Knox Box, gate/alarm codes, annunciator,
  utility shutoffs, water supply, hazards, contacts, and notes.
- Interactive Leaflet map — occupancies, footprints, hydrants, access points,
  routes, and custom zones as toggleable layers. Draw with Leaflet-Geoman, click
  to place hydrants (NFPA 291 flow-class colours), draw a footprint on the form.
- Map annotation tools — a **placeable fire-service symbol palette** (FDC, Knox
  box, standpipe, gas/electric/water shutoffs, hazmat, command post, staging…)
  whose symbols save as map features, and a **distance ruler** (measures in feet /
  miles).
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
- New WMS overlays default to transparent, and the form clarifies that the layers
  field takes the server's WMS *layer name* (not a display label).
- In development (`localhost`/`127.0.0.1`) the service worker is skipped so CSS/JS
  edits appear on a plain refresh; production still gets full offline/PWA caching.

### Fixed
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
