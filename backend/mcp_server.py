"""MCP server: lets an MCP client (Claude Desktop, Claude Code, ...) read and update the marathon log.

Run it with:   python -m backend.mcp_server        (speaks MCP over stdio)

Connecting a client (it must be started from the project root so `backend` is importable):
  - Claude Code: the project's .mcp.json already registers it as "marathon".
  - Claude Desktop: add the same entry to claude_desktop_config.json under "mcpServers", with an
    absolute path to .venv\\Scripts\\python.exe and "cwd" set to the project root.

It works on one user's data: the account named by $MARATHON_MCP_USER, or else the first admin
(the database's original owner). Like the web app, it only ever sees that user's rows.

There is no AI in here. It just exposes a set of tools; whichever client connects decides when to
call them. Every tool goes through the same repository layer and validation as the REST API, and
the history triggers live in the database, so changes made here show up in the History tab and can
be reverted from there like any other change.

stdout is the protocol channel: never print() in this module (log to stderr instead).

Logging: every tool call is logged (name, arguments, outcome, duration), and so are the data changes
the repository makes. Records go to stderr, which most clients discard, and to logs/mcp_server.log
(see core/logging_config.py; $MARATHON_LOG_DIR and $MARATHON_LOG_LEVEL change where and how much).
"""
import functools
import logging
import os
import time
from contextlib import contextmanager
from datetime import date
from typing import Annotated, Any, Callable, Optional

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError
from sqlalchemy import select

from backend.app.core import config
from backend.app.core.database import SessionLocal, Tenant, tenant_of, user_session, init_db
from backend.app.core.errors import AppError
from backend.app.core.logging_config import setup_logging
from backend.app.models.models import ACTIVITY_COLUMNS, User
from backend.app.repository import repository as repo
from backend.app.schemas.schemas import ActivityIn, Category
from backend.app.services import planning

mcp = MCPServer(
    "marathon",
    instructions=(
        "Marathon training log. Distances are miles, durations are minutes, dates are YYYY-MM-DD. "
        "A plan_id (like 'W1-Thu') links an activity to a planned session; without one it is an "
        "unplanned add-on. Call get_week first to find valid plan_ids and see what is done, missed "
        "or skipped. Changes are recorded in the history and can be undone with revert_history_entry."
    ),
)

log = logging.getLogger("marathon.mcp")

READ = ToolAnnotations(read_only_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True)


def tool(annotations: ToolAnnotations) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a function as an MCP tool and log each call: arguments, outcome and duration.
    Argument-validation failures happen before this runs and are logged by the MCP library."""
    def register(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)  # keeps the signature and docstring the client sees
        def logged(*args: Any, **kwargs: Any) -> Any:
            shown = ", ".join(f"{k}={v!r:.60}" for k, v in kwargs.items() if v is not None)
            started = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
            except ToolError as e:  # the client is told why
                log.warning("%s(%s) refused: %s", fn.__name__, shown, e)
                raise
            except Exception:  # the client only sees a generic error, so keep the traceback here
                log.exception("%s(%s) crashed", fn.__name__, shown)
                raise
            log.info("%s(%s) ok in %.0f ms", fn.__name__, shown, (time.perf_counter() - started) * 1000)
            return result
        return mcp.tool(annotations=annotations)(logged)
    return register


# What get_week reports for each planned session.
_SESSION_FIELDS = (
    "plan_id", "day", "date", "category", "planned_session", "target_distance_mi", "target_duration_min",
    "status", "actual_distance_mi", "actual_duration_min", "skip_reason",
)


_tenant: Optional[Tenant] = None


def tenant() -> Tenant:
    """The user this server works for (see the module docstring), looked up once."""
    global _tenant
    if _tenant is None:
        name = os.environ.get("MARATHON_MCP_USER")
        with SessionLocal() as session:
            stmt = select(User).where(User.username == name) if name else \
                select(User).where(User.is_admin).order_by(User.user_id).limit(1)
            user = session.scalars(stmt).first()
        if user is None:
            raise ToolError(f"No user named {name!r}." if name else "There are no users yet.")
        _tenant = Tenant(user.user_id, user.plan_start)
        log.info("Working on the data of %s (user %s)", user.username, user.user_id)
    return _tenant


@contextmanager
def db():
    """One database session per tool call, over the server's user's data. The project's domain
    errors become tool errors that keep their message, so the client can read why a call failed
    (anything else is hidden)."""
    with user_session(tenant()) as session:
        try:
            yield session
        except AppError as e:
            raise ToolError(e.detail) from e


def _activity_values(**fields: Any) -> dict[str, Any]:
    """Validate with the same model as the REST API; report problems in plain words."""
    try:
        return ActivityIn(**fields).model_dump()
    except ValidationError as e:
        raise ToolError("; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())) from e


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------

@tool(READ)
def get_summary() -> dict[str, Any]:
    """Race countdown, current training week, miles run, plan adherence and skipped sessions."""
    with db() as s:
        today, start = date.today(), tenant_of(s).plan_start
        plan = planning.build_plan(repo.plan_rows(s), today, start)
        acts = repo.activity_rows(s)
        weeks = planning.build_weeks(repo.plan_week_rows(s), plan, acts, start)
        return planning.build_summary(plan, acts, weeks, today, start)


@tool(READ)
def get_week(week: Optional[int] = None) -> dict[str, Any]:
    """One training week (default: the current week): its totals, every planned session with its
    plan_id, status (done / missed / upcoming / skipped ...) and what was logged, plus activities
    logged that week without a plan link. Use the plan_ids here when logging activities."""
    with db() as s:
        today, start = date.today(), tenant_of(s).plan_start
        plan = planning.build_plan(repo.plan_rows(s), today, start)
        acts = repo.activity_rows(s)
        weeks = planning.build_weeks(repo.plan_week_rows(s), plan, acts, start)
        last = weeks[-1]["week"]
        number = week if week is not None else min(max(planning.week_of(today, start), 1), last)
        totals = next((w for w in weeks if w["week"] == number), None)
        if totals is None:
            raise ToolError(f"Week {number} is not in the plan (weeks 1-{last}).")
        return {
            "week": totals,
            "sessions": [{k: p[k] for k in _SESSION_FIELDS} for p in plan if p["week"] == number],
            "unplanned_activities": [a for a in acts if a["week"] == number and not a["plan_id"]],
        }


@tool(READ)
def list_activities(
    limit: Annotated[int, Field(ge=1, le=200)] = 20,
    week: Optional[int] = None,
    category: Optional[Category] = None,
) -> list[dict[str, Any]]:
    """Logged activities, newest first, optionally only one plan week and/or one category."""
    with db() as s:
        rows = repo.activity_rows(s)
    if week is not None:
        rows = [a for a in rows if a["week"] == week]
    if category is not None:
        rows = [a for a in rows if a["category"] == category]
    return rows[:limit]


@tool(READ)
def list_history(
    limit: Annotated[int, Field(ge=1, le=200)] = 10,
    activity_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Recent changes to the activity log (adds, edits, deletes), newest first, including changes
    made outside this app. Each entry has the history_id needed to revert it."""
    with db() as s:
        return repo.history_entries(s, activity_id, limit)


