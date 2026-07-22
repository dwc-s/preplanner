"""Server-rendered pages: the map, occupancy CRUD, and hydrant CRUD.

Forms POST straight back here (no JS required for data entry, which keeps the
app usable on flaky field connections). The map view is the only page that
relies on the JSON API.

Every route is login-gated and department-scoped via the helpers in scoping.py.
"""
import io
import json
import os
import secrets
from datetime import datetime, timezone

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    abort, send_file, jsonify, current_app
)
from flask_login import login_required, current_user, login_user
from sqlalchemy import or_
from werkzeug.utils import secure_filename

from .extensions import db, limiter
from .models import (
    Department, User, Occupancy, Contact, Hazard, Hydrant, FloorPlan, WmsLayer,
    MapFeature, Announcement, Asset, PreplanElement, MAP_FEATURE_CATEGORIES,
    ASSET_KINDS, PREPLAN_ELEMENT_KINDS, DEFAULT_ELEMENT_SEQUENCE,
)
from .scoping import dept_query, get_owned, get_owned_child
from .auth import admin_required
from .sandbox import sandbox_forbidden, purge_expired_sandboxes
from .assets import save_asset, delete_asset_file, asset_file_path, ALLOWED_ASSET_EXTS, ext_of
from .export import build_preplan_pdf
from . import gis_import

ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "webp", "gif"}
# Standalone GIS files (one file = one dataset) vs. loose Shapefile component
# files (many files = one dataset, grouped by basename).
STANDALONE_GIS_EXTS = {"geojson", "json", "kml", "gpx", "zip"}
SHAPEFILE_PART_EXTS = {"shp", "shx", "dbf", "prj", "cpg", "sbn", "sbx", "xml"}

main_bp = Blueprint("main", __name__)


# --- form parsing helpers ----------------------------------------------------

def _str(form, key):
    val = (form.get(key) or "").strip()
    return val or None


def _int(form, key):
    raw = (form.get(key) or "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def _float(form, key):
    raw = (form.get(key) or "").strip()
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def _bool(form, key):
    return form.get(key) is not None


# Occupancy text fields that map straight from form -> column.
_OCC_STR_FIELDS = [
    "name", "address", "city", "state", "zip_code",
    "occupancy_type", "construction_type", "building_condition",
    "roof_construction", "sprinkler_details", "standpipe_details",
    "fdc_location", "knox_box_location", "gate_code", "alarm_pin",
    "annunciator_location", "electric_shutoff_location",
    "gas_shutoff_location", "water_shutoff_location", "water_supply_notes",
    "hazards_summary", "access_notes", "notes", "footprint_geojson",
]
_OCC_INT_FIELDS = ["stories", "square_footage", "year_built"]
_OCC_FLOAT_FIELDS = ["latitude", "longitude"]
_OCC_BOOL_FIELDS = ["sprinkler_system", "standpipe_system", "fire_alarm_system"]


def _populate_occupancy(occ, form):
    for f in _OCC_STR_FIELDS:
        setattr(occ, f, _str(form, f))
    for f in _OCC_INT_FIELDS:
        setattr(occ, f, _int(form, f))
    for f in _OCC_FLOAT_FIELDS:
        setattr(occ, f, _float(form, f))
    for f in _OCC_BOOL_FIELDS:
        setattr(occ, f, _bool(form, f))


def _is_autosave():
    """True when a request came from the client-side autosave helper (autosave.js).
    Such requests want a JSON {ok} reply and no flash/redirect."""
    return request.headers.get("X-Autosave") == "1"


def _saved_ok():
    return jsonify(ok=True, saved_at=datetime.now(timezone.utc).isoformat())


# --- landing / dashboard / map ----------------------------------------------

@main_bp.get("/")
def index():
    """Logged-out visitors get the public splash; members get their dashboard."""
    if not current_user.is_authenticated:
        return render_template("landing.html")
    dept_id = current_user.department_id
    recent = (dept_query(Occupancy)
              .order_by(Occupancy.created_at.desc()).limit(8).all())
    announcements = (Announcement.query.filter_by(department_id=dept_id)
                     .order_by(Announcement.created_at.desc()).limit(10).all())
    my_preplans = (dept_query(Occupancy).filter_by(created_by=current_user.id)
                   .order_by(Occupancy.updated_at.desc()).all())
    # Pre-plans awaiting this user's review (routed here by the review policy).
    review_queue = (dept_query(Occupancy)
                    .filter_by(submitted_to_id=current_user.id, status="in_review")
                    .order_by(Occupancy.submitted_at.desc()).all())
    return render_template("dashboard.html", recent=recent, announcements=announcements,
                           my_preplans=my_preplans, review_queue=review_queue)


@main_bp.get("/map")
@login_required
def map_view():
    """The interactive map (previously served at /)."""
    return render_template("index.html")


@main_bp.get("/sandbox")
def sandbox_redirect():
    """A bare GET must never create anything — crawlers, link prefetchers, and
    typed URLs would otherwise spawn throwaway departments. The landing CTA POSTs;
    send GETs to the landing instead."""
    return redirect(url_for("main.index"))


@main_bp.post("/sandbox")
@limiter.limit("6 per hour; 30 per day")
def sandbox_start():
    """Spin up a private, throwaway workspace and log the visitor into it so they can
    explore the full app without signing up. Isolated per visitor by the usual
    department scoping and purged after a TTL (see app/sandbox.py)."""
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    try:
        purge_expired_sandboxes()  # opportunistic cleanup; must never block a sandbox
    except Exception:
        db.session.rollback()
    from seed import seed_department  # local import avoids a seed<->app import cycle

    token = secrets.token_hex(4)  # 32-bit: keeps the dept name short; ample given the
    #                              TTL purge keeps the live sandbox count tiny
    dept = Department(name=f"Sandbox {token}", is_sandbox=True)
    db.session.add(dept)
    db.session.flush()  # assign dept.id
    user = User(email=f"sandbox-{token}@sandbox.invalid", name="Sandbox User",
                role="superuser", rank="Chief", department_id=dept.id)
    user.set_password(secrets.token_urlsafe(16))  # random; never surfaced
    db.session.add(user)
    db.session.flush()  # assign user.id so demo pre-plans can be attributed to them
    seed_department(dept, created_by=user)
    db.session.commit()

    login_user(user)  # the persistent sandbox banner (base.html) is the welcome
    return redirect(url_for("main.index"))


@main_bp.get("/conflicts")
@login_required
def conflicts_page():
    """Client-rendered list of unresolved offline-sync conflicts."""
    return render_template("conflicts.html")


# --- occupancies -------------------------------------------------------------

@main_bp.get("/occupancies")
@login_required
def occupancy_list():
    q = (request.args.get("q") or "").strip()
    query = dept_query(Occupancy)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Occupancy.name.ilike(like),
            Occupancy.address.ilike(like),
            Occupancy.city.ilike(like),
        ))
    occupancies = query.order_by(Occupancy.name).all()
    return render_template("occupancy_list.html", occupancies=occupancies, q=q)


