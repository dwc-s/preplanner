"""Pre-plan data model.

This is the schema every later feature hangs off of, so it is worth reading
top-to-bottom. The central record is :class:`Occupancy` (one building /
property / pre-plan). Related detail lives in child tables (:class:`Contact`,
:class:`Hazard`, :class:`FloorPlan`). :class:`Hydrant` and :class:`MapFeature`
are standalone map features not tied to a single occupancy.

Multi-tenancy: :class:`Department` owns users and data. Every top-level data
record carries a ``department_id`` and queries are scoped to the current user's
department (see ``app/scoping.py``) so departments never see each other's data.

Geometry (building footprints, drawn features) is stored as GeoJSON text so the
model stays backend-agnostic between SQLite and Postgres. When the hosted
instance moves to PostGIS, these text columns can be upgraded to real spatial
columns (GeoAlchemy2) without disturbing the rest of the app.
"""
import json
import uuid as uuid_lib
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from .extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


def _new_uuid():
    """Stable, client-generatable key used for offline sync (see app/sync.py)."""
    return str(uuid_lib.uuid4())


# --- Controlled vocabularies -------------------------------------------------
# Exposed to templates (see create_app) so form dropdowns and validation share
# one source of truth. Kept as plain lists to stay simple to edit.

OCCUPANCY_TYPES = [
    "Residential - Single Family",
    "Residential - Multi Family",
    "Assembly",
    "Educational",
    "Institutional / Health Care",
    "Business",
    "Mercantile",
    "Industrial",
    "Storage",
    "Utility / Misc",
    "Agricultural",
]

# NFPA 220 / ISO construction classifications.
CONSTRUCTION_TYPES = [
    "Type I - Fire Resistive",
    "Type II - Non-Combustible",
    "Type III - Ordinary",
    "Type IV - Heavy Timber",
    "Type V - Wood Frame",
]

BUILDING_CONDITIONS = ["Good", "Fair", "Poor", "Dilapidated / Hazardous"]

CONTACT_ROLES = [
    "Owner",
    "Property Manager",
    "Key Holder",
    "Emergency Contact",
    "Utility Company",
    "Other",
]

HAZARD_TYPES = [
    "Hazardous Materials",
    "Flammable / Combustible Storage",
    "Structural / Collapse",
    "Electrical",
    "Confined Space",
    "Biological",
    "Compressed / LP Gas",
    "Solar / Battery Storage",
    "Truss Construction",
    "Other",
]

HAZARD_SEVERITIES = ["Low", "Medium", "High", "Critical"]

USER_ROLES = ["admin", "member"]

# Fire-service ranks, listed most-senior first (drives roster ordering).
FIRE_RANKS = [
    "Chief",
    "Deputy Chief",
    "Assistant Chief",
    "Captain",
    "Lieutenant",
    "Firefighter/Paramedic",
    "Firefighter/EMT",
    "Firefighter",
    "Probationary Firefighter",
]

# Drawn map features. Category drives styling and the map layer it lives in.
# "Symbol" holds placeable fire-service symbols (see MAP_SYMBOLS).
MAP_FEATURE_CATEGORIES = ["Access Point", "Route", "Hazard Zone", "Custom", "Symbol"]

FEATURE_COLORS = {
    "Access Point": "#1c7ed6",  # blue
    "Route": "#e8590c",         # orange
    "Hazard Zone": "#e03131",   # red
    "Custom": "#7048e8",        # purple
    "Symbol": "#495057",        # slate (symbols carry their own badge colour)
}

DEFAULT_FEATURE_COLOR = "#7048e8"

# Placeable point symbols for the pre-plan map. Each Point MapFeature with
# category "Symbol" stores one of these keys in `symbol`; the client renders it
# as a coloured badge (`code`). Editing this list is the only place symbols live.
MAP_SYMBOLS = [
    {"key": "fdc", "label": "Fire Dept Connection", "code": "FDC", "color": "#c0392b"},
    {"key": "knox", "label": "Knox Box", "code": "KNOX", "color": "#1c7ed6"},
    {"key": "standpipe", "label": "Standpipe", "code": "STP", "color": "#c0392b"},
    {"key": "sprinkler", "label": "Sprinkler Riser", "code": "SPR", "color": "#c0392b"},
    {"key": "gas", "label": "Gas Shutoff", "code": "GAS", "color": "#e8590c"},
    {"key": "electric", "label": "Electric Shutoff", "code": "ELEC", "color": "#f59f00"},
    {"key": "water", "label": "Water Shutoff", "code": "H2O", "color": "#1c7ed6"},
    {"key": "hazmat", "label": "Hazmat", "code": "HAZ", "color": "#e03131"},
    {"key": "command", "label": "Command Post", "code": "CMD", "color": "#2f9e44"},
    {"key": "staging", "label": "Staging Area", "code": "STG", "color": "#7048e8"},
    {"key": "watersupply", "label": "Water Supply / Draft", "code": "DRAFT", "color": "#1971c2"},
]
MAP_SYMBOL_KEYS = {s["key"] for s in MAP_SYMBOLS}


