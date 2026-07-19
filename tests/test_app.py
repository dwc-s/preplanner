"""Smoke + behavior tests for the pre-planner.

Run:  pytest
Uses a throwaway file-based SQLite DB per test so nothing touches instance/.
CSRF is disabled in the default test config; one test re-enables it to prove
protection is wired.
"""
import io
import os
import shutil
import tempfile
import uuid

import pytest

from app import create_app
from app.extensions import db
from app.models import (
    Department, User, Hydrant, FloorPlan, Occupancy, Hazard, MapFeature,
)


def _make_config(path, csrf=False, ratelimit=False):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "test"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{path}"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        WTF_CSRF_ENABLED = csrf
        RATELIMIT_ENABLED = ratelimit
    return TestConfig


@pytest.fixture
def app():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    upload_dir = tempfile.mkdtemp()
    cfg = _make_config(path)
    cfg.UPLOAD_FOLDER = upload_dir
    app = create_app(cfg)
    with app.app_context():
        db.create_all()
    yield app
    os.unlink(path)
    shutil.rmtree(upload_dir, ignore_errors=True)


def make_dept_user(app, dept_name, email, password="pw", role="admin"):
    with app.app_context():
        dept = Department(name=dept_name)
        db.session.add(dept)
        db.session.flush()
        user = User(email=email, name="Tester", role=role, department_id=dept.id)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return dept.id


def login(client, email, password="pw"):
    return client.post("/login", data={"email": email, "password": password},
                       follow_redirects=True)


@pytest.fixture
def client(app):
    """A client logged in as an admin of a single department (Dept A)."""
    make_dept_user(app, "Dept A", "a@example.com")
    c = app.test_client()
    login(c, "a@example.com")
    return c


# --- auth gating -------------------------------------------------------------

def test_requires_login(app):
    c = app.test_client()
    r = c.get("/occupancies")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_login_and_index(client):
    assert client.get("/").status_code == 200


def test_bad_password_rejected(app):
    make_dept_user(app, "Dept A", "a@example.com")
    c = app.test_client()
    login(c, "a@example.com", "wrong")
    # Still gated afterwards.
    assert c.get("/occupancies").status_code == 302


# --- occupancy CRUD (scoped to the logged-in department) ---------------------

def test_empty_api_is_valid_geojson(client):
    data = client.get("/api/occupancies").get_json()
    assert data["type"] == "FeatureCollection"
    assert data["features"] == []


