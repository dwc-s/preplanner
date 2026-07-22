#!/usr/bin/env bash
#
# Pre-Planner — one-command setup for local development or a simple self-hosted
# deployment (Linux / macOS).
#
#     ./install.sh
#
# It will:
#   1. create a virtualenv (.venv)
#   2. install Python dependencies
#   3. write a .env with a strong, randomly generated SECRET_KEY (only if none exists)
#   4. apply database migrations (flask db upgrade)
#   5. offer to create the first admin, or load the demo department
#
# Safe to re-run: it never overwrites an existing .env or your database.
#
# Overrides:  PYTHON=python3.12 ./install.sh    VENV=/path/to/venv ./install.sh
# For PythonAnywhere + MySQL instead, use deploy/install_pythonanywhere.sh.

set -euo pipefail
cd "$(dirname "$0")"

# 1. Python -------------------------------------------------------------------
PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "Error: '$PY' not found. Install Python 3.9+ (or set PYTHON=...) and re-run." >&2
  exit 1
fi
echo "==> Using $("$PY" --version 2>&1)"

# 2. Virtualenv ---------------------------------------------------------------
VENV="${VENV:-.venv}"
if [ ! -d "$VENV" ]; then
  echo "==> Creating virtualenv in $VENV"
  "$PY" -m venv "$VENV"
fi
# shellcheck disable=SC1091
. "$VENV/bin/activate"

# 3. Dependencies -------------------------------------------------------------
echo "==> Installing dependencies (this can take a minute)"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

# 4. .env + SECRET_KEY --------------------------------------------------------
if [ ! -f .env ]; then
  echo "==> Creating .env with a freshly generated SECRET_KEY"
  cp .env.example .env
  SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
  python - "$SECRET" <<'PY'
import re, sys, pathlib
secret = sys.argv[1]
p = pathlib.Path(".env")
p.write_text(re.sub(r'(?m)^SECRET_KEY=.*$', f'SECRET_KEY={secret}', p.read_text()))
PY
else
  echo "==> .env already exists — leaving it untouched"
fi

# 5. Database schema ----------------------------------------------------------
echo "==> Applying database migrations"
export FLASK_APP=run
flask db upgrade

# 6. First admin / demo data --------------------------------------------------
echo
echo "Set up an account?"
echo "  1) Create a department + admin interactively   (flask create-admin)"
echo "  2) Load the demo department + sample data       (flask seed-db)"
echo "  3) Skip for now"
printf "Choose [1/2/3]: "
read -r choice || choice=3
case "${choice:-3}" in
  1) flask create-admin ;;
  2) flask seed-db ;;
  *) echo "Skipped — run 'flask create-admin' when you're ready." ;;
esac

# Done ------------------------------------------------------------------------
cat <<EOF

Setup complete.

Start the app:
    source $VENV/bin/activate
    python run.py                      # dev server → http://127.0.0.1:5000

For production, run it under a WSGI server behind TLS:
    gunicorn --workers 3 'app:create_app()'

Optional: install the 'tesseract' binary for photo text search
    (apt install tesseract-ocr  /  brew install tesseract).
EOF
