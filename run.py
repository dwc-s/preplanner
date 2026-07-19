"""Development entry point.

    python run.py

The factory creates any missing tables on startup, so this just serves with
Flask's built-in dev server. For production self-hosting use a real WSGI
server, e.g.:  gunicorn "app:create_app()"
"""
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
