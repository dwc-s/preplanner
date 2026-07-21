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


# --- public landing + sandbox ------------------------------------------------

def test_landing_is_public(app):
    """Logged-out visitors get a public splash (not the login wall) at /."""
    r = app.test_client().get("/")
    assert r.status_code == 200
    assert "/sandbox" in r.get_data(as_text=True)  # the "Try the sandbox" CTA


def test_register_stub_is_public(app):
    r = app.test_client().get("/register")
    assert r.status_code == 200
    assert "coming soon" in r.get_data(as_text=True).lower()


def test_sandbox_get_creates_nothing(app):
    """A bare GET (crawler/prefetch) must not spin up a workspace — it redirects."""
    c = app.test_client()
    r = c.get("/sandbox")
    assert r.status_code == 302
    with app.app_context():
        assert Department.query.filter_by(is_sandbox=True).count() == 0


def test_sandbox_start_creates_seeded_workspace(app):
    c = app.test_client()
    r = c.post("/sandbox")
    assert r.status_code == 302  # redirects into the app
    with app.app_context():
        depts = Department.query.filter_by(is_sandbox=True).all()
        assert len(depts) == 1
        dept = depts[0]
        assert Occupancy.query.filter_by(department_id=dept.id).count() > 0
        assert Hydrant.query.filter_by(department_id=dept.id).count() > 0
        user = User.query.filter_by(department_id=dept.id).first()
        assert user is not None and user.is_admin  # admin so every feature is explorable
    # the visitor is now signed in — the map renders at /
    assert c.get("/").status_code == 200


def test_sandbox_blocks_file_uploads(app):
    c = app.test_client()
    c.post("/sandbox")  # signs in as a sandbox admin
    with app.app_context():
        dept = Department.query.filter_by(is_sandbox=True).first()
        occ_id = Occupancy.query.filter_by(department_id=dept.id).first().id
        dept_id = dept.id
        floorplans_before = FloorPlan.query.count()

    # Floor-plan image upload is blocked.
    r = c.post(f"/occupancies/{occ_id}/floorplans",
               data={"image": (io.BytesIO(b"not-really-an-image"), "x.png")},
               content_type="multipart/form-data", follow_redirects=True)
    assert r.status_code == 200
    assert "sandbox" in r.get_data(as_text=True).lower()

    # GIS import is blocked too.
    r2 = c.post("/overlays/import",
                data={"files": (io.BytesIO(b'{"type":"FeatureCollection","features":[]}'),
                                "x.geojson")},
                content_type="multipart/form-data", follow_redirects=True)
    assert r2.status_code == 200
    assert "sandbox" in r2.get_data(as_text=True).lower()

    with app.app_context():
        assert FloorPlan.query.count() == floorplans_before        # nothing uploaded
        assert MapFeature.query.filter_by(department_id=dept_id).count() == 0  # nothing imported


def test_purge_expired_sandboxes(app):
    from datetime import datetime, timezone, timedelta
    from app.sandbox import purge_expired_sandboxes
    from seed import seed_department

    with app.app_context():
        # A real department must survive the purge.
        real = Department(name="Real FD")
        db.session.add(real)
        db.session.flush()
        seed_department(real)

        # An aged sandbox (with data + a user) must be removed entirely.
        old = Department(name="Sandbox old", is_sandbox=True)
        db.session.add(old)
        db.session.flush()
        u = User(email="sb@sandbox.invalid", role="admin", department_id=old.id)
        u.set_password("x")
        db.session.add(u)
        seed_department(old)
        db.session.commit()
        old.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=48)
        db.session.commit()
        old_id, real_id = old.id, real.id

        assert purge_expired_sandboxes(max_age_hours=24) == 1
        assert db.session.get(Department, old_id) is None
        assert Occupancy.query.filter_by(department_id=old_id).count() == 0
        assert User.query.filter_by(department_id=old_id).count() == 0
        # untouched real department
        assert db.session.get(Department, real_id) is not None
        assert Occupancy.query.filter_by(department_id=real_id).count() > 0


# --- dashboard (private home) + review + announcements -----------------------

def test_dashboard_is_home_for_members(client):
    """Logged-in members get the dashboard at /, and the map moves to /map."""
    r = client.get("/")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Your pre-plans" in body
    assert 'id="map"' not in body            # the map is no longer served at /
    assert client.get("/map").status_code == 200  # it lives here now


def test_new_occupancy_stamps_created_by(client, app):
    client.post("/occupancies/new", data={"name": "Ownership Test Bldg"},
                follow_redirects=True)
    with app.app_context():
        occ = Occupancy.query.filter_by(name="Ownership Test Bldg").first()
        author = User.query.filter_by(email="a@example.com").first()
        assert occ is not None
        assert occ.created_by == author.id
        assert occ.status == "draft"