@main_bp.route("/occupancies/new", methods=["GET", "POST"])
@login_required
def occupancy_new():
    if request.method == "POST":
        if not _str(request.form, "name"):
            flash("Name is required.", "error")
            return render_template(
                "occupancy_form.html", occupancy=None, form=request.form
            )
        occ = Occupancy(department_id=current_user.department_id,
                        created_by=current_user.id)
        _populate_occupancy(occ, request.form)
        db.session.add(occ)
        db.session.commit()
        flash(f"Created pre-plan for {occ.name}.", "success")
        return redirect(url_for("main.occupancy_detail", occ_id=occ.id))
    return render_template("occupancy_form.html", occupancy=None, form={})


@main_bp.get("/occupancies/edit")
@login_required
def occupancy_editor():
    """Unified local-first occupancy page. Renders an empty shell; occupancy.js
    fills and saves it from the offline store, keyed by ?u=<uuid> or ?new=1."""
    return render_template("occupancy_form.html", occupancy=None, form={}, client_mode=True)


@main_bp.get("/occupancies/<int:occ_id>")
@login_required
def occupancy_detail(occ_id):
    occ = get_owned(Occupancy, occ_id)
    return render_template("occupancy_detail.html", occupancy=occ)


@main_bp.get("/occupancies/<int:occ_id>/export.pdf")
@login_required
def occupancy_export_pdf(occ_id):
    """Download the pre-plan as a formatted PDF with attachments as appendices."""
    occ = get_owned(Occupancy, occ_id)
    pdf = build_preplan_pdf(occ)
    slug = secure_filename((occ.name or "preplan").lower()) or "preplan"
    return send_file(io.BytesIO(pdf), mimetype="application/pdf",
                     as_attachment=True, download_name=f"{slug}_preplan.pdf")


@main_bp.route("/occupancies/<int:occ_id>/edit", methods=["GET", "POST"])
@login_required
def occupancy_edit(occ_id):
    occ = get_owned(Occupancy, occ_id)
    if request.method == "POST":
        if not _str(request.form, "name"):
            if _is_autosave():  # don't wipe the name to blank mid-edit; report it
                return jsonify(ok=False, error="Name can't be empty."), 200
            flash("Name is required.", "error")
            return render_template(
                "occupancy_form.html", occupancy=occ, form=request.form
            )
        _populate_occupancy(occ, request.form)
        db.session.commit()
        if _is_autosave():  # real-time save from the editor — no redirect/flash
            return _saved_ok()
        flash("Pre-plan updated.", "success")
        return redirect(url_for("main.occupancy_detail", occ_id=occ.id))
    return render_template("occupancy_form.html", occupancy=occ, form=occ.__dict__)


