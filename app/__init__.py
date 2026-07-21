"""Application factory.

Keeps app creation in a function so tests can spin up isolated instances and so
the same code serves dev (SQLite) and production (Postgres) without edits.
"""
import os

import click
from flask import Flask, make_response
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_migrate import Migrate

from .extensions import db, limiter
from . import models

# Extension singletons (bound to the app inside create_app).
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please sign in to continue."
login_manager.login_message_category = "error"
csrf = CSRFProtect()
migrate = Migrate()


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(models.User, int(user_id))


def create_app(config_object="config.Config"):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object)

    # SQLAlchemy needs the "postgresql://" scheme; some hosts hand out the older
    # "postgres://" form in DATABASE_URL. Normalize it.
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if uri.startswith("postgres://"):
        app.config["SQLALCHEMY_DATABASE_URI"] = uri.replace(
            "postgres://", "postgresql://", 1
        )

    # instance/ holds the SQLite DB and uploaded floor plans; make sure it exists.
    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    migrate.init_app(app, db)
    limiter.init_app(app)

    from .main import main_bp
    from .api import api_bp
    from .auth import auth_bp
    from .sync import sync_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(sync_bp)
    # /api/sync is session-authenticated same-origin JSON; exempt it from CSRF so
    # a token cached before going offline can't expire mid-sync.
    csrf.exempt(sync_bp)

    # Serve the service worker from the root so its scope covers the whole app
    # (a /static/ scope would be too narrow to control page navigations).
    @app.route("/sw.js")
    def service_worker():
        resp = make_response(app.send_static_file("sw.js"))
        resp.headers["Content-Type"] = "application/javascript"
        resp.headers["Service-Worker-Allowed"] = "/"
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    _register_cli(app)

    # Make the controlled vocabularies available to every template.
    @app.context_processor
    def inject_choices():
        return {
            "OCCUPANCY_TYPES": models.OCCUPANCY_TYPES,
            "CONSTRUCTION_TYPES": models.CONSTRUCTION_TYPES,
            "BUILDING_CONDITIONS": models.BUILDING_CONDITIONS,
            "CONTACT_ROLES": models.CONTACT_ROLES,
            "HAZARD_TYPES": models.HAZARD_TYPES,
            "HAZARD_SEVERITIES": models.HAZARD_SEVERITIES,
            "MAP_FEATURE_CATEGORIES": models.MAP_FEATURE_CATEGORIES,
            "FIRE_RANKS": models.FIRE_RANKS,
            "PREPLAN_STATUSES": models.PREPLAN_STATUSES,
        }

    return app


def _register_cli(app):
    @app.cli.command("create-admin")
    def create_admin():
        """Bootstrap a department and its first admin user (interactive)."""
        from getpass import getpass
        from .models import Department, User

        dept_name = input("Department name: ").strip()
        if not dept_name:
            click.echo("Department name is required.")
            return
        email = input("Admin email: ").strip().lower()
        name = input("Admin full name: ").strip()
        password = getpass("Admin password: ")
        if not (email and password):
            click.echo("Email and password are required.")
            return
        if User.query.filter_by(email=email).first():
            click.echo(f"A user with email {email} already exists.")
            return

        dept = Department.query.filter_by(name=dept_name).first()
        if dept is None:
            dept = Department(name=dept_name)
            db.session.add(dept)
            db.session.flush()
        user = User(email=email, name=name, role="admin", department_id=dept.id)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Created admin {email} for department '{dept_name}'.")

    @app.cli.command("seed-db")
    def seed_db():
        """Load sample department, occupancies, and hydrants for demos."""
        from seed import seed_database
        count = seed_database()
        click.echo(f"Seeded {count} records." if count else "Already seeded.")

    @app.cli.command("purge-sandboxes")
    @click.option("--max-age-hours", default=24, show_default=True,
                  help="Delete sandbox workspaces older than this many hours.")
    def purge_sandboxes(max_age_hours):
        """Delete expired sandbox departments and all their data (wire to cron)."""
        from .sandbox import purge_expired_sandboxes
        n = purge_expired_sandboxes(max_age_hours)
        click.echo(f"Purged {n} expired sandbox department(s).")
