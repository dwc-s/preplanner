# PythonAnywhere WSGI config for Pre-Planner (TEMPLATE).
#
# The installer writes a filled-in copy to deploy/wsgi_generated.py. If you'd
# rather do it by hand: replace <USERNAME> below with your PythonAnywhere
# username, then paste the whole file into your web app's WSGI file (the "Web"
# tab links to it, e.g. /var/www/<USERNAME>_pythonanywhere_com_wsgi.py).
#
# You do NOT need to set environment variables here — config.py loads them from
# the project's .env file automatically.
import sys

project_home = "/home/<USERNAME>/preplanner"
if project_home not in sys.path:
    sys.path.insert(0, project_home)

from app import create_app          # noqa: E402
application = create_app()
