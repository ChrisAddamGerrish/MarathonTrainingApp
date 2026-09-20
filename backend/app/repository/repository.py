"""All database access, written against the ORM models. Functions take a Session and return
plain dicts / raise domain errors, so callers never touch SQL or SQLAlchemy objects."""
from datetime import date
from typing import Any, Optional

from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.errors import ConflictError, NotFoundError, UnprocessableError
from backend.app.models.models import (
    ACTIVITY_COLUMNS,
    ActivityHistory,
    ActivityLog,
    PlanSkip,
    PlanVsActual,
    TrainingPlan,
)
from backend.app.services.planning import week_of

# --------------------------------------------------------------------------
# Row -> dict
# --------------------------------------------------------------------------


def _plain_activity(a: ActivityLog) -> dict[str, Any]:
    return {
        "activity_id": a.activity_id,
        "activity_date": a.activity_date.isoformat(),
        "category": a.category,
        "actual_session": a.actual_session,
        "distance_mi": a.distance_mi,
        "duration_min": a.duration_min,
        "output_kj": a.output_kj,
        "plan_id": a.plan_id,
        "notes": a.notes,
    }


def _activity(a: ActivityLog) -> dict[str, Any]:
    return {**_plain_activity(a), "week": week_of(a.activity_date)}


def _snapshot(a: ActivityLog) -> dict[str, Any]:
    """The tracked columns of an activity, in the same form history stores them."""
    plain = _plain_activity(a)
    return {c: plain[c] for c in ACTIVITY_COLUMNS}


def _from_snapshot(values: dict[str, Any]) -> dict[str, Any]:
    """History stores dates as ISO text; the model wants date objects."""
    return {k: date.fromisoformat(v) if k == "activity_date" and v is not None else v for k, v in values.items()}


# --------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------


def plan_rows(session: Session) -> list[dict[str, Any]]:
    """Planned sessions with what was actually done against them (from the plan_vs_actual
    view) and any skip. Ordered by week, then the order the rows were entered in."""
    stmt = (
        select(TrainingPlan, PlanVsActual, PlanSkip)
        .join(PlanVsActual, PlanVsActual.plan_id == TrainingPlan.plan_id)
        .outerjoin(PlanSkip, PlanSkip.plan_id == TrainingPlan.plan_id)
        .order_by(TrainingPlan.week, TrainingPlan.entry_order)
    )
    return [
        {
            "plan_id": p.plan_id,
            "week": p.week,
            "day": p.day,
            "week_type": p.week_type,
            "category": p.category,
            "run_subtype": p.run_subtype,
            "planned_session": p.planned_session,
            "target_distance_mi": p.target_distance_mi,
            "target_duration_min": p.target_duration_min,
            "notes": p.notes,
            "actual_distance_mi": v.actual_distance_mi,
            "actual_duration_min": v.actual_duration_min,
            "distance_variance_mi": v.distance_variance_mi,
            "duration_variance_min": v.duration_variance_min,
            "linked_activity_count": v.linked_activity_count,
            "skipped": s is not None,
            "skip_reason": s.reason if s else None,
        }
        for p, v, s in session.execute(stmt)
    ]


def skip_session(session: Session, plan_id: str, reason: Optional[str]) -> dict[str, Any]:
    """Mark a planned session as deliberately skipped (optionally with a reason)."""
    plan = session.get(TrainingPlan, plan_id)
    if plan is None:
        raise NotFoundError(f"Unknown plan session '{plan_id}'")
    if plan.category == "Rest":
        raise UnprocessableError("Rest days can't be skipped")
    if plan.activities:
        raise ConflictError("That session already has a logged activity, so it can't be skipped")
    if plan.skip is None:
        session.add(PlanSkip(plan_id=plan_id, reason=reason))
    else:
        plan.skip.reason = reason
    session.commit()
    return {"plan_id": plan_id, "skipped": True, "reason": reason}


def unskip_session(session: Session, plan_id: str) -> dict[str, Any]:
    skip = session.get(PlanSkip, plan_id)
    if skip is None:
        raise NotFoundError("That session isn't skipped")
    session.delete(skip)
    session.commit()
    return {"plan_id": plan_id, "skipped": False}


# --------------------------------------------------------------------------
# Activities
# --------------------------------------------------------------------------


def activity_rows(session: Session) -> list[dict[str, Any]]:
    stmt = select(ActivityLog).order_by(desc(ActivityLog.activity_date), desc(ActivityLog.activity_id))
    return [_activity(a) for a in session.scalars(stmt)]


