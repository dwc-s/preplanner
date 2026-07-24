"""JSON / GeoJSON API.

The map front-end pulls its layers from here and writes drawn features back.
All endpoints are login-gated and department-scoped. Write endpoints are AJAX
and rely on the CSRF token sent in the ``X-CSRFToken`` header (see base.html).
"""
import json

from flask import Blueprint, jsonify, request, abort, url_for
from flask_login import login_required, current_user

from .extensions import db
from .models import Occupancy, Hydrant, MapFeature, WmsLayer, Asset, MAP_FEATURE_CATEGORIES
from .scoping import dept_query, get_owned

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _feature_collection(features):
    return {
        "type": "FeatureCollection",
        "features": [f for f in features if f is not None],
    }


# --- read layers -------------------------------------------------------------

@api_bp.get("/occupancies")
@login_required
def occupancies_geojson():
    """Located occupancies (points) for the current department."""
    features = [o.to_geojson_feature() for o in dept_query(Occupancy).all()]
    return jsonify(_feature_collection(features))


@api_bp.get("/footprints")
@login_required
def footprints_geojson():
    """Building footprint polygons for the current department."""
    features = [o.footprint_feature() for o in dept_query(Occupancy).all()]
    return jsonify(_feature_collection(features))


@api_bp.get("/hydrants")
@login_required
def hydrants_geojson():
    """Hydrants for the current department."""
    features = [h.to_geojson_feature() for h in dept_query(Hydrant).all()]
    return jsonify(_feature_collection(features))


@api_bp.get("/library-locations")
@login_required
def library_locations():
    """Geotagged library assets (photos whose EXIF carried GPS) for the map's
    Library layer. Only assets with coordinates can appear on a map."""
    assets = (dept_query(Asset)
              .filter(Asset.latitude.isnot(None), Asset.longitude.isnot(None)).all())
    return jsonify([{
        "id": a.id, "title": a.title, "kind": a.kind,
        "latitude": a.latitude, "longitude": a.longitude,
        "url": url_for("main.asset_file", asset_id=a.id),
        "is_image": bool(a.content_type and a.content_type.startswith("image/")),
    } for a in assets])


@api_bp.get("/wms-layers")
@login_required
def wms_layers():
    """Enabled WMS overlay configs for the current department."""
    layers = (dept_query(WmsLayer).filter_by(enabled=True)
              .order_by(WmsLayer.name).all())
    return jsonify([layer.to_dict() for layer in layers])


@api_bp.get("/map-features")
@login_required
def map_features_geojson():
    """Drawn features (access points, routes, custom) for the department.
    Optional ?category= filter."""
    query = dept_query(MapFeature)
    category = request.args.get("category")
    if category:
        query = query.filter_by(category=category)
    features = [m.to_geojson_feature() for m in query.all()]
    return jsonify(_feature_collection(features))


# --- write drawn features (AJAX; CSRF via X-CSRFToken header) -----------------

@api_bp.post("/map-features")
@login_required
def map_feature_create():
    data = request.get_json(silent=True) or {}
    geometry = data.get("geometry")
    category = data.get("category")
    if not geometry or category not in MAP_FEATURE_CATEGORIES:
        abort(400)
    mf = MapFeature(
        department_id=current_user.department_id,
        category=category,
        label=(data.get("label") or "").strip() or None,
        geometry_json=json.dumps(geometry),
        color=(data.get("color") or "").strip() or None,
        notes=(data.get("notes") or "").strip() or None,
        created_by=current_user.id,
    )
    db.session.add(mf)
    db.session.commit()
    return jsonify(mf.to_geojson_feature()), 201


@api_bp.put("/map-features/<int:feature_id>")
@login_required
def map_feature_update(feature_id):
    mf = get_owned(MapFeature, feature_id)
    data = request.get_json(silent=True) or {}
    if data.get("geometry"):
        mf.geometry_json = json.dumps(data["geometry"])
    if "label" in data:
        mf.label = (data.get("label") or "").strip() or None
    if "color" in data:
        mf.color = (data.get("color") or "").strip() or None
    if "notes" in data:
        mf.notes = (data.get("notes") or "").strip() or None
    db.session.commit()
    return jsonify(mf.to_geojson_feature())


@api_bp.delete("/map-features/<int:feature_id>")
@login_required
def map_feature_delete(feature_id):
    mf = get_owned(MapFeature, feature_id)
    db.session.delete(mf)
    db.session.commit()
    return jsonify({"status": "ok"})