def test_create_occupancy_flow(client):
    resp = client.post("/occupancies/new", data={
        "name": "Test Firehouse",
        "address": "1 Test Way",
        "city": "Testville",
        "latitude": "44.26",
        "longitude": "-72.57",
        "occupancy_type": "Business",
        "sprinkler_system": "on",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Test Firehouse" in resp.data

    assert b"Test Firehouse" in client.get("/occupancies").data
    features = client.get("/api/occupancies").get_json()["features"]
    assert len(features) == 1
    props = features[0]["properties"]
    assert props["name"] == "Test Firehouse"
    assert props["sprinkler"] is True
    assert features[0]["geometry"]["coordinates"] == [-72.57, 44.26]


def test_create_requires_name(client):
    resp = client.post("/occupancies/new", data={"name": ""})
    assert resp.status_code == 200
    assert client.get("/api/occupancies").get_json()["features"] == []


def test_edit_and_delete_occupancy(client):
    client.post("/occupancies/new", data={"name": "Temp", "latitude": "1", "longitude": "2"})
    occ_id = client.get("/api/occupancies").get_json()["features"][0]["properties"]["id"]
    client.post(f"/occupancies/{occ_id}/edit",
                data={"name": "Renamed", "latitude": "1", "longitude": "2"})
    assert b"Renamed" in client.get(f"/occupancies/{occ_id}").data

    client.post(f"/occupancies/{occ_id}/delete")
    assert client.get(f"/occupancies/{occ_id}").status_code == 404
    assert client.get("/api/occupancies").get_json()["features"] == []


def test_unlocated_occupancy_absent_from_map(client):
    client.post("/occupancies/new", data={"name": "No Coords"})
    assert b"No Coords" in client.get("/occupancies").data
    assert client.get("/api/occupancies").get_json()["features"] == []


# --- multi-tenant isolation (the security-critical property) -----------------

def test_cross_department_isolation(app):
    make_dept_user(app, "Dept A", "a@example.com")
    make_dept_user(app, "Dept B", "b@example.com")
    ca, cb = app.test_client(), app.test_client()
    login(ca, "a@example.com")
    login(cb, "b@example.com")

    ca.post("/occupancies/new",
            data={"name": "Secret Bunker", "latitude": "1", "longitude": "2"})
    occ_id = ca.get("/api/occupancies").get_json()["features"][0]["properties"]["id"]

    # A sees its record; B sees nothing.
    assert b"Secret Bunker" in ca.get("/occupancies").data
    assert b"Secret Bunker" not in cb.get("/occupancies").data
    assert cb.get("/api/occupancies").get_json()["features"] == []

    # B cannot open, edit, or delete A's record by id.
    assert cb.get(f"/occupancies/{occ_id}").status_code == 404
    assert cb.post(f"/occupancies/{occ_id}/edit",
                   data={"name": "hijack", "latitude": "1", "longitude": "2"}).status_code == 404
    assert cb.post(f"/occupancies/{occ_id}/delete").status_code == 404
    # ...and A's record is untouched.
    assert b"Secret Bunker" in ca.get(f"/occupancies/{occ_id}").data


# --- CSRF ---------------------------------------------------------------------

def test_csrf_enabled_rejects_tokenless_post():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    app = create_app(_make_config(path, csrf=True))
    with app.app_context():
        db.create_all()
    # A POST with no CSRF token is rejected (400) rather than processed.
    resp = app.test_client().post("/login", data={"email": "x@e.com", "password": "pw"})
    assert resp.status_code == 400
    os.unlink(path)


# --- floor plans -------------------------------------------------------------

def _create_occupancy(client, name="Bldg"):
    client.post("/occupancies/new",
                data={"name": name, "latitude": "1", "longitude": "2"})
    return client.get("/api/occupancies").get_json()["features"][0]["properties"]["id"]


def test_floorplan_upload_serve_annotate(client):
    occ_id = _create_occupancy(client, "Warehouse")
    resp = client.post(
        f"/occupancies/{occ_id}/floorplans",
        data={"title": "Ground floor", "image": (io.BytesIO(b"fakeimage"), "plan.png")},
        content_type="multipart/form-data", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Ground floor" in resp.data

    with client.application.app_context():
        fp = FloorPlan.query.first()
        assert fp is not None and fp.image_filename
        fp_id = fp.id

    # Authenticated image serving returns the bytes we uploaded.
    img = client.get(f"/floorplans/{fp_id}/image")
    assert img.status_code == 200
    assert img.data == b"fakeimage"

    # Annotations round-trip and re-render into the page (embedded via tojson).
    annotations = [{"id": "anno-xyz", "target": {}, "body": []}]
    assert client.post(f"/floorplans/{fp_id}/annotations", json=annotations).status_code == 200
    assert b"anno-xyz" in client.get(f"/floorplans/{fp_id}").data


def test_floorplan_rejects_bad_extension(client):
    occ_id = _create_occupancy(client)
    client.post(f"/occupancies/{occ_id}/floorplans",
                data={"image": (io.BytesIO(b"nope"), "plan.exe")},
                content_type="multipart/form-data", follow_redirects=True)
    with client.application.app_context():
        assert FloorPlan.query.count() == 0


def test_floorplan_cross_department(app):
    make_dept_user(app, "Dept A", "a@example.com")
    make_dept_user(app, "Dept B", "b@example.com")
    ca, cb = app.test_client(), app.test_client()
    login(ca, "a@example.com")
    login(cb, "b@example.com")

    occ_id = _create_occupancy(ca, "A Building")
    ca.post(f"/occupancies/{occ_id}/floorplans",
            data={"image": (io.BytesIO(b"x"), "p.png")},
            content_type="multipart/form-data")
    with app.app_context():
        fp_id = FloorPlan.query.first().id

    for path in (f"/floorplans/{fp_id}", f"/floorplans/{fp_id}/image"):
        assert cb.get(path).status_code == 404
    assert cb.post(f"/floorplans/{fp_id}/annotations", json=[]).status_code == 404
    assert ca.get(f"/floorplans/{fp_id}").status_code == 200


# --- map features + footprints ----------------------------------------------

def test_map_feature_crud(client):
    r = client.post("/api/map-features", json={
        "category": "Access Point",
        "label": "Gate A",
        "geometry": {"type": "Point", "coordinates": [-72.57, 44.26]},
    })
    assert r.status_code == 201
    fid = r.get_json()["properties"]["id"]

    fc = client.get("/api/map-features").get_json()
    assert len(fc["features"]) == 1
    props = fc["features"][0]["properties"]
    assert props["label"] == "Gate A"
    assert props["category"] == "Access Point"
    assert props["color"]  # default category color applied

    # category filter
    assert client.get("/api/map-features?category=Route").get_json()["features"] == []

    # update, then delete
    assert client.put(f"/api/map-features/{fid}", json={"label": "Gate B"}).status_code == 200
    assert client.get("/api/map-features").get_json()["features"][0]["properties"]["label"] == "Gate B"
    assert client.delete(f"/api/map-features/{fid}").status_code == 200
    assert client.get("/api/map-features").get_json()["features"] == []


def test_map_feature_rejects_bad_category(client):
    r = client.post("/api/map-features", json={
        "category": "Nonsense",
        "geometry": {"type": "Point", "coordinates": [0, 0]},
    })
    assert r.status_code == 400


def test_map_feature_cross_department(app):
    make_dept_user(app, "Dept A", "a@example.com")
    make_dept_user(app, "Dept B", "b@example.com")
    ca, cb = app.test_client(), app.test_client()
    login(ca, "a@example.com")
    login(cb, "b@example.com")
    fid = ca.post("/api/map-features", json={
        "category": "Route",
        "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
    }).get_json()["properties"]["id"]

    assert cb.get("/api/map-features").get_json()["features"] == []
    assert cb.put(f"/api/map-features/{fid}", json={"label": "x"}).status_code == 404
    assert cb.delete(f"/api/map-features/{fid}").status_code == 404
    assert len(ca.get("/api/map-features").get_json()["features"]) == 1


def test_footprint_api(client):
    poly = '{"type":"Polygon","coordinates":[[[0,0],[0,1],[1,1],[0,0]]]}'
    client.post("/occupancies/new", data={
        "name": "Footprinted", "latitude": "1", "longitude": "2",
        "footprint_geojson": poly,
    })
    fc = client.get("/api/footprints").get_json()
    assert len(fc["features"]) == 1
    assert fc["features"][0]["geometry"]["type"] == "Polygon"


# --- account hardening -------------------------------------------------------

def test_password_change(client):
    r = client.post("/account", data={
        "current_password": "pw", "new_password": "newpass123",
        "confirm_password": "newpass123",
    }, follow_redirects=True)
    assert b"Password changed" in r.data
    # New password works, old one no longer does.
    good = client.application.test_client()
    login(good, "a@example.com", "newpass123")
    assert good.get("/occupancies").status_code == 200
    bad = client.application.test_client()
    login(bad, "a@example.com", "pw")
    assert bad.get("/occupancies").status_code == 302


def test_password_change_rejects_wrong_current(client):
    r = client.post("/account", data={
        "current_password": "wrong", "new_password": "newpass123",
        "confirm_password": "newpass123",
    })
    assert b"Current password is incorrect" in r.data


def test_password_change_enforces_length_and_match(client):
    assert b"at least 8" in client.post("/account", data={
        "current_password": "pw", "new_password": "short", "confirm_password": "short",
    }).data
    assert b"do not match" in client.post("/account", data={
        "current_password": "pw", "new_password": "newpass123", "confirm_password": "other12345",
    }).data


def test_user_create_enforces_min_password(client):
    client.post("/users", data={"email": "new@example.com", "role": "member", "password": "short"})
    with client.application.app_context():
        assert User.query.filter_by(email="new@example.com").first() is None


def test_admin_reset_password(client):
    client.post("/users", data={"email": "crew@example.com", "role": "member", "password": "initpass123"})
    with client.application.app_context():
        uid = User.query.filter_by(email="crew@example.com").first().id
    r = client.post(f"/users/{uid}/reset-password", follow_redirects=True)
    assert b"Temporary password for crew@example.com" in r.data


def test_login_rate_limited():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    app = create_app(_make_config(path, ratelimit=True))
    with app.app_context():
        db.create_all()
    c = app.test_client()
    codes = [c.post("/login", data={"email": "x@e.com", "password": "nope"}).status_code
             for _ in range(12)]
    assert 429 in codes  # limit is 10/min
    os.unlink(path)


# --- WMS overlays + GIS import -----------------------------------------------

def test_wms_overlay_crud(client):
    client.post("/overlays", data={
        "name": "Parcels", "url": "https://gis.example/wms",
        "layers": "parcels", "opacity": "0.5",
    })
    api = client.get("/api/wms-layers").get_json()
    assert len(api) == 1 and api[0]["name"] == "Parcels" and api[0]["opacity"] == 0.5

    from app.models import WmsLayer
    with client.application.app_context():
        wid = WmsLayer.query.first().id
    client.post(f"/overlays/{wid}/delete")
    assert client.get("/api/wms-layers").get_json() == []


def test_overlays_admin_only(app):
    make_dept_user(app, "Dept A", "member@example.com", role="member")
    c = app.test_client()
    login(c, "member@example.com")
    assert c.get("/overlays").status_code == 403
    assert c.post("/overlays", data={"name": "x", "url": "y", "layers": "z"}).status_code == 403


def test_wms_cross_department(app):
    make_dept_user(app, "Dept A", "a@example.com")
    make_dept_user(app, "Dept B", "b@example.com")
    ca, cb = app.test_client(), app.test_client()
    login(ca, "a@example.com")
    login(cb, "b@example.com")
    ca.post("/overlays", data={"name": "A parcels", "url": "https://a/wms", "layers": "p"})
    assert len(ca.get("/api/wms-layers").get_json()) == 1
    assert cb.get("/api/wms-layers").get_json() == []


def test_gis_import_geojson(client):
    import json as _json
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "Gate 1"},
         "geometry": {"type": "Point", "coordinates": [-72.5, 44.2]}},
        {"type": "Feature", "properties": {"name": "Approach"},
         "geometry": {"type": "LineString", "coordinates": [[-72.5, 44.2], [-72.4, 44.3]]}},
        {"type": "Feature", "properties": {"name": "Parcel"},
         "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}},
    ]}
    data = {"file": (io.BytesIO(_json.dumps(fc).encode()), "import.geojson")}
    r = client.post("/overlays/import", data=data,
                    content_type="multipart/form-data", follow_redirects=True)
    assert b"Imported 3" in r.data
    feats = client.get("/api/map-features").get_json()["features"]
    assert sorted(f["properties"]["category"] for f in feats) == ["Access Point", "Custom", "Route"]