@main_bp.post("/occupancies/<int:occ_id>/delete")
@login_required
def occupancy_delete(occ_id):
    occ = get_owned(Occupancy, occ_id)
    name = occ.name
    db.session.delete(occ)
    db.session.commit()
    flash(f"Deleted pre-plan for {name}.", "success")
    return redirect(url_for("main.occupancy_list"))


# --- pre-plan review workflow + department announcements ---------------------

def _can_review(occ):
    """A pre-plan may be actioned by the person it was submitted to, or any superuser."""
    return occ.submitted_to_id == current_user.id or current_user.is_superuser


@main_bp.post("/occupancies/<int:occ_id>/submit-review")
@login_required
def occupancy_submit_review(occ_id):
    """Submit a pre-plan for review, auto-routed by the author's rank and the
    department's officer-review policy (see OFFICER_REVIEW_POLICIES). Non-officers are
    never auto-approved."""
    occ = get_owned(Occupancy, occ_id)
    if occ.created_by != current_user.id:
        abort(403)  # you may only submit your own pre-plan for review
    author = current_user
    dept = author.department
    chief = dept.superuser()

    def _auto_approve():
        occ.status = "approved"
        occ.submitted_to_id = None
        occ.reviewed_by_id = author.id
        occ.reviewed_at = datetime.now(timezone.utc)
        occ.review_note = None

    if author.is_superuser or (author.is_officer
                               and dept.officer_review_policy == "auto_approve"):
        _auto_approve()
    else:
        # Officers under the "chief" policy go to the chief; everyone else (officers
        # under "commanding_officer", and all non-officers) goes to their CO, falling
        # back to the chief.
        if author.is_officer and dept.officer_review_policy == "chief":
            reviewer = chief
        else:
            reviewer = author.commanding_officer or chief
        if reviewer is None or reviewer.id == author.id:
            flash("No reviewer is set up — ask an admin to assign you a commanding "
                  "officer (or designate a chief).", "error")
            return redirect(url_for("main.index"))
        occ.status = "in_review"
        occ.submitted_to_id = reviewer.id
        occ.reviewed_by_id = None
        occ.review_note = None

    occ.submitted_at = datetime.now(timezone.utc)
    db.session.commit()
    if occ.status == "approved":
        flash(f"“{occ.name}” is approved.", "success")
    else:
        who = occ.reviewer.name or occ.reviewer.email
        flash(f"Submitted “{occ.name}” to {who} for review.", "success")
    return redirect(url_for("main.index"))


@main_bp.post("/occupancies/<int:occ_id>/review/approve")
@login_required
def occupancy_review_approve(occ_id):
    occ = get_owned(Occupancy, occ_id)
    if not _can_review(occ):
        abort(403)
    occ.status = "approved"
    occ.reviewed_by_id = current_user.id
    occ.reviewed_at = datetime.now(timezone.utc)
    occ.review_note = None
    db.session.commit()
    flash(f"Approved “{occ.name}”.", "success")
    return redirect(url_for("main.index"))


@main_bp.post("/occupancies/<int:occ_id>/review/request-changes")
@login_required
def occupancy_review_changes(occ_id):
    occ = get_owned(Occupancy, occ_id)
    if not _can_review(occ):
        abort(403)
    occ.status = "needs_changes"
    occ.review_note = _str(request.form, "note")
    occ.reviewed_by_id = current_user.id
    occ.reviewed_at = datetime.now(timezone.utc)
    db.session.commit()
    flash(f"Requested changes on “{occ.name}”.", "success")
    return redirect(url_for("main.index"))


@main_bp.post("/announcements")
@admin_required
def announcement_create():
    body = _str(request.form, "body")
    if not body:
        flash("Announcement text is required.", "error")
    else:
        db.session.add(Announcement(department_id=current_user.department_id,
                                    author_id=current_user.id, body=body))
        db.session.commit()
        flash("Announcement posted.", "success")
    return redirect(url_for("main.index"))


@main_bp.post("/announcements/<int:ann_id>/delete")
@admin_required
def announcement_delete(ann_id):
    ann = Announcement.query.filter_by(
        id=ann_id, department_id=current_user.department_id).first_or_404()
    db.session.delete(ann)
    db.session.commit()
    flash("Announcement removed.", "success")
    return redirect(url_for("main.index"))


# --- contacts (nested under an occupancy) ------------------------------------

@main_bp.post("/occupancies/<int:occ_id>/contacts")
@login_required
def contact_add(occ_id):
    occ = get_owned(Occupancy, occ_id)
    if _str(request.form, "name"):
        db.session.add(Contact(
            occupancy_id=occ.id,
            name=_str(request.form, "name"),
            role=_str(request.form, "role"),
            phone=_str(request.form, "phone"),
            email=_str(request.form, "email"),
            notes=_str(request.form, "notes"),
        ))
        db.session.commit()
        flash("Contact added.", "success")
    else:
        flash("Contact name is required.", "error")
    return redirect(url_for("main.occupancy_detail", occ_id=occ.id))


