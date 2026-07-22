"""Authentication and user management.

Accounts are admin-created only (public sign-up is still a stub — see ``register``):
the first admin per department is bootstrapped with ``flask create-admin``, and
department admins add their own crew here. Real department data stays behind login
and per-department scoping; the only public surfaces are the splash landing, the
sign-up stub, and the throwaway sandbox (see app/sandbox.py).
"""
import secrets
import string
from functools import wraps

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify
)
from flask_login import (
    login_user, logout_user, login_required, current_user
)

from .extensions import db, limiter
from .models import User, USER_ROLES, FIRE_RANKS, OFFICER_REVIEW_POLICIES

auth_bp = Blueprint("auth", __name__)

MIN_PASSWORD_LENGTH = 8


def _random_password(length=12):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def admin_required(f):
    """Require an authenticated admin (superusers qualify — is_admin is widened).
    Stacks on top of login_required."""
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return wrapper


def superuser_required(f):
    """Require the department's top authority. For superuser-only powers (review
    policy, granting superuser). Stacks on top of login_required."""
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_superuser:
            abort(403)
        return f(*args, **kwargs)
    return wrapper


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute; 40 per hour", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = User.query.filter_by(email=email).first()
        if user and user.is_active and user.check_password(password):
            login_user(user)
            # Only honor a local next path (guards against open redirects).
            next_url = request.args.get("next")
            if next_url and next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)
            return redirect(url_for("main.index"))
        flash("Invalid email or password.", "error")
    # Keep the email on a failed attempt so it needn't be retyped (never the password).
    return render_template("login.html", email=request.form.get("email", ""))


@auth_bp.get("/register")
def register():
    """Public sign-up isn't open yet — a placeholder linked from the splash so the
    entry point exists while departments are still onboarded manually."""
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    return render_template("register.html")


@auth_bp.post("/logout")
@login_required
def logout():
    logout_user()
    flash("Signed out.", "success")
    return redirect(url_for("main.index"))


# --- User management (admin only, scoped to the admin's own department) ------

@auth_bp.get("/users")
@admin_required
def users_list():
    users = (User.query
             .filter_by(department_id=current_user.department_id)
             .order_by(User.email).all())
    return render_template("users.html", users=users)


@auth_bp.post("/users")
@admin_required
def user_create():
    email = (request.form.get("email") or "").strip().lower()
    name = (request.form.get("name") or "").strip()
    role = request.form.get("role") or "member"
    rank = (request.form.get("rank") or "").strip()
    password = request.form.get("password") or ""
    if role not in USER_ROLES:
        role = "member"
    # Only a superuser may mint another superuser (the template hides the option for
    # plain admins; this guards against a tampered request).
    if role == "superuser" and not current_user.is_superuser:
        role = "member"
    if rank not in FIRE_RANKS:
        rank = None

    error = None
    if not email or not password:
        error = "Email and password are required."
    elif len(password) < MIN_PASSWORD_LENGTH:
        error = f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    elif User.query.filter_by(email=email).first():
        error = "A user with that email already exists."
    if error:
        # Re-render with the entered values so the admin needn't retype them (the
        # password field stays blank — never echo a password back into HTML).
        flash(error, "error")
        users = (User.query.filter_by(department_id=current_user.department_id)
                 .order_by(User.email).all())
        return render_template("users.html", users=users, form=request.form)
    user = User(email=email, name=name, role=role, rank=rank,
                department_id=current_user.department_id)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    flash(f"Added {email}.", "success")
    return redirect(url_for("auth.users_list"))


@auth_bp.post("/users/<int:user_id>/toggle")
@admin_required
def user_toggle(user_id):
    """Activate/deactivate a crew member (soft delete — preserves history)."""
    user = (User.query
            .filter_by(id=user_id, department_id=current_user.department_id)
            .first_or_404())
    deactivating_last_superuser = (
        user.is_active and user.role == "superuser"
        and User.query.filter_by(department_id=user.department_id, role="superuser",
                                 is_active=True).filter(User.id != user.id).count() == 0)
    if user.id == current_user.id:
        flash("You can't deactivate your own account.", "error")
    elif deactivating_last_superuser:
        flash("You can't deactivate the department's only superuser.", "error")
    else:
        user.is_active = not user.is_active
        db.session.commit()
        state = "Reactivated" if user.is_active else "Deactivated"
        flash(f"{state} {user.email}.", "success")
    return redirect(url_for("auth.users_list"))