def test_gis_import_rejects_bad_type(client):
    data = {"file": (io.BytesIO(b"nope"), "bad.txt")}
    r = client.post("/overlays/import", data=data,
                    content_type="multipart/form-data", follow_redirects=True)
    assert b"Unsupported file" in r.data


def test_gis_import_parsers():
    from app import gis_import
    kml = (b'<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2">'
           b'<Placemark><name>P</name><Point><coordinates>-72.5,44.2,0</coordinates>'
           b'</Point></Placemark></kml>')
    kf = gis_import.parse_kml(kml)
    assert len(kf) == 1 and kf[0]["geometry"]["type"] == "Point"
    assert kf[0]["category"] == "Access Point" and kf[0]["label"] == "P"

    gpx = (b'<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1">'
           b'<wpt lat="44.2" lon="-72.5"><name>W</name></wpt>'
           b'<trk><name>T</name><trkseg><trkpt lat="44.2" lon="-72.5"/>'
           b'<trkpt lat="44.3" lon="-72.4"/></trkseg></trk></gpx>')
    gf = gis_import.parse_gpx(gpx)
    assert len(gf) == 2  # one waypoint + one track line
    assert gf[0]["geometry"]["coordinates"] == [-72.5, 44.2]


# --- offline sync (/api/sync) ------------------------------------------------

