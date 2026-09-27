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
