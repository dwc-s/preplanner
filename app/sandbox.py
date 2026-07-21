"""Ephemeral, no-signup sandbox workspaces.

A visitor can try the full app without registering: ``main.sandbox_start`` creates
a throwaway department flagged ``is_sandbox``, seeds it with the demo pre-plans and
hydrants, logs the visitor in, and lets them use everything except file uploads.

Sandboxes are isolated from real data (and from each other) by the normal
per-department scoping, and are purged after a TTL — opportunistically whenever a
new sandbox is created, and via the ``flask purge-sandboxes`` CLI for a cron job.
"""
import os
import shutil
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import flash, redirect, request, url_for, current_app
from flask_login import current_user

from .extensions import db
from .models import Department, Occupancy, Hydrant, MapFeature, WmsLayer, Deletion

SANDBOX_TTL_HOURS = 24


def in_sandbox():
    """True when the signed-in user belongs to a sandbox department."""
    return (current_user.is_authenticated
            and current_user.department is not None
            and current_user.department.is_sandbox)


def sandbox_forbidden(f):
    """Block a route while in the sandbox (used on the file-upload endpoints).
    Flashes a note and bounces back instead of performing the write. Stack it
    *below* ``login_required``/``admin_required`` so auth is resolved first."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if in_sandbox():
            flash("That's disabled in the sandbox — sign up to upload your own files.",
                  "error")
            return redirect(request.referrer or url_for("main.index"))
        return f(*args, **kwargs)
    return wrapper


def purge_sandbox(dept):
    """Delete a sandbox department and everything scoped to it.

    Ordered so foreign keys stay satisfied on MySQL/Postgres (SQLite dev doesn't
    enforce them by default, but keeping the order portable costs nothing).
    ``MapFeature`` goes before occupancies and users (its ``occupancy_id`` /
    ``created_by`` FKs); occupancies are deleted through the ORM so their contacts,
    hazards, and floor plans cascade; the department delete cascades its users.
    """
    dept_id = dept.id
    Deletion.query.filter_by(department_id=dept_id).delete(synchronize_session=False)
    MapFeature.query.filter_by(department_id=dept_id).delete(synchronize_session=False)
    Hydrant.query.filter_by(department_id=dept_id).delete(synchronize_session=False)
    WmsLayer.query.filter_by(department_id=dept_id).delete(synchronize_session=False)
    for occ in Occupancy.query.filter_by(department_id=dept_id).all():
        db.session.delete(occ)  # ORM delete → cascades contacts/hazards/floor plans
    # Uploaded floor-plan images live under UPLOAD_FOLDER/<dept_id>/<occ_id>/.
    updir = os.path.join(current_app.config["UPLOAD_FOLDER"], str(dept_id))
    shutil.rmtree(updir, ignore_errors=True)
    db.session.delete(dept)  # cascades users
    db.session.commit()


def purge_expired_sandboxes(max_age_hours=SANDBOX_TTL_HOURS):
    """Purge sandbox departments older than the TTL. Returns how many were removed.
    created_at is stored naive-UTC (plain DateTime column), so compare naive."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=max_age_hours)
    stale = (Department.query
             .filter(Department.is_sandbox.is_(True), Department.created_at < cutoff)
             .all())
    for dept in stale:
        purge_sandbox(dept)
    return len(stale)
