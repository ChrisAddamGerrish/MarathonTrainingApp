import json
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

BASE_DIR = Path(__file__).parent
DIST_DIR = BASE_DIR / "frontend" / "dist"  # built React app (npm run build in frontend/)
DB_PATH = Path(os.environ.get("MARATHON_DB", BASE_DIR / "marathon.db"))

# The plan stores week + weekday, not calendar dates. Week 1 / Monday is the
# first logged day in activity_log (W1-Mon = 2026-09-14). Override with the
# MARATHON_PLAN_START env var (must be a Monday) if the plan is ever re-based.
PLAN_START = date.fromisoformat(os.environ.get("MARATHON_PLAN_START", "2026-09-14"))

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
RUN_CATEGORIES = ("Run", "Race")

ACTIVITY_COLUMNS = [
    "activity_date", "category", "actual_session", "distance_mi",
    "duration_min", "output_kj", "plan_id", "notes",
]


def _row_json(prefix: str) -> str:
    """SQL expression that snapshots an activity_log row (OLD or NEW) as JSON."""
    pairs = ", ".join(f"'{c}', {prefix}.{c}" for c in ACTIVITY_COLUMNS)
    return f"json_object({pairs})"


# Every change to activity_log is recorded by triggers, so edits made outside
# this app (PyCharm's DB tool, scripts, sqlite3 CLI) end up in the history too.
# History rows deliberately have no foreign key: they must outlive deleted activities.
HISTORY_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS activity_history (
    history_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    changed_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),  -- UTC
    action       TEXT NOT NULL CHECK (action IN ('INSERT', 'UPDATE', 'DELETE')),
    activity_id  INTEGER NOT NULL,
    old_values   TEXT,   -- JSON snapshot before the change (NULL for INSERT)
    new_values   TEXT,   -- JSON snapshot after the change  (NULL for DELETE)
    reverted_entry INTEGER  -- set when this change is a revert of history entry N
);

CREATE INDEX IF NOT EXISTS idx_activity_history_activity ON activity_history (activity_id);

CREATE TRIGGER IF NOT EXISTS activity_log_history_insert
AFTER INSERT ON activity_log
BEGIN
    INSERT INTO activity_history (action, activity_id, new_values)
    VALUES ('INSERT', NEW.activity_id, {_row_json('NEW')});
END;

CREATE TRIGGER IF NOT EXISTS activity_log_history_update
AFTER UPDATE ON activity_log
WHEN {" OR ".join(f"OLD.{c} IS NOT NEW.{c}" for c in ACTIVITY_COLUMNS)}
BEGIN
    INSERT INTO activity_history (action, activity_id, old_values, new_values)
    VALUES ('UPDATE', NEW.activity_id, {_row_json('OLD')}, {_row_json('NEW')});
END;

CREATE TRIGGER IF NOT EXISTS activity_log_history_delete
AFTER DELETE ON activity_log
BEGIN
    INSERT INTO activity_history (action, activity_id, old_values)
    VALUES ('DELETE', OLD.activity_id, {_row_json('OLD')});
END;
"""


# Sessions the athlete chose to skip. Kept out of training_plan and activity_log so the plan
# stays as written and "skipped" never masquerades as a logged activity.
SKIPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS plan_skips (
    plan_id     TEXT PRIMARY KEY REFERENCES training_plan (plan_id),
    reason      TEXT,
    skipped_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))  -- UTC
);
"""


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(HISTORY_SCHEMA)
        conn.executescript(SKIPS_SCHEMA)
        # Databases created before revert support lack this column.
        cols = [r[1] for r in conn.execute("PRAGMA table_info(activity_history)")]
        if "reverted_entry" not in cols:
            conn.execute("ALTER TABLE activity_history ADD COLUMN reverted_entry INTEGER")
            conn.commit()
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Marathon Training", lifespan=lifespan)


def get_db() -> Iterator[sqlite3.Connection]:
    # check_same_thread=False: FastAPI runs the dependency and the endpoint in
    # different worker threads, but each request still has its own connection.
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


Db = Depends(get_db)


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------

Category = Literal["Strength", "Bike", "Run", "Race", "Row", "Rest"]