def test_announcement_requires_admin(app):
    dept_id = make_dept_user(app, "Dept A", "admin@a.com", role="admin")
    with app.app_context():
        member = User(email="member@a.com", name="Member", role="member",
                      department_id=dept_id)
        member.set_password("pw")
        db.session.add(member)
        db.session.commit()

    admin_c = app.test_client()
    login(admin_c, "admin@a.com")
    r = admin_c.post("/announcements", data={"body": "Drill Saturday 0800"},
                     follow_redirects=True)
    assert r.status_code == 200
    assert "Drill Saturday 0800" in admin_c.get("/").get_data(as_text=True)

    member_c = app.test_client()
    login(member_c, "member@a.com")
    assert member_c.post("/announcements", data={"body": "nope"}).status_code == 403
    # ...but the member still sees the admin's announcement on their dashboard
    assert "Drill Saturday 0800" in member_c.get("/").get_data(as_text=True)


def test_submit_for_review_sets_status(client, app):
    client.post("/occupancies/new", data={"name": "Review Test Bldg"},
                follow_redirects=True)
    with app.app_context():
        occ = Occupancy.query.filter_by(name="Review Test Bldg").first()
        reviewer = User(email="reviewer@a.com", name="Reviewer", role="member",
                        department_id=occ.department_id)
        reviewer.set_password("pw")
        db.session.add(reviewer)
        db.session.commit()
        occ_id, reviewer_id = occ.id, reviewer.id

    r = client.post(f"/occupancies/{occ_id}/submit-review",
                    data={"reviewer_id": reviewer_id}, follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        occ = db.session.get(Occupancy, occ_id)
        assert occ.status == "in_review"
        assert occ.submitted_to_id == reviewer_id
        assert occ.submitted_at is not None


def test_submit_for_review_requires_a_reviewer(client, app):
    client.post("/occupancies/new", data={"name": "No Reviewer Bldg"},
                follow_redirects=True)
    with app.app_context():
        occ_id = Occupancy.query.filter_by(name="No Reviewer Bldg").first().id
    # Submitting without picking a reviewer must not flip the status.
    client.post(f"/occupancies/{occ_id}/submit-review", data={"reviewer_id": ""},
                follow_redirects=True)
    with app.app_context():
        occ = db.session.get(Occupancy, occ_id)
        assert occ.status == "draft"
        assert occ.submitted_to_id is None


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


def test_wms_overlay_add_bulk(client):
    r = client.post("/overlays/add-bulk", json={
        "url": "https://gis.example/wms",
        "layers": [
            {"name": "massgis:GISDATA.STRUCTURES_POLY", "title": "Building Structures (2-D)"},
            {"name": "massgis:GISDATA.L3_TAXPAR_POLY_ASSESS", "title": "Parcels"},
        ],
    })
    assert r.status_code == 200 and r.get_json()["added"] == 2
    api = client.get("/api/wms-layers").get_json()
    by_layer = {w["layers"]: w for w in api}
    # display name comes from the human title; the WMS layer name lives in `layers`
    assert by_layer["massgis:GISDATA.STRUCTURES_POLY"]["name"] == "Building Structures (2-D)"
    # overlays must be transparent by default so they don't hide the basemap
    assert all(w["transparent"] is True for w in api)

    # re-adding the same layer is idempotent (no duplicate rows)
    r2 = client.post("/overlays/add-bulk", json={
        "url": "https://gis.example/wms",
        "layers": [{"name": "massgis:GISDATA.STRUCTURES_POLY", "title": "dupe"}],
    })
    assert r2.get_json()["added"] == 0
    assert len(client.get("/api/wms-layers").get_json()) == 2


def test_wms_overlay_add_bulk_validates(client):
    assert client.post("/overlays/add-bulk", json={"url": "", "layers": []}).status_code == 400
    assert client.post("/overlays/add-bulk", json={"url": "https://x/wms", "layers": []}).status_code == 400
    # a layer entry with no usable name is skipped, not fatal
    r = client.post("/overlays/add-bulk", json={"url": "https://x/wms", "layers": [{"title": "no name"}]})
    assert r.status_code == 200 and r.get_json()["added"] == 0


def test_wms_overlay_add_bulk_admin_only(app):
    make_dept_user(app, "Dept A", "member@example.com", role="member")
    c = app.test_client()
    login(c, "member@example.com")
    r = c.post("/overlays/add-bulk", json={"url": "https://x/wms", "layers": [{"name": "p"}]})
    assert r.status_code == 403


def test_tile_preset_adds_xyz(client):
    r = client.post("/overlays/tiles", data={"preset": "usgs_topo"}, follow_redirects=True)
    assert b"USGS Topo" in r.data
    tiles = [w for w in client.get("/api/wms-layers").get_json() if w["kind"] == "xyz"]
    assert len(tiles) == 1
    assert "{z}" in tiles[0]["url"] and tiles[0]["attribution"] and tiles[0]["max_zoom"]


def test_tile_custom_requires_template(client):
    r = client.post("/overlays/tiles", data={"name": "X", "url": "https://x/tiles.png"},
                    follow_redirects=True)
    assert b"{z}/{x}/{y}" in r.data
    assert client.get("/api/wms-layers").get_json() == []


def test_tile_custom_adds_xyz(client):
    client.post("/overlays/tiles", data={
        "name": "My tiles", "url": "https://tiles.example/{z}/{x}/{y}.png",
        "attribution": "Me", "max_zoom": "18"}, follow_redirects=True)
    w = client.get("/api/wms-layers").get_json()[0]
    assert w["kind"] == "xyz" and w["max_zoom"] == 18 and w["name"] == "My tiles"


def test_tile_admin_only(app):
    make_dept_user(app, "Dept A", "member@example.com", role="member")
    c = app.test_client()
    login(c, "member@example.com")
    assert c.post("/overlays/tiles", data={"preset": "usgs_topo"}).status_code == 403


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


def _make_shapefile(points, prj=None):
    """Build a point Shapefile in memory. points = [(x, y, name)].
    Returns {ext: bytes} for shp/shx/dbf (+ prj if given)."""
    import shapefile  # pyshp
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf)
    w.field("name", "C", size=40)
    for x, y, name in points:
        w.point(x, y)
        w.record(name)
    w.close()
    parts = {"shp": shp.getvalue(), "shx": shx.getvalue(), "dbf": dbf.getvalue()}
    if prj:
        parts["prj"] = prj.encode()
    return parts