@main_bp.post("/contacts/<int:contact_id>/delete")
@login_required
def contact_delete(contact_id):
    c = get_owned_child(Contact, contact_id)
    occ_id = c.occupancy_id
    db.session.delete(c)
    db.session.commit()
    flash("Contact removed.", "success")
    return redirect(url_for("main.occupancy_detail", occ_id=occ_id))


# --- hazards (nested under an occupancy) -------------------------------------

@main_bp.post("/occupancies/<int:occ_id>/hazards")
@login_required
def hazard_add(occ_id):
    occ = get_owned(Occupancy, occ_id)
    if _str(request.form, "hazard_type"):
        db.session.add(Hazard(
            occupancy_id=occ.id,
            hazard_type=_str(request.form, "hazard_type"),
            severity=_str(request.form, "severity"),
            location=_str(request.form, "location"),
            description=_str(request.form, "description"),
        ))
        db.session.commit()
        flash("Hazard added.", "success")
    else:
        flash("Hazard type is required.", "error")
    return redirect(url_for("main.occupancy_detail", occ_id=occ.id))


@main_bp.post("/hazards/<int:hazard_id>/delete")
@login_required
def hazard_delete(hazard_id):
    h = get_owned_child(Hazard, hazard_id)
    occ_id = h.occupancy_id
    db.session.delete(h)
    db.session.commit()
    flash("Hazard removed.", "success")
    return redirect(url_for("main.occupancy_detail", occ_id=occ_id))


# --- hydrants ----------------------------------------------------------------

@main_bp.get("/hydrants")
@login_required
def hydrant_list():
    hydrants = dept_query(Hydrant).order_by(Hydrant.id).all()
    return render_template("hydrant_list.html", hydrants=hydrants)


def _populate_hydrant(h, form):
    h.label = _str(form, "label")
    h.latitude = _float(form, "latitude")
    h.longitude = _float(form, "longitude")
    h.flow_gpm = _int(form, "flow_gpm")
    h.static_pressure = _int(form, "static_pressure")
    h.residual_pressure = _int(form, "residual_pressure")
    h.size_inches = _str(form, "size_inches")
    h.hydrant_type = _str(form, "hydrant_type")
    h.in_service = _bool(form, "in_service")
    h.notes = _str(form, "notes")


@main_bp.route("/hydrants/new", methods=["GET", "POST"])
@login_required
def hydrant_new():
    if request.method == "POST":
        lat = _float(request.form, "latitude")
        lon = _float(request.form, "longitude")
        if lat is None or lon is None:
            flash("Latitude and longitude are required.", "error")
            return render_template("hydrant_form.html", form=request.form)
        h = Hydrant(department_id=current_user.department_id)
        _populate_hydrant(h, request.form)
        db.session.add(h)
        db.session.commit()
        flash("Hydrant added.", "success")
        return redirect(url_for("main.hydrant_list"))
    # Support pre-filling coordinates (e.g. from "click map to add hydrant").
    prefill = {
        "latitude": request.args.get("lat", ""),
        "longitude": request.args.get("lon", ""),
        "in_service": "on",
    }
    return render_template("hydrant_form.html", form=prefill)


@main_bp.route("/hydrants/<int:hydrant_id>/edit", methods=["GET", "POST"])
@login_required
def hydrant_edit(hydrant_id):
    h = get_owned(Hydrant, hydrant_id)
    if request.method == "POST":
        lat = _float(request.form, "latitude")
        lon = _float(request.form, "longitude")
        if lat is None or lon is None:
            msg = "Latitude and longitude are required."
            if _is_autosave():  # don't clear coordinates mid-edit
                return jsonify(ok=False, error=msg), 200
            flash(msg, "error")
            return render_template("hydrant_form.html", form=request.form, hydrant=h)
        _populate_hydrant(h, request.form)
        db.session.commit()
        if _is_autosave():
            return _saved_ok()
        flash("Hydrant updated.", "success")
        return redirect(url_for("main.hydrant_list"))
    return render_template("hydrant_form.html", form=h.__dict__, hydrant=h)


@main_bp.post("/hydrants/<int:hydrant_id>/delete")
@login_required
def hydrant_delete(hydrant_id):
    h = get_owned(Hydrant, hydrant_id)
    db.session.delete(h)
    db.session.commit()
    flash("Hydrant removed.", "success")
    return redirect(url_for("main.hydrant_list"))


# --- floor plans (upload + annotation, nested under an occupancy) ------------

def _floorplan_dir(occ):
    return os.path.join(
        current_app.config["UPLOAD_FOLDER"],
        str(occ.department_id), str(occ.id),
    )