# --------------------------------------------------------------------------
# Write
# --------------------------------------------------------------------------

@tool(WRITE)
def log_activity(
    activity_date: date,
    category: Category,
    actual_session: str,
    distance_mi: Optional[float] = None,
    duration_min: Optional[float] = None,
    output_kj: Optional[float] = None,
    plan_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict[str, Any]:
    """Log something you did. Set plan_id (from get_week) to count it toward a planned session."""
    values = _activity_values(**locals())
    with db() as s:
        return repo.create_activity(s, values)


@tool(WRITE)
def update_activity(
    activity_id: int,
    activity_date: Optional[date] = None,
    category: Optional[Category] = None,
    actual_session: Optional[str] = None,
    distance_mi: Optional[float] = None,
    duration_min: Optional[float] = None,
    output_kj: Optional[float] = None,
    plan_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict[str, Any]:
    """Change some fields of an activity; anything you leave out stays as it is. Pass an empty
    string for plan_id or notes to clear them (an empty plan_id unlinks it from its session)."""
    given = dict(locals())
    changes = {k: v for k, v in given.items() if k != "activity_id" and v is not None}
    with db() as s:
        current = repo.get_activity(s, activity_id)
        values = _activity_values(**({c: current[c] for c in ACTIVITY_COLUMNS} | changes))
        return repo.update_activity(s, activity_id, values)


@tool(WRITE)
def skip_planned_session(plan_id: str, reason: Optional[str] = None) -> dict[str, Any]:
    """Mark a planned session as deliberately skipped. It then counts neither as done nor missed.
    Not allowed for rest days or sessions that already have a logged activity."""
    with db() as s:
        return repo.skip_session(s, plan_id, reason)


@tool(WRITE)
def unskip_planned_session(plan_id: str) -> dict[str, Any]:
    """Remove the skip from a planned session."""
    with db() as s:
        return repo.unskip_session(s, plan_id)


# --------------------------------------------------------------------------
# Destructive (recoverable through history)
# --------------------------------------------------------------------------

@tool(DESTRUCTIVE)
def delete_activity(activity_id: int) -> dict[str, Any]:
    """Delete an activity. The deletion is recorded in history and can be undone with
    revert_history_entry."""
    with db() as s:
        return repo.delete_activity(s, activity_id)


@tool(DESTRUCTIVE)
def revert_history_entry(history_id: int) -> dict[str, Any]:
    """Undo one history entry (find ids with list_history). An add is removed, a delete is
    restored, and an edit puts back the old value of just the fields that edit changed."""
    with db() as s:
        return repo.revert_history(s, history_id)


if __name__ == "__main__":
    setup_logging("mcp_server.log")
    init_db()
    log.info("marathon MCP server starting (db %s)", config.DB_PATH)
    try:
        mcp.run()  # stdio
    finally:
        log.info("marathon MCP server stopped")
