"""All database access, written against the ORM models. Functions take a Session and return
plain dicts / raise domain errors, so callers never touch SQL or SQLAlchemy objects.

Every change to the data is logged here, once, whichever entry point (REST or MCP) made it."""
import logging
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
    StravaImport,
    TrainingPlan,
)
from backend.app.services.planning import DAYS, RUN_CATEGORIES, week_of

log = logging.getLogger("marathon.repo")

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
    log.info("Session %s skipped (%s)", plan_id, reason or "no reason given")
    return {"plan_id": plan_id, "skipped": True, "reason": reason}


def _plan_row(session: Session, plan_id: str) -> dict[str, Any]:
    row = next((r for r in plan_rows(session) if r["plan_id"] == plan_id), None)
    if row is None:
        raise NotFoundError(f"Unknown plan session '{plan_id}'")
    return row


def _get_plan(session: Session, plan_id: str) -> TrainingPlan:
    plan = session.get(TrainingPlan, plan_id)
    if plan is None:
        raise NotFoundError(f"Unknown plan session '{plan_id}'")
    return plan


def _week_type(session: Session, week: int) -> str:
    week_type = session.scalar(select(TrainingPlan.week_type).where(TrainingPlan.week == week).limit(1))
    if week_type is None:
        raise NotFoundError(f"The plan has no week {week}")
    return week_type


def _new_plan_id(session: Session, week: int, day: str) -> str:
    """W3-Tue, or W3-Tue-2, -3... when that is taken. Ids never change afterwards, even if the
    session moves to another day: activities and skips refer to them."""
    base, n = f"W{week}-{day}", 1
    plan_id = base
    while session.get(TrainingPlan, plan_id) is not None:
        n += 1
        plan_id = f"{base}-{n}"
    return plan_id


def _drop_skip_if_rest(session: Session, plan: TrainingPlan) -> None:
    # Rest days can't be skipped, so a session turned into a rest day loses its skip.
    if plan.category == "Rest" and plan.skip is not None:
        session.delete(plan.skip)


def create_plan_session(session: Session, week: int, values: dict[str, Any]) -> dict[str, Any]:
    plan = TrainingPlan(plan_id=_new_plan_id(session, week, values["day"]), week=week,
                        week_type=_week_type(session, week), **values)
    session.add(plan)
    session.commit()
    log.info("Plan session %s added: %s on W%s %s", plan.plan_id, plan.planned_session, week, plan.day)
    return _plan_row(session, plan.plan_id)


def update_plan_session(session: Session, plan_id: str, values: dict[str, Any]) -> dict[str, Any]:
    plan = _get_plan(session, plan_id)
    changed = []
    for column, value in values.items():
        if getattr(plan, column) != value:
            changed.append(column)
            setattr(plan, column, value)
    _drop_skip_if_rest(session, plan)
    session.commit()
    log.info("Plan session %s updated: %s", plan_id, ", ".join(changed) or "nothing changed")
    return _plan_row(session, plan_id)


def delete_plan_session(session: Session, plan_id: str) -> dict[str, Any]:
    plan = _get_plan(session, plan_id)
    if plan.activities:
        n = len(plan.activities)
        raise ConflictError(f"{n} logged {'activity counts' if n == 1 else 'activities count'} toward this session. "
                            "Link them to another session (or unlink them) in the Activity log first.")
    if plan.skip is not None:
        session.delete(plan.skip)
    session.delete(plan)
    session.commit()
    log.info("Plan session %s deleted (%s)", plan_id, plan.planned_session)
    return {"deleted": plan_id}


def set_week_type(session: Session, week: int, week_type: str) -> dict[str, Any]:
    _week_type(session, week)  # 404 for a week the plan doesn't have
    for plan in session.scalars(select(TrainingPlan).where(TrainingPlan.week == week)):
        plan.week_type = week_type
    session.commit()
    log.info("Plan week %s set to %s", week, week_type)
    return {"week": week, "week_type": week_type}


def unskip_session(session: Session, plan_id: str) -> dict[str, Any]:
    skip = session.get(PlanSkip, plan_id)
    if skip is None:
        raise NotFoundError("That session isn't skipped")
    session.delete(skip)
    session.commit()
    log.info("Session %s unskipped", plan_id)
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


def get_activity(session: Session, activity_id: int) -> dict[str, Any]:
    return _activity(_get_activity(session, activity_id))


def _check_plan_id(session: Session, plan_id: Optional[str]) -> None:
    if plan_id and session.get(TrainingPlan, plan_id) is None:
        raise UnprocessableError(f"Unknown plan session '{plan_id}'")


def create_activity(session: Session, values: dict[str, Any]) -> dict[str, Any]:
    _check_plan_id(session, values.get("plan_id"))
    activity = ActivityLog(**values)
    session.add(activity)
    session.commit()
    log.info("Activity %s created: %s on %s (plan %s)", activity.activity_id, activity.category,
             activity.activity_date, activity.plan_id or "none")
    return _activity(activity)


def update_activity(session: Session, activity_id: int, values: dict[str, Any]) -> dict[str, Any]:
    activity = _get_activity(session, activity_id)
    _check_plan_id(session, values.get("plan_id"))
    changed = []
    for column, value in values.items():
        if getattr(activity, column) != value:
            changed.append(column)
            setattr(activity, column, value)
    session.commit()
    log.info("Activity %s updated: %s", activity_id, ", ".join(changed) or "nothing changed")
    return _activity(activity)


def delete_activity(session: Session, activity_id: int) -> dict[str, Any]:
    session.delete(_get_activity(session, activity_id))
    session.commit()
    log.info("Activity %s deleted", activity_id)
    return {"deleted": activity_id}


# --------------------------------------------------------------------------
# Strava imports
# --------------------------------------------------------------------------


