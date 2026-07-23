"""Development entry point.

    python run.py

Applies any pending database migrations on startup (so a fresh clone just runs),
then serves with Flask's built-in dev server. For production use a real WSGI
server, e.g.  gunicorn "app:create_app()"  — see the Dockerfile / deploy guide.
"""
from app import create_app

app = create_app()
# The dev server is plain HTTP on localhost; a Secure-only session cookie would
# never come back, breaking login. Production (gunicorn / the PythonAnywhere WSGI
# entry) uses the factory directly and keeps Secure cookies on.
app.config["SESSION_COOKIE_SECURE"] = False

if __name__ == "__main__":
    with app.app_context():
        from flask_migrate import upgrade
        upgrade()  # create/upgrade the schema so the first run needs no manual step
    app.run(debug=True, host="127.0.0.1", port=5000)
