"""Database triggers that record every change to activity_log in activity_history."""
from sqlalchemy import Connection, func, insert, literal_column, or_
from sqlalchemy.dialects import sqlite
from sqlalchemy.sql import ClauseElement

from backend.app.models.models import ACTIVITY_COLUMNS, ActivityHistory, ActivityLog

_HISTORY = ActivityHistory.__table__
_SOURCE = ActivityLog.__tablename__


def _sql(clause: ClauseElement) -> str:
    """Render an expression as SQLite SQL with literals inlined (triggers can't take parameters)."""
    return str(clause.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))


def _row(alias: str):
    """json_object('activity_date', NEW.activity_date, ...) for the OLD or NEW row."""
    args = []
    for column in ACTIVITY_COLUMNS:
        args += [column, literal_column(f"{alias}.{column}")]
    return func.json_object(*args)


def _create_trigger(name: str, event: str, body: ClauseElement, when: ClauseElement | None = None) -> str:
    when_sql = f"\nWHEN {_sql(when)}" if when is not None else ""
    return f"CREATE TRIGGER IF NOT EXISTS {name}\nAFTER {event} ON {_SOURCE}{when_sql}\nBEGIN\n    {_sql(body)};\nEND"


def _history_triggers() -> list[str]:
    changed = or_(*(
        literal_column(f"OLD.{c}").is_distinct_from(literal_column(f"NEW.{c}"))  # SQLite: OLD.c IS NOT NEW.c
        for c in ACTIVITY_COLUMNS
    ))
    return [
        _create_trigger(
            "activity_log_history_insert", "INSERT",
            insert(_HISTORY).values(action="INSERT", activity_id=literal_column("NEW.activity_id"), new_values=_row("NEW")),
        ),
        # Only when something actually changed, so saving identical values adds no noise.
        _create_trigger(
            "activity_log_history_update", "UPDATE",
            insert(_HISTORY).values(
                action="UPDATE", activity_id=literal_column("NEW.activity_id"),
                old_values=_row("OLD"), new_values=_row("NEW"),
            ),
            when=changed,
        ),
        _create_trigger(
            "activity_log_history_delete", "DELETE",
            insert(_HISTORY).values(action="DELETE", activity_id=literal_column("OLD.activity_id"), old_values=_row("OLD")),
        ),
    ]


def install_history_triggers(conn: Connection) -> None:
    """Idempotent: existing triggers with these names are left as they are."""
    for statement in _history_triggers():
        conn.exec_driver_sql(statement)
