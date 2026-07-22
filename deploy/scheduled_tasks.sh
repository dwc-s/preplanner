#!/usr/bin/env bash
#
# Pre-Planner scheduled maintenance — wire this to cron or a PythonAnywhere
# "Scheduled task". It is safe to run as often as you like; each command is a no-op
# when there is nothing to do. Running every few minutes keeps things fresh; the
# free PythonAnywhere tier allows one run per day, which is also fine.
#
#   Runs:
#     flask ocr-pending      OCR photos uploaded to the asset library. OCR is the
#                            slow step, so it is deferred at upload and done here.
#                            (No-op unless the `tesseract` binary is installed.)
#     flask purge-sandboxes  Delete expired "try the sandbox" demo workspaces.
#
# Usage:
#     deploy/scheduled_tasks.sh
#
#   The project root and virtualenv are auto-detected. Override them if needed:
#     PREPLANNER_ROOT=/home/me/preplanner \
#     PREPLANNER_VENV=/home/me/.virtualenvs/preplanner \
#     deploy/scheduled_tasks.sh
#
# Example cron entry (every 10 minutes), logging to the project's instance/ folder:
#     */10 * * * * /home/me/preplanner/deploy/scheduled_tasks.sh >> /home/me/preplanner/instance/scheduled.log 2>&1
#
set -uo pipefail

ROOT="${PREPLANNER_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"

# Locate the virtualenv: an explicit override, then the project's .venv, then the
# virtualenvwrapper location the installer offers.
if [ -n "${PREPLANNER_VENV:-}" ]; then
  VENV="$PREPLANNER_VENV"
elif [ -x "$ROOT/.venv/bin/flask" ]; then
  VENV="$ROOT/.venv"
elif [ -x "$HOME/.virtualenvs/preplanner/bin/flask" ]; then
  VENV="$HOME/.virtualenvs/preplanner"
else
  echo "Pre-Planner: could not find the virtualenv; set PREPLANNER_VENV." >&2
  exit 1
fi

export FLASK_APP=run
cd "$ROOT"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Pre-Planner scheduled tasks"
"$VENV/bin/flask" ocr-pending     || echo "  ocr-pending failed" >&2
"$VENV/bin/flask" purge-sandboxes || echo "  purge-sandboxes failed" >&2
