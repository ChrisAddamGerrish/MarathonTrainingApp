# syntax=docker/dockerfile:1
# The Marathon Training app in one image: the built React front end, served by the FastAPI app.
# Nothing personal goes into the image: the database, logs and Strava tokens live in /app/data and
# the cookie-signing key in /app/secrets (both volumes; see compose.yaml).
#
# Kept small: Python and the app are installed and pruned in the `runtime` stage, then copied into an
# empty image, so what the pruning removes really is gone (deleting files in a later layer of the same
# image would not make it smaller).

# --- Front end ---------------------------------------------------------------------------------
FROM node:24-alpine AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Python, the app's dependencies, and pruning -----------------------------------------------
# Alpine: every compiled dependency has a musl wheel in uv.lock, so nothing is built from source.
FROM python:3.14-alpine AS runtime

# tzdata: the plan's "today" follows the TZ environment variable.
RUN apk add --no-cache tzdata

WORKDIR /app
# uv and its download cache are only mounted for this step, so neither ends up in the image.
# greenlet is left out: it's SQLAlchemy's asyncio support, and the app's sessions are synchronous.
# (cryptography stays, big as it is: the mcp package imports it on startup.)
COPY pyproject.toml uv.lock ./
RUN --mount=from=ghcr.io/astral-sh/uv:0.12,source=/uv,target=/usr/local/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_COMPILE_BYTECODE=0 \
    uv sync --locked --no-dev --no-install-project --no-install-package greenlet

# Remove what a server never runs. Python compiles modules in memory at startup instead of shipping
# bytecode (PYTHONDONTWRITEBYTECODE below: the code is read-only to the app's user anyway).
RUN set -eux; \
    py=/usr/local/lib/python3.14; sp=/opt/venv/lib/python3.14/site-packages; \
    # Python: pip, IDLE, Tk, docs, tests, build config, and the modules that need readline/ncurses/gdbm
    rm -rf $py/site-packages/pip* $py/ensurepip $py/idlelib $py/tkinter $py/turtledemo $py/turtle.py \
        $py/pydoc_data $py/test $py/__phello__ $py/config-3.14-* \
        $py/lib-dynload/_test* $py/lib-dynload/xxlimited* $py/lib-dynload/_ctypes_test* \
        $py/lib-dynload/readline.* $py/lib-dynload/_curses* $py/lib-dynload/_dbm.* $py/lib-dynload/_gdbm.* \
        /usr/local/bin/pip* /usr/local/bin/idle* /usr/local/bin/pydoc* /usr/local/bin/python*-config \
        /usr/local/lib/pkgconfig /usr/local/include; \
    # SQLAlchemy: other databases' dialects, its test suite, and the optional C speedups (it falls
    # back to pure Python)
    rm -rf $sp/sqlalchemy/dialects/mysql $sp/sqlalchemy/dialects/postgresql $sp/sqlalchemy/dialects/oracle \
        $sp/sqlalchemy/dialects/mssql $sp/sqlalchemy/testing $sp/sqlalchemy/cyextension/*.so; \
    # Type stubs, bundled test suites and any bytecode
    find / -xdev \( -name '*.pyi' -o -name '*.pyc' \) -delete; \
    find / -xdev -type d \( -name __pycache__ -o -path "$sp/*/tests" \) -prune -exec rm -rf {} +; \
    # Alpine: the package manager, and the terminal/dbm libraries nothing uses any more
    rm -rf /sbin/apk /etc/apk /lib/apk /usr/share/apk /var/cache/apk /usr/lib/libapk* \
        /usr/lib/libreadline* /usr/lib/libncurses* /usr/lib/libpanel* /usr/lib/libform* /usr/lib/libmenu* \
        /usr/lib/libgdbm* /usr/share/terminfo /etc/terminfo

COPY backend/ backend/
COPY --from=frontend /src/frontend/dist frontend/dist
RUN adduser -D -H -u 1000 -s /sbin/nologin marathon \
    && mkdir -p data secrets && chown marathon:marathon data secrets

# --- The image ---------------------------------------------------------------------------------
FROM scratch
COPY --from=runtime / /

ENV PATH=/opt/venv/bin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MARATHON_AUTH_FILE=/app/secrets/auth.env
WORKDIR /app
USER marathon

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]
# --proxy-headers: behind Caddy, trust its X-Forwarded-* headers (which hosts is FORWARDED_ALLOW_IPS)
# so the app sees https, sets secure cookies and builds the right Strava callback URL.
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
