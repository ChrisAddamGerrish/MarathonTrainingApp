"""SQLAlchemy ORM models for marathon.db.

Every row of training data belongs to one user (the `Owned` models). A signed-in request's
database session only ever sees and changes its own user's rows: core/database.py adds
`user_id = <that user>` to every ORM query and stamps it on every new row, so the repository
never has to filter by hand.

training_plan, activity_log and the plan_vs_actual view predate the app (database.py migrates
them to per-user tables); the rest are created by database.init_db().
"""
from datetime import date
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    text,
)
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


class User(Base):
    """Someone who can sign in. Their plan's week 1 starts on plan_start (a Monday)."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("strftime('%w', plan_start) = '1'", name="ck_users_plan_start_monday"),
        {"sqlite_autoincrement": True},  # ids are never reused: other tables refer to them
    )

    user_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # COLLATE NOCASE: "Chris" and "chris" are the same account.
    username: Mapped[str] = mapped_column(String(collation="NOCASE"), unique=True)
    password_hash: Mapped[str]  # scrypt (core/auth.py); "" means the account can't sign in yet
    is_admin: Mapped[bool] = mapped_column(default=False)
    plan_start: Mapped[date] = mapped_column(Date)
    created_at: Mapped[str] = mapped_column(String, server_default=_UTC_NOW)


class Invite(Base):
    """A single-use code an admin hands out; registering needs one. Only its hash is stored."""

    __tablename__ = "invites"

    code_hash: Mapped[str] = mapped_column(String, primary_key=True)  # sha256 hex of the code
    created_by: Mapped[int] = mapped_column(ForeignKey("users.user_id"))
    created_at: Mapped[str] = mapped_column(String, server_default=_UTC_NOW)
    expires_at: Mapped[str]  # UTC ISO-8601
    used_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.user_id"))
    used_at: Mapped[Optional[str]]


class Owned:
    """Mixin for tables whose rows belong to a user (see the module docstring)."""

    # sort_order: first in the table, so keys read (user_id, ...).
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"), primary_key=True, sort_order=-1)


class PlanWeek(Owned, Base):
    """One week of a user's plan, so a week exists (with its type) even before it has sessions."""

    __tablename__ = "plan_weeks"
    __table_args__ = (
        CheckConstraint("week >= 1", name="ck_plan_weeks_week"),
        CheckConstraint("week_type IN ('Normal','Step-back','Taper','Peak','Race')", name="ck_plan_weeks_type"),
    )

    week: Mapped[int] = mapped_column(primary_key=True)
    week_type: Mapped[str] = mapped_column(default="Normal")


class TrainingPlan(Owned, Base):
    """One planned session, e.g. 'W1-Thu'. Plan ids are unique per user."""

    __tablename__ = "training_plan"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "week"], ["plan_weeks.user_id", "plan_weeks.week"]),
        CheckConstraint("day IN ('Mon','Tue','Wed','Thu','Fri','Sat','Sun')", name="ck_training_plan_day"),
        CheckConstraint(f"category IN ({', '.join(repr(c) for c in PLAN_CATEGORIES)})",
                        name="ck_training_plan_category"),
        CheckConstraint("run_subtype IN ('Easy','Tempo','Intervals','Hills','Strides','Race-Pace Segments','Long Run')",
                        name="ck_training_plan_run_subtype"),
    )

    plan_id: Mapped[str] = mapped_column(String, primary_key=True)
    # SQLite's implicit rowid: the order the plan rows were entered in, which is the order
    # sessions appear within a day. Read-only: SQLite assigns it, so added sessions come last.
    entry_order: Mapped[int] = mapped_column("rowid", Integer, system=True)  # system: not in CREATE TABLE
    week: Mapped[int]
    day: Mapped[str]
    category: Mapped[str]
    run_subtype: Mapped[Optional[str]]
    planned_session: Mapped[str]
    target_distance_mi: Mapped[Optional[float]]
    target_duration_min: Mapped[Optional[float]]
    notes: Mapped[Optional[str]]

    # Read-only: activities and skips are changed through their own plan_id columns.
    activities: Mapped[list["ActivityLog"]] = relationship(
        primaryjoin="and_(TrainingPlan.user_id == foreign(ActivityLog.user_id), "
                    "TrainingPlan.plan_id == foreign(ActivityLog.plan_id))",
        viewonly=True,
    )
    skip: Mapped[Optional["PlanSkip"]] = relationship(
        primaryjoin="and_(TrainingPlan.user_id == foreign(PlanSkip.user_id), "
                    "TrainingPlan.plan_id == foreign(PlanSkip.plan_id))",
        viewonly=True,
    )