def _same_kind(category: str) -> tuple[str, ...]:
    """Categories treated as the same workout: a race is a run (and a planned run can be raced)."""
    return RUN_CATEGORIES if category in RUN_CATEGORIES else (category,)


def _plan_session_for(session: Session, day: date, category: str, duration_min: Optional[float]) -> Optional[str]:
    """The planned session an imported workout should count towards, if any.

    Candidates are sessions of the same kind that are neither done nor skipped.
    1. That day's: the earliest in plan order wins (so two strength sessions fill up in the order
       they're planned), and optional ones are only used when nothing required is left.
    2. Otherwise a required one from earlier in the same plan week, the most recent first: a
       session moved to a later day (Saturday's ride done on Sunday). Never a later day's, so an
       extra workout can't take the slot of one you still have to do, and only when the workout
       lasted at least half the session's target, so a short extra can't pass for a missed session.
    """
    stmt = (
        select(TrainingPlan)
        .where(TrainingPlan.week == week_of(day), TrainingPlan.category.in_(_same_kind(category)))
        .order_by(TrainingPlan.entry_order)
    )
    open_sessions = [p for p in session.scalars(stmt) if not p.activities and p.skip is None]
    weekday = day.weekday()
    today = [p for p in open_sessions if DAYS.index(p.day) == weekday]
    required_today = [p for p in today if not p.planned_session.startswith("Optional")]
    if required_today or today:
        return (required_today or today)[0].plan_id
    moved = [p for p in open_sessions
             if DAYS.index(p.day) < weekday and not p.planned_session.startswith("Optional")
             and p.target_duration_min and duration_min and duration_min >= p.target_duration_min / 2]
    moved.sort(key=lambda p: DAYS.index(p.day), reverse=True)  # stable: plan order within a day
    return moved[0].plan_id if moved else None


def _already_logged(session: Session, day: date, category: str) -> Optional[ActivityLog]:
    """An activity you logged yourself that day, of the same kind, not yet tied to a Strava one."""
    claimed = select(StravaImport.activity_id)
    stmt = (
        select(ActivityLog)
        .where(ActivityLog.activity_date == day, ActivityLog.category.in_(_same_kind(category)),
               ActivityLog.activity_id.not_in(claimed))
        .order_by(ActivityLog.activity_id)
    )
    return session.scalars(stmt).first()


def import_strava_activity(session: Session, strava_id: int, values: dict[str, Any]) -> dict[str, Any]:
    """Bring one Strava activity (already converted to activity_log values) into the log.

    - Seen before (even if you have since deleted it): nothing happens.
    - You already logged that workout: the two are tied together and your entry is left as is.
    - Otherwise it is added, linked to a planned session when one fits (see _plan_session_for).
    """
    if session.get(StravaImport, strava_id) is not None:
        return {"strava_id": strava_id, "outcome": "known"}

    existing = _already_logged(session, values["activity_date"], values["category"])
    if existing is not None:
        session.add(StravaImport(strava_id=strava_id, outcome="matched", activity_id=existing.activity_id))
        session.commit()
        log.info("Strava activity %s matched existing activity %s", strava_id, existing.activity_id)
        return {"strava_id": strava_id, "outcome": "matched", "activity_id": existing.activity_id}

    plan_id = _plan_session_for(session, values["activity_date"], values["category"], values.get("duration_min"))
    activity = ActivityLog(**values, plan_id=plan_id)
    session.add(activity)
    session.flush()
    session.add(StravaImport(strava_id=strava_id, outcome="created", activity_id=activity.activity_id))
    session.commit()
    log.info("Strava activity %s imported as activity %s: %s on %s (plan %s)", strava_id, activity.activity_id,
             activity.category, activity.activity_date, plan_id or "none")
    return {"strava_id": strava_id, "outcome": "created", "activity_id": activity.activity_id, "plan_id": plan_id}


def strava_import_for(session: Session, strava_ids: list[int]) -> Optional[dict[str, Any]]:
    """The first of these Strava activities already dealt with, if any: {strava_id, activity_id}."""
    record = session.scalars(select(StravaImport).where(StravaImport.strava_id.in_(strava_ids))
                             .order_by(StravaImport.imported_at, StravaImport.strava_id)).first()
    return {"strava_id": record.strava_id, "activity_id": record.activity_id} if record else None


def tie_strava_duplicate(session: Session, strava_id: int, activity_id: int,
                         values: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Record a second recording of a workout already in the log (a watch and Peloton both
    uploading the same ride) against that entry instead of adding it again. Distance and output
    the entry is missing are filled in from `values`; nothing it already has is changed."""
    if session.get(StravaImport, strava_id) is not None:
        return {"strava_id": strava_id, "outcome": "known"}
    session.add(StravaImport(strava_id=strava_id, outcome="matched", activity_id=activity_id))
    activity = session.get(ActivityLog, activity_id)
    filled = []
    if activity is not None and values:
        for column in ("distance_mi", "output_kj"):
            if getattr(activity, column) is None and values.get(column) is not None:
                setattr(activity, column, values[column])
                filled.append(column)
    session.commit()
    log.info("Strava activity %s is a second recording of activity %s%s", strava_id, activity_id,
             f" (filled in {', '.join(filled)})" if filled else "")
    return {"strava_id": strava_id, "outcome": "duplicate", "activity_id": activity_id}


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
        log.info("History entry %s reverted (%s of activity %s)", history_id, entry.action, activity_id)
    except IntegrityError as e:
        session.rollback()
        raise ConflictError(f"Can't revert: {e.orig}") from e
    except Exception:
        session.rollback()
        raise

    restored = session.get(ActivityLog, activity_id)
    return {"history_id": new_entry_id, "activity": _plain_activity(restored) if restored else None}