class ActivityIn(BaseModel):
    activity_date: date
    category: Category
    actual_session: str = Field(min_length=1)
    distance_mi: Optional[float] = Field(None, ge=0)
    duration_min: Optional[float] = Field(None, ge=0)
    output_kj: Optional[float] = Field(None, ge=0)
    plan_id: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("actual_session", "plan_id", "notes", mode="before")
    @classmethod
    def strip_text(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("plan_id", "notes", mode="after")
    @classmethod
    def blank_to_none(cls, v):
        return v or None


class SkipIn(BaseModel):
    reason: Optional[str] = Field(None, max_length=200)

    @field_validator("reason", mode="before")
    @classmethod
    def clean_reason(cls, v):
        v = v.strip() if isinstance(v, str) else v
        return v or None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def week_of(d: date) -> int:
    """1-based plan week for a calendar date (<=0 before the plan, >20 after)."""
    return (d - PLAN_START).days // 7 + 1


def plan_date(week: int, day: str) -> date:
    return PLAN_START + timedelta(weeks=week - 1, days=DAYS.index(day))


def plan_status(row: dict, today: date) -> str:
    if row["category"] == "Rest":
        return "rest"
    if row["linked_activity_count"]:
        return "done"
    if row["skipped"]:
        return "skipped"
    if row["optional"]:
        return "optional"
    d = date.fromisoformat(row["date"])
    if d < today:
        return "missed"
    return "today" if d == today else "upcoming"


def load_plan(db: sqlite3.Connection, today: date) -> list[dict]:
    rows = db.execute(
        """
        SELECT p.plan_id, p.week, p.day, p.week_type, p.category, p.run_subtype,
               p.planned_session, p.target_distance_mi, p.target_duration_min, p.notes,
               v.actual_distance_mi, v.actual_duration_min,
               v.distance_variance_mi, v.duration_variance_min,
               v.linked_activity_count,
               s.plan_id IS NOT NULL AS skipped, s.reason AS skip_reason
        FROM training_plan p
        JOIN plan_vs_actual v ON v.plan_id = p.plan_id
        LEFT JOIN plan_skips s ON s.plan_id = p.plan_id
        ORDER BY p.week, p.rowid
        """
    ).fetchall()
    plan = []
    for r in rows:
        item = dict(r)
        item["date"] = plan_date(item["week"], item["day"]).isoformat()
        item["optional"] = item["planned_session"].startswith("Optional")
        item["skipped"] = bool(item["skipped"])
        item["status"] = plan_status(item, today)
        # Rest days, optional slots and skipped sessions never count for/against adherence.
        item["counts"] = item["category"] != "Rest" and not item["optional"] and item["status"] != "skipped"
        plan.append(item)
    # Stable order within a week: by weekday, keeping the table's own row order.
    plan.sort(key=lambda p: (p["week"], DAYS.index(p["day"])))
    return plan


def load_activities(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        "SELECT * FROM activity_log ORDER BY activity_date DESC, activity_id DESC"
    ).fetchall()
    activities = []
    for r in rows:
        item = dict(r)
        item["week"] = week_of(date.fromisoformat(item["activity_date"]))
        activities.append(item)
    return activities


def build_weeks(plan: list[dict], activities: list[dict]) -> list[dict]:
    weeks: dict[int, dict] = {}
    for p in plan:
        w = weeks.setdefault(
            p["week"],
            {
                "week": p["week"],
                "week_type": p["week_type"],
                "start": (PLAN_START + timedelta(weeks=p["week"] - 1)).isoformat(),
                "end": (PLAN_START + timedelta(weeks=p["week"] - 1, days=6)).isoformat(),
                "planned_run_mi": 0.0,
                "actual_run_mi": 0.0,
                "actual_bike_mi": 0.0,
                "planned_min": 0.0,
                "actual_min": 0.0,
                "sessions_planned": 0,
                "sessions_done": 0,
            },
        )
        if p["category"] in RUN_CATEGORIES:
            w["planned_run_mi"] += p["target_distance_mi"] or 0
        w["planned_min"] += p["target_duration_min"] or 0
        if p["counts"]:
            w["sessions_planned"] += 1
            w["sessions_done"] += 1 if p["status"] == "done" else 0
    # Actual volume follows the calendar date, so unplanned add-ons still count.
    for a in activities:
        w = weeks.get(a["week"])
        if not w:
            continue
        if a["category"] in RUN_CATEGORIES:
            w["actual_run_mi"] += a["distance_mi"] or 0
        elif a["category"] == "Bike":
            w["actual_bike_mi"] += a["distance_mi"] or 0
        w["actual_min"] += a["duration_min"] or 0
    result = [weeks[k] for k in sorted(weeks)]
    for w in result:
        for key in ("planned_run_mi", "actual_run_mi", "actual_bike_mi", "planned_min", "actual_min"):
            w[key] = round(w[key], 2)
    return result


def get_activity(db: sqlite3.Connection, activity_id: int) -> dict:
    row = db.execute("SELECT * FROM activity_log WHERE activity_id = ?", (activity_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"Activity {activity_id} not found")
    item = dict(row)
    item["week"] = week_of(date.fromisoformat(item["activity_date"]))
    return item


def check_plan_id(db: sqlite3.Connection, plan_id: Optional[str]) -> None:
    if plan_id and db.execute("SELECT 1 FROM training_plan WHERE plan_id = ?", (plan_id,)).fetchone() is None:
        raise HTTPException(422, f"Unknown plan session '{plan_id}'")


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

@app.get("/api/data")
def all_data(db: sqlite3.Connection = Db):
    """Everything the front end needs in one round trip (the dataset is tiny)."""
    today = date.today()
    plan = load_plan(db, today)
    activities = load_activities(db)
    weeks = build_weeks(plan, activities)

    last_week = max(w["week"] for w in weeks)
    race_date = plan_date(last_week, "Sun")
    due = [p for p in plan if p["counts"] and p["status"] in ("done", "missed")]
    summary = {
        "plan_start": PLAN_START.isoformat(),
        "race_date": race_date.isoformat(),
        "today": today.isoformat(),
        "days_to_race": (race_date - today).days,
        "current_week": week_of(today),
        "last_week": last_week,
        "total_run_mi": round(sum(a["distance_mi"] or 0 for a in activities if a["category"] in RUN_CATEGORIES), 2),
        "planned_run_mi_to_date": round(
            sum(p["target_distance_mi"] or 0 for p in plan
                if p["category"] in RUN_CATEGORIES and p["status"] != "skipped"
                and date.fromisoformat(p["date"]) <= today), 2),
        "sessions_due": len(due),
        "sessions_done": sum(1 for p in due if p["status"] == "done"),
        "sessions_skipped": sum(1 for p in plan if p["status"] == "skipped" and not p["optional"]),
        "total_minutes": round(sum(a["duration_min"] or 0 for a in activities), 1),
        "activity_count": len(activities),
    }
    return {"summary": summary, "weeks": weeks, "plan": plan, "activities": activities}


@app.put("/api/plan/{plan_id}/skip")
def skip_session(plan_id: str, body: Optional[SkipIn] = None, db: sqlite3.Connection = Db):
    """Mark a planned session as deliberately skipped (optionally with a reason)."""
    row = db.execute("SELECT category FROM training_plan WHERE plan_id = ?", (plan_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"Unknown plan session '{plan_id}'")
    if row["category"] == "Rest":
        raise HTTPException(422, "Rest days can't be skipped")
    logged = db.execute("SELECT COUNT(*) FROM activity_log WHERE plan_id = ?", (plan_id,)).fetchone()[0]
    if logged:
        raise HTTPException(409, "That session already has a logged activity, so it can't be skipped")
    reason = body.reason if body else None
    db.execute(
        "INSERT INTO plan_skips (plan_id, reason) VALUES (?, ?) "
        "ON CONFLICT (plan_id) DO UPDATE SET reason = excluded.reason",
        (plan_id, reason),
    )
    db.commit()
    return {"plan_id": plan_id, "skipped": True, "reason": reason}


@app.delete("/api/plan/{plan_id}/skip")
def unskip_session(plan_id: str, db: sqlite3.Connection = Db):
    cur = db.execute("DELETE FROM plan_skips WHERE plan_id = ?", (plan_id,))
    db.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "That session isn't skipped")
    return {"plan_id": plan_id, "skipped": False}


@app.post("/api/activities", status_code=201)
def create_activity(body: ActivityIn, db: sqlite3.Connection = Db):
    check_plan_id(db, body.plan_id)
    cur = db.execute(
        """
        INSERT INTO activity_log
            (activity_date, category, actual_session, distance_mi, duration_min, output_kj, plan_id, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (body.activity_date.isoformat(), body.category, body.actual_session,
         body.distance_mi, body.duration_min, body.output_kj, body.plan_id, body.notes),
    )
    db.commit()
    return get_activity(db, cur.lastrowid)


@app.put("/api/activities/{activity_id}")
def update_activity(activity_id: int, body: ActivityIn, db: sqlite3.Connection = Db):
    get_activity(db, activity_id)
    check_plan_id(db, body.plan_id)
    db.execute(
        """
        UPDATE activity_log
        SET activity_date = ?, category = ?, actual_session = ?, distance_mi = ?,
            duration_min = ?, output_kj = ?, plan_id = ?, notes = ?
        WHERE activity_id = ?
        """,
        (body.activity_date.isoformat(), body.category, body.actual_session,
         body.distance_mi, body.duration_min, body.output_kj, body.plan_id, body.notes, activity_id),
    )
    db.commit()
    return get_activity(db, activity_id)


@app.delete("/api/activities/{activity_id}")
def delete_activity(activity_id: int, db: sqlite3.Connection = Db):
    get_activity(db, activity_id)
    db.execute("DELETE FROM activity_log WHERE activity_id = ?", (activity_id,))
    db.commit()
    return {"deleted": activity_id}


@app.get("/api/history")
def history(
    activity_id: Optional[int] = None,
    limit: int = Query(200, ge=1, le=1000),
    db: sqlite3.Connection = Db,
):
    """Change log for activity_log, newest first."""
    sql = "SELECT * FROM activity_history"
    params: list = []
    if activity_id is not None:
        sql += " WHERE activity_id = ?"
        params.append(activity_id)
    sql += " ORDER BY history_id DESC LIMIT ?"
    params.append(limit)

    entries = []
    for r in db.execute(sql, params):
        old = json.loads(r["old_values"]) if r["old_values"] else None
        new = json.loads(r["new_values"]) if r["new_values"] else None
        changes = (
            [{"field": c, "old": old[c], "new": new[c]} for c in ACTIVITY_COLUMNS if old[c] != new[c]]
            if old and new else []
        )
        entries.append({
            "history_id": r["history_id"],
            "changed_at": r["changed_at"],
            "action": r["action"],
            "activity_id": r["activity_id"],
            "old": old,
            "new": new,
            "changes": changes,
            "reverts": r["reverted_entry"],
        })
    return entries


@app.post("/api/history/{history_id}/revert")
def revert_history(history_id: int, db: sqlite3.Connection = Db):
    """Undo one history entry.

    - INSERT -> delete the activity again.
    - DELETE -> re-create it with its original id and values.
    - UPDATE -> put back the old value of each field that entry changed (only those
      fields, so later edits to other fields are left alone).

    The revert is an ordinary change, so the triggers record it too; we tag that new
    entry with the one it reverts.
    """
    entry = db.execute("SELECT * FROM activity_history WHERE history_id = ?", (history_id,)).fetchone()
    if entry is None:
        raise HTTPException(404, f"History entry {history_id} not found")
    done = db.execute("SELECT history_id FROM activity_history WHERE reverted_entry = ?", (history_id,)).fetchone()
    if done:
        raise HTTPException(409, "That change has already been reverted")

    old = json.loads(entry["old_values"]) if entry["old_values"] else None
    new = json.loads(entry["new_values"]) if entry["new_values"] else None
    activity_id = entry["activity_id"]
    current = db.execute("SELECT * FROM activity_log WHERE activity_id = ?", (activity_id,)).fetchone()

    try:
        if entry["action"] == "INSERT":
            if current is None:
                raise HTTPException(409, "That activity has already been removed")
            db.execute("DELETE FROM activity_log WHERE activity_id = ?", (activity_id,))
        elif entry["action"] == "DELETE":
            if current is not None:
                raise HTTPException(409, "That activity already exists")
            cols = ", ".join(ACTIVITY_COLUMNS)
            marks = ", ".join("?" for _ in ACTIVITY_COLUMNS)
            db.execute(
                f"INSERT INTO activity_log (activity_id, {cols}) VALUES (?, {marks})",
                [activity_id, *(old[c] for c in ACTIVITY_COLUMNS)],
            )
        else:  # UPDATE
            if current is None:
                raise HTTPException(409, "That activity has since been deleted; restore the deletion first")
            restore = {c: old[c] for c in ACTIVITY_COLUMNS if old[c] != new[c] and current[c] != old[c]}
            if not restore:
                raise HTTPException(409, "The activity already has those values")
            sets = ", ".join(f"{c} = ?" for c in restore)
            db.execute(f"UPDATE activity_log SET {sets} WHERE activity_id = ?", [*restore.values(), activity_id])

        # The triggers just wrote the history row for this revert; tag it.
        new_entry = db.execute(
            "SELECT MAX(history_id) FROM activity_history WHERE activity_id = ?", (activity_id,)
        ).fetchone()[0]
        db.execute("UPDATE activity_history SET reverted_entry = ? WHERE history_id = ?", (history_id, new_entry))
        db.commit()
    except sqlite3.IntegrityError as e:
        db.rollback()
        raise HTTPException(409, f"Can't revert: {e}")
    except HTTPException:
        db.rollback()
        raise

    row = db.execute("SELECT * FROM activity_log WHERE activity_id = ?", (activity_id,)).fetchone()
    return {"history_id": new_entry, "activity": dict(row) if row else None}


# --------------------------------------------------------------------------
# Front end
# --------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
def index():
    if not (DIST_DIR / "index.html").exists():
        return HTMLResponse(
            "<h1>Front end not built</h1>"
            "<p>Run <code>npm install</code> and <code>npm run build</code> in the "
            "<code>frontend</code> folder, then reload. (For development, run "
            "<code>npm run dev</code> there and open http://localhost:5173.)</p>",
            status_code=503,
        )
    # index.html is tiny and must never be stale, or it would point at old hashed bundles.
    return FileResponse(DIST_DIR / "index.html", headers={"Cache-Control": "no-cache"})


# Vite writes hashed JS/CSS bundles to dist/assets. check_dir=False lets the app start (and
# pick the folder up later) even if the front end hasn't been built yet.
app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets", check_dir=False), name="assets")
