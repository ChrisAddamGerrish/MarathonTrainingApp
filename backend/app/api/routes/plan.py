"""Training plan routes: editing the plan, and skipping sessions."""
from datetime import date
from typing import Optional

from fastapi import APIRouter

from backend.app.api.deps import SessionDep
from backend.app.repository import (
    create_plan_session,
    delete_plan_session,
    rebuild_plan,
    recent_fitness,
    set_week_type,
    skip_session,
    unskip_session,
    update_plan_session,
)
from backend.app.schemas import PlanSessionIn, RebuildIn, SkipIn, WeekIn
from backend.app.services import generator

router = APIRouter(prefix="/plan", tags=["plan"])


@router.get("/rebuild")
def rebuild_defaults(session: SessionDep):
    """What the rebuild form starts from: the runner's last four weeks, and the safe minimum."""
    fitness = recent_fitness(session, date.today())
    longest = fitness["longest_run_mi"] or 0
    return {**fitness, "min_long_run_mi": generator.MIN_LONG_RUN_MI, "min_weekly_mi": generator.MIN_WEEKLY_MI,
            "min_remaining_weeks": generator.min_weeks(longest, new_runner=False)}


@router.post("/rebuild")
def rebuild(body: RebuildIn, session: SessionDep, apply: bool = False):
    """Replace the weeks after this one with a generated plan. Without ?apply=true nothing is
    written: the response says what would change."""
    return rebuild_plan(session, date.today(), body.weeks, body.goal.model_dump(), apply)


@router.post("/weeks/{week}/sessions", status_code=201)
def add_plan_session(week: int, body: PlanSessionIn, session: SessionDep):
    return create_plan_session(session, week, body.model_dump())


@router.put("/weeks/{week}")
def change_week_type(week: int, body: WeekIn, session: SessionDep):
    return set_week_type(session, week, body.week_type)


@router.put("/{plan_id}")
def edit_plan_session(plan_id: str, body: PlanSessionIn, session: SessionDep):
    return update_plan_session(session, plan_id, body.model_dump())


@router.delete("/{plan_id}")
def remove_plan_session(plan_id: str, session: SessionDep):
    return delete_plan_session(session, plan_id)


@router.put("/{plan_id}/skip")
def skip_plan_session(plan_id: str, session: SessionDep, body: Optional[SkipIn] = None):
    return skip_session(session, plan_id, body.reason if body else None)


@router.delete("/{plan_id}/skip")
def unskip_plan_session(plan_id: str, session: SessionDep):
    return unskip_session(session, plan_id)
