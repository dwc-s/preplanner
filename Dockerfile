# Minimal container for turnkey self-hosting.
# Build:  docker build -t preplanner .
# Run:    docker run -p 8000:8000 -e SECRET_KEY=change-me preplanner
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Missing tables are created at startup by the app factory.
EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "app:create_app()"]