def _upload_parts(client, parts, base="pts"):
    files = [(io.BytesIO(raw), f"{base}.{ext}") for ext, raw in parts.items()]
    return client.post("/overlays/import", data={"files": files},
                       content_type="multipart/form-data", follow_redirects=True)


def test_gis_import_shapefile_parts(client):
    parts = _make_shapefile([(-72.5, 44.2, "Hydrant A"), (-72.4, 44.3, "Hydrant B")])
    r = _upload_parts(client, parts)  # loose .shp/.shx/.dbf, already WGS84
    assert b"Imported 2" in r.data
    feats = client.get("/api/map-features").get_json()["features"]
    assert len(feats) == 2
    assert sorted(round(f["geometry"]["coordinates"][0], 1) for f in feats) == [-72.5, -72.4]


def test_gis_import_shapefile_parts_needs_shp(client):
    parts = _make_shapefile([(-72.5, 44.2, "X")])
    del parts["shp"]  # only .shx/.dbf — nothing to read
    r = _upload_parts(client, parts)
    assert b"No importable features" in r.data
    assert client.get("/api/map-features").get_json()["features"] == []


def test_gis_import_shapefile_skips_bad_records(client):
    import shapefile  # pyshp
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf)
    w.field("name", "C", size=40)
    w.point(-72.5, 44.2); w.record("Good A")
    w.null(); w.record("Null one")            # an empty/null record in the middle
    w.point(-72.4, 44.3); w.record("Good B")
    w.close()
    parts = {"shp": shp.getvalue(), "shx": shx.getvalue(), "dbf": dbf.getvalue()}
    _upload_parts(client, parts, base="mixed")
    labels = [f["properties"]["label"] for f in client.get("/api/map-features").get_json()["features"]]
    assert "Good A" in labels and "Good B" in labels  # the null didn't abort the import


def test_gis_import_shapefile_reprojected(client):
    pytest.importorskip("pyproj")
    from pyproj import CRS, Transformer
    prj = CRS.from_epsg(3857).to_wkt("WKT1_ESRI")   # a real .prj is WKT1
    fwd = Transformer.from_crs(4326, 3857, always_xy=True)
    x, y = fwd.transform(-72.5, 44.2)               # project a known lon/lat
    parts = _make_shapefile([(x, y, "Projected")], prj=prj)
    r = _upload_parts(client, parts, base="proj")
    assert b"Imported 1" in r.data
    lon, lat = client.get("/api/map-features").get_json()["features"][0]["geometry"]["coordinates"][:2]
    assert abs(lon - (-72.5)) < 1e-6 and abs(lat - 44.2) < 1e-6


def test_gis_import_clip_to_bbox(client):
    parts = _make_shapefile([(-72.50, 44.20, "Inside"), (-71.00, 42.30, "Outside")])
    files = [(io.BytesIO(raw), f"pts.{ext}") for ext, raw in parts.items()]
    r = client.post("/overlays/import", content_type="multipart/form-data",
                    follow_redirects=True, data={
                        "files": files, "clip": "1",
                        "min_lat": "44.0", "max_lat": "44.4",
                        "min_lon": "-72.7", "max_lon": "-72.3"})
    assert b"within your map area" in r.data
    labels = [f["properties"]["label"] for f in client.get("/api/map-features").get_json()["features"]]
    assert "Inside" in labels and "Outside" not in labels


