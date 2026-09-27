"""One-time upgrades of older databases, run by database.init_db, and the backup made before each.

Each works on the database file directly (sqlite3, not the ORM), in one transaction, and does
nothing when the database is already up to date.
"""
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateTable

from backend.app.core import config
from backend.app.core.config import DB_PATH
from backend.app.models import ACTIVITY_CATEGORIES, PLAN_VS_ACTUAL_VIEW, TABLES

log = logging.getLogger("marathon.db")

_CATEGORY_CHECK = re.compile(r"CHECK\s*\(\s*category\s+IN\s*\(([^)]*)\)\s*\)", re.IGNORECASE)


def backup_database(reason: str, db_path: Path = DB_PATH) -> str:
    """Copy the database to backups/marathon-<time>-<reason>.db in its folder; returns the path."""
    folder = Path(db_path).parent / "backups"
    folder.mkdir(exist_ok=True)
    target = folder / f"marathon-{datetime.now():%Y%m%d-%H%M%S}-{reason}.db"
    source, copy = sqlite3.connect(db_path), sqlite3.connect(target)
    try:
        source.backup(copy)
    finally:
        source.close()
        copy.close()
    return str(target)


def allow_activity_categories(db_path: Path = DB_PATH) -> bool:
    """Make activity_log's CHECK constraint accept every category in ACTIVITY_CATEGORIES.

    activity_log predates the app and lists its categories in a CHECK constraint, which SQLite
    can't alter. So when one is missing (Stretch was added later) the table is rebuilt the way
    SQLite documents (https://sqlite.org/lang_altertable.html#otheralter), in one transaction:
    same columns, rows and ids, and the AUTOINCREMENT counter is kept so deleted ids are never
    reused (history refers to them). The view over it is dropped and recreated around the
    rebuild; the history triggers go with the old table and init_db reinstalls them. The
    database is backed up first. Returns whether anything changed.
    """
    con = sqlite3.connect(db_path, isolation_level=None, timeout=30)
    try:
        row = con.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'activity_log'").fetchone()
        match = _CATEGORY_CHECK.search(row[0]) if row else None
        if match is None:
            return False
        allowed = re.findall(r"'([^']*)'", match.group(1))
        missing = [c for c in ACTIVITY_CATEGORIES if c not in allowed]
        if not missing:
            return False

        backup = backup_database("before-categories", db_path)
        check = "CHECK (category IN (" + ",".join(f"'{c}'" for c in allowed + missing) + "))"
        create = row[0][:match.start()] + check + row[0][match.end():]
        create = re.sub(r"^CREATE TABLE\s+\"?activity_log\"?", "CREATE TABLE activity_log_new", create, count=1)
        views = con.execute("SELECT name, sql FROM sqlite_master WHERE type = 'view'").fetchall()
        seq = con.execute("SELECT seq FROM sqlite_sequence WHERE name = 'activity_log'").fetchone()
        max_id = con.execute("SELECT COALESCE(MAX(activity_id), 0) FROM activity_log").fetchone()[0]

        con.execute("PRAGMA foreign_keys = OFF")  # must be outside the transaction
        con.execute("BEGIN IMMEDIATE")
        try:
            for name, _ in views:
                con.execute(f'DROP VIEW "{name}"')
            con.execute(create)
            con.execute("INSERT INTO activity_log_new SELECT * FROM activity_log")
            con.execute("DROP TABLE activity_log")
            con.execute("ALTER TABLE activity_log_new RENAME TO activity_log")
            con.execute("DELETE FROM sqlite_sequence WHERE name IN ('activity_log', 'activity_log_new')")
            con.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('activity_log', ?)",
                        (max(seq[0] if seq else 0, max_id),))
            for _, sql in views:
                con.execute(sql)
            problems = con.execute("PRAGMA foreign_key_check").fetchall()
            if problems:
                raise RuntimeError(f"Foreign key problems after rebuilding activity_log: {problems}")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        log.info("activity_log now accepts categories %s (backup: %s)", ", ".join(missing), backup)
        return True
    finally:
        con.close()


def _columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in con.execute(f'PRAGMA table_info("{table}")')]