# --- Tenancy: departments & users --------------------------------------------

class Department(db.Model):
    __tablename__ = "department"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=_utcnow)

    users = db.relationship(
        "User", backref="department", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Department {self.id} {self.name!r}>"


class User(UserMixin, db.Model):
    # "user" is a reserved word in PostgreSQL, so name the table explicitly.
    __tablename__ = "app_user"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(200))
    role = db.Column(db.String(20), nullable=False, default="member")
    rank = db.Column(db.String(40))  # fire-service rank (see FIRE_RANKS)
    department_id = db.Column(
        db.Integer, db.ForeignKey("department.id"), nullable=False
    )
    # Overrides UserMixin.is_active: Flask-Login refuses login for inactive users.
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=_utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == "admin"

    def __repr__(self):
        return f"<User {self.id} {self.email!r} ({self.role})>"


# --- Central record ----------------------------------------------------------

class Occupancy(db.Model):
    """A single pre-plan: one building or property."""

    __tablename__ = "occupancy"

    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.String(36), unique=True, index=True, default=_new_uuid)
    department_id = db.Column(
        db.Integer, db.ForeignKey("department.id"), nullable=False, index=True
    )

    # Identification
    name = db.Column(db.String(200), nullable=False)
    address = db.Column(db.String(200))
    city = db.Column(db.String(100))
    state = db.Column(db.String(50))
    zip_code = db.Column(db.String(20))

    # Location (point) + optional building footprint polygon as GeoJSON text.
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    footprint_geojson = db.Column(db.Text)

    # Classification
    occupancy_type = db.Column(db.String(80))
    construction_type = db.Column(db.String(80))
    stories = db.Column(db.Integer)
    square_footage = db.Column(db.Integer)
    year_built = db.Column(db.Integer)
    building_condition = db.Column(db.String(40))
    roof_construction = db.Column(db.String(200))

    # Fire-protection systems
    sprinkler_system = db.Column(db.Boolean, default=False)
    sprinkler_details = db.Column(db.Text)
    standpipe_system = db.Column(db.Boolean, default=False)
    standpipe_details = db.Column(db.Text)
    fire_alarm_system = db.Column(db.Boolean, default=False)
    fdc_location = db.Column(db.String(200))  # Fire Department Connection

    # Access & security  (NOTE: gate_code / alarm_pin are sensitive — see README
    # security note.)
    knox_box_location = db.Column(db.String(200))
    gate_code = db.Column(db.String(80))
    alarm_pin = db.Column(db.String(80))
    annunciator_location = db.Column(db.String(200))

    # Utility shutoffs
    electric_shutoff_location = db.Column(db.String(200))
    gas_shutoff_location = db.Column(db.String(200))
    water_shutoff_location = db.Column(db.String(200))

    # Water supply (nearest hydrant / draft site narrative)
    water_supply_notes = db.Column(db.Text)

    # Free-text
    hazards_summary = db.Column(db.Text)
    access_notes = db.Column(db.Text)
    notes = db.Column(db.Text)

    # Meta
    created_at = db.Column(db.DateTime, default=_utcnow)
    updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    department = db.relationship("Department")
    contacts = db.relationship(
        "Contact", backref="occupancy",
        cascade="all, delete-orphan", order_by="Contact.name",
    )
    hazards = db.relationship(
        "Hazard", backref="occupancy",
        cascade="all, delete-orphan", order_by="Hazard.id",
    )
    floor_plans = db.relationship(
        "FloorPlan", backref="occupancy",
        cascade="all, delete-orphan", order_by="FloorPlan.uploaded_at",
    )

    def __repr__(self):
        return f"<Occupancy {self.id} {self.name!r}>"

    @property
    def full_address(self):
        parts = [self.address, self.city, self.state, self.zip_code]
        return ", ".join(p for p in parts if p)

    @property
    def has_point(self):
        return self.latitude is not None and self.longitude is not None

    def to_geojson_feature(self):
        """Point feature for the map layer. Returns None if un-located."""
        if not self.has_point:
            return None
        return {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [self.longitude, self.latitude],
            },
            "properties": {
                "id": self.id,
                "name": self.name,
                "address": self.full_address,
                "occupancy_type": self.occupancy_type,
                "construction_type": self.construction_type,
                "sprinkler": bool(self.sprinkler_system),
                "url": f"/occupancies/{self.id}",
            },
        }

    def footprint_feature(self):
        """Polygon feature from the stored footprint, or None."""
        if not self.footprint_geojson:
            return None
        try:
            geometry = json.loads(self.footprint_geojson)
        except (ValueError, TypeError):
            return None
        # Accept either a bare geometry or a wrapped Feature.
        if geometry.get("type") == "Feature":
            geometry = geometry.get("geometry")
        if not geometry:
            return None
        return {
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "id": self.id,
                "name": self.name,
                "url": f"/occupancies/{self.id}",
            },
        }


