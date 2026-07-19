"""Multi-tenant isolation helpers.

**Every** data route funnels reads through these so a department can only ever
see its own records. Enforcing it here — rather than re-deriving the filter in
each handler — means a single audited chokepoint instead of dozens of places a
``department_id`` filter could be forgotten (which would leak data across
departments).
"""
from flask import abort
from flask_login import current_user

from .extensions import db
from .models import Occupancy


def dept_query(model):
    """A base query for `model` restricted to the current user's department."""
    return model.query.filter_by(department_id=current_user.department_id)


def get_owned(model, obj_id):
    """Fetch a department-scoped record by id, 404ing if it isn't ours."""
    obj = db.session.get(model, obj_id)
    if obj is None or getattr(obj, "department_id", None) != current_user.department_id:
        abort(404)
    return obj


def get_owned_child(model, child_id):
    """Fetch a child record (Contact / Hazard / FloorPlan) and verify its parent
    occupancy belongs to the current department."""
    child = db.session.get(model, child_id)
    if child is None:
        abort(404)
    occ = db.session.get(Occupancy, child.occupancy_id)
    if occ is None or occ.department_id != current_user.department_id:
        abort(404)
    return child
