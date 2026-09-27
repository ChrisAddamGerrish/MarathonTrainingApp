"""All database access, written against the ORM models. Functions take a Session and return
plain dicts / raise domain errors, so callers never touch SQL or SQLAlchemy objects.

Sessions belong to one user (database.user_session): every query here only sees that user's rows
and new rows are theirs, without filtering by hand. Plan ids are unique per user, so plan rows are
looked up by query (_find_plan), never Session.get.

Every change to the data is logged here, once, whichever entry point (REST or MCP) made it."""
import logging
from datetime import date
from typing import Any, Optional

from sqlalchemy import delete, desc, func, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.database import tenant_of
from backend.app.core.migrations import backup_database
from backend.app.core.errors import ConflictError, NotFoundError, UnprocessableError
from backend.app.models import (
    ACTIVITY_COLUMNS,
    ActivityHistory,
    ActivityLog,
    ActivityMetrics,
    AthleteZones,
    Gear,
    PlanSkip,
    PlanVsActual,
    PlanWeek,
    StravaImport,
    TrainingPlan,
)
from backend.app.services import planning
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


def _activity(session: Session, a: ActivityLog) -> dict[str, Any]:
    return {**_plain_activity(a), "week": week_of(a.activity_date, tenant_of(session).plan_start)}


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
        select(TrainingPlan, PlanWeek.week_type, PlanVsActual, PlanSkip)
        .join(PlanWeek, (PlanWeek.user_id == TrainingPlan.user_id) & (PlanWeek.week == TrainingPlan.week))
        .join(PlanVsActual, (PlanVsActual.user_id == TrainingPlan.user_id)
              & (PlanVsActual.plan_id == TrainingPlan.plan_id))
        .outerjoin(PlanSkip, (PlanSkip.user_id == TrainingPlan.user_id) & (PlanSkip.plan_id == TrainingPlan.plan_id))
        .order_by(TrainingPlan.week, TrainingPlan.entry_order)
    )
    return [
        {
            "plan_id": p.plan_id,
            "week": p.week,
            "day": p.day,
            "week_type": week_type,
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
        for p, week_type, v, s in session.execute(stmt)
    ]


def plan_week_rows(session: Session) -> list[dict[str, Any]]:
    """The weeks of the plan, with their type, including weeks that have no sessions yet."""
    return [{"week": w.week, "week_type": w.week_type}
            for w in session.scalars(select(PlanWeek).order_by(PlanWeek.week))]


def _find_plan(session: Session, plan_id: str) -> Optional[TrainingPlan]:
    return session.scalars(select(TrainingPlan).where(TrainingPlan.plan_id == plan_id)).first()


def skip_session(session: Session, plan_id: str, reason: Optional[str]) -> dict[str, Any]:
    """Mark a planned session as deliberately skipped (optionally with a reason)."""
    plan = _get_plan(session, plan_id)
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
    plan = _find_plan(session, plan_id)
    if plan is None:
        raise NotFoundError(f"Unknown plan session '{plan_id}'")
    return plan


def _get_week(session: Session, week: int) -> PlanWeek:
    plan_week = session.scalars(select(PlanWeek).where(PlanWeek.week == week)).first()
    if plan_week is None:
        raise NotFoundError(f"The plan has no week {week}")
    return plan_week


def _new_plan_id(session: Session, week: int, day: str) -> str:
    """W3-Tue, or W3-Tue-2, -3... when that is taken. Ids never change afterwards, even if the
    session moves to another day: activities and skips refer to them."""
    base, n = f"W{week}-{day}", 1
    plan_id = base
    while _find_plan(session, plan_id) is not None:
        n += 1
        plan_id = f"{base}-{n}"
    return plan_id


def _drop_skip_if_rest(session: Session, plan: TrainingPlan) -> None:
    # Rest days can't be skipped, so a session turned into a rest day loses its skip.
    if plan.category == "Rest" and plan.skip is not None:
        session.delete(plan.skip)


def create_plan_session(session: Session, week: int, values: dict[str, Any]) -> dict[str, Any]:
    _get_week(session, week)  # 404 for a week the plan doesn't have
    plan = TrainingPlan(plan_id=_new_plan_id(session, week, values["day"]), week=week, **values)
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
    _get_week(session, week).week_type = week_type
    session.commit()
    log.info("Plan week %s set to %s", week, week_type)
    return {"week": week, "week_type": week_type}


def unskip_session(session: Session, plan_id: str) -> dict[str, Any]:
    skip = session.scalars(select(PlanSkip).where(PlanSkip.plan_id == plan_id)).first()
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
    """Every activity, newest first, each with its Strava summary ("strava", None if there's none)."""
    stmt = (
        select(ActivityLog, ActivityMetrics)
        .outerjoin(ActivityMetrics, ActivityMetrics.activity_id == ActivityLog.activity_id)
        .order_by(desc(ActivityLog.activity_date), desc(ActivityLog.activity_id))
    )
    return [{**_activity(session, a), "strava": _metrics_summary(m)} for a, m in session.execute(stmt)]


def training_overview(session: Session, today: date) -> tuple[list[dict], list[dict], list[dict]]:
    """(plan, activities, weeks): the plan with dates and statuses, every activity, and weekly totals."""
    start = tenant_of(session).plan_start
    plan = planning.build_plan(plan_rows(session), today, start)
    activities = activity_rows(session)
    weeks = planning.build_weeks(plan_week_rows(session), plan, activities, start)
    return plan, activities, weeks


def _get_activity(session: Session, activity_id: int) -> ActivityLog:
    activity = session.get(ActivityLog, activity_id)
    if activity is None:
        raise NotFoundError(f"Activity {activity_id} not found")
    return activity


def get_activity(session: Session, activity_id: int) -> dict[str, Any]:
    return _activity(session, _get_activity(session, activity_id))


def _check_plan_id(session: Session, plan_id: Optional[str]) -> None:
    if plan_id and _find_plan(session, plan_id) is None:
        raise UnprocessableError(f"Unknown plan session '{plan_id}'")


def create_activity(session: Session, values: dict[str, Any]) -> dict[str, Any]:
    _check_plan_id(session, values.get("plan_id"))
    activity = ActivityLog(**values)
    session.add(activity)
    session.commit()
    log.info("Activity %s created: %s on %s (plan %s)", activity.activity_id, activity.category,
             activity.activity_date, activity.plan_id or "none")
    return _activity(session, activity)


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
    return _activity(session, activity)


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
        .where(TrainingPlan.week == week_of(day, tenant_of(session).plan_start),
               TrainingPlan.category.in_(_same_kind(category)))
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


# --------------------------------------------------------------------------
# Restoring backups
# --------------------------------------------------------------------------

# Plan columns a backup carries (plus plan_id); what the app works out itself isn't in it.
_PLAN_BACKUP_COLUMNS = ["week", "day", "category", "run_subtype", "planned_session",
                        "target_distance_mi", "target_duration_min", "notes"]


def _activity_id_taken(session: Session, activity_id: int) -> bool:
    """Whether another user has (or had) an activity with this id. Ids are unique across users,
    so a backup can only bring an id back if it was never someone else's."""
    everyone = {"all_users": True}
    return (session.scalar(select(ActivityLog.activity_id).where(ActivityLog.activity_id == activity_id)
                           .execution_options(**everyone)) is not None
            or session.scalar(select(ActivityHistory.history_id)
                              .where(ActivityHistory.activity_id == activity_id,
                                     ActivityHistory.user_id != tenant_of(session).user_id)
                              .limit(1).execution_options(**everyone)) is not None)


def restore_activities(session: Session, rows: list[dict[str, Any]], apply: bool) -> dict[str, Any]:
    """Make the activity log match a backup (services/backup.parse_workouts).

    Rows with an id update that activity (or bring it back with that id if it was deleted), rows
    without one are added, and activities the backup doesn't have are deleted. Each of those is an
    ordinary change, so the history records it and it can be reverted one by one. With apply=False
    nothing is written: the counts say what would happen.
    """
    unknown = sorted({r["plan_id"] for r in rows if r["plan_id"]} - set(session.scalars(select(TrainingPlan.plan_id))))
    if unknown:
        raise UnprocessableError(f"The plan has no session {', '.join(unknown)}. Restore the plan backup first.")

    current = {a.activity_id: a for a in session.scalars(select(ActivityLog))}
    wanted = {r["activity_id"] for r in rows if r["activity_id"] is not None}
    added, changed, unchanged = 0, 0, 0
    for r in rows:
        values = {c: r[c] for c in ACTIVITY_COLUMNS}
        existing = current.get(r["activity_id"])
        if existing is None:
            added += 1
            if apply:
                keep_id = r["activity_id"] is not None and not _activity_id_taken(session, r["activity_id"])
                session.add(ActivityLog(activity_id=r["activity_id"] if keep_id else None, **values))
        elif any(getattr(existing, c) != v for c, v in values.items()):
            changed += 1
            if apply:
                for c, v in values.items():
                    setattr(existing, c, v)
        else:
            unchanged += 1
    removed = [a for i, a in current.items() if i not in wanted]
    result = {"added": added, "changed": changed, "removed": len(removed), "unchanged": unchanged}
    if not apply:
        return result

    result["backup"] = backup_database("before-workout-restore")
    for a in removed:
        session.delete(a)
    session.commit()
    log.info("Workouts restored from a backup: %(added)s added, %(changed)s changed, %(removed)s removed "
             "(database copied to %(backup)s)", result)
    return result


def restore_plan(session: Session, rows: list[dict[str, Any]], apply: bool) -> dict[str, Any]:
    """Replace the training plan (sessions, week types, skips) with a backup's
    (services/backup.parse_plan). Sessions keep the backup's order within each day. The plan gets
    as many weeks as the backup's last one; weeks it has no sessions for are Normal weeks.

    Logged activities that count toward a session the backup doesn't have are unlinked, which the
    history records. Plan edits have no history of their own, so the database is copied to
    data/backups/ first. With apply=False nothing is written: the counts say what would happen.
    """
    current = {p.plan_id: p for p in session.scalars(select(TrainingPlan))}
    skips = {s.plan_id: s.reason for s in session.scalars(select(PlanSkip))}
    week_types = {w.week: w.week_type for w in session.scalars(select(PlanWeek))}
    wanted = {r["plan_id"]: r for r in rows}
    added = [i for i in wanted if i not in current]
    removed = [i for i in current if i not in wanted]
    changed = [i for i, r in wanted.items() if i in current and (
        any(getattr(current[i], c) != r[c] for c in _PLAN_BACKUP_COLUMNS)
        or week_types.get(r["week"]) != r["week_type"]
        or (i in skips) != r["skipped"] or skips.get(i) != r["skip_reason"])]
    new_types = {r["week"]: r["week_type"] for r in rows}
    weeks = [{"user_id": tenant_of(session).user_id, "week": n, "week_type": new_types.get(n, "Normal")}
             for n in range(1, max(new_types) + 1)]
    orphans = list(session.scalars(select(ActivityLog).where(ActivityLog.plan_id.in_(removed))
                                   .order_by(ActivityLog.activity_date)))
    # A skipped session that has a logged activity isn't skipped; drop those skips.
    linked = {a.plan_id for a in session.scalars(select(ActivityLog).where(ActivityLog.plan_id.is_not(None)))}
    result = {"added": len(added), "changed": len(changed), "removed": len(removed),
              "unchanged": len(rows) - len(added) - len(changed), "unlinked": len(orphans)}
    if not apply:
        return result

    result["backup"] = backup_database("before-plan-restore")
    try:
        for a in orphans:
            a.plan_id = None
        session.flush()
        # Delete and re-add every session so rowid (the order within a day) follows the file.
        # Activities still point at the sessions being re-added, so check that at COMMIT only.
        session.execute(delete(PlanSkip))  # a write, so the transaction the pragma applies to has begun
        session.execute(text("PRAGMA defer_foreign_keys = ON"))
        session.execute(delete(TrainingPlan))
        session.execute(delete(PlanWeek))
        # Core inserts: nothing stamps the user on these, so it's spelled out.
        user_id = tenant_of(session).user_id
        session.execute(insert(PlanWeek.__table__), weeks)
        session.execute(insert(TrainingPlan.__table__),
                        [{"user_id": user_id, "plan_id": r["plan_id"], **{c: r[c] for c in _PLAN_BACKUP_COLUMNS}}
                         for r in rows])
        skipped = [{"user_id": user_id, "plan_id": r["plan_id"], "reason": r["skip_reason"]}
                   for r in rows if r["skipped"] and r["plan_id"] not in linked]
        if skipped:
            session.execute(insert(PlanSkip.__table__), skipped)
        session.commit()
    except IntegrityError as e:
        session.rollback()
        raise ConflictError(f"Can't restore the plan: {e.orig}") from e
    except Exception:
        session.rollback()
        raise
    log.info("Plan restored from a backup: %(added)s added, %(changed)s changed, %(removed)s removed, "
             "%(unlinked)s activities unlinked (database copied to %(backup)s)", result)
    return result


# --------------------------------------------------------------------------
# Strava metrics, zones and gear (services/strava.py fetches them)
# --------------------------------------------------------------------------

# What each activity carries in activity_rows; the rest (splits, laps, route...) is in activity_details.
_METRICS_SUMMARY = [
    "start_time", "elapsed_min", "elevation_gain_ft", "avg_hr", "max_hr", "avg_cadence", "avg_watts",
    "weighted_avg_watts", "max_watts", "avg_speed_mph", "max_speed_mph", "suffer_score", "pr_count", "trainer",
    "gear_id", "calories", "hr_zone_seconds",
]
_METRICS_DETAIL = _METRICS_SUMMARY + [
    "strava_id", "description", "device_name", "splits", "laps", "best_efforts", "polyline", "details_fetched_at",
]
DEFAULT_SHOE_MILES = 400.0  # a common rule of thumb: running shoes last roughly 300-500 miles


def _metric(m: ActivityMetrics, column: str) -> Any:
    value = getattr(m, column)
    # Averages stored before dropouts were filtered out (see strava.MIN_REAL_HR) aren't readings.
    return None if column == "avg_hr" and value is not None and value < 30 else value


def _metrics_summary(m: Optional[ActivityMetrics]) -> Optional[dict[str, Any]]:
    return {c: _metric(m, c) for c in _METRICS_SUMMARY} if m is not None else None


def save_metrics(session: Session, activity_id: int, values: dict[str, Any]) -> None:
    """Store (or update) an activity's Strava metrics. Nothing happens if the activity is gone.
    A summary (from the activity list) never replaces the full-resolution route of the detailed
    activity, nor a route with none."""
    if session.get(ActivityLog, activity_id) is None:
        return
    metrics = session.get(ActivityMetrics, activity_id)
    if metrics is None:
        session.add(ActivityMetrics(activity_id=activity_id, **values))
    else:
        keep_route = values.get("polyline") is None or (
            metrics.details_fetched_at is not None and "details_fetched_at" not in values)
        for column, value in values.items():
            if column == "polyline" and keep_route:
                continue
            setattr(metrics, column, value)
    session.commit()


def activities_needing_details(session: Session) -> list[tuple[int, int]]:
    """(activity id, Strava id) of imported activities without their detailed Strava activity yet,
    newest first. For a workout recorded twice, the recording its metrics came from wins."""
    stmt = (
        select(ActivityLog.activity_id, StravaImport.strava_id, StravaImport.outcome, ActivityMetrics)
        .join(StravaImport, StravaImport.activity_id == ActivityLog.activity_id)
        .outerjoin(ActivityMetrics, ActivityMetrics.activity_id == ActivityLog.activity_id)
        .order_by(desc(ActivityLog.activity_date), desc(ActivityLog.activity_id), StravaImport.strava_id)
    )
    chosen: dict[int, int] = {}
    for activity_id, strava_id, outcome, metrics in session.execute(stmt):
        if metrics is not None and metrics.details_fetched_at is not None:
            continue
        preferred = metrics.strava_id if metrics is not None else None
        if activity_id not in chosen or strava_id == preferred or (outcome == "created" and preferred is None):
            chosen[activity_id] = strava_id
    return list(chosen.items())


def activity_details(session: Session, activity_id: int) -> dict[str, Any]:
    """Everything Strava told us about one activity (404 if it has nothing)."""
    _get_activity(session, activity_id)
    metrics = session.get(ActivityMetrics, activity_id)
    if metrics is None:
        raise NotFoundError("This activity has no Strava data")
    gear = session.scalars(select(Gear).where(Gear.gear_id == metrics.gear_id)).first() if metrics.gear_id else None
    return {"activity_id": activity_id, **{c: _metric(metrics, c) for c in _METRICS_DETAIL},
            "gear_name": gear.name if gear else None}


def personal_bests(session: Session) -> list[dict[str, Any]]:
    """The fastest time for each of Strava's best-effort distances (1 mile, 5K, half marathon...),
    across the activities in the log, shortest distance first."""
    stmt = (
        select(ActivityLog, ActivityMetrics.best_efforts)
        .join(ActivityMetrics, ActivityMetrics.activity_id == ActivityLog.activity_id)
        .where(ActivityMetrics.best_efforts.is_not(None))
    )
    best: dict[str, dict[str, Any]] = {}
    for activity, efforts in session.execute(stmt):
        for e in efforts or []:
            if not e.get("name") or not e.get("elapsed_s"):
                continue
            if e["name"] not in best or e["elapsed_s"] < best[e["name"]]["elapsed_s"]:
                best[e["name"]] = {"name": e["name"], "distance_m": e.get("distance_m"), "elapsed_s": e["elapsed_s"],
                                   "activity_id": activity.activity_id, "date": activity.activity_date.isoformat(),
                                   "session": activity.actual_session}
    return sorted(best.values(), key=lambda b: b["distance_m"] or 0)


def hr_zones(session: Session) -> Optional[list[dict[str, Any]]]:
    zones = session.scalars(select(AthleteZones)).first()
    return zones.heart_rate if zones is not None else None


def save_zones(session: Session, heart_rate: Optional[list[Any]], power: Optional[list[Any]]) -> None:
    zones = session.scalars(select(AthleteZones)).first()
    if zones is None:
        session.add(AthleteZones(heart_rate=heart_rate, power=power))
    else:
        zones.heart_rate, zones.power = heart_rate, power
        zones.fetched_at = func.strftime("%Y-%m-%dT%H:%M:%SZ", "now")
    session.commit()


def _gear(g: Gear) -> dict[str, Any]:
    due = g.kind == "shoe" and g.replace_at_mi and not g.retired and g.distance_mi >= g.replace_at_mi
    return {"gear_id": g.gear_id, "kind": g.kind, "name": g.name, "distance_mi": g.distance_mi,
            "is_primary": g.is_primary, "retired": g.retired, "replace_at_mi": g.replace_at_mi, "replace_due": bool(due)}


def gear_rows(session: Session) -> list[dict[str, Any]]:
    """Shoes then bikes; in use before retired, the primary one first, then by distance."""
    stmt = select(Gear).order_by(desc(Gear.kind == "shoe"), Gear.retired, desc(Gear.is_primary), desc(Gear.distance_mi))
    return [_gear(g) for g in session.scalars(stmt)]


def save_gear(session: Session, items: list[dict[str, Any]]) -> None:
    """Bring the gear list in line with the athlete's Strava profile. New shoes get the default
    replacement mileage; one set in the app is kept. Gear no longer listed there counts as retired."""
    current = {g.gear_id: g for g in session.scalars(select(Gear))}
    for item in items:
        gear = current.pop(item["gear_id"], None)
        if gear is None:
            session.add(Gear(**item, replace_at_mi=DEFAULT_SHOE_MILES if item["kind"] == "shoe" else None))
        else:
            for column, value in item.items():
                setattr(gear, column, value)
            gear.updated_at = func.strftime("%Y-%m-%dT%H:%M:%SZ", "now")
    for gone in current.values():
        gone.retired = True
    session.commit()


def set_gear_replace_at(session: Session, gear_id: str, miles: Optional[float]) -> dict[str, Any]:
    gear = session.scalars(select(Gear).where(Gear.gear_id == gear_id)).first()
    if gear is None:
        raise NotFoundError(f"Unknown gear '{gear_id}'")
    gear.replace_at_mi = miles
    session.commit()
    log.info("Gear %s (%s): replace at %s mi", gear_id, gear.name, miles)
    return _gear(gear)