# --- Child tables ------------------------------------------------------------

class Contact(db.Model):
    __tablename__ = "contact"

    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.String(36), unique=True, index=True, default=_new_uuid)
    occupancy_id = db.Column(
        db.Integer, db.ForeignKey("occupancy.id"), nullable=False
    )
    name = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(80))
    phone = db.Column(db.String(50))
    email = db.Column(db.String(120))
    notes = db.Column(db.String(300))
    updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)


class Hazard(db.Model):
    __tablename__ = "hazard"

    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.String(36), unique=True, index=True, default=_new_uuid)
    occupancy_id = db.Column(
        db.Integer, db.ForeignKey("occupancy.id"), nullable=False
    )
    hazard_type = db.Column(db.String(80), nullable=False)
    severity = db.Column(db.String(20))
    location = db.Column(db.String(200))
    description = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)


class FloorPlan(db.Model):
    """Uploaded floor-plan image + its annotations (W3C Web Annotation JSON)."""

    __tablename__ = "floor_plan"

    id = db.Column(db.Integer, primary_key=True)
    occupancy_id = db.Column(
        db.Integer, db.ForeignKey("occupancy.id"), nullable=False
    )
    title = db.Column(db.String(200))
    image_filename = db.Column(db.String(300))
    # W3C Web Annotation list as JSON text (not GeoJSON — image-pixel geometry).
    annotations_json = db.Column(db.Text)
    uploaded_at = db.Column(db.DateTime, default=_utcnow)


# --- Standalone map features -------------------------------------------------

class Hydrant(db.Model):
    """A fire hydrant. Independent of any single occupancy — it is a shared
    water-supply feature on the map."""

    __tablename__ = "hydrant"

    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.String(36), unique=True, index=True, default=_new_uuid)
    department_id = db.Column(
        db.Integer, db.ForeignKey("department.id"), nullable=False, index=True
    )
    label = db.Column(db.String(80))
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)

    flow_gpm = db.Column(db.Integer)          # rated flow, gallons per minute
    static_pressure = db.Column(db.Integer)   # psi
    residual_pressure = db.Column(db.Integer)  # psi
    size_inches = db.Column(db.String(40))    # main / outlet sizes, free text
    hydrant_type = db.Column(db.String(80))   # e.g. Dry barrel, Wet barrel
    in_service = db.Column(db.Boolean, default=True)
    notes = db.Column(db.String(300))
    updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

    department = db.relationship("Department")

    # NFPA 291 flow classifications: (min GPM, class code, cap color).
    _FLOW_CLASSES = [
        (1500, "AA", "#4dabf7"),  # light blue
        (1000, "A", "#40c057"),   # green
        (500, "B", "#ff922b"),    # orange
        (0, "C", "#fa5252"),      # red
    ]

    @property
    def flow_class(self):
        """(class_code, color) per NFPA 291, or (None, gray) if flow unknown."""
        if self.flow_gpm is None:
            return (None, "#adb5bd")
        for threshold, code, color in self._FLOW_CLASSES:
            if self.flow_gpm >= threshold:
                return (code, color)
        return (None, "#adb5bd")

    def to_geojson_feature(self):
        code, color = self.flow_class
        return {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [self.longitude, self.latitude],
            },
            "properties": {
                "id": self.id,
                "label": self.label or f"Hydrant {self.id}",
                "flow_gpm": self.flow_gpm,
                "flow_class": code,
                "color": color,
                "in_service": bool(self.in_service),
                "type": self.hydrant_type,
                "static_pressure": self.static_pressure,
                "residual_pressure": self.residual_pressure,
            },
        }


