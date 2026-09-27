# Marathon Training

A training log for a marathon plan. It shows planned sessions week by week, the workouts you
logged against them, and how closely you're following the plan. Workouts can be imported from
Strava. It has invite-only user accounts, each with its own plan and data, and an MCP server so
Claude (or another MCP client) can read and update the log.

- **Backend:** FastAPI and SQLAlchemy on SQLite (`backend/`)
- **Frontend:** React, built with Vite (`frontend/`)
- **Proxy:** Caddy, optional, for reaching the app from other devices (`deploy/`)

## Getting started (Windows)

Requirements: Python 3.14+ (or [uv](https://docs.astral.sh/uv/)) and Node.js.

Double-click `start.bat`, or run:

```powershell
.\start.ps1
```

On the first run it:

1. creates `.venv` and installs the Python dependencies;
2. builds the frontend;
3. asks for a username and password to create the first admin account;
4. starts the app at http://localhost:8000 and opens your browser;
5. starts Caddy, installing it with winget if needed, and asks where it should listen.

Useful options:

| Option        | What it does                                                             |
|---------------|--------------------------------------------------------------------------|
| `-Dev`        | Auto-reloads the server and runs the Vite dev server on port 5173 (no Caddy) |
| `-LocalOnly`  | Skips Caddy, so the app is only reachable from this PC                   |
| `-Port 8100`  | Runs the app on a different port                                         |
| `-ResetLogin` | Changes a user's password, or adds an admin                              |
| `-Rebuild`    | Forces a fresh frontend build                                            |
| `-NoBrowser`  | Doesn't open the browser                                                 |

### Accounts

Admins invite people from the app itself. To manage sign-in from the command line:

```powershell
.venv\Scripts\python.exe -m backend.cli           # set a password (creates an admin if the user is new)
.venv\Scripts\python.exe -m backend.cli invite    # print a single-use invite code
```

### Strava

1. Create an API application at https://www.strava.com/settings/api. Set its
   *Authorization Callback Domain* to the host you open the app on (e.g. `localhost`).
2. Copy `strava.env.example` to `strava.env` and fill in the client ID and secret.
3. Each user connects their own Strava account; it's the last step of registering. Syncing is
   manual, from **Sync now** on the Activity log page.

Strava allows one connected athlete per API application (its owner) until they approve more.

### MCP server

`.mcp.json` registers the server with Claude Code as `marathon`. For other clients, run
`python -m backend.mcp_server` from the project root; it speaks MCP over stdio. It works on the
data of the user named in `MARATHON_MCP_USER`, or the first admin if that isn't set.

## Docker

`Dockerfile` builds one image (frontend included) that runs as a non-root user. `compose.yaml`
runs it with two named volumes: `data` (the database, backups, logs and Strava tokens) and
`secrets` (`auth.env`, the cookie-signing key). No data or secrets go into the image.

```powershell
docker compose up -d --build                    # the app, on http://localhost:8000 (this PC only)
docker compose exec app python -m backend.cli   # create the first admin account
```

The CLI asks a new account for its plan's start date (the Monday of week 1). When you'll import a
plan backup, give the same Monday as the old install: the plan stores weeks and weekdays, and the
dates follow from that.

**Settings** (all optional and git-ignored):

- `.env` (copy `.env.example`): `TZ`, which makes the plan's "today" your today (the default is
  UTC); `MARATHON_PORT`; `MARATHON_LOG_LEVEL`.
- `strava.env` is passed to the container as environment variables, so the same file works for
  Docker and `start.ps1`. Restart the container after changing it: `docker compose up -d`.

**On your home network without a proxy** (e.g. a home lab machine), set `MARATHON_BIND=0.0.0.0` and
`FORWARDED_ALLOW_IPS=127.0.0.1` in `.env`, then open `http://<machine's IP>:8000`. This is plain
HTTP, so keep it to a network you trust.

**Reaching it from other devices with HTTPS:** add the Caddy proxy. It uses `deploy/Caddyfile` and
`deploy/caddy.env`, the same as `start.ps1`, and publishes ports 80, 443 and 8080.

```powershell
docker compose --profile proxy up -d --build
```

The app itself is only published on `127.0.0.1`. Keep it that way while Caddy is in front: the app
trusts the proxy's `X-Forwarded-*` headers (for secure cookies and the Strava callback URL).

**MCP server:** point your MCP client at
`docker compose exec -T app python -m backend.mcp_server`, run from this folder.

**Moving existing data in** (from a `start.ps1` install). Stop the app first, then:

```powershell
docker compose create app
docker compose cp data\. app:/app/data/
docker compose cp auth.env app:/app/secrets/auth.env
docker compose run --rm --no-deps --user root --entrypoint sh app -c "chown -R marathon:marathon /app/data /app/secrets"
docker compose up -d
```

Copying `auth.env` keeps everyone signed in. Without it, people just sign in again.

**Backups:** the data lives in the `marathon_data` volume. The app's **Export CSV** buttons, or
`docker compose cp app:/app/data/marathon.db .`, get a copy out.

## Project layout

```
backend/
  app/
    api/          FastAPI routes and dependencies (who is signed in)
    core/         config, auth, database engine and sessions, migrations, history triggers
    services/     business logic: planning, Strava sync, accounts, CSV backups
    models.py     SQLAlchemy models
    repository.py all database reads and writes
    schemas.py    request bodies (Pydantic)
    main.py       the FastAPI app
  cli.py          command-line account management
  mcp_server.py   MCP server
frontend/src/     React app: views/, components/, contexts/, hooks/, services/, utils/
deploy/           Caddyfile and caddy.env.example
Dockerfile, compose.yaml, .env.example   Docker setup (see "Docker")
tests/            unittest suite (runs against a temporary copy of tests/fixtures/single-user.db)
data/             created at runtime, git-ignored: marathon.db, backups/, strava_tokens.json, logs/
```

These files hold secrets and are git-ignored: `auth.env` (the cookie-signing key, created
automatically), `strava.env`, and `deploy/caddy.env`. Paths can be overridden with environment
variables such as `MARATHON_DB` and `MARATHON_LOG_DIR`; see `backend/app/core/config.py`.

## Tests

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

Type checking: `.venv\Scripts\pyrefly.exe check`.