@main_bp.post("/occupancies/<int:occ_id>/floorplans")
@login_required
@sandbox_forbidden
def floorplan_upload(occ_id):
    occ = get_owned(Occupancy, occ_id)
    file = request.files.get("image")
    if not file or not file.filename:
        flash("Choose an image file to upload.", "error")
        return redirect(url_for("main.occupancy_detail", occ_id=occ.id))
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_IMAGE_EXT:
        flash("Unsupported file type. Use PNG, JPG, WEBP, or GIF.", "error")
        return redirect(url_for("main.occupancy_detail", occ_id=occ.id))

    fp = FloorPlan(occupancy_id=occ.id,
                   title=_str(request.form, "title") or file.filename)
    db.session.add(fp)
    db.session.flush()  # assign fp.id for a collision-free filename

    filename = f"{fp.id}_{secure_filename(file.filename)}"
    dest_dir = _floorplan_dir(occ)
    os.makedirs(dest_dir, exist_ok=True)
    file.save(os.path.join(dest_dir, filename))
    fp.image_filename = filename
    db.session.commit()
    flash("Floor plan uploaded.", "success")
    return redirect(url_for("main.floorplan_view", fp_id=fp.id))


@main_bp.get("/floorplans/<int:fp_id>")
@login_required
def floorplan_view(fp_id):
    fp = get_owned_child(FloorPlan, fp_id)
    occ = db.session.get(Occupancy, fp.occupancy_id)
    annotations = []
    if fp.annotations_json:
        try:
            annotations = json.loads(fp.annotations_json)
        except ValueError:
            annotations = []
    return render_template("floorplan.html", fp=fp, occupancy=occ,
                           annotations=annotations)


@main_bp.get("/floorplans/<int:fp_id>/image")
@login_required
def floorplan_image(fp_id):
    """Serve the image only to an authenticated owner (never via static URL)."""
    fp = get_owned_child(FloorPlan, fp_id)
    occ = db.session.get(Occupancy, fp.occupancy_id)
    if not fp.image_filename:
        abort(404)
    path = os.path.join(_floorplan_dir(occ), fp.image_filename)
    if not os.path.exists(path):
        abort(404)
    return send_file(path)


@main_bp.post("/floorplans/<int:fp_id>/annotations")
@login_required
def floorplan_annotations(fp_id):
    """Persist the W3C annotation list (AJAX; CSRF via X-CSRFToken header)."""
    fp = get_owned_child(FloorPlan, fp_id)
    data = request.get_json(silent=True)
    if data is None:
        abort(400)
    fp.annotations_json = json.dumps(data)
    db.session.commit()
    return jsonify({"status": "ok", "count": len(data)})


@main_bp.post("/floorplans/<int:fp_id>/delete")
@login_required
def floorplan_delete(fp_id):
    fp = get_owned_child(FloorPlan, fp_id)
    occ = db.session.get(Occupancy, fp.occupancy_id)
    occ_id = occ.id
    if fp.image_filename:
        path = os.path.join(_floorplan_dir(occ), fp.image_filename)
        if os.path.exists(path):
            os.remove(path)
    db.session.delete(fp)
    db.session.commit()
    flash("Floor plan removed.", "success")
    return redirect(url_for("main.occupancy_detail", occ_id=occ_id))


# --- asset library (shared department files) + pre-plan builder --------------

def _dept_sequence():
    """The department's builder element order (admin-defined), validated."""
    raw = (current_user.department.element_sequence or DEFAULT_ELEMENT_SEQUENCE)
    seq = [k for k in raw.split(",") if k in PREPLAN_ELEMENT_KINDS]
    return seq or PREPLAN_ELEMENT_KINDS


def _append_element(occ, kind, asset_id=None, caption=None):
    """Add an element to the end of a pre-plan's ordered element list."""
    last = (PreplanElement.query.filter_by(occupancy_id=occ.id)
            .order_by(PreplanElement.position.desc()).first())
    el = PreplanElement(occupancy_id=occ.id, kind=kind, asset_id=asset_id,
                        caption=caption, position=(last.position + 1) if last else 0)
    db.session.add(el)
    db.session.commit()
    return el


@main_bp.get("/library")
@login_required
def library():
    q = (request.args.get("q") or "").strip()
    kind = request.args.get("kind") or ""
    query = dept_query(Asset)
    if kind in ASSET_KINDS:
        query = query.filter_by(kind=kind)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Asset.title.ilike(like),
                                 Asset.text_content.ilike(like),
                                 Asset.original_name.ilike(like)))
    assets = query.order_by(Asset.uploaded_at.desc()).all()
    return render_template("library.html", assets=assets, q=q, kind=kind,
                           dept_sequence=",".join(_dept_sequence()))


@main_bp.post("/settings/element-sequence")
@admin_required
def element_sequence_set():
    """Admin sets the department's standard builder element order. Robust to typos:
    keeps valid kinds in the given order, de-dupes, then appends any that are missing
    so the sequence always covers every element type."""
    raw = (request.form.get("sequence") or "").replace(" ", "").lower()
    seen, seq = set(), []
    for k in raw.split(","):
        if k in PREPLAN_ELEMENT_KINDS and k not in seen:
            seq.append(k)
            seen.add(k)
    for k in PREPLAN_ELEMENT_KINDS:
        if k not in seen:
            seq.append(k)
    current_user.department.element_sequence = ",".join(seq)
    db.session.commit()
    flash("Standard element order updated.", "success")
    return redirect(url_for("main.library"))


