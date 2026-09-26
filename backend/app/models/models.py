"""SQLAlchemy ORM models for marathon.db.

training_plan, activity_log and the plan_vs_actual view already exist in the database and are
only mapped here (the Plan tab edits training_plan's rows, never its structure). activity_history, plan_skips and strava_imports are owned by this app:
database.init_db() creates them (and adds any columns added to the models later).
"""
from datetime import date
from typing import Any, Optional

from sqlalchemy import JSON, BigInteger, CheckConstraint, Date, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Categories a logged activity can have. training_plan has its own, shorter list (no Stretch):
# both are enforced by CHECK constraints, and database.init_db widens activity_log's to this.
PLAN_CATEGORIES = ("Strength", "Bike", "Run", "Race", "Row", "Rest")
ACTIVITY_CATEGORIES = PLAN_CATEGORIES + ("Stretch",)

# Columns of activity_log that are tracked in history and can be edited / reverted.
ACTIVITY_COLUMNS = [
    "activity_date", "category", "actual_session", "distance_mi",
    "duration_min", "output_kj", "plan_id", "notes",
]

# SQLite fills these in itself, in UTC.
_UTC_NOW = text("(strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))")


class Base(DeclarativeBase):
    pass


class TrainingPlan(Base):
    """One planned session, e.g. 'W1-Thu'."""

    __tablename__ = "training_plan"

    plan_id: Mapped[str] = mapped_column(String, primary_key=True)
    # SQLite's implicit rowid: the order the plan rows were entered in, which is the order
    # sessions appear within a day. Read-only: SQLite assigns it, so added sessions come last.
    entry_order: Mapped[int] = mapped_column("rowid", Integer)
    week: Mapped[int]
    day: Mapped[str]
    week_type: Mapped[str]
    category: Mapped[str]
    run_subtype: Mapped[Optional[str]]
    planned_session: Mapped[str]
    target_distance_mi: Mapped[Optional[float]]
    target_duration_min: Mapped[Optional[float]]
    notes: Mapped[Optional[str]]

    activities: Mapped[list["ActivityLog"]] = relationship(back_populates="plan")
    skip: Mapped[Optional["PlanSkip"]] = relationship(back_populates="plan")


class PlanVsActual(Base):
    """Read-only mapping of the plan_vs_actual view (planned targets vs. linked activities)."""

    __tablename__ = "plan_vs_actual"

    plan_id: Mapped[str] = mapped_column(String, primary_key=True)
    actual_distance_mi: Mapped[float]
    actual_duration_min: Mapped[float]
    distance_variance_mi: Mapped[float]
    duration_variance_min: Mapped[float]
    linked_activity_count: Mapped[int]


class ActivityLog(Base):
    """One thing you actually did. plan_id is NULL for unplanned add-ons."""

    __tablename__ = "activity_log"

    activity_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    activity_date: Mapped[date] = mapped_column(Date)
    category: Mapped[str]
    actual_session: Mapped[str]
    distance_mi: Mapped[Optional[float]]
    duration_min: Mapped[Optional[float]]
    output_kj: Mapped[Optional[float]]
    plan_id: Mapped[Optional[str]] = mapped_column(ForeignKey("training_plan.plan_id"))
    notes: Mapped[Optional[str]]

    plan: Mapped[Optional[TrainingPlan]] = relationship(back_populates="activities")


class ActivityHistory(Base):
    """Every insert / update / delete on activity_log, written by database triggers
    (see triggers.py) so it also captures edits made outside this app.

    No foreign key on purpose: history must outlive deleted activities.
    """

    __tablename__ = "activity_history"
    __table_args__ = (
        CheckConstraint("action IN ('INSERT', 'UPDATE', 'DELETE')", name="ck_activity_history_action"),
        Index("idx_activity_history_activity", "activity_id"),
        {"sqlite_autoincrement": True},  # ids are never reused
    )

    history_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    changed_at: Mapped[str] = mapped_column(String, server_default=_UTC_NOW)  # UTC ISO-8601
    action: Mapped[str]
    activity_id: Mapped[int]
    # JSON snapshots of the row (ACTIVITY_COLUMNS) before / after the change.
    old_values: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON(none_as_null=True))  # NULL for INSERT
    new_values: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON(none_as_null=True))  # NULL for DELETE
    # Set when this change is itself a revert of history entry N.
    reverted_entry: Mapped[Optional[int]]


class PlanSkip(Base):
    """A planned session the athlete chose to skip. Kept out of training_plan and activity_log
    so the plan stays as written and 'skipped' never masquerades as a logged activity."""

    __tablename__ = "plan_skips"

    plan_id: Mapped[str] = mapped_column(ForeignKey("training_plan.plan_id"), primary_key=True)
    reason: Mapped[Optional[str]]
    skipped_at: Mapped[str] = mapped_column(String, server_default=_UTC_NOW)

    plan: Mapped[TrainingPlan] = relationship(back_populates="skip")


class StravaImport(Base):
    """One Strava activity the sync has dealt with, so it is never imported twice.

    No foreign key on purpose: the row outlives a deleted (or reverted) activity, so an import
    you undid stays undone instead of coming back on the next sync.
    """

    __tablename__ = "strava_imports"
    __table_args__ = (
        CheckConstraint("outcome IN ('created', 'matched')", name="ck_strava_imports_outcome"),
    )

    strava_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    # 'created': the sync added activity_id. 'matched': you had already logged it as activity_id.
    outcome: Mapped[str]
    activity_id: Mapped[int]
    imported_at: Mapped[str] = mapped_column(String, server_default=_UTC_NOW)
