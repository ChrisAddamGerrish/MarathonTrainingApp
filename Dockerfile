# syntax=docker/dockerfile:1
# The Marathon Training app in one image: the built React front end, served by the FastAPI app.
# Nothing personal goes into the image: the database, logs and Strava tokens live in /app/data and
# the cookie-signing key in /app/secrets (both volumes; see compose.yaml).

# --- Front end ---------------------------------------------------------------------------------
FROM node:24-alpine AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- App ---------------------------------------------------------------------------------------
FROM python:3.14-slim

# tzdata: the plan's "today" follows the TZ environment variable.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    MARATHON_AUTH_FILE=/app/secrets/auth.env

WORKDIR /app
# Dependencies first, so code changes don't reinstall them. uv is only mounted for this step, not shipped.
COPY pyproject.toml uv.lock ./
RUN --mount=from=ghcr.io/astral-sh/uv:0.12,source=/uv,target=/usr/local/bin/uv \
    uv sync --locked --no-dev --no-install-project
COPY backend/ backend/
COPY --from=frontend /src/frontend/dist frontend/dist

RUN useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin marathon \
    && mkdir -p data secrets && chown marathon:marathon data secrets
USER marathon

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]
# --proxy-headers: behind Caddy, trust its X-Forwarded-* headers (which hosts is FORWARDED_ALLOW_IPS)
# so the app sees https, sets secure cookies and builds the right Strava callback URL.
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
