"""Authentication and user management.

Accounts are admin-created only (public sign-up is still a stub — see ``register``):
the first admin per department is bootstrapped with ``flask create-admin``, and
department admins add their own crew here. Real department data stays behind login
and per-department scoping; the only public surfaces are the splash landing, the
sign-up stub, and the throwaway sandbox (see app/sandbox.py).
"""
import secrets
import string
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, abort,
    jsonify, current_app, session
)
from flask_login import (
    login_user, logout_user, login_required, current_user
)
from flask_mail import Message
from werkzeug.security import generate_password_hash, check_password_hash

from .extensions import db, limiter, mail
from .models import (
    User, PasswordResetCode, USER_ROLES, FIRE_RANKS, OFFICER_REVIEW_POLICIES,
    RANK_EDIT_POLICIES
)

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


# --- self-service password reset (email code) --------------------------------

def _naive_utcnow():
    # SQLite stores naive datetimes; compare like-with-like to avoid tz errors.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _send_reset_code(user):
    """Generate a fresh 6-digit code (15-min, single-use), store its hash, email it.
    Prior unused codes for the user are invalidated."""
    code = f"{secrets.randbelow(1000000):06d}"
    PasswordResetCode.query.filter_by(user_id=user.id, used=False).update({"used": True})
    db.session.add(PasswordResetCode(
        user_id=user.id, code_hash=generate_password_hash(code),
        expires_at=_naive_utcnow() + timedelta(minutes=15)))
    db.session.commit()
    body = (f"Your Pre-Planner password reset code is: {code}\n\n"
            f"Enter it within 15 minutes to set a new password. "
            f"If you didn't request this, you can ignore this email.")
    try:
        mail.send(Message("Pre-Planner password reset code",
                          recipients=[user.email], body=body))
    except Exception:  # don't leak SMTP errors to the user; log them
        current_app.logger.exception("Failed to send password-reset email")
    if current_app.config.get("MAIL_SUPPRESS_SEND"):  # dev: no SMTP, so surface the code
        current_app.logger.warning("Password-reset code for %s: %s", user.email, code)


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour; 20 per day", methods=["POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        user = User.query.filter_by(email=email, is_active=True).first()
        if user:
            _send_reset_code(user)
        # Generic response either way, so a stranger can't probe which emails exist.
        flash("If that email is registered, a reset code is on its way — it expires "
              "in 15 minutes.", "success")
        # Carry the email in the session (not the URL) to prefill the reset form —
        # keeps addresses out of browser history and server access logs.
        session["reset_email"] = email
        return redirect(url_for("auth.reset_password"))
    return render_template("forgot_password.html")