def _sync(client, ops, last_synced_at=None):
    return client.post("/api/sync",
                       json={"ops": ops, "last_synced_at": last_synced_at}).get_json()


def test_sync_create_and_pull(client):
    ou = str(uuid.uuid4())
    r = _sync(client, [{"entity": "occupancy", "op": "create", "uuid": ou,
                        "data": {"name": "Offline Bldg", "latitude": 1, "longitude": 2}}])
    assert len(r["applied"]) == 1 and r["applied"][0]["uuid"] == ou and r["applied"][0]["id"]
    assert r["conflicts"] == []
    with client.application.app_context():
        o = Occupancy.query.filter_by(uuid=ou).first()
        assert o and o.name == "Offline Bldg" and o.department_id
    names = [c["name"] for c in _sync(client, [])["changes"]["occupancy"]]
    assert "Offline Bldg" in names


def test_sync_child_applies_after_parent(client):
    ou, hu = str(uuid.uuid4()), str(uuid.uuid4())
    # hazard listed BEFORE its parent in the array — APPLY_ORDER must reorder.
    r = _sync(client, [
        {"entity": "hazard", "op": "create", "uuid": hu, "parent_uuid": ou,
         "data": {"hazard_type": "Electrical", "severity": "High"}},
        {"entity": "occupancy", "op": "create", "uuid": ou,
         "data": {"name": "Parent", "latitude": 1, "longitude": 2}},
    ])
    assert len(r["applied"]) == 2
    with client.application.app_context():
        o = Occupancy.query.filter_by(uuid=ou).first()
        h = Hazard.query.filter_by(uuid=hu).first()
        assert h and h.occupancy_id == o.id


