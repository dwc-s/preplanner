"""Shared extension instances.

Kept in their own module so the app factory, blueprints, and models can import
the same objects without creating circular imports.
"""
from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

db = SQLAlchemy()

# In-memory rate-limit store: fine for a single-process self-host. For a
# multi-worker deployment, point RATELIMIT_STORAGE_URI at Redis so limits are
# shared across workers.
limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")
