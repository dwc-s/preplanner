"""Application configuration.

The database URL is read from the DATABASE_URL environment variable so the same
code runs on SQLite (default, zero-config self-host), MySQL (e.g. PythonAnywhere),
or PostgreSQL/PostGIS. Secrets/URLs are loaded from a local ``.env`` file if
python-dotenv is installed (see deploy/PYTHONANYWHERE.md).
"""
import os

basedir = os.path.abspath(os.path.dirname(__file__))

# Load .env if python-dotenv is available. interpolate=False is important: a
# PythonAnywhere MySQL database is named like "user$preplanner", and $-expansion
# would otherwise mangle it. Real environment variables still take precedence.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(basedir, ".env"), interpolate=False)
except ImportError:
    pass

_DB_URL = os.environ.get("DATABASE_URL") or (
    "sqlite:///" + os.path.join(basedir, "instance", "preplanner.db")
)


class Config:
    # Change this in production (the installer generates a random one). It signs
    # sessions, flash messages, and CSRF tokens.
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")

    SQLALCHEMY_DATABASE_URI = _DB_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Session-cookie hardening. Secure defaults to on, so the cookie is only sent
    # over HTTPS in production (PythonAnywhere and any real deploy serve HTTPS).
    # Local HTTP dev relaxes this in run.py; the test client relaxes it in the
    # app factory. Set SESSION_COOKIE_SECURE=false in .env only for HTTP-only hosts.
    SESSION_COOKIE_SECURE = os.environ.get(
        "SESSION_COOKIE_SECURE", "true").lower() in ("1", "true", "yes")
    SESSION_COOKIE_HTTPONLY = True   # JS can't read the session cookie
    SESSION_COOKIE_SAMESITE = "Lax"  # not sent on cross-site requests (CSRF depth)

    # MySQL hosts (PythonAnywhere included) drop idle connections after a few
    # minutes; recycle below that window and pre-ping so a stale connection is
    # never handed to a request. Harmless/ignored for SQLite.
    SQLALCHEMY_ENGINE_OPTIONS = (
        {"pool_recycle": 280, "pool_pre_ping": True}
        if _DB_URL.startswith("mysql") else {}
    )

    # Preserve field order in JSON responses (GeoJSON reads better that way).
    JSON_SORT_KEYS = False

    # Rate-limit store (Flask-Limiter reads this from config). In-memory by default,
    # which is fine for a single process; for multiple workers point it at Redis so
    # login limits are shared:  RATELIMIT_STORAGE_URI=redis://localhost:6379
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")

    # Global cap on any request body (uploads: floor plans, library assets, GIS files).
    # 5 GB — generous headroom for large floor-plan PDFs / imagery; the host's own disk
    # quota is the practical limit below this.
    MAX_CONTENT_LENGTH = 5 * 1024 ** 3

    # Where uploaded floor-plan images live. Kept OUT of static/ so they are only
    # reachable through an authenticated, ownership-checked route.
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER") or os.path.join(
        basedir, "instance", "uploads"
    )

    # Email (self-service password-reset codes). Defaults suit Porkbun SMTP; supply
    # MAIL_USERNAME (the full email address) and MAIL_PASSWORD (the email-specific
    # password, NOT your Porkbun account password) via .env. With no credentials,
    # sending is suppressed and the code is logged (fine for local dev).
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.porkbun.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "true").lower() in ("1", "true", "yes")
    MAIL_USE_SSL = os.environ.get("MAIL_USE_SSL", "false").lower() in ("1", "true", "yes")
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = (os.environ.get("MAIL_DEFAULT_SENDER")
                           or os.environ.get("MAIL_USERNAME"))
    MAIL_SUPPRESS_SEND = os.environ.get(
        "MAIL_SUPPRESS_SEND",
        "false" if os.environ.get("MAIL_USERNAME") else "true"
    ).lower() in ("1", "true", "yes")
