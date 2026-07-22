# User Guide

For fire-department admins and members using Pre-Planner. (Setting up a server?
See [DEPLOYMENT.md](DEPLOYMENT.md).)

## Roles

- **Admin** — manages the department: adds crew, sets ranks, posts announcements,
  configures overlays/GIS import and the builder's element order.
- **Member** — creates and edits pre-plans, uses the map and asset library, submits
  pre-plans for review.

There is **no public sign-up**. Admins add crew on the **Users** page; a new
department's first admin is created by whoever runs the server (`flask create-admin`).

## Just looking? Try the sandbox

From the logged-out landing page, click **Try the sandbox** for a private, throwaway
demo workspace — the whole app, pre-loaded with sample data, no account needed. It's
isolated from real departments and is deleted automatically after a while. Uploading
files is disabled in the sandbox.

## Signing in & the dashboard

Sign in at `/login`. Your home is the **dashboard**:

- **Your pre-plans** — a sortable table (click a column header) of the pre-plans you
  authored, with each one's **status** and, if submitted, its **reviewer**. Each row
  has a **Submit for review** action.
- **Announcements** — notices from your department's admins. Admins get a box to post
  one.
- **Recent activity** — the newest pre-plans across your department.

The top nav gets you to the **Map**, **Occupancies**, **Hydrants**, **Library**,
**Roster**, and **+ New Pre-Plan** (admins also see **Users** and **Layers**).

## Pre-plans (occupancies)

**Create:** *+ New Pre-Plan*. Fill in identification, classification, construction,
fire-protection systems, access & security (Knox Box, gate/alarm codes), utility
shutoffs, water supply, and notes. Set the location by clicking the mini-map or
entering coordinates; draw a **building footprint** right on the form. The editor's
map also shows your department's **hydrants** (toggleable) so water supply is visible
while you plan.

**Hazards & contacts** live on the pre-plan and are **edited inline** — add, edit, or
remove without leaving the page.

**Floor plans:** on a pre-plan, upload an image and **annotate** it — draw rectangles
or polygons over hazards, shutoffs, the Knox Box, etc.

**Search:** the Occupancies page filters by name, address, or city.

## Building a pre-plan document

Open a pre-plan and click **Build** for the drag-and-drop builder. The left palette
lists element types in your department's standard order:

- **Map** — links the interactive map into the pre-plan.
- **Floor Plans / Photos / SDS** — add *from the library* or *upload* a new file
  (it's added to the library and attached in one step). SDS also has a **Search
  chemicalsafety.com** link (opens in a new tab — their site can't be embedded).
- **Inspection reports** — a placeholder noting where they live (external software).

Added elements appear on the right as an ordered document; **drag** them to reorder.
The order is saved automatically.

## Asset library

**Library** (top nav) is your department's shared files — floor plans, photos, SDS,
documents — reusable across pre-plans.

- **Upload** an image or PDF (incl. **iPhone HEIC**, converted automatically). A
  photo's **GPS** is read from the file; PDFs are indexed for **text search**
  immediately, and photos are OCR'd for search **shortly after upload** (a background
  job).
- **Find** files with the search box (matches titles *and* extracted text) and the
  kind filters. A 📍 marks files that carry a location.
- **Admins** set the **standard element order** the builder uses (the palette
  order), at the top of the Library page.

## The map

**Map** shows occupancies, footprints, hydrants, access points, routes, and custom
zones as toggleable layers.

- **Draw** access points, routes, and zones with the toolbar (polygons/lines finish
  on double-click).
- **Symbols** — the palette (🚩) places fire-service symbols (FDC, Knox, gas/electric/
  water shutoffs, hazmat, hazard, command post, staging, water supply, and rotatable
  arrows). Symbols and their labels are drag-to-move; arrows have on-hover rotate /
  resize / lengthen controls.
- **Ruler** — measure ground distance in feet/miles.
- **Hydrants** — click the 💧 tool, then the map, to place one (colour = NFPA 291
  flow class).
- The map **reopens where you last left it**.

**Basemaps & overlays** (admins, **Layers** page):

- One-click **tiled basemaps** — USGS topo/imagery, terrain hillshade, OpenTopoMap —
  sit under your data.
- **WMS overlays** — paste a WMS server URL and **pick from its live layer list**;
  each becomes a toggleable overlay.
- **GIS import** — upload **GeoJSON / KML / GPX / Shapefiles** (a zip or the loose
  `.shp`+`.dbf`/`.shx`/`.prj` parts). Projected shapefiles are reprojected to WGS84
  automatically. Tick **clip to area** to import only what falls in your current map
  view.

## Hydrants

The **Hydrants** page lists all department hydrants; add flow (GPM), static/residual
pressure, size, type, and in-service status. Flow-class colours follow NFPA 291.

## Review workflow

On the dashboard, **Submit for review** on one of your pre-plans and pick a reviewer
(another department member). Its status becomes **In review** and shows who it went
to. *(Reviewer notifications and approve / request-changes are on the roadmap; today
submission records the request.)*

## Roster & ranks

Everyone can open the **Roster** — the department by name and rank, most senior first.
Admins set each member's **rank** (Chief → Probationary Firefighter) on the Users page.

## Working offline

Add the site to your phone's home screen (it's an installable **PWA**). The map and
pre-plans work with **no signal** — draw features, edit fields, add hazards/contacts.
Changes queue and **sync** when you reconnect. If two people edited the same record,
a **Conflicts** page lets you keep yours or theirs. (New *file uploads* still need a
connection.) Signing out clears the offline copy from the device.

## Admin tasks

- **Users** — add crew (email + temporary password), set roles and ranks, deactivate
  accounts, issue a temporary-password reset.
- **Announcements** — post/remove department notices from the dashboard.
- **Layers** — manage basemaps, WMS overlays, and GIS import.
- **Library** — set the builder's standard element order.

## Your account

**Account** (your name in the nav) — change your password.