def test_sync_update_conflict_not_applied(client):
    from datetime import timedelta
    from app.sync import _parse
    ou = str(uuid.uuid4())
    base = _sync(client, [{"entity": "occupancy", "op": "create", "uuid": ou,
                           "data": {"name": "C", "latitude": 1, "longitude": 2}}])["applied"][0]["updated_at"]
    with client.application.app_context():
        o = Occupancy.query.filter_by(uuid=ou).first()
        o.name = "Server Changed"
        o.updated_at = _parse(base) + timedelta(seconds=5)  # clearly newer than client base
        db.session.commit()
    r = _sync(client, [{"entity": "occupancy", "op": "update", "uuid": ou,
                        "base_updated_at": base, "data": {"name": "Client Changed"}}])
    assert len(r["conflicts"]) == 1 and r["conflicts"][0]["server"]["name"] == "Server Changed"
    with client.application.app_context():
        assert Occupancy.query.filter_by(uuid=ou).first().name == "Server Changed"  # not clobbered


def test_sync_update_success(client):
    ou = str(uuid.uuid4())
    base = _sync(client, [{"entity": "occupancy", "op": "create", "uuid": ou,
                           "data": {"name": "U", "latitude": 1, "longitude": 2}}])["applied"][0]["updated_at"]
    r = _sync(client, [{"entity": "occupancy", "op": "update", "uuid": ou,
                        "base_updated_at": base, "data": {"name": "Updated", "gate_code": "1234"}}])
    assert r["conflicts"] == []
    with client.application.app_context():
        o = Occupancy.query.filter_by(uuid=ou).first()
        assert o.name == "Updated" and o.gate_code == "1234"


