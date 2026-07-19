# Deploying Pre-Planner on PythonAnywhere (MySQL)

A step-by-step guide to hosting Pre-Planner on [PythonAnywhere](https://www.pythonanywhere.com)
with a MySQL database. There's a **guided installer** that does the console-side
work for you, plus manual instructions if you'd rather do each step yourself.

Pre-Planner is a good fit for PythonAnywhere: all front-end libraries are
**vendored locally** (no CDN needed) and the server makes **no outbound network
calls**, so it runs even on a free "Beginner" account. Map tiles and any WMS
overlays load in the user's browser, not on the server.

---

## What you'll end up with

- A virtualenv built with the Python version you pick — either project-local
  (`~/preplanner/.venv`) or the PythonAnywhere virtualenvwrapper style
  (`~/.virtualenvs/preplanner`, usable with `workon preplanner`)
- A `.env` file holding your `SECRET_KEY` and MySQL `DATABASE_URL`
- The database schema created (via Flask-Migrate)
- Your first **admin ("root") account** and department
- A running web app at `https://<username>.pythonanywhere.com`

---

## 1. Get the code onto PythonAnywhere

Open **Consoles → Bash** and either clone your repo or upload the code:

```bash
git clone <your-repo-url> preplanner      # creates ~/preplanner
# ...or upload a .zip via the Files tab and unzip it to ~/preplanner
cd preplanner
```

## 2. Create the MySQL database

Go to the **Databases** tab:

1. If it's your first time, set a **MySQL password** (this initializes MySQL).
2. Under "Create a database", enter `preplanner`. PythonAnywhere prefixes it with
   your username, so the real name becomes **`<username>$preplanner`**.

Note the values you'll need:

| Setting  | Value                                               |
|----------|-----------------------------------------------------|
| Host     | `<username>.mysql.pythonanywhere-services.com`      |
| Username | `<username>` (your PythonAnywhere username)         |
| Database | `<username>$preplanner`                             |
| Password | the MySQL password you just set                     |

## 3. Run the installer

Back in the **Bash console**, from the project directory:

```bash
bash deploy/install_pythonanywhere.sh
```

It will:

- let you choose the **Python version** and the **virtualenv location**
  (project-local `.venv` or `~/.virtualenvs/preplanner`), then create it and
  install dependencies,
- ask for your MySQL host / username / database / password (with sensible
  defaults filled in) and generate a random `SECRET_KEY`,
- write `.env` (kept private, `chmod 600`),
- test the database connection,
- run `flask db upgrade` to create the tables,
- run `flask create-admin` so you can set your **admin email + password**,
- generate `deploy/wsgi_generated.py` ready to paste into the Web tab.

## 4. Create the web app

Go to the **Web** tab → **Add a new web app** → **Manual configuration** →
choose the **same Python version the installer used** (it prints it at the end,
e.g. Python 3.10). (Don't pick the "Flask" quick-start — Manual configuration
lets us use our own app factory and virtualenv.) A version mismatch between the
web app and the virtualenv is the most common cause of a broken deploy.

## 5. Configure the web app

Still on the **Web** tab:

- **Virtualenv:** enter the path the installer printed — either
  `/home/<username>/preplanner/.venv` or `/home/<username>/.virtualenvs/preplanner`.
- **WSGI configuration file:** click the link to edit it, delete the sample
  contents, and paste the contents of `deploy/wsgi_generated.py`
  (the installer prints its path; it's just three lines that add the project to
  `sys.path` and call `create_app()`). You do **not** set environment variables
  here — `config.py` reads them from `.env`.
- **Static files** (recommended for speed): add a mapping

  | URL        | Directory                              |
  |------------|----------------------------------------|
  | `/static/` | `/home/<username>/preplanner/app/static` |

## 6. Reload and sign in

Click the green **Reload** button, then open
`https://<username>.pythonanywhere.com` and sign in with the admin account you
created in step 3.

---

## The `.env` file

`config.py` loads `~/preplanner/.env` on startup (via python-dotenv). It holds:

```dotenv
SECRET_KEY='<random hex>'
DATABASE_URL='mysql+pymysql://<user>:<password>@<user>.mysql.pythonanywhere-services.com/<user>$preplanner?charset=utf8mb4'
```

Important details (the installer handles these for you):

- **Single-quote the values.** The database name contains a `$`
  (`<user>$preplanner`); single quotes stop it being treated as a variable.
- **URL-encode the password** if it contains special characters (`@ : / ? # %`
  etc.).
- Never commit `.env` — it's already in `.gitignore`.

To change any setting later, edit `.env` and hit **Reload**.

## The admin ("root") account

There is no public sign-up. The first account is created with:

```bash
.venv/bin/flask create-admin
```

It creates a **department** and an **admin** user in it. That admin can then add
crew members and reset passwords from the **Users** page, and configure map
layers from the **Layers** page. Run `create-admin` again to add another
department.

---

## Updating the app later

```bash
cd ~/preplanner
git pull
.venv/bin/pip install -r requirements.txt
.venv/bin/flask db upgrade      # applies any new migrations
# then click Reload on the Web tab
```

## Doing it manually (no script)

```bash
cd ~/preplanner

# Create the virtualenv with a SPECIFIC Python version — you must select the same
# version for the web app in the Web tab. Pick one of:
python3.10 -m venv .venv                                   # project-local
# mkvirtualenv --python=/usr/bin/python3.10 preplanner     # virtualenvwrapper (~/.virtualenvs/preplanner)

.venv/bin/pip install -r requirements.txt

# Create .env (mind the single quotes and URL-encoded password):
cat > .env <<'EOF'
SECRET_KEY='PASTE_A_RANDOM_HEX_STRING'
DATABASE_URL='mysql+pymysql://USER:PASSWORD@USER.mysql.pythonanywhere-services.com/USER$preplanner?charset=utf8mb4'
EOF
chmod 600 .env
# (generate a secret with:  .venv/bin/python -c 'import secrets;print(secrets.token_hex(32))')

export FLASK_APP=run            # or rely on the committed .flaskenv
.venv/bin/flask db upgrade
.venv/bin/flask create-admin
```

Then do Web-tab steps 4–6 above.

---

## Troubleshooting

Check **Web tab → Error log** first — it usually names the problem.

| Symptom | Fix |
|---|---|
| `ImportError: No module named 'app'` | `project_home` in the WSGI file must be `/home/<username>/preplanner`; make sure the Virtualenv field points at your venv path. |
| Web app won't start / odd import errors | The web app's **Python version must match the virtualenv's**. Re-check the version dropdown vs the version the installer used, and that the Virtualenv path is correct. |
| Site loads but is **unstyled** | Add/verify the `/static/` → `.../app/static` mapping and Reload. |
| `Access denied` / can't connect during install | Wrong MySQL password, or the database wasn't created in the Databases tab. Re-check host/user/db name and re-run. |
| `(2006) MySQL server has gone away` | Already mitigated by `pool_recycle`/`pool_pre_ping` in `config.py`; if you see it once after a long idle, just retry. |
| Changes to code or `.env` have no effect | Click the green **Reload** button. |
| `.env` values ignored | Ensure `python-dotenv` is installed in `.venv` (the installer does this) and `.env` is in the project root next to `config.py`. |
| Floor-plan image uploads fail | Uploads go to `~/preplanner/instance/uploads/`; make sure the disk isn't full and the folder is writable. |

## Notes on free accounts

- The app itself needs no outbound internet, so it works on the free tier.
- Free accounts expire web apps after ~3 months of no login and have CPU/traffic
  limits — fine for a single volunteer department. The always-maintained public
  instance would use a paid plan for reliability.