def migrate_to_multi_user(db_path: Path = DB_PATH) -> bool:
    """Give the single-user database's data an owner: user 1 (an admin), from auth.env's old login.

    Before, training_plan, activity_log and the app's own tables had no user. Each is renamed
    aside, created again from the models (with user_id and per-user keys), filled from the old
    one with user_id 1, and dropped: rows, ids, entry order and AUTOINCREMENT counters are kept.
    Week types move to plan_weeks. All in one transaction, after a backup; init_db reinstalls the
    history triggers (they went with the old table). Returns whether anything changed.
    """
    con = sqlite3.connect(db_path, isolation_level=None, timeout=30)
    try:
        existing = _columns(con, "training_plan")
        if not existing or "user_id" in existing:
            return False  # a new database (init_db creates it), or already migrated

        backup = backup_database("before-multi-user", db_path)
        legacy = config.read_env_file(config.AUTH_FILE)
        username = legacy.get("MARATHON_USER") or "owner"
        old = {name for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        moved = [t for t in ("training_plan", "activity_log", "activity_history", "plan_skips", "strava_imports")
                 if t in old]
        sequences = dict(con.execute("SELECT name, seq FROM sqlite_sequence")) if "sqlite_sequence" in old else {}

        def copy(table: str, columns: str, select: str) -> None:
            if table in moved:
                con.execute(f"INSERT INTO {table} ({columns}) SELECT {select} FROM {table}_old")

        con.execute("PRAGMA foreign_keys = OFF")  # must be outside the transaction
        con.execute("BEGIN IMMEDIATE")
        try:
            for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'view'").fetchall():
                con.execute(f'DROP VIEW "{name}"')
            for t in moved:
                con.execute(f'ALTER TABLE "{t}" RENAME TO "{t}_old"')
            for table in TABLES:
                if table.name not in old or table.name in moved:
                    con.execute(str(CreateTable(table).compile(dialect=sqlite.dialect())))

            con.execute("INSERT INTO users (user_id, username, password_hash, is_admin, plan_start) "
                        "VALUES (1, ?, ?, 1, ?)",
                        (username, legacy.get("MARATHON_PASSWORD_HASH") or "", config.PLAN_START.isoformat()))
            con.execute("INSERT INTO plan_weeks (user_id, week, week_type) "
                        "SELECT 1, week, MIN(week_type) FROM training_plan_old GROUP BY week")
            copy("training_plan",
                 "rowid, user_id, plan_id, week, day, category, run_subtype, planned_session, "
                 "target_distance_mi, target_duration_min, notes",
                 "rowid, 1, plan_id, week, day, category, run_subtype, planned_session, "
                 "target_distance_mi, target_duration_min, notes")
            columns = "activity_date, category, actual_session, distance_mi, duration_min, output_kj, plan_id, notes"
            copy("activity_log", f"activity_id, user_id, {columns}", f"activity_id, 1, {columns}")
            columns = "changed_at, action, activity_id, old_values, new_values, reverted_entry"
            copy("activity_history", f"history_id, user_id, {columns}", f"history_id, 1, {columns}")
            copy("plan_skips", "user_id, plan_id, reason, skipped_at", "1, plan_id, reason, skipped_at")
            copy("strava_imports", "strava_id, user_id, outcome, activity_id, imported_at",
                 "strava_id, 1, outcome, activity_id, imported_at")

            for t in moved:
                con.execute(f'DROP TABLE "{t}_old"')
            # Keep the AUTOINCREMENT counters, so ids of deleted rows are never handed out again.
            con.execute("DELETE FROM sqlite_sequence WHERE name LIKE '%\\_old' ESCAPE '\\'")
            for t in ("activity_log", "activity_history"):
                if t in sequences:
                    con.execute("INSERT INTO sqlite_sequence (name, seq) SELECT ?, 0 "
                                "WHERE NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = ?)", (t, t))
                    con.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = ?", (sequences[t], t))
            con.execute(PLAN_VS_ACTUAL_VIEW)
            problems = con.execute("PRAGMA foreign_key_check").fetchall()
            if problems:
                raise RuntimeError(f"Foreign key problems after the multi-user migration: {problems}")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        log.info("Database migrated to multiple users; the existing data belongs to %s (backup: %s)",
                 username, backup)
        return True
    finally:
        con.close()
