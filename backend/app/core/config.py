import os
from datetime import date
from pathlib import Path

# Project root directory
BASE_DIR = Path(__file__).resolve().parents[3]
DIST_DIR = BASE_DIR / "frontend" / "dist"  # built React app (npm run build in frontend/)
DB_PATH = Path(os.environ.get("MARATHON_DB", BASE_DIR / "marathon.db"))

# The web app's login (user, password hash, cookie-signing key). See backend/app/core/auth.py.
AUTH_FILE = Path(os.environ.get("MARATHON_AUTH_FILE", BASE_DIR / "auth.env"))

# Strava import (see backend/app/services/strava.py). strava.env holds the Strava API app's
# STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET; the token file is written when you connect. Both are
# git-ignored, and kept out of marathon.db because the database is committed.
STRAVA_CONFIG_FILE = Path(os.environ.get("MARATHON_STRAVA_CONFIG", BASE_DIR / "strava.env"))
STRAVA_TOKEN_FILE = Path(os.environ.get("MARATHON_STRAVA_TOKENS", BASE_DIR / "strava_tokens.json"))


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines from a settings file (blank lines and # comments ignored); {} if it's missing."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    values = {}
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values

# Log files (app.log for the web app, mcp_server.log for the MCP server) go in LOG_DIR. LOG_LEVEL is
# a standard level name: DEBUG, INFO, WARNING, ERROR.
LOG_DIR = Path(os.environ.get("MARATHON_LOG_DIR", BASE_DIR / "logs"))
LOG_LEVEL = os.environ.get("MARATHON_LOG_LEVEL", "INFO").upper()

# The plan stores week + weekday, not calendar dates. Week 1 / Monday is the
# first logged day in activity_log (W1-Mon = 2026-09-14). Override with the
# MARATHON_PLAN_START env var (must be a Monday) if the plan is ever re-based.
PLAN_START = date.fromisoformat(os.environ.get("MARATHON_PLAN_START", "2026-09-14"))