@auth_bp.route("/reset-password", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def reset_password():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        code = (request.form.get("code") or "").strip()
        new = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""
        user = User.query.filter_by(email=email, is_active=True).first()
        rc = (PasswordResetCode.query
              .filter_by(user_id=user.id, used=False)
              .order_by(PasswordResetCode.id.desc()).first()) if user else None
        if len(new) < MIN_PASSWORD_LENGTH:
            flash(f"New password must be at least {MIN_PASSWORD_LENGTH} characters.", "error")
        elif new != confirm:
            flash("New passwords do not match.", "error")
        elif not (rc and rc.expires_at > _naive_utcnow()
                  and check_password_hash(rc.code_hash, code)):
            flash("That code is invalid or has expired.", "error")
        else:
            user.set_password(new)
            rc.used = True
            db.session.commit()
            session.pop("reset_email", None)
            flash("Password updated — you can sign in now.", "success")
            return redirect(url_for("auth.login"))
        return render_template("reset_password.html", email=email)
    return render_template("reset_password.html", email=session.get("reset_email", ""))


@auth_bp.post("/logout")
@login_required
def logout():
    logout_user()
    flash("Signed out.", "success")
    return redirect(url_for("main.index"))


# --- roster + user management (scoped to the caller's department) ------------

@auth_bp.get("/users")
@login_required
def users_list():
    """The merged roster/users page — everyone sees it; action controls are gated by
    role, and rank editing by the department's rank-edit policy."""
    order = {r: i for i, r in enumerate(FIRE_RANKS)}
    users = (User.query.filter_by(department_id=current_user.department_id).all())
    users.sort(key=lambda u: (not u.is_active,
                              order.get(u.rank, len(FIRE_RANKS)),
                              (u.name or u.email).lower()))
    return render_template(
        "users.html", users=users,
        can_edit_ranks=current_user.department.can_edit_ranks(current_user))


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
@superuser_required
def user_toggle(user_id):
    """Activate/deactivate a crew member — the department's membership control,
    restricted to the superuser (deactivation is the app's soft 'remove'). The
    self-check keeps the department from ever losing its last active superuser: only a
    superuser can act, and they can't deactivate themselves."""
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
@login_required
def user_set_rank(user_id):
    """Set a crew member's fire-service rank — allowed per the department's rank-edit
    policy (admins always; officers/all members when the superuser widens it)."""
    if not current_user.department.can_edit_ranks(current_user):
        abort(403)
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


@auth_bp.post("/users/<int:user_id>/name")
@admin_required
def user_set_name(user_id):
    """Edit a crew member's display name."""
    user = (User.query
            .filter_by(id=user_id, department_id=current_user.department_id)
            .first_or_404())
    user.name = (request.form.get("name") or "").strip()[:200] or None
    db.session.commit()
    if request.headers.get("X-Autosave") == "1":
        return jsonify(ok=True)
    flash(f"Updated name for {user.email}.", "success")
    return redirect(url_for("auth.users_list"))


@auth_bp.post("/users/<int:user_id>/email")
@admin_required
def user_set_email(user_id):
    """Edit a crew member's email — their login, so it must stay valid and unique."""
    user = (User.query
            .filter_by(id=user_id, department_id=current_user.department_id)
            .first_or_404())
    autosave = request.headers.get("X-Autosave") == "1"

    def _fail(msg):
        if autosave:
            return jsonify(ok=False, error=msg), 200
        flash(msg, "error")
        return redirect(url_for("auth.users_list"))

    email = (request.form.get("email") or "").strip().lower()
    if not email or "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        return _fail("Enter a valid email address.")
    if User.query.filter(User.email == email, User.id != user.id).first():
        return _fail("That email is already in use.")
    user.email = email
    db.session.commit()
    if autosave:
        return jsonify(ok=True)
    flash(f"Updated email to {email}.", "success")
    return redirect(url_for("auth.users_list"))


@auth_bp.post("/users/<int:user_id>/special-role")
@admin_required
def user_set_special_role(user_id):
    """Set a member's free-text special role (e.g. "EMS officer"). Admin or superuser."""
    user = (User.query
            .filter_by(id=user_id, department_id=current_user.department_id)
            .first_or_404())
    user.special_role = (request.form.get("special_role") or "").strip()[:80] or None
    db.session.commit()
    if request.headers.get("X-Autosave") == "1":
        return jsonify(ok=True)
    flash(f"Updated special role for {user.email}.", "success")
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


# --- roster (merged into the users page; keep the old URL working) -----------

@auth_bp.get("/roster")
@login_required
def roster():
    return redirect(url_for("auth.users_list"))


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


@auth_bp.post("/preferences/rank-edit-policy")
@superuser_required
def set_rank_edit_policy():
    """Superuser sets who may edit members' ranks on the roster."""
    policy = request.form.get("rank_edit_policy") or ""
    if policy in RANK_EDIT_POLICIES:
        current_user.department.rank_edit_policy = policy
        db.session.commit()
        flash("Roster permissions updated.", "success")
    else:
        flash("Unknown policy.", "error")
    return redirect(url_for("auth.preferences"))
