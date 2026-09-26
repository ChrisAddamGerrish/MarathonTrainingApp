"""Database engine, session management, and schema initialization."""
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterator

from sqlalchemy import Connection, Table, create_engine, event, inspect
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateColumn

from backend.app.core.config import DB_PATH
from backend.app.models.models import ACTIVITY_CATEGORIES, ActivityHistory, Base, PlanSkip, StravaImport
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


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, closed afterwards."""
    with SessionLocal() as session:
        yield session


# Tables this app owns. training_plan / activity_log / plan_vs_actual are yours and pre-exist.
_APP_TABLES: list[Table] = [ActivityHistory.__table__, PlanSkip.__table__, StravaImport.__table__]


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


def init_db() -> None:
    """Create the app's tables, bring older ones up to date, and install the history triggers."""
    allow_activity_categories()
    Base.metadata.create_all(engine, tables=_APP_TABLES)
    with engine.begin() as conn:
        for table in _APP_TABLES:
            _add_missing_columns(conn, table)
        install_history_triggers(conn)
