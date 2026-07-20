"""Offline sync endpoint.

`POST /api/sync` is the single door between the browser's local IndexedDB store
(see app/static/js/store.js) and the server. A client sends its queued offline
edits ("ops") plus the timestamp of its last successful sync; the server:

  1. applies the ops (create/update/delete), parents before children, resolving
     cross-entity references by uuid,
  2. uses **optimistic concurrency** — an update/delete whose ``base_updated_at``
     is older than the server row's ``updated_at`` is a *conflict*, returned for
     the user to resolve (keep-mine / keep-theirs) rather than silently applied,
  3. returns the delta of everything changed since the client's last sync, plus
     tombstones (deletions), so the client can update its local store.

Timestamps are normalized to naive-UTC on the wire; the server clock is the sole
authority, so no hybrid logical clocks are needed.
"""
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user

from .extensions import db
from .models import (
    Occupancy, Contact, Hazard, Hydrant, MapFeature, Deletion,
)

sync_bp = Blueprint("sync", __name__)


# --- entity registry ---------------------------------------------------------

class EntitySpec:
    def __init__(self, model, fields, parent=None, parent_fk=None,
                 parent_required=False, on_create=None):
        self.model = model
        self.fields = fields              # client-settable columns (whitelist)
        self.parent = parent              # parent entity name, or None
        self.parent_fk = parent_fk        # FK column, e.g. "occupancy_id"
        self.parent_required = parent_required
        self.on_create = on_create        # callable(row) for server-set fields

    @property
    def has_dept(self):
        return hasattr(self.model, "department_id")


_OCC_FIELDS = [
    "name", "address", "city", "state", "zip_code", "latitude", "longitude",
    "footprint_geojson", "occupancy_type", "construction_type", "stories",
    "square_footage", "year_built", "building_condition", "roof_construction",
    "sprinkler_system", "sprinkler_details", "standpipe_system",
    "standpipe_details", "fire_alarm_system", "fdc_location", "knox_box_location",
    "gate_code", "alarm_pin", "annunciator_location", "electric_shutoff_location",
    "gas_shutoff_location", "water_shutoff_location", "water_supply_notes",
    "hazards_summary", "access_notes", "notes",
]


def _set_created_by(row):
    row.created_by = current_user.id


SYNCABLE = {
    "occupancy": EntitySpec(Occupancy, _OCC_FIELDS),
    "hydrant": EntitySpec(Hydrant, [
        "label", "latitude", "longitude", "flow_gpm", "static_pressure",
        "residual_pressure", "size_inches", "hydrant_type", "in_service", "notes",
    ]),
    "map_feature": EntitySpec(
        MapFeature, ["category", "symbol", "rotation", "scale", "length",
                     "label", "label_lat", "label_lng", "geometry_json", "color", "notes"],
        parent="occupancy", parent_fk="occupancy_id", on_create=_set_created_by),
    "contact": EntitySpec(
        Contact, ["name", "role", "phone", "email", "notes"],
        parent="occupancy", parent_fk="occupancy_id", parent_required=True),
    "hazard": EntitySpec(
        Hazard, ["hazard_type", "severity", "location", "description"],
        parent="occupancy", parent_fk="occupancy_id", parent_required=True),
}

# Apply order guarantees parents (occupancy) exist before children reference them.
APPLY_ORDER = ["occupancy", "hydrant", "map_feature", "contact", "hazard"]


# --- timestamp helpers (naive-UTC everywhere) --------------------------------

def _naive_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _iso(dt):
    dt = _naive_utc(dt)
    return dt.isoformat() if dt else None


def _parse(s):
    if not s:
        return None
    try:
        return _naive_utc(datetime.fromisoformat(s))
    except (ValueError, TypeError):
        return None


# --- scoping / serialization -------------------------------------------------

def _dept_query(spec, dept):
    """Base query for an entity restricted to the department (children scope
    through their parent occupancy)."""
    if spec.has_dept:
        return spec.model.query.filter(spec.model.department_id == dept)
    return (spec.model.query
            .join(Occupancy, getattr(spec.model, spec.parent_fk) == Occupancy.id)
            .filter(Occupancy.department_id == dept))


def _get_row(spec, uuid, dept):
    row = spec.model.query.filter_by(uuid=uuid).first()
    if row is None:
        return None
    if spec.has_dept:
        return row if row.department_id == dept else None
    occ = db.session.get(Occupancy, getattr(row, spec.parent_fk))
    return row if (occ and occ.department_id == dept) else None


