"""Database engine, per-user sessions, and creating or upgrading the schema (init_db)."""
import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import Connection, Table, create_engine, event, inspect
from sqlalchemy.engine import URL
from sqlalchemy.orm import ORMExecuteState, Session, sessionmaker, with_loader_criteria
from sqlalchemy.schema import CreateColumn

from backend.app.core.config import DB_PATH
from backend.app.core.migrations import allow_activity_categories, migrate_to_multi_user
from backend.app.core.triggers import install_history_triggers
from backend.app.models import (
    PLAN_VS_ACTUAL_VIEW,
    TABLES,
    ActivityHistory,
    ActivityMetrics,
    AthleteZones,
    Base,
    Gear,
    Owned,
    PlanSkip,
    StravaImport,
)

log = logging.getLogger("marathon.db")

# check_same_thread=False: FastAPI may run a request's dependency and its endpoint in different
# worker threads; each request still gets its own session (and so its own connection).
DB_PATH.parent.mkdir(parents=True, exist_ok=True)  # SQLite creates the file, not its folder
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

# Tables whose later-added columns _add_missing_columns fills in.
_APP_TABLES: list[Table] = [ActivityHistory.__table__, PlanSkip.__table__, StravaImport.__table__,
                            ActivityMetrics.__table__, Gear.__table__, AthleteZones.__table__]


def _add_missing_columns(conn: Connection, table: Table) -> None:
    """create_all skips tables that already exist, so add columns introduced by newer models."""
    existing = {c["name"] for c in inspect(conn).get_columns(table.name)}
    for column in table.columns:
        if column.name not in existing:
            definition = CreateColumn(column).compile(dialect=conn.dialect)
            conn.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN {definition}")
            log.info("Added missing column %s.%s", table.name, column.name)


def init_db() -> None:
    """Create the schema (new database) or bring an older one up to date, and install the triggers."""
    allow_activity_categories()
    migrate_to_multi_user()
    Base.metadata.create_all(engine, tables=TABLES)
    with engine.begin() as conn:
        conn.exec_driver_sql(PLAN_VS_ACTUAL_VIEW)
        for table in _APP_TABLES:
            _add_missing_columns(conn, table)
        install_history_triggers(conn)