class MapFeature(db.Model):
    """A user-drawn map feature: access point (Point), route (LineString),
    hazard zone / custom (Polygon). One generic model covers all drawn layers."""

    __tablename__ = "map_feature"

    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.String(36), unique=True, index=True, default=_new_uuid)
    department_id = db.Column(
        db.Integer, db.ForeignKey("department.id"), nullable=False, index=True
    )
    # Optional link to a building this feature belongs to.
    occupancy_id = db.Column(db.Integer, db.ForeignKey("occupancy.id"))
    category = db.Column(db.String(40), nullable=False)
    symbol = db.Column(db.String(40))  # for category "Symbol" (see MAP_SYMBOLS)
    label = db.Column(db.String(200))
    geometry_json = db.Column(db.Text, nullable=False)  # GeoJSON geometry
    color = db.Column(db.String(20))
    notes = db.Column(db.String(500))
    created_by = db.Column(db.Integer, db.ForeignKey("app_user.id"))
    created_at = db.Column(db.DateTime, default=_utcnow)
    updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

    department = db.relationship("Department")

    @property
    def display_color(self):
        return self.color or FEATURE_COLORS.get(self.category, DEFAULT_FEATURE_COLOR)

    def to_geojson_feature(self):
        try:
            geometry = json.loads(self.geometry_json)
        except (ValueError, TypeError):
            return None
        if geometry.get("type") == "Feature":
            geometry = geometry.get("geometry")
        if not geometry:
            return None
        return {
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "id": self.id,
                "category": self.category,
                "symbol": self.symbol,
                "label": self.label,
                "color": self.display_color,
                "notes": self.notes,
                "occupancy_id": self.occupancy_id,
            },
        }


class WmsLayer(db.Model):
    """A configured map overlay the department can toggle on the map.

    Two kinds share this table: ``kind="wms"`` (a WMS endpoint + ``layers``) and
    ``kind="xyz"`` (a slippy-tile URL template like a topo/imagery basemap, where
    ``url`` holds the ``{z}/{x}/{y}`` template and ``layers`` is unused)."""

    __tablename__ = "wms_layer"

    id = db.Column(db.Integer, primary_key=True)
    department_id = db.Column(
        db.Integer, db.ForeignKey("department.id"), nullable=False, index=True
    )
    kind = db.Column(db.String(8), nullable=False, default="wms")  # "wms" | "xyz"
    name = db.Column(db.String(120), nullable=False)     # display name
    url = db.Column(db.String(500), nullable=False)      # WMS base URL or XYZ template
    layers = db.Column(db.String(300), nullable=False)   # WMS layer names (unused for xyz)
    image_format = db.Column(db.String(40), default="image/png")
    transparent = db.Column(db.Boolean, default=True)
    opacity = db.Column(db.Float, default=0.7)
    attribution = db.Column(db.String(200))              # tile-service credit (xyz)
    max_zoom = db.Column(db.Integer)                     # tile service max zoom (xyz)
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=_utcnow)

    department = db.relationship("Department")

    def to_dict(self):
        return {
            "id": self.id,
            "kind": self.kind or "wms",
            "name": self.name,
            "url": self.url,
            "layers": self.layers,
            "format": self.image_format or "image/png",
            "transparent": bool(self.transparent),
            "opacity": self.opacity if self.opacity is not None else 0.7,
            "attribution": self.attribution,
            "max_zoom": self.max_zoom,
        }


# --- Offline sync: deletion tombstones ---------------------------------------

class Deletion(db.Model):
    """Records that a syncable row was deleted, so offline clients can learn
    about deletes on their next pull (the row itself is hard-deleted). See
    ``app/sync.py``."""

    __tablename__ = "deletion"

    id = db.Column(db.Integer, primary_key=True)
    department_id = db.Column(
        db.Integer, db.ForeignKey("department.id"), nullable=False, index=True
    )
    entity_type = db.Column(db.String(40), nullable=False)  # e.g. "map_feature"
    uuid = db.Column(db.String(36), nullable=False, index=True)
    deleted_at = db.Column(db.DateTime, default=_utcnow, index=True)