@main_bp.post("/library/upload")
@login_required
@sandbox_forbidden
def library_upload():
    file = request.files.get("file")
    kind = request.form.get("kind")
    if kind not in ASSET_KINDS:
        kind = "document"
    title = _str(request.form, "title")
    occ_id = _int(request.form, "occupancy_id")

    def _retry(msg):
        # Keep the user's kind + title on error (the browser can't re-fill a file
        # input, so the file must be re-picked). Builder uploads return to the
        # builder; library uploads round-trip the fields as query params the upload
        # form reads back.
        flash(msg, "error")
        if occ_id:
            return redirect(request.referrer or url_for("main.library"))
        return redirect(url_for("main.library", up_kind=kind, up_title=title))

    if not file or not file.filename:
        return _retry("Choose a file to upload.")
    if ext_of(file.filename) not in ALLOWED_ASSET_EXTS:
        return _retry("Unsupported file type. Upload an image (PNG/JPG/…) or a PDF.")
    try:
        asset = save_asset(file, kind, current_user.department_id, current_user.id,
                           title=title)
    except ValueError as exc:  # unreadable image (e.g. corrupt HEIC)
        return _retry(str(exc))
    note = " — location read from the photo" if asset.latitude is not None else ""
    flash(f"Uploaded “{asset.title}”{note}.", "success")
    # Uploaded from the builder? Attach it straight onto that pre-plan (only the
    # kinds the builder places — a plain "document" just lands in the library).
    if occ_id and kind in ("floorplan", "photo", "sds"):
        occ = get_owned(Occupancy, occ_id)
        _append_element(occ, kind, asset_id=asset.id)
        return redirect(url_for("main.builder", occ_id=occ.id))
    return redirect(url_for("main.library", kind=kind))


@main_bp.get("/library/<int:asset_id>/file")
@login_required
def asset_file(asset_id):
    """Serve a library file to an authenticated owner (never via a static URL)."""
    asset = get_owned(Asset, asset_id)
    path = asset_file_path(asset)
    if not asset.filename or not os.path.exists(path):
        abort(404)
    return send_file(path)


@main_bp.post("/library/<int:asset_id>/delete")
@login_required
def asset_delete(asset_id):
    asset = get_owned(Asset, asset_id)
    # Detach it from any pre-plans that use it, then remove the file + row.
    PreplanElement.query.filter_by(asset_id=asset.id).delete(synchronize_session=False)
    delete_asset_file(asset)
    db.session.delete(asset)
    db.session.commit()
    flash("Asset removed from the library.", "success")
    return redirect(request.referrer or url_for("main.library"))


@main_bp.get("/occupancies/<int:occ_id>/builder")
@login_required
def builder(occ_id):
    occ = get_owned(Occupancy, occ_id)
    elements = (PreplanElement.query.filter_by(occupancy_id=occ.id)
                .order_by(PreplanElement.position).all())
    library_assets = dept_query(Asset).order_by(Asset.uploaded_at.desc()).all()
    return render_template("builder.html", occupancy=occ, elements=elements,
                           library_assets=library_assets, sequence=_dept_sequence())


@main_bp.post("/occupancies/<int:occ_id>/elements")
@login_required
def element_add(occ_id):
    occ = get_owned(Occupancy, occ_id)
    kind = request.form.get("kind")
    if kind not in PREPLAN_ELEMENT_KINDS:
        flash("Unknown element type.", "error")
        return redirect(url_for("main.builder", occ_id=occ.id))
    if kind in ("floorplan", "photo", "sds"):  # these attach a library asset
        asset_id = _int(request.form, "asset_id")
        if not asset_id:
            flash("Pick an item from the library, or upload one.", "error")
            return redirect(url_for("main.builder", occ_id=occ.id))
        asset = get_owned(Asset, asset_id)
        _append_element(occ, kind, asset_id=asset.id)
    else:  # map / inspection — no attached file
        _append_element(occ, kind, caption=_str(request.form, "caption"))
    return redirect(url_for("main.builder", occ_id=occ.id))


@main_bp.post("/elements/<int:element_id>/delete")
@login_required
def element_delete(element_id):
    el = get_owned_child(PreplanElement, element_id)
    occ_id = el.occupancy_id
    db.session.delete(el)
    db.session.commit()
    return redirect(url_for("main.builder", occ_id=occ_id))


@main_bp.post("/elements/<int:element_id>/caption")
@login_required
def element_caption(element_id):
    """Edit a builder element's caption in place (autosaved from builder.html)."""
    el = get_owned_child(PreplanElement, element_id)
    el.caption = _str(request.form, "caption")
    db.session.commit()
    if _is_autosave():
        return _saved_ok()
    return redirect(url_for("main.builder", occ_id=el.occupancy_id))


