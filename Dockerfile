# Turnkey container for self-hosting.
#   Build:  docker build -t preplanner .
#   Run:    docker run -p 8000:8000 -e SECRET_KEY=$(openssl rand -hex 32) \
#                       -v preplanner-data:/app/instance preplanner
#   Then:   docker exec -it <container> flask create-admin
FROM python:3.12-slim

# tesseract-ocr powers optional photo text search (the `ocr-pending` task). Pillow,
# pypdf and pillow-heif ship self-contained wheels, so nothing else is needed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x deploy/docker-entrypoint.sh

# The entrypoint runs `flask db upgrade` on start, so a fresh instance/ volume is
# ready immediately. Mount a volume at /app/instance to persist the SQLite DB and
# uploaded files (or set DATABASE_URL to point at an external database).
ENV FLASK_APP=run
EXPOSE 8000
ENTRYPOINT ["/app/deploy/docker-entrypoint.sh"]
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "app:create_app()"]