def test_gis_import_clip_projected(client):
    pytest.importorskip("pyproj")
    from pyproj import CRS, Transformer
    prj = CRS.from_epsg(26986).to_wkt("WKT1_ESRI")           # MA State Plane
    fwd = Transformer.from_crs(4326, 26986, always_xy=True)
    inside = fwd.transform(-72.50, 42.40)
    outside = fwd.transform(-70.90, 42.00)                   # ~130 km away
    parts = _make_shapefile([(inside[0], inside[1], "Inside"),
                             (outside[0], outside[1], "Outside")], prj=prj)
    files = [(io.BytesIO(raw), f"proj.{ext}") for ext, raw in parts.items()]
    client.post("/overlays/import", content_type="multipart/form-data", follow_redirects=True,
                data={"files": files, "clip": "1", "min_lat": "42.2", "max_lat": "42.6",
                      "min_lon": "-72.7", "max_lon": "-72.3"})
    feats = client.get("/api/map-features").get_json()["features"]
    labels = [f["properties"]["label"] for f in feats]
    assert "Inside" in labels and "Outside" not in labels
    lon, lat = next(f for f in feats if f["properties"]["label"] == "Inside")["geometry"]["coordinates"][:2]
    assert abs(lon + 72.5) < 1e-4 and abs(lat - 42.4) < 1e-4  # kept point reprojected to WGS84


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


# --- map symbols, ranks & roster ---------------------------------------------

def test_sync_map_feature_symbol_round_trips(client):
    fu = str(uuid.uuid4())
    r = _sync(client, [{"entity": "map_feature", "op": "create", "uuid": fu, "data": {
        "category": "Symbol", "symbol": "arrow", "rotation": 90, "scale": 1.5, "length": 2.0,
        "label": "Egress", "label_lat": 44.21, "label_lng": -72.49,
        "geometry_json": '{"type":"Point","coordinates":[-72.5,44.2]}'}}])
    assert len(r["applied"]) == 1
    with client.application.app_context():
        row = MapFeature.query.filter_by(uuid=fu).first()
        assert row.symbol == "arrow" and row.rotation == 90 and row.scale == 1.5 and row.length == 2.0
        assert row.label_lat == 44.21 and row.label_lng == -72.49
    pulled = _sync(client, [])["changes"]["map_feature"]
    assert any(f["symbol"] == "arrow" and f["label"] == "Egress"
               and f["label_lat"] == 44.21 and f["label_lng"] == -72.49 for f in pulled)


def test_user_create_with_rank(client):
    client.post("/users", data={"email": "cap@example.com", "password": "longenough1",
                                "role": "member", "rank": "Captain"})
    with client.application.app_context():
        assert User.query.filter_by(email="cap@example.com").first().rank == "Captain"
    # an unknown rank is ignored (stored as null)
    client.post("/users", data={"email": "bogus@example.com", "password": "longenough1",
                                "rank": "Grand Poobah"})
    with client.application.app_context():
        assert User.query.filter_by(email="bogus@example.com").first().rank is None


def test_user_set_rank(client):
    with client.application.app_context():
        uid = User.query.filter_by(email="a@example.com").first().id
    client.post(f"/users/{uid}/rank", data={"rank": "Lieutenant"})
    with client.application.app_context():
        assert db.session.get(User, uid).rank == "Lieutenant"
    client.post(f"/users/{uid}/rank", data={"rank": ""})  # clear
    with client.application.app_context():
        assert db.session.get(User, uid).rank is None


def test_roster_visible_to_member_and_scoped(client, app):
    # `client` is admin a@example.com in Dept A; add a named member to the same dept.
    client.post("/users", data={"email": "member@example.com", "name": "Pat Member",
                                "password": "longenough1", "role": "member"})
    with app.app_context():                          # a member in another department
        deptb = Department(name="Dept B")
        db.session.add(deptb)
        db.session.flush()
        ub = User(email="b@example.com", name="Bravo Person", role="admin",
                  department_id=deptb.id)
        ub.set_password("pw")
        db.session.add(ub)
        db.session.commit()
    c = app.test_client()
    login(c, "member@example.com", "longenough1")    # a plain member, not an admin
    r = c.get("/roster")
    assert r.status_code == 200
    assert b"Pat Member" in r.data                    # own department shown
    assert b"Bravo Person" not in r.data              # other department not shown


def test_roster_requires_login(app):
    assert app.test_client().get("/roster").status_code in (302, 401)