class PlanVsActual(Owned, Base):
    """Read-only mapping of the plan_vs_actual view (planned targets vs. linked activities)."""

    __tablename__ = "plan_vs_actual"

    plan_id: Mapped[str] = mapped_column(String, primary_key=True)
    actual_distance_mi: Mapped[float]
    actual_duration_min: Mapped[float]
    distance_variance_mi: Mapped[float]
    duration_variance_min: Mapped[float]
    linked_activity_count: Mapped[int]


class ActivityLog(Owned, Base):
    """One thing someone actually did. plan_id is NULL for unplanned add-ons."""

    __tablename__ = "activity_log"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "plan_id"], ["training_plan.user_id", "training_plan.plan_id"]),
        CheckConstraint(f"category IN ({', '.join(repr(c) for c in ACTIVITY_CATEGORIES)})",
                        name="ck_activity_log_category"),
        {"sqlite_autoincrement": True},  # ids are never reused: history refers to them
    )

    # Ids are unique across users (so links like #/log/12 are unambiguous); user_id isn't part of the key.
    activity_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"))
    activity_date: Mapped[date] = mapped_column(Date)
    category: Mapped[str]
    actual_session: Mapped[str]
    distance_mi: Mapped[Optional[float]]
    duration_min: Mapped[Optional[float]]
    output_kj: Mapped[Optional[float]]
    plan_id: Mapped[Optional[str]]
    notes: Mapped[Optional[str]]

    plan: Mapped[Optional[TrainingPlan]] = relationship(
        primaryjoin="and_(foreign(ActivityLog.user_id) == TrainingPlan.user_id, "
                    "foreign(ActivityLog.plan_id) == TrainingPlan.plan_id)",
        viewonly=True,
    )


class ActivityHistory(Owned, Base):
    """Every insert / update / delete on activity_log, written by database triggers
    (see triggers.py) so it also captures edits made outside this app.

    No foreign key to the activity on purpose: history must outlive deleted activities.
    """

    __tablename__ = "activity_history"
    __table_args__ = (
        CheckConstraint("action IN ('INSERT', 'UPDATE', 'DELETE')", name="ck_activity_history_action"),
        Index("idx_activity_history_activity", "activity_id"),
        {"sqlite_autoincrement": True},  # ids are never reused
    )

    history_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"))
    changed_at: Mapped[str] = mapped_column(String, server_default=_UTC_NOW)  # UTC ISO-8601
    action: Mapped[str]
    activity_id: Mapped[int]
    # JSON snapshots of the row (ACTIVITY_COLUMNS) before / after the change.
    old_values: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON(none_as_null=True))  # NULL for INSERT
    new_values: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON(none_as_null=True))  # NULL for DELETE
    # Set when this change is itself a revert of history entry N.
    reverted_entry: Mapped[Optional[int]]


class PlanSkip(Owned, Base):
    """A planned session the athlete chose to skip. Kept out of training_plan and activity_log
    so the plan stays as written and 'skipped' never masquerades as a logged activity."""

    __tablename__ = "plan_skips"
    __table_args__ = (ForeignKeyConstraint(["user_id", "plan_id"], ["training_plan.user_id", "training_plan.plan_id"]),)

    plan_id: Mapped[str] = mapped_column(String, primary_key=True)
    reason: Mapped[Optional[str]]
    skipped_at: Mapped[str] = mapped_column(String, server_default=_UTC_NOW)


class StravaImport(Owned, Base):
    """One Strava activity the sync has dealt with, so it is never imported twice.

    No foreign key to the activity on purpose: the row outlives a deleted (or reverted) activity,
    so an import you undid stays undone instead of coming back on the next sync.
    """

    __tablename__ = "strava_imports"
    __table_args__ = (
        CheckConstraint("outcome IN ('created', 'matched')", name="ck_strava_imports_outcome"),
    )

    strava_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"))
    # 'created': the sync added activity_id. 'matched': you had already logged it as activity_id.
    outcome: Mapped[str]
    activity_id: Mapped[int]
    imported_at: Mapped[str] = mapped_column(String, server_default=_UTC_NOW)
