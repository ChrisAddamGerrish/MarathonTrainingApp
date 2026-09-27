"""Data aggregation route."""
from datetime import date

from fastapi import APIRouter

from backend.app.api.deps import SessionDep
from backend.app.core.database import tenant_of
from backend.app.repository import gear_rows, hr_zones, personal_bests, training_overview
from backend.app.services import planning

router = APIRouter(tags=["data"])


@router.get("/data")
def all_data(session: SessionDep):
    """Everything the front end needs about the signed-in user, in one round trip (it's tiny)."""
    today = date.today()
    plan, activities, weeks = training_overview(session, today)
    summary = planning.build_summary(plan, activities, weeks, today, tenant_of(session).plan_start)
    return {"summary": summary, "weeks": weeks, "plan": plan, "activities": activities,
            # From Strava (empty until a sync has fetched them)
            "gear": gear_rows(session), "personal_bests": personal_bests(session), "hr_zones": hr_zones(session)}