@main_bp.post("/occupancies/<int:occ_id>/elements/reorder")
@login_required
def elements_reorder(occ_id):
    """Persist a new element order (drag-and-drop; JSON list of element ids)."""
    occ = get_owned(Occupancy, occ_id)
    ids = (request.get_json(silent=True) or {}).get("order") or []
    elements = {e.id: e for e in PreplanElement.query.filter_by(occupancy_id=occ.id)}
    pos = 0
    for raw in ids:
        el = elements.get(raw if isinstance(raw, int) else None)
        if el:
            el.position = pos
            pos += 1
    db.session.commit()
    return jsonify(status="ok", count=pos)


# --- map layers: WMS overlays + GIS import (admin) ---------------------------

MAX_IMPORT_FEATURES = 2000


# One-click XYZ tile basemaps (topo / imagery / hillshade). All public, no API
# key, EPSG:3857 (Leaflet default). Verified live over Massachusetts.
TILE_PRESETS = {
    "usgs_topo": {
        "name": "USGS Topo",
        "url": "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}",
        "attribution": "USGS The National Map", "max_zoom": 16, "opacity": 1.0},
    "usgs_imagery": {
        "name": "USGS Imagery",
        "url": "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}",
        "attribution": "USGS The National Map", "max_zoom": 16, "opacity": 1.0},
    "usgs_imagery_topo": {
        "name": "USGS Imagery + Topo",
        "url": "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryTopo/MapServer/tile/{z}/{y}/{x}",
        "attribution": "USGS The National Map", "max_zoom": 16, "opacity": 1.0},
    "esri_imagery": {
        "name": "Esri World Imagery",
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Esri, Maxar, Earthstar Geographics", "max_zoom": 19, "opacity": 1.0},
    "esri_hillshade": {
        "name": "Terrain Hillshade",
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Esri", "max_zoom": 16, "opacity": 0.5},
    "opentopomap": {
        "name": "OpenTopoMap",
        "url": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        "attribution": "© OpenTopoMap (CC-BY-SA)", "max_zoom": 17, "opacity": 1.0},
}


@main_bp.get("/overlays")
@admin_required
def overlays():
    wms = dept_query(WmsLayer).order_by(WmsLayer.kind, WmsLayer.name).all()
    return render_template("overlays.html", wms_layers=wms, tile_presets=TILE_PRESETS)


@main_bp.post("/overlays")
@admin_required
def overlay_add():
    name = _str(request.form, "name")
    url = _str(request.form, "url")
    layers = _str(request.form, "layers")
    if not (name and url and layers):
        flash("Name, URL, and layer names are required.", "error")
        return redirect(url_for("main.overlays"))
    opacity = _float(request.form, "opacity")
    db.session.add(WmsLayer(
        department_id=current_user.department_id,
        name=name, url=url, layers=layers,
        image_format=_str(request.form, "image_format") or "image/png",
        transparent=request.form.get("transparent") is not None,
        opacity=opacity if opacity is not None else 0.7,
        enabled=True,
    ))
    db.session.commit()
    flash(f"Added overlay “{name}”.", "success")
    return redirect(url_for("main.overlays"))


@main_bp.post("/overlays/add-bulk")
@admin_required
def overlay_add_bulk():
    """Add several WMS overlays at once from the layer picker (JSON POST).

    The browser reads the WMS server's GetCapabilities directly (client-side, so
    the app itself makes no outbound call — this keeps it runnable on hosts that
    restrict server-side networking). It posts the chosen layers here; each layer
    becomes its own toggleable overlay so crews can turn them on independently.
    """
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    layers = data.get("layers")
    if not url or not isinstance(layers, list) or not layers:
        return jsonify(error="A WMS URL and at least one layer are required."), 400

    existing = {(w.url, w.layers) for w in dept_query(WmsLayer).all()}
    added = 0
    for item in layers:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name or (url, name) in existing:
            continue
        title = (item.get("title") or name).strip() or name
        db.session.add(WmsLayer(
            department_id=current_user.department_id,
            name=title[:120], url=url[:500], layers=name[:300],
            image_format="image/png", transparent=True, opacity=0.7, enabled=True,
        ))
        existing.add((url, name))
        added += 1
    db.session.commit()
    return jsonify(added=added)


@main_bp.post("/overlays/tiles")
@admin_required
def overlay_add_tiles():
    """Add an XYZ tile basemap — either a built-in preset or a custom template."""
    preset_key = _str(request.form, "preset")
    if preset_key:
        preset = TILE_PRESETS.get(preset_key)
        if not preset:
            flash("Unknown basemap.", "error")
            return redirect(url_for("main.overlays"))
        name, url = preset["name"], preset["url"]
        attribution, max_zoom, opacity = preset["attribution"], preset["max_zoom"], preset["opacity"]
    else:
        name = _str(request.form, "name")
        url = _str(request.form, "url")
        if not name or "{z}" not in url or "{x}" not in url or "{y}" not in url:
            flash("A name and an XYZ URL template with {z}/{x}/{y} are required.", "error")
            return redirect(url_for("main.overlays"))
        attribution = _str(request.form, "attribution") or None
        max_zoom = _int(request.form, "max_zoom")
        opacity = _float(request.form, "opacity")
        opacity = opacity if opacity is not None else 1.0

    if dept_query(WmsLayer).filter_by(url=url).first():
        flash(f"“{name}” is already added.", "error")
        return redirect(url_for("main.overlays"))
    db.session.add(WmsLayer(
        department_id=current_user.department_id, kind="xyz",
        name=name[:120], url=url[:500], layers="",
        opacity=opacity, attribution=(attribution or None), max_zoom=max_zoom, enabled=True,
    ))
    db.session.commit()
    flash(f"Added basemap “{name}”.", "success")
    return redirect(url_for("main.overlays"))


@main_bp.post("/overlays/<int:layer_id>/delete")
@admin_required
def overlay_delete(layer_id):
    layer = get_owned(WmsLayer, layer_id)
    db.session.delete(layer)
    db.session.commit()
    flash("Overlay removed.", "success")
    return redirect(url_for("main.overlays"))


@main_bp.post("/overlays/import")
@admin_required
@sandbox_forbidden
def gis_import_upload():
    uploads = [f for f in request.files.getlist("files") if f and f.filename]
    if not uploads:  # backward-compat with the old single-file field name
        one = request.files.get("file")
        if one and one.filename:
            uploads = [one]
    if not uploads:
        flash("Choose a file to import.", "error")
        return redirect(url_for("main.overlays"))

    # Optional clip-to-area: only import features intersecting this WGS84 box,
    # which keeps statewide files down to the department's own area.
    bbox, clipped = None, False
    if request.form.get("clip"):
        try:
            lats = (float(request.form["min_lat"]), float(request.form["max_lat"]))
            lons = (float(request.form["min_lon"]), float(request.form["max_lon"]))
        except (KeyError, ValueError, TypeError):
            flash("“Clip to area” is on but the area is missing or invalid — open the "
                  "Map and position it over your area first, then try again.", "error")
            return redirect(url_for("main.overlays"))
        bbox = (min(lons), min(lats), max(lons), max(lats))
        clipped = True

    # Split loose Shapefile parts (grouped by basename so several shapefiles can
    # be uploaded at once) from standalone GeoJSON/KML/GPX/zip files.
    shp_groups = {}   # basename -> {ext: bytes}
    standalone = []   # (filename, bytes)
    bad = []
    for f in uploads:
        base, _, ext = f.filename.rpartition(".")
        ext = ext.lower()
        if ext in SHAPEFILE_PART_EXTS:
            shp_groups.setdefault(base.lower(), {})[ext] = f.read()
        elif ext in STANDALONE_GIS_EXTS:
            standalone.append((f.filename, f.read()))
        else:
            bad.append(f.filename)
    if bad:
        flash("Unsupported file(s): " + ", ".join(bad) + ". Use GeoJSON, KML, GPX, a "
              "zipped Shapefile, or loose Shapefile parts (.shp with .dbf/.shx/.prj).",
              "error")
        return redirect(url_for("main.overlays"))

    features, errors = [], []
    for base, parts in shp_groups.items():
        if "shp" not in parts:
            continue  # e.g. a stray .prj/.xml with no geometry — ignore quietly
        try:
            features += gis_import.parse_shapefile_parts(parts, bbox=bbox,
                                                          limit=MAX_IMPORT_FEATURES)
        except Exception as exc:
            errors.append(f"{base}.shp: {exc}")
    for name, raw in standalone:
        try:
            features += gis_import.parse_upload(name, raw, bbox=bbox)
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    total = len(features)
    features = features[:MAX_IMPORT_FEATURES]
    for feat in features:
        category = feat["category"]
        if category not in MAP_FEATURE_CATEGORIES:
            category = "Custom"
        db.session.add(MapFeature(
            department_id=current_user.department_id,
            category=category,
            label=feat.get("label"),
            geometry_json=json.dumps(feat["geometry"]),
            created_by=current_user.id,
        ))
    db.session.commit()

    n = len(features)
    where = " within your map area" if clipped else ""
    capped = " (capped at %d of %d found)" % (n, total) if total > MAX_IMPORT_FEATURES else ""
    if n and errors:
        flash(f"Imported {n} feature(s){where}{capped}; some files couldn't be read: "
              + "; ".join(errors), "success")
    elif n:
        flash(f"Imported {n} feature(s){where}{capped} — see them on the map.", "success")
    elif errors:
        flash("Could not import: " + "; ".join(errors), "error")
    elif clipped:
        flash("No features fell within your map area. Try zooming out a bit, then re-import.",
              "error")
    else:
        flash("No importable features found.", "error")
    return redirect(url_for("main.overlays"))
