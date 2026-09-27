"""Database engine, per-user sessions, and schema initialization / migration."""
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import Connection, Table, create_engine, event, inspect
from sqlalchemy.dialects import sqlite
from sqlalchemy.engine import URL
from sqlalchemy.orm import ORMExecuteState, Session, sessionmaker, with_loader_criteria
from sqlalchemy.schema import CreateColumn, CreateTable

from backend.app.core import config
from backend.app.core.config import DB_PATH
from backend.app.models.models import (
    ACTIVITY_CATEGORIES,
    ActivityHistory,
    ActivityMetrics,
    AthleteZones,
    Base,
    Gear,
    Owned,
    PlanSkip,
    PlanVsActual,
    StravaImport,
)
from backend.app.services.triggers import install_history_triggers

log = logging.getLogger("marathon.db")

# check_same_thread=False: FastAPI may run a request's dependency and its endpoint in different
# worker threads; each request still gets its own session (and so its own connection).
engine = create_engine(URL.create("sqlite", database=str(DB_PATH)), connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _enforce_foreign_keys(dbapi_connection, _record) -> None:
    # SQLite ignores foreign keys unless asked to, per connection.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


SessionLocal = sessionmaker(engine, expire_on_commit=False)


# --------------------------------------------------------------------------
# Per-user sessions
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Tenant:
    """Whose data a session works on, and where their plan's week 1 starts."""

    user_id: int
    plan_start: date


def tenant_of(session: Session) -> Tenant:
    tenant = session.info.get("tenant")
    if tenant is None:
        raise RuntimeError("This needs a user's session (see database.user_session)")
    return tenant


def user_session(tenant: Tenant) -> Session:
    """A session that only sees and changes `tenant`'s rows of the Owned tables."""
    session = SessionLocal()
    session.info["tenant"] = tenant
    return session


@event.listens_for(Session, "do_orm_execute")
def _only_own_rows(state: ORMExecuteState) -> None:
    # Every ORM SELECT / UPDATE / DELETE (Session.get included) gets `user_id = <tenant>` on each
    # Owned table it touches. Relationship and column loads inherit it from the query that loaded
    # their object. Sessions without a tenant (sign-in, migrations) are left alone, and so is a
    # query marked .execution_options(all_users=True) (only ever to check an id is free).
    tenant = state.session.info.get("tenant")
    if (tenant is None or state.is_column_load or state.is_relationship_load
            or state.execution_options.get("all_users")):
        return
    if state.is_select or state.is_update or state.is_delete:
        uid = tenant.user_id
        state.statement = state.statement.options(
            with_loader_criteria(Owned, lambda cls: cls.user_id == uid, include_aliases=True))


@event.listens_for(Session, "before_flush")
def _stamp_owner(session: Session, _context, _instances) -> None:
    """New rows of Owned tables belong to the session's user; refuse writing anyone else's."""
    tenant = session.info.get("tenant")
    if tenant is None:
        return
    for obj in session.new:
        if isinstance(obj, Owned):
            if obj.user_id is None:
                obj.user_id = tenant.user_id
            elif obj.user_id != tenant.user_id:
                raise RuntimeError(f"A session for user {tenant.user_id} tried to add a row for user {obj.user_id}")


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

# Everything the models describe, except plan_vs_actual, which is a view (PLAN_VS_ACTUAL_VIEW).
_TABLES: list[Table] = [t for t in Base.metadata.sorted_tables if t is not PlanVsActual.__table__]
# Tables whose later-added columns _add_missing_columns fills in.
_APP_TABLES: list[Table] = [ActivityHistory.__table__, PlanSkip.__table__, StravaImport.__table__,
                            ActivityMetrics.__table__, Gear.__table__, AthleteZones.__table__]

PLAN_VS_ACTUAL_VIEW = """CREATE VIEW IF NOT EXISTS plan_vs_actual AS
SELECT
    p.user_id,
    p.plan_id,
    p.week,
    p.day,
    p.category                     AS planned_category,
    p.run_subtype,
    p.planned_session,
    p.target_distance_mi,
    p.target_duration_min,
    COALESCE(SUM(a.distance_mi), 0)   AS actual_distance_mi,
    COALESCE(SUM(a.duration_min), 0)  AS actual_duration_min,
    COALESCE(SUM(a.distance_mi), 0) - COALESCE(p.target_distance_mi, 0)  AS distance_variance_mi,
    COALESCE(SUM(a.duration_min), 0) - COALESCE(p.target_duration_min, 0) AS duration_variance_min,
    COUNT(a.activity_id)              AS linked_activity_count,
    CASE WHEN COUNT(a.activity_id) = 0 THEN 1 ELSE 0 END AS not_yet_done
FROM training_plan p
LEFT JOIN activity_log a ON a.user_id = p.user_id AND a.plan_id = p.plan_id
GROUP BY p.user_id, p.plan_id"""


def _add_missing_columns(conn: Connection, table: Table) -> None:
    """create_all skips tables that already exist, so add columns introduced by newer models."""
    existing = {c["name"] for c in inspect(conn).get_columns(table.name)}
    for column in table.columns:
        if column.name not in existing:
            definition = CreateColumn(column).compile(dialect=conn.dialect)
            conn.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN {definition}")
            log.info("Added missing column %s.%s", table.name, column.name)


_CATEGORY_CHECK = re.compile(r"CHECK\s*\(\s*category\s+IN\s*\(([^)]*)\)\s*\)", re.IGNORECASE)


def backup_database(reason: str, db_path: Path = DB_PATH) -> str:
    """Copy the database to backups/marathon-<time>-<reason>.db next to it; returns the path."""
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
            for table in _TABLES:
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


def init_db() -> None:
    """Create the schema (new database) or bring an older one up to date, and install the triggers."""
    allow_activity_categories()
    migrate_to_multi_user()
    Base.metadata.create_all(engine, tables=_TABLES)
    with engine.begin() as conn:
        conn.exec_driver_sql(PLAN_VS_ACTUAL_VIEW)
        for table in _APP_TABLES:
            _add_missing_columns(conn, table)
        install_history_triggers(conn)
