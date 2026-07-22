"""Shared extension instances.

Kept in their own module so the app factory, blueprints, and models can import
the same objects without creating circular imports.
"""
from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

db = SQLAlchemy()

# Storage comes from config (RATELIMIT_STORAGE_URI): in-memory by default, which is
# fine for a single-process self-host; point it at Redis for a multi-worker
# deployment so limits are shared. The constructor default is just a fallback.
limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")