def test_sync_delete_creates_tombstone(client):
    fu = str(uuid.uuid4())
    r = _sync(client, [{"entity": "map_feature", "op": "create", "uuid": fu,
                        "data": {"category": "Access Point",
                                 "geometry_json": '{"type":"Point","coordinates":[0,0]}'}}])
    base, watermark = r["applied"][0]["updated_at"], r["server_time"]
    r2 = _sync(client, [{"entity": "map_feature", "op": "delete", "uuid": fu, "base_updated_at": base}])
    assert any(a.get("deleted") for a in r2["applied"])
    with client.application.app_context():
        assert MapFeature.query.filter_by(uuid=fu).first() is None
        from app.models import Deletion
        assert Deletion.query.filter_by(uuid=fu).first() is not None
    assert any(d["uuid"] == fu for d in _sync(client, [], last_synced_at=watermark)["deletions"])


def test_sync_idempotent_create(client):
    ou = str(uuid.uuid4())
    op = {"entity": "occupancy", "op": "create", "uuid": ou,
          "data": {"name": "Once", "latitude": 1, "longitude": 2}}
    id1 = _sync(client, [op])["applied"][0]["id"]
    id2 = _sync(client, [op])["applied"][0]["id"]
    assert id1 == id2
    with client.application.app_context():
        assert Occupancy.query.filter_by(uuid=ou).count() == 1


def test_sync_missing_parent_is_conflict(client):
    hu = str(uuid.uuid4())
    r = _sync(client, [{"entity": "hazard", "op": "create", "uuid": hu,
                        "parent_uuid": str(uuid.uuid4()), "data": {"hazard_type": "Electrical"}}])
    assert any(c.get("reason") == "missing_parent" for c in r["conflicts"])
    with client.application.app_context():
        assert Hazard.query.filter_by(uuid=hu).first() is None


def test_sync_department_scoping(app):
    make_dept_user(app, "Dept A", "a@example.com")
    make_dept_user(app, "Dept B", "b@example.com")
    ca, cb = app.test_client(), app.test_client()
    login(ca, "a@example.com")
    login(cb, "b@example.com")
    ou = str(uuid.uuid4())
    _sync(ca, [{"entity": "occupancy", "op": "create", "uuid": ou,
                "data": {"name": "A secret", "latitude": 1, "longitude": 2}}])
    rb = _sync(cb, [])
    assert all(o["uuid"] != ou for o in rb["changes"]["occupancy"])   # B can't see A's row
    rb2 = _sync(cb, [{"entity": "occupancy", "op": "update", "uuid": ou,
                      "base_updated_at": None, "data": {"name": "hijack"}}])
    assert rb2["applied"] == []                                        # B can't touch it
    with app.app_context():
        assert Occupancy.query.filter_by(uuid=ou).first().name == "A secret"


# --- model unit test ---------------------------------------------------------

@pytest.mark.parametrize("gpm,expected", [
    (1600, "AA"), (1500, "AA"), (1200, "A"), (1000, "A"),
    (700, "B"), (500, "B"), (300, "C"), (0, "C"), (None, None),
])
def test_hydrant_flow_class(gpm, expected):
    assert Hydrant(latitude=0, longitude=0, flow_gpm=gpm).flow_class[0] == expected