@auth_bp.post("/users/<int:user_id>/reset-password")
@admin_required
def user_reset_password(user_id):
    """Admin sets a random temporary password (shown once) for a crew member."""
    user = (User.query
            .filter_by(id=user_id, department_id=current_user.department_id)
            .first_or_404())
    temp = _random_password()
    user.set_password(temp)
    db.session.commit()
    flash(f"Temporary password for {user.email}: {temp} — share it securely; "
          f"they should change it after signing in.", "success")
    return redirect(url_for("auth.users_list"))


@auth_bp.post("/users/<int:user_id>/rank")
@admin_required
def user_set_rank(user_id):
    """Set a crew member's fire-service rank."""
    user = (User.query
            .filter_by(id=user_id, department_id=current_user.department_id)
            .first_or_404())
    rank = (request.form.get("rank") or "").strip()
    user.rank = rank if rank in FIRE_RANKS else None
    db.session.commit()
    if request.headers.get("X-Autosave") == "1":  # inline autosave, no page reload
        return jsonify(ok=True)
    flash(f"Updated rank for {user.email}.", "success")
    return redirect(url_for("auth.users_list"))


@auth_bp.post("/users/<int:user_id>/commanding-officer")
@admin_required
def user_set_co(user_id):
    """Assign a crew member's commanding officer (their reviewer for pre-plans)."""
    user = (User.query
            .filter_by(id=user_id, department_id=current_user.department_id)
            .first_or_404())
    co_id = request.form.get("commanding_officer_id") or ""
    co = None
    if co_id.isdigit():
        co = User.query.filter_by(
            id=int(co_id), department_id=current_user.department_id).first()
    # A user can't be their own commanding officer.
    user.commanding_officer_id = co.id if (co and co.id != user.id) else None
    db.session.commit()
    if request.headers.get("X-Autosave") == "1":
        return jsonify(ok=True)
    flash(f"Updated commanding officer for {user.email}.", "success")
    return redirect(url_for("auth.users_list"))


@auth_bp.post("/users/<int:user_id>/role")
@admin_required
def user_set_role(user_id):
    """Change a crew member's role. Granting OR revoking superuser is superuser-only,
    and a department must always keep at least one superuser."""
    user = (User.query
            .filter_by(id=user_id, department_id=current_user.department_id)
            .first_or_404())
    role = request.form.get("role") or "member"
    if role not in USER_ROLES:
        role = "member"
    if (role == "superuser" or user.role == "superuser") and not current_user.is_superuser:
        abort(403)
    if user.role == "superuser" and role != "superuser":
        others = (User.query
                  .filter_by(department_id=current_user.department_id, role="superuser")
                  .filter(User.id != user.id).count())
        if others == 0:
            flash("A department must keep at least one superuser.", "error")
            return redirect(url_for("auth.users_list"))
    user.role = role
    db.session.commit()
    if request.headers.get("X-Autosave") == "1":
        return jsonify(ok=True)
    flash(f"Updated role for {user.email}.", "success")
    return redirect(url_for("auth.users_list"))


# --- department roster (visible to every signed-in member) -------------------

@auth_bp.get("/roster")
@login_required
def roster():
    members = (User.query
               .filter_by(department_id=current_user.department_id, is_active=True)
               .all())
    order = {r: i for i, r in enumerate(FIRE_RANKS)}
    members.sort(key=lambda u: (order.get(u.rank, len(FIRE_RANKS)),
                                (u.name or u.email).lower()))
    return render_template("roster.html", members=members)


# --- self-service (Preferences) ----------------------------------------------

@auth_bp.get("/preferences")
@login_required
def preferences():
    """Per-user settings, with sections gated by class (superuser / officer / member).
    A framework we'll grow; today it holds password change + the review policy."""
    return render_template("preferences.html")


@auth_bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    """Password change handler (the form lives on the Preferences page). GET redirects
    there so old /account links keep working."""
    if request.method == "POST":
        current = request.form.get("current_password") or ""
        new = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""
        if not current_user.check_password(current):
            flash("Current password is incorrect.", "error")
        elif len(new) < MIN_PASSWORD_LENGTH:
            flash(f"New password must be at least {MIN_PASSWORD_LENGTH} characters.", "error")
        elif new != confirm:
            flash("New passwords do not match.", "error")
        else:
            current_user.set_password(new)
            db.session.commit()
            flash("Password changed.", "success")
    return redirect(url_for("auth.preferences"))


@auth_bp.post("/preferences/review-policy")
@superuser_required
def set_review_policy():
    """Superuser sets how officer-created pre-plans are routed for review."""
    policy = request.form.get("officer_review_policy") or ""
    if policy in OFFICER_REVIEW_POLICIES:
        current_user.department.officer_review_policy = policy
        db.session.commit()
        flash("Review policy updated.", "success")
    else:
        flash("Unknown review policy.", "error")
    return redirect(url_for("auth.preferences"))
