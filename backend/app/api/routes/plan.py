"""Training plan routes."""
from typing import Optional

from fastapi import APIRouter

from backend.app.api.deps import SessionDep
from backend.app.repository.repository import skip_session, unskip_session
from backend.app.schemas.schemas import SkipIn

router = APIRouter(prefix="/plan", tags=["plan"])


@router.put("/{plan_id}/skip")
def skip_plan_session(plan_id: str, session: SessionDep, body: Optional[SkipIn] = None):
    return skip_session(session, plan_id, body.reason if body else None)


@router.delete("/{plan_id}/skip")
def unskip_plan_session(plan_id: str, session: SessionDep):
    return unskip_session(session, plan_id)
