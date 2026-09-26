"""Training plan routes: editing the plan, and skipping sessions."""
from typing import Optional

from fastapi import APIRouter

from backend.app.api.deps import SessionDep
from backend.app.repository.repository import (
    create_plan_session,
    delete_plan_session,
    set_week_type,
    skip_session,
    unskip_session,
    update_plan_session,
)
from backend.app.schemas.schemas import PlanSessionIn, SkipIn, WeekIn

router = APIRouter(prefix="/plan", tags=["plan"])


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
