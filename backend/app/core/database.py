"""Database engine, session management, and schema initialization."""
from typing import Iterator

from sqlalchemy import Connection, Table, create_engine, event, inspect
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateColumn

from backend.app.core.config import DB_PATH
from backend.app.models.models import ActivityHistory, Base, PlanSkip
from backend.app.services.triggers import install_history_triggers

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
_APP_TABLES: list[Table] = [ActivityHistory.__table__, PlanSkip.__table__]


def _add_missing_columns(conn: Connection, table: Table) -> None:
    """create_all skips tables that already exist, so add columns introduced by newer models."""
    existing = {c["name"] for c in inspect(conn).get_columns(table.name)}
    for column in table.columns:
        if column.name not in existing:
            definition = CreateColumn(column).compile(dialect=conn.dialect)
            conn.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN {definition}")


def init_db() -> None:
    """Create the app's tables, bring older ones up to date, and install the history triggers."""
    Base.metadata.create_all(engine, tables=_APP_TABLES)
    with engine.begin() as conn:
        for table in _APP_TABLES:
            _add_missing_columns(conn, table)
        install_history_triggers(conn)
