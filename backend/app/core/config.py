import os
from datetime import date
from pathlib import Path

# Project root directory
BASE_DIR = Path(__file__).resolve().parents[3]
DIST_DIR = BASE_DIR / "frontend" / "dist"  # built React app (npm run build in frontend/)
DB_PATH = Path(os.environ.get("MARATHON_DB", BASE_DIR / "marathon.db"))

# Log files (app.log for the web app, mcp_server.log for the MCP server) go in LOG_DIR. LOG_LEVEL is
# a standard level name: DEBUG, INFO, WARNING, ERROR.
LOG_DIR = Path(os.environ.get("MARATHON_LOG_DIR", BASE_DIR / "logs"))
LOG_LEVEL = os.environ.get("MARATHON_LOG_LEVEL", "INFO").upper()

# The plan stores week + weekday, not calendar dates. Week 1 / Monday is the
# first logged day in activity_log (W1-Mon = 2026-09-14). Override with the
# MARATHON_PLAN_START env var (must be a Monday) if the plan is ever re-based.
PLAN_START = date.fromisoformat(os.environ.get("MARATHON_PLAN_START", "2026-09-14"))