def _serialize(entity, row, id2uuid):
    spec = SYNCABLE[entity]
    out = {"uuid": row.uuid, "id": row.id, "updated_at": _iso(row.updated_at)}
    for f in spec.fields:
        out[f] = getattr(row, f)
    if spec.parent:
        out["parent_uuid"] = id2uuid.get(getattr(row, spec.parent_fk))
    return out


# --- apply a single op -------------------------------------------------------

def _apply_one(entity, op, dept, uuid2occid, id2uuid, applied, conflicts):
    spec = SYNCABLE[entity]
    kind = op.get("op")
    uuid = op.get("uuid")
    if not uuid or kind not in ("create", "update", "delete"):
        return
    data = op.get("data") or {}
    base = _parse(op.get("base_updated_at"))
    row = _get_row(spec, uuid, dept)

    if kind == "create":
        if row is not None:  # idempotent retry — already created
            applied.append({"entity": entity, "uuid": uuid, "id": row.id,
                            "updated_at": _iso(row.updated_at)})
            return
        row = spec.model(uuid=uuid)
        if spec.has_dept:
            row.department_id = dept
        if spec.parent:
            occ_id = uuid2occid.get(op.get("parent_uuid"))
            if occ_id is None and spec.parent_required:
                conflicts.append({"entity": entity, "uuid": uuid, "reason": "missing_parent"})
                return
            setattr(row, spec.parent_fk, occ_id)
        for f in spec.fields:
            if f in data:
                setattr(row, f, data[f])
        if spec.on_create:
            spec.on_create(row)
        db.session.add(row)
        db.session.flush()
        if entity == "occupancy":
            id2uuid[row.id] = row.uuid
            uuid2occid[row.uuid] = row.id
        applied.append({"entity": entity, "uuid": uuid, "id": row.id,
                        "updated_at": _iso(row.updated_at)})

    elif kind == "update":
        if row is None:
            return  # gone server-side; client learns via the deletions pull
        if base and _naive_utc(row.updated_at) and _naive_utc(row.updated_at) > base:
            conflicts.append({"entity": entity, "uuid": uuid,
                              "base_updated_at": op.get("base_updated_at"),
                              "server": _serialize(entity, row, id2uuid)})
            return
        for f in spec.fields:
            if f in data:
                setattr(row, f, data[f])
        db.session.flush()
        applied.append({"entity": entity, "uuid": uuid, "id": row.id,
                        "updated_at": _iso(row.updated_at)})

    elif kind == "delete":
        if row is None:
            return  # already gone (idempotent)
        if base and _naive_utc(row.updated_at) and _naive_utc(row.updated_at) > base:
            conflicts.append({"entity": entity, "uuid": uuid,
                              "base_updated_at": op.get("base_updated_at"),
                              "server": _serialize(entity, row, id2uuid)})
            return
        db.session.delete(row)
        db.session.add(Deletion(department_id=dept, entity_type=entity, uuid=uuid))
        applied.append({"entity": entity, "uuid": uuid, "deleted": True})


# --- endpoint ----------------------------------------------------------------

@sync_bp.post("/api/sync")
@login_required
def sync():
    payload = request.get_json(silent=True) or {}
    ops = [o for o in (payload.get("ops") or []) if isinstance(o, dict)]
    last_synced_at = _parse(payload.get("last_synced_at"))
    dept = current_user.department_id

    occs = Occupancy.query.filter_by(department_id=dept).all()
    id2uuid = {o.id: o.uuid for o in occs}
    uuid2occid = {o.uuid: o.id for o in occs}

    applied, conflicts = [], []
    grouped = {}
    for op in ops:
        grouped.setdefault(op.get("entity"), []).append(op)
    for entity in APPLY_ORDER:
        for op in grouped.get(entity, []):
            _apply_one(entity, op, dept, uuid2occid, id2uuid, applied, conflicts)

    db.session.commit()
    server_time = _utcnow_naive()

    # Pull everything changed since the client's last watermark.
    changes = {}
    for entity, spec in SYNCABLE.items():
        q = _dept_query(spec, dept)
        if last_synced_at:
            q = q.filter(spec.model.updated_at > last_synced_at)
        changes[entity] = [_serialize(entity, r, id2uuid) for r in q.all()]

    dq = Deletion.query.filter_by(department_id=dept)
    if last_synced_at:
        dq = dq.filter(Deletion.deleted_at > last_synced_at)
    deletions = [{"entity": d.entity_type, "uuid": d.uuid} for d in dq.all()]

    return jsonify({
        "server_time": _iso(server_time),
        "applied": applied,
        "conflicts": conflicts,
        "changes": changes,
        "deletions": deletions,
    })


def _utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)
