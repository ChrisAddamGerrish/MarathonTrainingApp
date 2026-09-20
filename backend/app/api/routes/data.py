"""Data aggregation route."""
from datetime import date

from fastapi import APIRouter

from backend.app.api.deps import SessionDep
from backend.app.repository.repository import activity_rows, plan_rows
from backend.app.services import planning

router = APIRouter(tags=["data"])


@router.get("/data")
def all_data(session: SessionDep):
    """Everything the front end needs in one round trip (the dataset is tiny)."""
    today = date.today()
    plan = planning.build_plan(plan_rows(session), today)
    activities = activity_rows(session)
    weeks = planning.build_weeks(plan, activities)
    summary = planning.build_summary(plan, activities, weeks, today)
    return {"summary": summary, "weeks": weeks, "plan": plan, "activities": activities}
