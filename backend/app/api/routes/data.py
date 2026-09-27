"""Data aggregation route."""
from datetime import date

from fastapi import APIRouter

from backend.app.api.deps import SessionDep
from backend.app.core.database import tenant_of
from backend.app.repository.repository import (
    activity_rows,
    gear_rows,
    hr_zones,
    personal_bests,
    plan_rows,
    plan_week_rows,
)
from backend.app.services import planning

router = APIRouter(tags=["data"])


@router.get("/data")
def all_data(session: SessionDep):
    """Everything the front end needs about the signed-in user, in one round trip (it's tiny)."""
    today = date.today()
    start = tenant_of(session).plan_start
    plan = planning.build_plan(plan_rows(session), today, start)
    activities = activity_rows(session)
    weeks = planning.build_weeks(plan_week_rows(session), plan, activities, start)
    summary = planning.build_summary(plan, activities, weeks, today, start)
    return {"summary": summary, "weeks": weeks, "plan": plan, "activities": activities,
            # From Strava (empty until a sync has fetched them)
            "gear": gear_rows(session), "personal_bests": personal_bests(session), "hr_zones": hr_zones(session)}
