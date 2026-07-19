"""Server-rendered pages: the map, occupancy CRUD, and hydrant CRUD.

Forms POST straight back here (no JS required for data entry, which keeps the
app usable on flaky field connections). The map view is the only page that
relies on the JSON API.

Every route is login-gated and department-scoped via the helpers in scoping.py.
"""
import json
import os

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    abort, send_file, jsonify, current_app
)
from flask_login import login_required, current_user
from sqlalchemy import or_
from werkzeug.utils import secure_filename

from .extensions import db
from .models import (
    Occupancy, Contact, Hazard, Hydrant, FloorPlan, WmsLayer, MapFeature,
    MAP_FEATURE_CATEGORIES,
)
from .scoping import dept_query, get_owned, get_owned_child
from .auth import admin_required
from . import gis_import

ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "webp", "gif"}
ALLOWED_GIS_EXT = {"geojson", "json", "kml", "gpx", "zip", "shp"}

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


# --- map ---------------------------------------------------------------------

@main_bp.get("/")
@login_required
def index():
    return render_template("index.html")


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
        occ = Occupancy(department_id=current_user.department_id)
        _populate_occupancy(occ, request.form)
        db.session.add(occ)
        db.session.commit()
        flash(f"Created pre-plan for {occ.name}.", "success")
        return redirect(url_for("main.occupancy_detail", occ_id=occ.id))
    return render_template("occupancy_form.html", occupancy=None, form={})


@main_bp.get("/occupancies/<int:occ_id>")
@login_required
def occupancy_detail(occ_id):
    occ = get_owned(Occupancy, occ_id)
    return render_template("occupancy_detail.html", occupancy=occ)


@main_bp.route("/occupancies/<int:occ_id>/edit", methods=["GET", "POST"])
@login_required
def occupancy_edit(occ_id):
    occ = get_owned(Occupancy, occ_id)
    if request.method == "POST":
        if not _str(request.form, "name"):
            flash("Name is required.", "error")
            return render_template(
                "occupancy_form.html", occupancy=occ, form=request.form
            )
        _populate_occupancy(occ, request.form)
        db.session.commit()
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


@main_bp.route("/hydrants/new", methods=["GET", "POST"])
@login_required
def hydrant_new():
    if request.method == "POST":
        lat = _float(request.form, "latitude")
        lon = _float(request.form, "longitude")
        if lat is None or lon is None:
            flash("Latitude and longitude are required.", "error")
            return render_template("hydrant_form.html", form=request.form)
        db.session.add(Hydrant(
            department_id=current_user.department_id,
            label=_str(request.form, "label"),
            latitude=lat,
            longitude=lon,
            flow_gpm=_int(request.form, "flow_gpm"),
            static_pressure=_int(request.form, "static_pressure"),
            residual_pressure=_int(request.form, "residual_pressure"),
            size_inches=_str(request.form, "size_inches"),
            hydrant_type=_str(request.form, "hydrant_type"),
            in_service=_bool(request.form, "in_service"),
            notes=_str(request.form, "notes"),
        ))
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


# --- map layers: WMS overlays + GIS import (admin) ---------------------------

MAX_IMPORT_FEATURES = 2000


@main_bp.get("/overlays")
@admin_required
def overlays():
    wms = dept_query(WmsLayer).order_by(WmsLayer.name).all()
    return render_template("overlays.html", wms_layers=wms)


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
def gis_import_upload():
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Choose a file to import.", "error")
        return redirect(url_for("main.overlays"))
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_GIS_EXT:
        flash("Unsupported file. Use GeoJSON, KML, GPX, or a zipped Shapefile.", "error")
        return redirect(url_for("main.overlays"))
    try:
        features = gis_import.parse_upload(file.filename, file.read())
    except Exception as exc:
        flash(f"Could not parse file: {exc}", "error")
        return redirect(url_for("main.overlays"))

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
    if features:
        flash(f"Imported {len(features)} feature(s) — see them on the map.", "success")
    else:
        flash("No importable features found in that file.", "error")
    return redirect(url_for("main.overlays"))
