"""Authentication and user management.

Accounts are admin-created only (no public registration): the first admin per
department is bootstrapped with ``flask create-admin``, and department admins
add their own crew here. All data is behind login — nothing is public.
"""
import secrets
import string
from functools import wraps

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, abort
)
from flask_login import (
    login_user, logout_user, login_required, current_user
)

from .extensions import db, limiter
from .models import User, USER_ROLES, FIRE_RANKS

auth_bp = Blueprint("auth", __name__)

MIN_PASSWORD_LENGTH = 8


def _random_password(length=12):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def admin_required(f):
    """Require an authenticated admin. Stacks on top of login_required."""
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
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
    return render_template("login.html")


@auth_bp.post("/logout")
@login_required
def logout():
    logout_user()
    flash("Signed out.", "success")
    return redirect(url_for("auth.login"))


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
    if rank not in FIRE_RANKS:
        rank = None

    if not email or not password:
        flash("Email and password are required.", "error")
    elif len(password) < MIN_PASSWORD_LENGTH:
        flash(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.", "error")
    elif User.query.filter_by(email=email).first():
        flash("A user with that email already exists.", "error")
    else:
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
    if user.id == current_user.id:
        flash("You can't deactivate your own account.", "error")
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
    flash(f"Updated rank for {user.email}.", "success")
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


# --- self-service ------------------------------------------------------------

@auth_bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
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
            return redirect(url_for("auth.account"))
    return render_template("account.html")