def _get_activity(session: Session, activity_id: int) -> ActivityLog:
    activity = session.get(ActivityLog, activity_id)
    if activity is None:
        raise NotFoundError(f"Activity {activity_id} not found")
    return activity


def _check_plan_id(session: Session, plan_id: Optional[str]) -> None:
    if plan_id and session.get(TrainingPlan, plan_id) is None:
        raise UnprocessableError(f"Unknown plan session '{plan_id}'")


def create_activity(session: Session, values: dict[str, Any]) -> dict[str, Any]:
    _check_plan_id(session, values.get("plan_id"))
    activity = ActivityLog(**values)
    session.add(activity)
    session.commit()
    return _activity(activity)


def update_activity(session: Session, activity_id: int, values: dict[str, Any]) -> dict[str, Any]:
    activity = _get_activity(session, activity_id)
    _check_plan_id(session, values.get("plan_id"))
    for column, value in values.items():
        setattr(activity, column, value)
    session.commit()
    return _activity(activity)


def delete_activity(session: Session, activity_id: int) -> dict[str, Any]:
    session.delete(_get_activity(session, activity_id))
    session.commit()
    return {"deleted": activity_id}


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------


def history_entries(session: Session, activity_id: Optional[int], limit: int) -> list[dict[str, Any]]:
    """Change log for activity_log, newest first."""
    stmt = select(ActivityHistory).order_by(desc(ActivityHistory.history_id)).limit(limit)
    if activity_id is not None:
        stmt = stmt.where(ActivityHistory.activity_id == activity_id)

    entries = []
    for e in session.scalars(stmt):
        old, new = e.old_values or None, e.new_values or None
        changes = (
            [{"field": c, "old": old[c], "new": new[c]} for c in ACTIVITY_COLUMNS if old[c] != new[c]]
            if old and new else []
        )
        entries.append({
            "history_id": e.history_id,
            "changed_at": e.changed_at,
            "action": e.action,
            "activity_id": e.activity_id,
            "old": old,
            "new": new,
            "changes": changes,
            "reverts": e.reverted_entry,
        })
    return entries


def revert_history(session: Session, history_id: int) -> dict[str, Any]:
    """Undo one history entry.

    - INSERT -> delete the activity again.
    - DELETE -> re-create it with its original id and values.
    - UPDATE -> put back the old value of each field that entry changed (only those
      fields, so later edits to other fields are left alone).

    The revert is an ordinary change, so the triggers record it too; we tag that new
    entry with the one it reverts. All of it happens in one transaction.
    """
    entry = session.get(ActivityHistory, history_id)
    if entry is None:
        raise NotFoundError(f"History entry {history_id} not found")
    already = session.scalar(select(ActivityHistory.history_id).where(ActivityHistory.reverted_entry == history_id))
    if already is not None:
        raise ConflictError("That change has already been reverted")

    old, new = entry.old_values, entry.new_values
    activity_id = entry.activity_id
    current = session.get(ActivityLog, activity_id)

    try:
        if entry.action == "INSERT":
            if current is None:
                raise ConflictError("That activity has already been removed")
            session.delete(current)
        elif entry.action == "DELETE":
            if current is not None:
                raise ConflictError("That activity already exists")
            session.add(ActivityLog(activity_id=activity_id, **_from_snapshot(old)))
        else:  # UPDATE
            if current is None:
                raise ConflictError("That activity has since been deleted; restore the deletion first")
            now = _snapshot(current)
            restore = {c: old[c] for c in ACTIVITY_COLUMNS if old[c] != new[c] and now[c] != old[c]}
            if not restore:
                raise ConflictError("The activity already has those values")
            for column, value in _from_snapshot(restore).items():
                setattr(current, column, value)

        session.flush()  # runs the write now, which fires the history trigger

        # The trigger just wrote the history row for this revert; tag it.
        new_entry_id = session.scalar(
            select(func.max(ActivityHistory.history_id)).where(ActivityHistory.activity_id == activity_id)
        )
        session.get(ActivityHistory, new_entry_id).reverted_entry = history_id
        session.commit()
    except IntegrityError as e:
        session.rollback()
        raise ConflictError(f"Can't revert: {e.orig}") from e
    except Exception:
        session.rollback()
        raise

    restored = session.get(ActivityLog, activity_id)
    return {"history_id": new_entry_id, "activity": _plain_activity(restored) if restored else None}
